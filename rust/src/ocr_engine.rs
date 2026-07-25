//! Optional PP-OCR inference through the pure-Rust RTen runtime.
//!
//! Model data is deliberately not embedded in the core wheel. The Python
//! layer locates the independently versioned `pylopdf-ocr-models` package and
//! constructs this engine with explicit detector, recognizer, and dictionary
//! paths.

use std::path::Path;
use std::sync::Arc;

use pyo3::prelude::*;
use rten::{Model, RunOptions, ThreadPool};
use rten_tensor::NdTensor;
use rten_tensor::prelude::*;

use crate::document::PdfError;
use crate::pixmap::Pixmap;

pyo3::create_exception!(
    pylopdf,
    OcrError,
    PdfError,
    "OCR model loading or inference failed."
);

const DETECTOR_THRESHOLD: f32 = 0.3;
const DETECTOR_BOX_THRESHOLD: f32 = 0.5;
const DETECTOR_UNCLIP_RATIO: f32 = 1.6;
const MAX_DETECTIONS: usize = 4096;
const RECOGNITION_HEIGHT: usize = 48;
const MAX_RECOGNITION_WIDTH: usize = 4096;
const COLUMN_GUTTER: f32 = 1.5;
const MAX_COLUMN_LINE_WIDTH_RATIO: f32 = 0.75;
const MIN_COLUMN_LINES: usize = 2;
const MIN_COLUMN_VERTICAL_OVERLAP: f32 = 0.25;
const MAX_COLUMN_DEPTH: usize = 8;

/// One internal OCR result in raster pixel coordinates.
type OcrTuple = (f32, f32, f32, f32, String, f32);

#[derive(Clone, Copy, Debug)]
struct Candidate {
    x0: f32,
    y0: f32,
    x1: f32,
    y1: f32,
    score: f32,
}

impl Candidate {
    fn width(self) -> f32 {
        self.x1 - self.x0
    }

    fn height(self) -> f32 {
        self.y1 - self.y0
    }

    fn area(self) -> f32 {
        self.width() * self.height()
    }

    fn intersection(self, other: Self) -> f32 {
        let width = (self.x1.min(other.x1) - self.x0.max(other.x0)).max(0.0);
        let height = (self.y1.min(other.y1) - self.y0.max(other.y0)).max(0.0);
        width * height
    }

    fn union(self, other: Self) -> Self {
        Self {
            x0: self.x0.min(other.x0),
            y0: self.y0.min(other.y0),
            x1: self.x1.max(other.x1),
            y1: self.y1.max(other.y1),
            score: self.score.max(other.score),
        }
    }
}

struct Engine {
    detector: Model,
    recognizer: Model,
    characters: Vec<String>,
    thread_pool: Arc<ThreadPool>,
}

impl Engine {
    fn load(
        detector_path: impl AsRef<Path>,
        recognizer_path: impl AsRef<Path>,
        dictionary_path: impl AsRef<Path>,
        threads: usize,
    ) -> Result<Self, String> {
        let detector_path = detector_path.as_ref();
        let recognizer_path = recognizer_path.as_ref();
        let dictionary_path = dictionary_path.as_ref();
        let detector = Model::load_file(detector_path).map_err(|error| {
            format!(
                "failed to load OCR detector {}: {error}",
                detector_path.display()
            )
        })?;
        let recognizer = Model::load_file(recognizer_path).map_err(|error| {
            format!(
                "failed to load OCR recognizer {}: {error}",
                recognizer_path.display()
            )
        })?;
        let dictionary = std::fs::read_to_string(dictionary_path).map_err(|error| {
            format!(
                "failed to read OCR dictionary {}: {error}",
                dictionary_path.display()
            )
        })?;
        let characters = dictionary.lines().map(str::to_owned).collect::<Vec<_>>();
        if characters.is_empty() {
            return Err(format!(
                "OCR dictionary {} contains no characters",
                dictionary_path.display()
            ));
        }
        Ok(Self {
            detector,
            recognizer,
            characters,
            thread_pool: Arc::new(ThreadPool::with_num_threads(threads)),
        })
    }

    fn run_options(&self) -> RunOptions {
        RunOptions::default().with_thread_pool(Some(Arc::clone(&self.thread_pool)))
    }

    fn recognize(
        &self,
        pixels: &[u8],
        width: usize,
        height: usize,
        tile_size: usize,
        overlap: usize,
        min_confidence: f32,
    ) -> Result<Vec<OcrTuple>, String> {
        let expected_len = width
            .checked_mul(height)
            .and_then(|value| value.checked_mul(4))
            .ok_or_else(|| "OCR pixmap dimensions overflow".to_owned())?;
        if pixels.len() != expected_len {
            return Err(format!(
                "OCR pixmap has {} bytes, expected {expected_len}",
                pixels.len()
            ));
        }

        let mut candidates = self.detect_tiled(pixels, width, height, tile_size, overlap)?;
        merge_candidates(&mut candidates);
        sort_candidates(&mut candidates);

        let mut results = Vec::with_capacity(candidates.len());
        for candidate in candidates {
            let (text, confidence) = self.recognize_candidate(pixels, width, height, candidate)?;
            if !text.trim().is_empty() && confidence >= min_confidence {
                results.push((
                    candidate.x0,
                    candidate.y0,
                    candidate.x1,
                    candidate.y1,
                    text,
                    confidence,
                ));
            }
        }
        Ok(results)
    }

    fn detect_tiled(
        &self,
        pixels: &[u8],
        width: usize,
        height: usize,
        tile_size: usize,
        overlap: usize,
    ) -> Result<Vec<Candidate>, String> {
        let x_starts = tile_starts(width, tile_size, overlap);
        let y_starts = tile_starts(height, tile_size, overlap);
        let mut candidates = Vec::new();

        for &tile_y in &y_starts {
            for &tile_x in &x_starts {
                let tile_width = tile_size.min(width - tile_x);
                let tile_height = tile_size.min(height - tile_y);
                let model_width = tile_width.next_multiple_of(32);
                let model_height = tile_height.next_multiple_of(32);
                let input = detector_input(
                    pixels,
                    width,
                    tile_x,
                    tile_y,
                    tile_width,
                    tile_height,
                    model_width,
                    model_height,
                );
                let probability: NdTensor<f32, 4> = self
                    .detector
                    .run_one((&input).into(), Some(self.run_options()))
                    .map_err(|error| format!("OCR detector inference failed: {error}"))?
                    .try_into()
                    .map_err(|_| {
                        "OCR detector output must be a four-dimensional f32 tensor".to_owned()
                    })?;
                let shape = probability.shape();
                if shape[0] != 1 || shape[1] != 1 {
                    return Err(format!(
                        "OCR detector output must have shape [1, 1, height, width], got {shape:?}"
                    ));
                }
                let probability_height = shape[2];
                let probability_width = shape[3];
                if probability_height == 0 || probability_width == 0 {
                    return Err("OCR detector returned an empty probability map".to_owned());
                }

                let scale_x = model_width as f32 / probability_width as f32;
                let scale_y = model_height as f32 / probability_height as f32;
                let remaining = MAX_DETECTIONS.saturating_sub(candidates.len());
                if remaining == 0 {
                    return Err(format!(
                        "OCR detector exceeded the {MAX_DETECTIONS}-region safety limit"
                    ));
                }
                let mut tile_candidates = connected_candidates(
                    &probability,
                    probability_width,
                    probability_height,
                    remaining,
                )?;
                for candidate in &mut tile_candidates {
                    candidate.x0 = (candidate.x0 * scale_x).min(tile_width as f32);
                    candidate.y0 = (candidate.y0 * scale_y).min(tile_height as f32);
                    candidate.x1 = (candidate.x1 * scale_x).min(tile_width as f32);
                    candidate.y1 = (candidate.y1 * scale_y).min(tile_height as f32);
                    candidate.x0 += tile_x as f32;
                    candidate.x1 += tile_x as f32;
                    candidate.y0 += tile_y as f32;
                    candidate.y1 += tile_y as f32;
                }
                candidates.extend(
                    tile_candidates
                        .into_iter()
                        .filter(|candidate| candidate.width() >= 3.0 && candidate.height() >= 3.0),
                );
                if candidates.len() > MAX_DETECTIONS {
                    return Err(format!(
                        "OCR detector exceeded the {MAX_DETECTIONS}-region safety limit"
                    ));
                }
            }
        }
        Ok(candidates)
    }

    fn recognize_candidate(
        &self,
        pixels: &[u8],
        image_width: usize,
        image_height: usize,
        candidate: Candidate,
    ) -> Result<(String, f32), String> {
        let x0 = candidate.x0.floor().max(0.0) as usize;
        let y0 = candidate.y0.floor().max(0.0) as usize;
        let x1 = candidate.x1.ceil().min(image_width as f32) as usize;
        let y1 = candidate.y1.ceil().min(image_height as f32) as usize;
        if x1 <= x0 || y1 <= y0 {
            return Ok((String::new(), 0.0));
        }

        let crop_width = x1 - x0;
        let crop_height = y1 - y0;
        let recognition_width =
            ((RECOGNITION_HEIGHT as f32 * crop_width as f32 / crop_height as f32).ceil() as usize)
                .clamp(8, MAX_RECOGNITION_WIDTH)
                .next_multiple_of(8)
                .min(MAX_RECOGNITION_WIDTH);
        let input = recognition_input(
            pixels,
            image_width,
            image_height,
            x0,
            y0,
            crop_width,
            crop_height,
            recognition_width,
        );
        let output: NdTensor<f32, 3> = self
            .recognizer
            .run_one((&input).into(), Some(self.run_options()))
            .map_err(|error| format!("OCR recognizer inference failed: {error}"))?
            .try_into()
            .map_err(|_| {
                "OCR recognizer output must be a three-dimensional f32 tensor".to_owned()
            })?;
        let shape = output.shape();
        if shape[0] != 1 {
            return Err(format!(
                "OCR recognizer output must have shape [1, sequence, classes], got {shape:?}"
            ));
        }
        let expected_classes = self.characters.len() + 2;
        if shape[2] != expected_classes {
            return Err(format!(
                "OCR recognizer produced {} classes but its dictionary requires {expected_classes}",
                shape[2]
            ));
        }

        let mut previous = usize::MAX;
        let mut text = String::new();
        let mut confidence_sum = 0.0;
        let mut confidence_count = 0usize;
        for step in 0..shape[1] {
            let mut best_index = 0usize;
            let mut best_value = f32::NEG_INFINITY;
            for class_index in 0..shape[2] {
                let value = output[[0, step, class_index]];
                if value > best_value {
                    best_index = class_index;
                    best_value = value;
                }
            }
            if best_index != 0 && best_index != previous {
                if best_index == self.characters.len() + 1 {
                    text.push(' ');
                } else if let Some(token) = self.characters.get(best_index - 1) {
                    text.push_str(token);
                }
                confidence_sum += best_value.clamp(0.0, 1.0);
                confidence_count += 1;
            }
            previous = best_index;
        }
        let confidence = if confidence_count == 0 {
            0.0
        } else {
            confidence_sum / confidence_count as f32
        };
        Ok((text, confidence))
    }
}

fn tile_starts(length: usize, tile_size: usize, overlap: usize) -> Vec<usize> {
    if length <= tile_size {
        return vec![0];
    }
    let step = tile_size - overlap;
    let mut starts = vec![0];
    let mut next = step;
    while next + tile_size < length {
        starts.push(next);
        next += step;
    }
    let final_start = length - tile_size;
    if starts.last().copied() != Some(final_start) {
        starts.push(final_start);
    }
    starts
}

#[allow(clippy::too_many_arguments)]
fn detector_input(
    pixels: &[u8],
    image_width: usize,
    tile_x: usize,
    tile_y: usize,
    tile_width: usize,
    tile_height: usize,
    model_width: usize,
    model_height: usize,
) -> NdTensor<f32, 4> {
    let mut data = vec![1.0; 3 * model_height * model_width];
    let plane = model_height * model_width;
    for y in 0..tile_height {
        for x in 0..tile_width {
            let source_offset = ((tile_y + y) * image_width + tile_x + x) * 4;
            let alpha = pixels[source_offset + 3] as f32 / 255.0;
            for channel in 0..3 {
                let source = pixels[source_offset + channel] as f32;
                let composite = source * alpha + 255.0 * (1.0 - alpha);
                data[channel * plane + y * model_width + x] = composite / 127.5 - 1.0;
            }
        }
    }
    NdTensor::from_data([1, 3, model_height, model_width], data)
}

fn connected_candidates(
    probability: &NdTensor<f32, 4>,
    width: usize,
    height: usize,
    limit: usize,
) -> Result<Vec<Candidate>, String> {
    let mut mask = vec![false; width * height];
    for y in 0..height {
        for x in 0..width {
            mask[y * width + x] = probability[[0, 0, y, x]] > DETECTOR_THRESHOLD;
        }
    }
    let mut dilated = vec![false; mask.len()];
    for y in 0..height {
        for x in 0..width {
            let x0 = x.saturating_sub(1);
            let y0 = y.saturating_sub(1);
            let x1 = (x + 1).min(width - 1);
            let y1 = (y + 1).min(height - 1);
            dilated[y * width + x] =
                (y0..=y1).any(|near_y| (x0..=x1).any(|near_x| mask[near_y * width + near_x]));
        }
    }

    let mut seen = vec![false; dilated.len()];
    let mut stack = Vec::new();
    let mut candidates = Vec::new();
    for start_y in 0..height {
        for start_x in 0..width {
            let start = start_y * width + start_x;
            if !dilated[start] || seen[start] {
                continue;
            }
            seen[start] = true;
            stack.push((start_x, start_y));
            let (mut x0, mut y0, mut x1, mut y1) = (start_x, start_y, start_x, start_y);
            let mut probability_sum = 0.0;
            let mut probability_count = 0usize;
            while let Some((x, y)) = stack.pop() {
                x0 = x0.min(x);
                y0 = y0.min(y);
                x1 = x1.max(x);
                y1 = y1.max(y);
                let value = probability[[0, 0, y, x]];
                if value > DETECTOR_THRESHOLD {
                    probability_sum += value;
                    probability_count += 1;
                }
                let mut visit = |next_x: usize, next_y: usize| {
                    let index = next_y * width + next_x;
                    if dilated[index] && !seen[index] {
                        seen[index] = true;
                        stack.push((next_x, next_y));
                    }
                };
                if x > 0 {
                    visit(x - 1, y);
                }
                if x + 1 < width {
                    visit(x + 1, y);
                }
                if y > 0 {
                    visit(x, y - 1);
                }
                if y + 1 < height {
                    visit(x, y + 1);
                }
            }
            if probability_count < 6 {
                continue;
            }
            let score = probability_sum / probability_count as f32;
            if score < DETECTOR_BOX_THRESHOLD {
                continue;
            }
            let component_width = (x1 - x0 + 1) as f32;
            let component_height = (y1 - y0 + 1) as f32;
            let perimeter = 2.0 * (component_width + component_height);
            let padding =
                (component_width * component_height * DETECTOR_UNCLIP_RATIO / perimeter).max(1.0);
            candidates.push(Candidate {
                x0: (x0 as f32 - padding).max(0.0),
                y0: (y0 as f32 - padding).max(0.0),
                x1: (x1 as f32 + 1.0 + padding).min(width as f32),
                y1: (y1 as f32 + 1.0 + padding).min(height as f32),
                score,
            });
            if candidates.len() > limit {
                return Err(format!(
                    "OCR detector exceeded the {MAX_DETECTIONS}-region safety limit"
                ));
            }
        }
    }
    Ok(candidates)
}

fn merge_candidates(candidates: &mut Vec<Candidate>) {
    fn root(parents: &mut [usize], mut index: usize) -> usize {
        let mut result = index;
        while parents[result] != result {
            result = parents[result];
        }
        while parents[index] != index {
            let next = parents[index];
            parents[index] = result;
            index = next;
        }
        result
    }

    let mut parents = (0..candidates.len()).collect::<Vec<_>>();
    for index in 0..candidates.len() {
        for other_index in index + 1..candidates.len() {
            let left = candidates[index];
            let right = candidates[other_index];
            let intersection = left.intersection(right);
            let overlap_ratio = intersection / left.area().min(right.area()).max(1.0);
            let vertical_overlap = (left.y1.min(right.y1) - left.y0.max(right.y0)).max(0.0)
                / left.height().min(right.height()).max(1.0);
            let height_ratio =
                left.height().max(right.height()) / left.height().min(right.height()).max(1.0);
            if overlap_ratio >= 0.35
                || (intersection > 0.0 && vertical_overlap >= 0.65 && height_ratio <= 2.5)
            {
                let left_root = root(&mut parents, index);
                let right_root = root(&mut parents, other_index);
                if left_root != right_root {
                    parents[right_root] = left_root;
                }
            }
        }
    }

    let mut groups = vec![None; candidates.len()];
    for (index, candidate) in candidates.iter().copied().enumerate() {
        let group = root(&mut parents, index);
        groups[group] = Some(
            groups[group]
                .map(|existing: Candidate| existing.union(candidate))
                .unwrap_or(candidate),
        );
    }
    *candidates = groups.into_iter().flatten().collect();
}

fn sort_top_to_bottom(candidates: &mut Vec<Candidate>) {
    candidates.sort_by(|left, right| {
        ((left.y0 + left.y1) * 0.5)
            .total_cmp(&((right.y0 + right.y1) * 0.5))
            .then_with(|| left.x0.total_cmp(&right.x0))
    });
    let mut rows: Vec<Vec<Candidate>> = Vec::new();
    for candidate in candidates.drain(..) {
        let same_row = rows
            .last()
            .and_then(|row| row.first())
            .is_some_and(|anchor| {
                let tolerance = anchor.height().min(candidate.height()).max(1.0) * 0.5;
                (anchor.y0 - candidate.y0).abs() <= tolerance
            });
        if same_row {
            rows.last_mut()
                .expect("a matching row was found immediately before")
                .push(candidate);
        } else {
            rows.push(vec![candidate]);
        }
    }
    for row in &mut rows {
        row.sort_by(|left, right| left.x0.total_cmp(&right.x0));
    }
    *candidates = rows.into_iter().flatten().collect();
}

fn typical_candidate_height(candidates: &[Candidate]) -> f32 {
    let mut heights = candidates
        .iter()
        .map(|candidate| candidate.height())
        .filter(|height| height.is_finite() && *height > 0.0)
        .collect::<Vec<_>>();
    if heights.is_empty() {
        return 12.0;
    }
    heights.sort_by(f32::total_cmp);
    heights[heights.len() / 2]
}

fn candidate_column_boundary(candidates: &[Candidate]) -> Option<f32> {
    if candidates.len() < MIN_COLUMN_LINES * 2 {
        return None;
    }
    let region_x0 = candidates
        .iter()
        .map(|candidate| candidate.x0)
        .reduce(f32::min)?;
    let region_x1 = candidates
        .iter()
        .map(|candidate| candidate.x1)
        .reduce(f32::max)?;
    let region_width = region_x1 - region_x0;
    if !region_width.is_finite() || region_width <= 0.0 {
        return None;
    }

    let mut intervals = candidates
        .iter()
        .filter(|candidate| candidate.width() <= region_width * MAX_COLUMN_LINE_WIDTH_RATIO)
        .map(|candidate| (candidate.x0, candidate.x1))
        .collect::<Vec<_>>();
    intervals.sort_by(|left, right| left.0.total_cmp(&right.0));
    let first = intervals.first().copied()?;
    let mut merged = vec![first];
    for (x0, x1) in intervals.into_iter().skip(1) {
        let last = merged
            .last_mut()
            .expect("the first interval was inserted immediately before");
        if x0 <= last.1 {
            last.1 = last.1.max(x1);
        } else {
            merged.push((x0, x1));
        }
    }

    let minimum_gap = (typical_candidate_height(candidates) * COLUMN_GUTTER).max(12.0);
    merged
        .windows(2)
        .filter_map(|pair| {
            let gap = pair[1].0 - pair[0].1;
            (gap >= minimum_gap).then_some((gap, (pair[0].1 + pair[1].0) * 0.5))
        })
        .max_by(|left, right| left.0.total_cmp(&right.0))
        .map(|(_, boundary)| boundary)
}

fn candidate_side_extent(
    candidates: &[Candidate],
    boundary: f32,
    left_side: bool,
) -> Option<(f32, f32, usize)> {
    let mut y0 = f32::INFINITY;
    let mut y1 = f32::NEG_INFINITY;
    let mut count = 0usize;
    for candidate in candidates {
        let belongs = if left_side {
            candidate.x1 <= boundary
        } else {
            candidate.x0 >= boundary
        };
        if belongs {
            y0 = y0.min(candidate.y0);
            y1 = y1.max(candidate.y1);
            count += 1;
        }
    }
    (count > 0).then_some((y0, y1, count))
}

fn valid_candidate_column_split(candidates: &[Candidate], boundary: f32) -> bool {
    let Some((left_y0, left_y1, left_count)) = candidate_side_extent(candidates, boundary, true)
    else {
        return false;
    };
    let Some((right_y0, right_y1, right_count)) =
        candidate_side_extent(candidates, boundary, false)
    else {
        return false;
    };
    if left_count < MIN_COLUMN_LINES || right_count < MIN_COLUMN_LINES {
        return false;
    }
    let overlap = left_y1.min(right_y1) - left_y0.max(right_y0);
    let shorter_height = (left_y1 - left_y0).min(right_y1 - right_y0);
    overlap > 0.0 && shorter_height > 0.0 && overlap / shorter_height >= MIN_COLUMN_VERTICAL_OVERLAP
}

fn order_candidate_columns(candidates: Vec<Candidate>, depth: usize) -> Vec<Candidate> {
    if depth >= MAX_COLUMN_DEPTH {
        return candidates;
    }
    let Some(boundary) = candidate_column_boundary(&candidates) else {
        return candidates;
    };
    if !valid_candidate_column_split(&candidates, boundary) {
        return candidates;
    }

    let side_centers = candidates
        .iter()
        .filter(|candidate| candidate.x1 <= boundary || candidate.x0 >= boundary)
        .map(|candidate| (candidate.y0 + candidate.y1) * 0.5)
        .collect::<Vec<_>>();
    let Some(first_center) = side_centers.iter().copied().reduce(f32::min) else {
        return candidates;
    };
    let Some(last_center) = side_centers.iter().copied().reduce(f32::max) else {
        return candidates;
    };
    if candidates.iter().any(|candidate| {
        let center = (candidate.y0 + candidate.y1) * 0.5;
        candidate.x0 < boundary
            && candidate.x1 > boundary
            && center > first_center
            && center < last_center
    }) {
        return candidates;
    }

    let mut top = Vec::new();
    let mut left = Vec::new();
    let mut right = Vec::new();
    let mut bottom = Vec::new();
    for candidate in candidates {
        if candidate.x1 <= boundary {
            left.push(candidate);
        } else if candidate.x0 >= boundary {
            right.push(candidate);
        } else if (candidate.y0 + candidate.y1) * 0.5 <= first_center {
            top.push(candidate);
        } else {
            bottom.push(candidate);
        }
    }

    top.extend(order_candidate_columns(left, depth + 1));
    top.extend(order_candidate_columns(right, depth + 1));
    top.extend(bottom);
    top
}

fn sort_candidates(candidates: &mut Vec<Candidate>) {
    sort_top_to_bottom(candidates);
    *candidates = order_candidate_columns(std::mem::take(candidates), 0);
}

#[allow(clippy::too_many_arguments)]
fn recognition_input(
    pixels: &[u8],
    image_width: usize,
    image_height: usize,
    crop_x: usize,
    crop_y: usize,
    crop_width: usize,
    crop_height: usize,
    output_width: usize,
) -> NdTensor<f32, 4> {
    let mut data = vec![0.0; 3 * RECOGNITION_HEIGHT * output_width];
    let plane = RECOGNITION_HEIGHT * output_width;
    for output_y in 0..RECOGNITION_HEIGHT {
        let source_y = ((output_y as f32 + 0.5) * crop_height as f32 / RECOGNITION_HEIGHT as f32
            - 0.5)
            .clamp(0.0, (crop_height - 1) as f32);
        let y0 = source_y.floor() as usize;
        let y1 = (y0 + 1).min(crop_height - 1);
        let y_weight = source_y - y0 as f32;
        for output_x in 0..output_width {
            let source_x = ((output_x as f32 + 0.5) * crop_width as f32 / output_width as f32
                - 0.5)
                .clamp(0.0, (crop_width - 1) as f32);
            let x0 = source_x.floor() as usize;
            let x1 = (x0 + 1).min(crop_width - 1);
            let x_weight = source_x - x0 as f32;
            for channel in 0..3 {
                let sample = |x: usize, y: usize| {
                    let image_x = (crop_x + x).min(image_width - 1);
                    let image_y = (crop_y + y).min(image_height - 1);
                    let offset = (image_y * image_width + image_x) * 4;
                    let alpha = pixels[offset + 3] as f32 / 255.0;
                    pixels[offset + channel] as f32 * alpha + 255.0 * (1.0 - alpha)
                };
                let top = sample(x0, y0) * (1.0 - x_weight) + sample(x1, y0) * x_weight;
                let bottom = sample(x0, y1) * (1.0 - x_weight) + sample(x1, y1) * x_weight;
                let value = top * (1.0 - y_weight) + bottom * y_weight;
                data[channel * plane + output_y * output_width + output_x] = value / 127.5 - 1.0;
            }
        }
    }
    NdTensor::from_data([1, 3, RECOGNITION_HEIGHT, output_width], data)
}

/// Loaded OCR model set. Python owns rendering and coordinate conversion.
#[pyclass(frozen, module = "pylopdf.pylopdf_core")]
pub struct _OcrEngine {
    engine: Engine,
}

#[pymethods]
impl _OcrEngine {
    #[new]
    fn new(
        py: Python<'_>,
        detector_path: &str,
        recognizer_path: &str,
        dictionary_path: &str,
        threads: usize,
    ) -> PyResult<Self> {
        if !(1..=16).contains(&threads) {
            return Err(OcrError::new_err("threads must be from 1 through 16"));
        }
        py.detach(|| Engine::load(detector_path, recognizer_path, dictionary_path, threads))
            .map(|engine| Self { engine })
            .map_err(OcrError::new_err)
    }

    #[pyo3(signature = (pixmap, *, tile_size=1408, overlap=192, min_confidence=0.5))]
    fn recognize_pixmap(
        &self,
        py: Python<'_>,
        pixmap: PyRef<'_, Pixmap>,
        tile_size: usize,
        overlap: usize,
        min_confidence: f32,
    ) -> PyResult<Vec<OcrTuple>> {
        if !(256..=2048).contains(&tile_size) || !tile_size.is_multiple_of(32) {
            return Err(OcrError::new_err(
                "tile_size must be a multiple of 32 from 256 through 2048",
            ));
        }
        if overlap < 32 || overlap > tile_size / 2 {
            return Err(OcrError::new_err(
                "overlap must be from 32 through half of tile_size",
            ));
        }
        if !(0.0..=1.0).contains(&min_confidence) || !min_confidence.is_finite() {
            return Err(OcrError::new_err(
                "min_confidence must be a finite number from 0 through 1",
            ));
        }
        let pixels = Arc::clone(&pixmap.data);
        let width = pixmap.width as usize;
        let height = pixmap.height as usize;
        drop(pixmap);
        py.detach(|| {
            self.engine
                .recognize(&pixels, width, height, tile_size, overlap, min_confidence)
                .map_err(OcrError::new_err)
        })
    }

    fn __repr__(&self) -> &'static str {
        "<pylopdf.OcrEngine native models>"
    }
}
