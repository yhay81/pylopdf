//! New documents that krilla writes as a whole: `pylopdf.create()`.
//!
//! A builder collects the recordings of its page canvases and, when it
//! finishes, replays them onto one krilla document with the document's
//! metadata, its structure tree when tagged, and the validators of the
//! requested standards. krilla refuses output that violates a standard, so a
//! finished document is either conformant as far as krilla can check or not
//! written at all.

use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::Arc;

use krilla::Document as KrillaDocument;
use krilla::SerializeSettings;
use krilla::configure::{Accessibility, Archival, Configuration, ConfigurationBuilder, Validator};
use krilla::destination::XyzDestination;
use krilla::error::KrillaError;
use krilla::geom::Point;
use krilla::metadata::{DateTime, Metadata};
use krilla::outline::{Outline, OutlineNode};
use krilla::page::PageSettings;
use krilla::tagging::TagTree;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

use crate::canvas::{_Canvas, CanvasRecording};
use crate::document::{PdfError, limit_err};
use crate::generate::{self, GenerationFace};

/// Pages that one builder may hold until it finishes.
const MAX_BUILDER_PAGES: usize = 65_536;

/// UTF-8 bytes of a builder's metadata texts together.
const MAX_METADATA_SIZE: usize = 1024 * 1024;

/// Outline entries that one document may hold.
const MAX_BOOKMARKS: usize = 65_536;

/// A creation date: year, month, day, hour, minute, second, and the offset
/// from UTC in minutes.
type Created = (u16, u8, u8, u8, u8, u8, i16);

/// The standards that `pylopdf.create(standard=...)` accepts.
fn validator(name: &str) -> Option<Validator> {
    Some(match name {
        "pdf/a-1a" => Validator::A(Archival::A1_A),
        "pdf/a-1b" => Validator::A(Archival::A1_B),
        "pdf/a-2a" => Validator::A(Archival::A2_A),
        "pdf/a-2b" => Validator::A(Archival::A2_B),
        "pdf/a-2u" => Validator::A(Archival::A2_U),
        "pdf/a-3a" => Validator::A(Archival::A3_A),
        "pdf/a-3b" => Validator::A(Archival::A3_B),
        "pdf/a-3u" => Validator::A(Archival::A3_U),
        "pdf/a-4" => Validator::A(Archival::A4),
        "pdf/a-4e" => Validator::A(Archival::A4E),
        "pdf/a-4f" => Validator::A(Archival::A4F),
        "pdf/ua-1" => Validator::Ua(Accessibility::UA1),
        _ => return None,
    })
}

/// The text of a krilla error; validation errors are listed one by one.
fn krilla_error(error: KrillaError) -> PyErr {
    match error {
        KrillaError::Validation(errors) => {
            let listed: Vec<String> = errors
                .iter()
                .map(|(error, _)| format!("{error:?}"))
                .collect();
            PdfError::new_err(format!(
                "the document violates the requested standard: {}",
                listed.join("; ")
            ))
        }
        other => PdfError::new_err(format!("krilla could not write the document: {other}")),
    }
}

/// A new document's settings and the pages drawn so far.
#[pyclass(module = "pylopdf.pylopdf_core")]
pub struct _Builder {
    configuration: Configuration,
    tagged: bool,
    language: Option<String>,
    title: Option<String>,
    author: Option<String>,
    subject: Option<String>,
    keywords: Vec<String>,
    created: Option<Created>,
    pages: Vec<CanvasRecording>,
}

#[pymethods]
impl _Builder {
    /// Start a new document that conforms to `standards`, tagged when
    /// `tagged` is true or a standard requires it.
    #[new]
    #[pyo3(signature = (standards, tagged, language=None, title=None, author=None, subject=None, keywords=Vec::new(), created=None))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        standards: Vec<String>,
        tagged: bool,
        language: Option<String>,
        title: Option<String>,
        author: Option<String>,
        subject: Option<String>,
        keywords: Vec<String>,
        created: Option<Created>,
    ) -> PyResult<Self> {
        let mut builder = ConfigurationBuilder::new();
        for name in &standards {
            let validator = validator(name)
                .ok_or_else(|| PdfError::new_err(format!("unknown standard {name:?}")))?;
            builder = builder.set_validator(validator);
        }
        let configuration = builder.finish().map_err(|error| {
            PdfError::new_err(format!(
                "the requested standards cannot be combined: {error:?}"
            ))
        })?;
        let size = [&language, &title, &author, &subject]
            .iter()
            .map(|text| text.as_ref().map_or(0, String::len))
            .sum::<usize>()
            + keywords.iter().map(String::len).sum::<usize>();
        if size > MAX_METADATA_SIZE {
            return Err(limit_err(
                "metadata_input_size",
                format!("the document metadata exceeds the {MAX_METADATA_SIZE}-byte limit"),
            ));
        }
        Ok(Self {
            configuration,
            tagged,
            language,
            title,
            author,
            subject,
            keywords,
            created,
            pages: Vec::new(),
        })
    }

    /// The number of pages added so far.
    fn page_count(&self) -> usize {
        self.pages.len()
    }

    /// Whether drawing outside tag scopes is marked as artifacts and tag
    /// scopes build a structure tree.
    fn is_tagged(&self) -> bool {
        self.tagged
    }

    /// Append the page that a canvas drew, which leaves the canvas empty.
    fn add_page(&mut self, canvas: &Bound<'_, _Canvas>) -> PyResult<()> {
        let mut canvas = canvas.borrow_mut();
        if !canvas.is_new_document() {
            return Err(PdfError::new_err(
                "the canvas draws a page of an open document",
            ));
        }
        if self.pages.len() >= MAX_BUILDER_PAGES {
            return Err(limit_err(
                "page_count",
                format!("a new document holds at most {MAX_BUILDER_PAGES} pages"),
            ));
        }
        self.pages.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!("failed to grow the document's pages: {error}"))
        })?;
        let recording = canvas.take_recording()?;
        self.pages.push(recording);
        Ok(())
    }

    /// Write the document and return its bytes. The builder keeps its pages,
    /// so a refusal can be corrected and the builder finished again.
    fn finish<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        let pdf = py.detach(|| {
            catch_unwind(AssertUnwindSafe(|| self.write())).unwrap_or_else(|_| {
                Err(PdfError::new_err(
                    "krilla panicked while writing the document",
                ))
            })
        })?;
        Ok(PyBytes::new(py, &pdf))
    }
}

impl _Builder {
    /// Build the outline from every page's bookmarks, nesting each entry
    /// under the closest earlier entry one level up.
    fn outline(&self) -> PyResult<Option<Outline>> {
        let count: usize = self.pages.iter().map(|page| page.bookmarks.len()).sum();
        if count == 0 {
            return Ok(None);
        }
        if count > MAX_BOOKMARKS {
            return Err(limit_err(
                "toc_entry_count",
                format!("a new document holds at most {MAX_BOOKMARKS} bookmarks"),
            ));
        }
        let mut outline = Outline::new();
        let mut open: Vec<(u16, OutlineNode)> = Vec::new();
        for (index, page) in self.pages.iter().enumerate() {
            for bookmark in &page.bookmarks {
                let deepest = open.last().map_or(0, |(level, _)| *level);
                if bookmark.level > deepest + 1 {
                    return Err(PdfError::new_err(format!(
                        "the bookmark {:?} at level {} follows level {deepest}; levels grow one at a time",
                        bookmark.title, bookmark.level
                    )));
                }
                while open
                    .last()
                    .is_some_and(|(level, _)| *level >= bookmark.level)
                {
                    let (_, node) = open.pop().ok_or_else(|| PdfError::new_err("outline"))?;
                    match open.last_mut() {
                        Some((_, parent)) => parent.push_child(node),
                        None => outline.push_child(node),
                    }
                }
                let point = Point::from_xy(bookmark.at.0 as f32, bookmark.at.1 as f32);
                open.push((
                    bookmark.level,
                    OutlineNode::new(bookmark.title.clone(), XyzDestination::new(index, point)),
                ));
            }
        }
        while let Some((_, node)) = open.pop() {
            match open.last_mut() {
                Some((_, parent)) => parent.push_child(node),
                None => outline.push_child(node),
            }
        }
        Ok(Some(outline))
    }

    fn write(&self) -> PyResult<Vec<u8>> {
        let settings = SerializeSettings {
            configuration: self.configuration,
            enable_tagging: self.tagged,
            ..SerializeSettings::default()
        };
        let mut document = KrillaDocument::new_with(settings);
        let mut metadata = Metadata::new();
        if let Some(title) = &self.title {
            metadata = metadata.title(title.clone());
        }
        if let Some(author) = &self.author {
            metadata = metadata.authors(vec![author.clone()]);
        }
        if let Some(subject) = &self.subject {
            metadata = metadata.description(subject.clone());
        }
        if !self.keywords.is_empty() {
            metadata = metadata.keywords(self.keywords.clone());
        }
        if let Some(language) = &self.language {
            metadata = metadata.language(language.clone());
        }
        if let Some((year, month, day, hour, minute, second, offset)) = self.created {
            metadata = metadata.creation_date(
                DateTime::new(year)
                    .month(month)
                    .day(day)
                    .hour(hour)
                    .minute(minute)
                    .second(second)
                    .utc_offset_hour((offset / 60) as i8)
                    .utc_offset_minute(offset.unsigned_abs().rem_euclid(60) as u8),
            );
        }
        metadata = metadata.producer(format!("pylopdf {}", env!("CARGO_PKG_VERSION")));
        document.set_metadata(metadata);
        let mut tree = TagTree::new().with_lang(self.language.clone());
        let mut nodes = Vec::new();
        // One krilla font per face, shared by every page that draws it, so
        // the document embeds one subset of each face.
        let mut known: Vec<(GenerationFace, krilla::text::Font)> = Vec::new();
        for recording in &self.pages {
            let mut fonts = Vec::new();
            for face in recording.faces() {
                let (data, index) = face.source();
                let cached = known.iter().find(|(seen, _)| {
                    let (seen_data, seen_index) = seen.source();
                    Arc::ptr_eq(&seen_data, &data)
                        && seen_index == index
                        && seen.weight() == face.weight()
                });
                let font = match cached {
                    Some((_, font)) => font.clone(),
                    None => {
                        let font = face.embedding_font().map_err(PdfError::new_err)?;
                        known.push((face.clone(), font.clone()));
                        font
                    }
                };
                fonts.push(font);
            }
            let (width, height) = recording.size;
            let settings = PageSettings::from_wh(
                generate::finite_f32(width, "page width").map_err(PdfError::new_err)?,
                generate::finite_f32(height, "page height").map_err(PdfError::new_err)?,
            )
            .ok_or_else(|| PdfError::new_err("the page size is outside krilla's range"))?;
            let mut page = document.start_page_with(settings);
            let mut surface = page.surface();
            let tags = self.tagged.then_some(&mut nodes);
            recording
                .draw_on(&mut surface, &fonts, tags)
                .map_err(PdfError::new_err)?;
            surface.finish();
            page.finish();
        }
        if let Some(outline) = self.outline()? {
            document.set_outline(outline);
        }
        if self.tagged {
            for node in nodes {
                tree.push(node);
            }
            document.set_tag_tree(tree);
        }
        document.finish().map_err(krilla_error)
    }
}
