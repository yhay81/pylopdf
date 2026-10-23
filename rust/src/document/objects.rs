//! Helpers that read PDF objects, treating null-equivalent values as
//! absent.

use super::*;

/// Resolve one reference level, returning the original object on failure.
pub(crate) fn deref_object<'a>(doc: &'a Document, obj: &'a Object) -> &'a Object {
    match obj {
        Object::Reference(id) => doc.get_object(*id).unwrap_or(obj),
        other => other,
    }
}

/// Resolve an indirect object, reading an indirect null and a missing object
/// alike as absent.
///
/// ISO 32000-2 §7.3.7 treats a null dictionary value as an omitted key, and
/// §7.3.10 reads a reference to a missing object as null. lopdf's error for a
/// cyclic or over-long reference chain still surfaces.
pub(super) fn non_null_object(doc: &Document, id: ObjectId) -> lopdf::Result<Option<&Object>> {
    match doc.get_object(id) {
        Ok(Object::Null) | Err(lopdf::Error::ObjectNotFound(_)) => Ok(None),
        Ok(object) => Ok(Some(object)),
        Err(error) => Err(error),
    }
}

/// Resolve a value, reading null, an indirect null, and a reference to a
/// missing object alike as absent, as [`non_null_object`] does.
pub(super) fn non_null_value<'a>(
    doc: &'a Document,
    object: &'a Object,
) -> lopdf::Result<Option<&'a Object>> {
    match object {
        Object::Reference(id) => non_null_object(doc, *id),
        Object::Null => Ok(None),
        other => Ok(Some(other)),
    }
}

/// Clone a dictionary while allowing an indirect reference.
pub(super) fn deref_dict(doc: &Document, obj: &Object) -> Option<Dictionary> {
    match obj {
        Object::Reference(id) => doc.get_object(*id).ok()?.as_dict().ok().cloned(),
        Object::Dictionary(d) => Some(d.clone()),
        _ => None,
    }
}

/// Borrow a dictionary while allowing an indirect reference.
pub(super) fn deref_dict_ref<'a>(doc: &'a Document, obj: &'a Object) -> Option<&'a Dictionary> {
    match obj {
        Object::Reference(id) => doc.get_object(*id).ok()?.as_dict().ok(),
        Object::Dictionary(dictionary) => Some(dictionary),
        _ => None,
    }
}

/// Read an integer while allowing an indirect reference.
pub(crate) fn resolve_i64(doc: &Document, obj: &Object) -> Option<i64> {
    match obj {
        Object::Reference(id) => doc.get_object(*id).ok()?.as_i64().ok(),
        other => other.as_i64().ok(),
    }
}

/// Read null, an indirect null, and a reference to a missing object alike.
///
/// ISO 32000-2 §7.3.7 treats a null dictionary value as an omitted key, and
/// §7.3.10 reads a reference to a missing object as null. A cyclic or
/// over-long reference chain is an unusable value rather than null.
pub(crate) fn is_null_equivalent(doc: &Document, object: &Object) -> bool {
    match object {
        Object::Null => true,
        Object::Reference(id) => matches!(
            doc.get_object(*id),
            Ok(Object::Null) | Err(lopdf::Error::ObjectNotFound(_))
        ),
        _ => false,
    }
}
