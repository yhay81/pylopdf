//! Place the images and pages that drawing placed in a document again.
//!
//! `insert_image` and `show_pdf_page` used to add a new Image or Form XObject
//! for every call, so a logo or letterhead placed on every page was embedded
//! once per page, and every page import cloned its complete source document.
//! A document now remembers what it placed and places the same XObject again:
//!
//! - An image is identified by the SHA-256 digest of the input that decides
//!   its XObject: the accepted encoded bytes, or a Pixmap's dimensions and
//!   RGBA samples. The rectangle and rotation live only in the placement
//!   matrix, so they play no part.
//! - An imported page is identified by its source document's revision and its
//!   page number. Revisions are process-unique, and every edit of a document
//!   replaces its revision, so a Form is reused only while its source is
//!   exactly as it was, and never for another document.
//!
//! An edit forgets everything a document remembers unless it leaves every
//! placed object unchanged; `_Document::invalidate_hayro_pdf_keeping_placements`
//! lists the edits that keep them. Removing unreachable objects is the only
//! change such an edit makes to a placed object, so a remembered object is
//! placed again only while it still exists with the subtype and content
//! length it was placed with. A Form that survives pruning was reachable, and
//! so are the objects it reaches.

use std::collections::HashMap;
use std::hash::Hash;
use std::sync::atomic::{AtomicU64, Ordering};

use lopdf::{Dictionary, Document, Object, ObjectId, Stream};
use sha2::{Digest, Sha256};

/// Images, and separately pages, that one document remembers.
///
/// A full map starts over, which bounds memory and keeps which placements
/// share an object deterministic.
const MAX_REMEMBERED_PLACEMENTS: usize = 4_096;

/// Source of process-unique document revisions.
static NEXT_REVISION: AtomicU64 = AtomicU64::new(1);

/// Return a revision that no document has had before.
pub(crate) fn next_revision() -> u64 {
    NEXT_REVISION.fetch_add(1, Ordering::Relaxed)
}

/// SHA-256 digest of the input that decides one image XObject.
pub(crate) type ImageDigest = [u8; 32];

/// Identify encoded JPEG or PNG input, which alone decides its XObject.
pub(crate) fn encoded_image_digest(data: &[u8]) -> ImageDigest {
    // The leading byte keeps encoded input apart from RGBA samples.
    Sha256::new()
        .chain_update([0])
        .chain_update(data)
        .finalize()
        .into()
}

/// Identify straight-alpha RGBA8 samples together with their dimensions.
pub(crate) fn pixmap_image_digest(
    width: u32,
    height: u32,
    alpha: bool,
    samples: &[u8],
) -> ImageDigest {
    Sha256::new()
        .chain_update([if alpha { 1 } else { 2 }])
        .chain_update(width.to_be_bytes())
        .chain_update(height.to_be_bytes())
        .chain_update(samples)
        .finalize()
        .into()
}

/// One stream that a placement added, and its content length at the time.
#[derive(Clone, Copy)]
struct PlacedStream {
    id: ObjectId,
    len: usize,
}

impl PlacedStream {
    fn read(doc: &Document, id: ObjectId) -> Option<Self> {
        let stream = doc.objects.get(&id)?.as_stream().ok()?;
        Some(Self {
            id,
            len: stream.content.len(),
        })
    }

    /// Return the stream while it keeps its subtype and content length.
    fn current<'a>(self, doc: &'a Document, subtype: &[u8]) -> Option<&'a Stream> {
        let stream = doc.objects.get(&self.id)?.as_stream().ok()?;
        let intact = stream.content.len() == self.len
            && stream
                .dict
                .get(b"Subtype")
                .and_then(Object::as_name)
                .is_ok_and(|name| name == subtype);
        intact.then_some(stream)
    }
}

/// An image XObject, with its soft mask, that drawing placed before.
#[derive(Clone, Copy)]
pub(crate) struct PlacedImage {
    image: PlacedStream,
    mask: Option<PlacedStream>,
    width: u32,
    height: u32,
}

impl PlacedImage {
    /// Record the image XObject that `id` names, right after it was added.
    pub(crate) fn read(doc: &Document, id: ObjectId) -> Option<Self> {
        let stream = doc.objects.get(&id)?.as_stream().ok()?;
        let mask = match stream.dict.get(b"SMask") {
            Ok(Object::Reference(mask_id)) => Some(PlacedStream::read(doc, *mask_id)?),
            Ok(_) => return None,
            Err(_) => None,
        };
        Some(Self {
            image: PlacedStream {
                id,
                len: stream.content.len(),
            },
            mask,
            width: dimension(&stream.dict, b"Width")?,
            height: dimension(&stream.dict, b"Height")?,
        })
    }

    pub(crate) fn id(self) -> ObjectId {
        self.image.id
    }

    pub(crate) fn width(self) -> u32 {
        self.width
    }

    pub(crate) fn height(self) -> u32 {
        self.height
    }

    fn is_intact(self, doc: &Document) -> bool {
        let Some(image) = self.image.current(doc, b"Image") else {
            return false;
        };
        let mask_intact = match (image.dict.get(b"SMask"), self.mask) {
            (Ok(Object::Reference(id)), Some(mask)) => {
                *id == mask.id && mask.current(doc, b"Image").is_some()
            }
            (Err(_), None) => true,
            _ => false,
        };
        mask_intact
            && dimension(&image.dict, b"Width") == Some(self.width)
            && dimension(&image.dict, b"Height") == Some(self.height)
    }
}

fn dimension(dict: &Dictionary, key: &[u8]) -> Option<u32> {
    let value = dict.get(key).and_then(Object::as_i64).ok()?;
    u32::try_from(value).ok()
}

/// A page that drawing imported as a Form XObject before.
#[derive(Clone, Copy)]
pub(crate) struct PlacedForm {
    form: PlacedStream,
    crop: [f64; 4],
    rotation: i64,
}

impl PlacedForm {
    /// Describe a Form that was just imported with `len` content bytes and
    /// its source page's crop box and rotation.
    pub(crate) fn new(id: ObjectId, len: usize, crop: [f64; 4], rotation: i64) -> Self {
        Self {
            form: PlacedStream { id, len },
            crop,
            rotation,
        }
    }

    pub(crate) fn id(self) -> ObjectId {
        self.form.id
    }

    pub(crate) fn crop(self) -> [f64; 4] {
        self.crop
    }

    pub(crate) fn rotation(self) -> i64 {
        self.rotation
    }

    fn is_intact(self, doc: &Document) -> bool {
        self.form.current(doc, b"Form").is_some()
    }
}

/// Source of one imported page: a document revision and a one-based page.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub(crate) struct FormSource {
    pub(crate) revision: u64,
    pub(crate) page: u32,
}

/// The images and pages that one document can place again.
#[derive(Default)]
pub(crate) struct PlacementReuse {
    images: HashMap<ImageDigest, PlacedImage>,
    forms: HashMap<FormSource, PlacedForm>,
}

impl PlacementReuse {
    /// Forget every placement, before an edit that may change one.
    pub(crate) fn clear(&mut self) {
        self.images.clear();
        self.forms.clear();
    }

    /// Return the image placed for `digest` while it is intact.
    pub(crate) fn image(&mut self, doc: &Document, digest: &ImageDigest) -> Option<PlacedImage> {
        intact_entry(&mut self.images, doc, digest, PlacedImage::is_intact)
    }

    /// Return the Form imported from `source` while it is intact.
    pub(crate) fn form(&mut self, doc: &Document, source: FormSource) -> Option<PlacedForm> {
        intact_entry(&mut self.forms, doc, &source, PlacedForm::is_intact)
    }

    /// Make room to remember one more image before the edit that places it.
    ///
    /// The placement proceeds unremembered when this returns false, because
    /// a refused allocation for reuse must not fail the edit itself.
    pub(crate) fn reserve_image(&mut self) -> bool {
        reserve_entry(&mut self.images)
    }

    /// Make room to remember one more Form before the edit that places it.
    pub(crate) fn reserve_form(&mut self) -> bool {
        reserve_entry(&mut self.forms)
    }

    /// Remember a committed placement in room that `reserve_image` made.
    pub(crate) fn remember_image(&mut self, digest: ImageDigest, image: PlacedImage) {
        self.images.insert(digest, image);
    }

    /// Remember a committed import in room that `reserve_form` made.
    pub(crate) fn remember_form(&mut self, source: FormSource, form: PlacedForm) {
        self.forms.insert(source, form);
    }

    /// Forget the Form of a source revision that no lookup can name again.
    pub(crate) fn forget_form(&mut self, source: FormSource) {
        self.forms.remove(&source);
    }
}

fn intact_entry<K: Eq + Hash, V: Copy>(
    entries: &mut HashMap<K, V>,
    doc: &Document,
    key: &K,
    is_intact: impl FnOnce(V, &Document) -> bool,
) -> Option<V> {
    let value = *entries.get(key)?;
    if is_intact(value, doc) {
        return Some(value);
    }
    entries.remove(key);
    None
}

fn reserve_entry<K: Eq + Hash, V>(entries: &mut HashMap<K, V>) -> bool {
    if entries.len() >= MAX_REMEMBERED_PLACEMENTS {
        entries.clear();
    }
    entries.try_reserve(1).is_ok()
}
