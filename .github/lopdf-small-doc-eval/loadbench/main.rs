//! Load-time A/B harness for lopdf's rayon dispatch.
//!
//! loadbench info FILE...                 entry counts, bytes and a digest per file
//! loadbench warm ITERS FILE...           back-to-back loads (pool kept spinning)
//! loadbench cold ITERS GAP_MS FILE...    loads separated by GAP_MS of sleep (pool parked)
//! loadbench after ITERS GAP_MS FILE...   a fixed calling-thread task timed right after each cold load
//! loadbench first FILE                   the first load in this process (pool not spawned yet)
//! loadbench objstm ITERS GAP_MS FILE     ObjectStream::new on every object stream, from the main thread
//! loadbench mk pages P OUT               synthetic P-page document with a cross-reference table
//! loadbench mk objstm P PER OUT          the same with object streams of PER objects and an xref stream
//! loadbench mk dicts N OUT               N small dictionaries referenced from the catalog
//! loadbench mk streams N KB OUT          one page drawing N image XObjects of KB kilobytes each
use lopdf::xref::XrefEntry;
use lopdf::{Dictionary, Document, Object, ObjectStream, SaveOptions, Stream, dictionary};
use std::time::{Duration, Instant};

fn variant() -> String {
    std::env::var("LOADBENCH_VARIANT").unwrap_or_else(|_| "unknown".into())
}

fn median(mut v: Vec<u128>) -> u128 {
    v.sort_unstable();
    let n = v.len();
    if n % 2 == 1 { v[n / 2] } else { (v[n / 2 - 1] + v[n / 2]) / 2 }
}

fn fnv(bytes: &[u8], mut h: u64) -> u64 {
    for b in bytes {
        h ^= *b as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    h
}

fn digest(doc: &Document) -> String {
    let mut h = 0xcbf29ce484222325u64;
    for (id, object) in &doc.objects {
        h = fnv(format!("{id:?}={object:?};").as_bytes(), h);
        // Object's Debug omits stream content.
        if let Object::Stream(stream) = object {
            h = fnv(&stream.content, h);
            h = fnv(format!("{:?} {}", stream.start_position, stream.allows_compression).as_bytes(), h);
        }
    }
    h = fnv(format!("{:?}", doc.trailer).as_bytes(), h);
    h = fnv(format!("{}", doc.max_id).as_bytes(), h);
    format!("{h:016x}")
}

fn name(path: &str) -> String {
    std::path::Path::new(path).file_name().unwrap().to_string_lossy().into_owned()
}

fn emit(mode: &str, file: &str, ns: u128, iters: usize) {
    println!(
        "{{\"variant\":\"{}\",\"mode\":\"{}\",\"file\":\"{}\",\"median_ns\":{},\"iters\":{}}}",
        variant(),
        mode,
        name(file),
        ns,
        iters
    );
}

fn load(data: &[u8]) -> Document {
    Document::load_mem(data).expect("load")
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    match args[1].as_str() {
        "info" => {
            for file in &args[2..] {
                let data = std::fs::read(file).unwrap();
                let doc = load(&data);
                let entries = doc.reference_table.entries.len();
                let normal = doc
                    .reference_table
                    .entries
                    .values()
                    .filter(|e| matches!(e, XrefEntry::Normal { .. }))
                    .count();
                let compressed = doc
                    .reference_table
                    .entries
                    .values()
                    .filter(|e| matches!(e, XrefEntry::Compressed { .. }))
                    .count();
                let objstms: Vec<usize> = doc
                    .objects
                    .values()
                    .filter_map(|o| o.as_stream().ok())
                    .filter(|s| s.dict.has_type(b"ObjStm"))
                    .filter_map(|s| s.dict.get(b"N").and_then(Object::as_i64).ok())
                    .map(|n| n as usize)
                    .collect();
                println!(
                    "{{\"variant\":\"{}\",\"file\":\"{}\",\"bytes\":{},\"entries\":{},\"normal\":{},\"compressed\":{},\"objects\":{},\"objstm_members\":{:?},\"digest\":\"{}\"}}",
                    variant(),
                    name(file),
                    data.len(),
                    entries,
                    normal,
                    compressed,
                    doc.objects.len(),
                    objstms,
                    digest(&doc)
                );
            }
        }
        "warm" => {
            let iters: usize = args[2].parse().unwrap();
            for file in &args[3..] {
                let data = std::fs::read(file).unwrap();
                for _ in 0..3 {
                    drop(load(&data));
                }
                let mut times = Vec::with_capacity(iters);
                for _ in 0..iters {
                    let start = Instant::now();
                    let doc = load(&data);
                    times.push(start.elapsed().as_nanos());
                    drop(doc);
                }
                emit("warm", file, median(times), iters);
            }
        }
        "cold" => {
            let iters: usize = args[2].parse().unwrap();
            let gap = Duration::from_millis(args[3].parse().unwrap());
            for file in &args[4..] {
                let data = std::fs::read(file).unwrap();
                for _ in 0..3 {
                    drop(load(&data));
                }
                let mut times = Vec::with_capacity(iters);
                for _ in 0..iters {
                    std::thread::sleep(gap);
                    let start = Instant::now();
                    let doc = load(&data);
                    times.push(start.elapsed().as_nanos());
                    drop(doc);
                }
                emit("cold", file, median(times), iters);
            }
        }
        "after" => {
            // Time a fixed 128 KiB hash on the calling thread right after each load,
            // while any workers the load woke may still be spinning.
            let iters: usize = args[2].parse().unwrap();
            let gap = Duration::from_millis(args[3].parse().unwrap());
            let work: Vec<u8> = (0..128 * 1024).map(|i| (i * 31 % 251) as u8).collect();
            for file in &args[4..] {
                let data = std::fs::read(file).unwrap();
                for _ in 0..3 {
                    drop(load(&data));
                }
                let mut times = Vec::with_capacity(iters);
                for _ in 0..iters {
                    std::thread::sleep(gap);
                    let doc = load(&data);
                    let start = Instant::now();
                    std::hint::black_box(fnv(std::hint::black_box(&work), 0xcbf29ce484222325));
                    times.push(start.elapsed().as_nanos());
                    drop(doc);
                }
                emit("after", file, median(times), iters);
            }
        }
        "first" => {
            let file = &args[2];
            let data = std::fs::read(file).unwrap();
            let start = Instant::now();
            let doc = load(&data);
            let ns = start.elapsed().as_nanos();
            drop(doc);
            emit("first", file, ns, 1);
        }
        "objstm" => {
            let iters: usize = args[2].parse().unwrap();
            let gap = Duration::from_millis(args[3].parse().unwrap());
            let file = &args[4];
            let data = std::fs::read(file).unwrap();
            let doc = load(&data);
            let streams: Vec<&Stream> = doc
                .objects
                .values()
                .filter_map(|o| o.as_stream().ok())
                .filter(|s| s.dict.has_type(b"ObjStm"))
                .collect();
            for _ in 0..3 {
                for s in &streams {
                    drop(ObjectStream::new(s).unwrap());
                }
            }
            let mut times = Vec::with_capacity(iters);
            for _ in 0..iters {
                if !gap.is_zero() {
                    std::thread::sleep(gap);
                }
                let start = Instant::now();
                for s in &streams {
                    drop(std::hint::black_box(ObjectStream::new(s).unwrap()));
                }
                times.push(start.elapsed().as_nanos());
            }
            emit(if gap.is_zero() { "objstm-warm" } else { "objstm-cold" }, file, median(times), iters);
        }
        "mk" => make(&args[2..]),
        other => panic!("unknown mode {other}"),
    }
}

fn content(page: usize) -> Vec<u8> {
    let mut ops = String::new();
    ops.push_str("BT /F1 11 Tf 72 760 Td 14 TL\n");
    for line in 0..12 {
        ops.push_str(&format!("(Page {page} line {line}: the quick brown fox jumps over the lazy dog) Tj T*\n"));
    }
    ops.push_str("ET\n");
    ops.into_bytes()
}

fn pages_document(pages: usize) -> Document {
    let mut doc = Document::with_version("1.7");
    let pages_id = doc.new_object_id();
    let font_id = doc.add_object(dictionary! {
        "Type" => "Font", "Subtype" => "Type1", "BaseFont" => "Helvetica",
    });
    let resources_id = doc.add_object(dictionary! {
        "Font" => dictionary! { "F1" => font_id },
    });
    let mut kids = Vec::with_capacity(pages);
    for page in 0..pages {
        let content_id = doc.add_object(Stream::new(dictionary! {}, content(page)));
        let page_id = doc.add_object(dictionary! {
            "Type" => "Page",
            "Parent" => pages_id,
            "Contents" => content_id,
            "Resources" => resources_id,
            "MediaBox" => vec![0.into(), 0.into(), 612.into(), 792.into()],
        });
        kids.push(page_id.into());
    }
    doc.objects.insert(
        pages_id,
        Object::Dictionary(dictionary! { "Type" => "Pages", "Kids" => kids, "Count" => pages as i64 }),
    );
    let catalog_id = doc.add_object(dictionary! { "Type" => "Catalog", "Pages" => pages_id });
    doc.trailer.set("Root", catalog_id);
    doc
}

fn make(args: &[String]) {
    let (mut doc, out, objstm) = match args[0].as_str() {
        "pages" => (pages_document(args[1].parse().unwrap()), &args[2], false),
        "objstm" => (pages_document(args[1].parse().unwrap()), &args[3], true),
        "dicts" => {
            let n: usize = args[1].parse().unwrap();
            let mut doc = pages_document(1);
            let mut refs = Vec::with_capacity(n);
            for i in 0..n {
                let id = doc.add_object(dictionary! {
                    "Type" => "Annot", "Subtype" => "Text",
                    "Rect" => vec![(i as i64).into(), 0.into(), 10.into(), 10.into()],
                    "Contents" => Object::string_literal(format!("note {i}")),
                    "F" => 4,
                });
                refs.push(Object::Reference(id));
            }
            let root = doc.trailer.get(b"Root").unwrap().as_reference().unwrap();
            doc.get_dictionary_mut(root).unwrap().set("Extra", refs);
            (doc, &args[2], false)
        }
        "streams" => {
            let n: usize = args[1].parse().unwrap();
            let kb: usize = args[2].parse().unwrap();
            let mut doc = pages_document(1);
            let mut xobjects = Dictionary::new();
            let mut seed = 0x9e3779b97f4a7c15u64;
            for i in 0..n {
                let mut bytes = vec![0u8; kb * 1024];
                for b in bytes.iter_mut() {
                    seed ^= seed << 13;
                    seed ^= seed >> 7;
                    seed ^= seed << 17;
                    *b = seed as u8;
                }
                let side = ((kb * 1024 / 3) as f64).sqrt() as i64;
                let mut stream = Stream::new(
                    dictionary! {
                        "Type" => "XObject", "Subtype" => "Image",
                        "Width" => side, "Height" => side,
                        "ColorSpace" => "DeviceRGB", "BitsPerComponent" => 8,
                    },
                    bytes,
                );
                stream.allows_compression = false;
                let id = doc.add_object(stream);
                xobjects.set(format!("Im{i}"), id);
            }
            let resources = dictionary! { "XObject" => xobjects };
            let resources_id = doc.add_object(resources);
            let page_id = doc.page_iter().next().unwrap();
            doc.get_dictionary_mut(page_id).unwrap().set("Resources", resources_id);
            (doc, &args[3], false)
        }
        other => panic!("unknown kind {other}"),
    };
    let mut bytes = Vec::new();
    if objstm {
        let options = SaveOptions::builder()
            .use_object_streams(true)
            .use_xref_streams(true)
            .max_objects_per_stream(args[2].parse().unwrap())
            .build();
        doc.save_with_options(&mut bytes, options).unwrap();
    } else {
        doc.save_to(&mut bytes).unwrap();
    }
    std::fs::write(out, &bytes).unwrap();
}
