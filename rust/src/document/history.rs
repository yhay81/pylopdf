//! Snapshots of the editable state, which transactions and undo restore.
//!
//! A snapshot is a complete copy of the lopdf graph and of the state that
//! edits keep beside it. Python's `Document.transaction` takes one before a
//! block of edits and restores it when the block raises; `doc.history`
//! keeps them as undo and redo steps within its memory budget.

use super::*;

/// The editable state of a `_Document` at one moment.
#[pyclass(module = "pylopdf.pylopdf_core")]
pub struct _DocumentSnapshot {
    /// Taken by the restore that uses it, so a snapshot restores once.
    state: Option<SnapshotState>,
    /// The estimated bytes that the copy holds.
    size: usize,
}

struct SnapshotState {
    doc: Document,
    isolated_content_pages: HashSet<ObjectId>,
}

#[pymethods]
impl _DocumentSnapshot {
    /// The estimated bytes that the snapshot holds, which undo budgets count.
    #[getter]
    fn size(&self) -> usize {
        self.size
    }

    /// Whether a restore has used the snapshot.
    #[getter]
    fn is_used(&self) -> bool {
        self.state.is_none()
    }
}

/// Estimate the bytes that a copy of `doc` holds.
///
/// Stream data dominates; every other value counts a fixed size, which is
/// close to what lopdf's `Object` and the maps that hold it take.
fn estimated_size(doc: &Document) -> usize {
    const VALUE: usize = 48;
    let mut size = 0usize;
    let mut stack: Vec<&Object> = Vec::new();
    for object in doc.objects.values() {
        stack.push(object);
        while let Some(object) = stack.pop() {
            size = size.saturating_add(VALUE);
            match object {
                Object::String(bytes, _) | Object::Name(bytes) => {
                    size = size.saturating_add(bytes.len());
                }
                Object::Array(items) => stack.extend(items.iter()),
                Object::Dictionary(dict) => {
                    stack.extend(dict.iter().map(|(_, value)| value));
                    size = size.saturating_add(dict.iter().map(|(key, _)| key.len()).sum());
                }
                Object::Stream(stream) => {
                    stack.extend(stream.dict.iter().map(|(_, value)| value));
                    size = size.saturating_add(stream.content.len());
                }
                _ => {}
            }
        }
    }
    size
}

/// Implements [`_Document::take_snapshot`].
pub(super) fn take_snapshot(this: &_Document, py: Python<'_>) -> PyResult<_DocumentSnapshot> {
    let doc: &Document = &this.doc;
    let isolated = &this.isolated_content_pages;
    py.detach(|| {
        let state = SnapshotState {
            doc: doc.clone(),
            isolated_content_pages: isolated.clone(),
        };
        let size = estimated_size(&state.doc);
        Ok(_DocumentSnapshot {
            state: Some(state),
            size,
        })
    })
}

/// Implements [`_Document::restore_snapshot`].
pub(super) fn restore_snapshot(
    this: &mut _Document,
    snapshot: &mut _DocumentSnapshot,
) -> PyResult<()> {
    let state = snapshot
        .state
        .take()
        .ok_or_else(|| PdfError::new_err("this snapshot was already restored"))?;
    // The restored graph may hold other objects under the numbers that
    // drawing placed, so placements go too.
    this.invalidate_hayro_pdf();
    this.doc.replace(state.doc);
    this.isolated_content_pages = state.isolated_content_pages;
    Ok(())
}
