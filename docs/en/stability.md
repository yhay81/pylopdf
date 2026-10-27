---
title: API stability
description: What counts as pylopdf's public API, what may change before and after v1.0, and how deprecations work.
---

# API stability

pylopdf follows [Semantic Versioning](https://semver.org/).

## Before v1.0 { #current-status }

pylopdf is pre-1.0. Releases can still contain breaking changes, such as the
API redesign of version 0.16 that the
[roadmap](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md) describes,
and there is no deprecation period for them yet. Every
breaking change is listed, with how to update your code, in the
[changelog](https://github.com/yhay81/pylopdf/blob/main/CHANGELOG.md).
Pin a minor version (for example `pylopdf~=0.16.0`) if you need to upgrade on
your own schedule.

The rules below take effect from v1.0.

## What is public { #public-api-boundary }

The public API is:

- names in `pylopdf.__all__`, and `pylopdf.__version__`;
- the documented methods, properties, and attributes of those classes;
- parameter names, defaults, and whether they are positional or keyword-only;
- documented return values, constants, and enum values;
- the keys of returned dictionaries and the fields of returned tuples;
- the exception classes and their hierarchy, and `LimitError.code`.

Everything else is private: names starting with `_`, the
`pylopdf.pylopdf_core` extension module, `repr()` output, the wording of
error messages, the order of warnings, and undocumented attributes. Import
public names from `pylopdf` itself, not from the module that defines them.

The exact bytes of saved PDFs, PNGs, and SVGs may change between releases. What
is stable is how they look, their structure, and the text extracted from them.

## After v1.0 { #after-v1 }

- A **major** release may remove or change public API.
- A **minor** release adds features without breaking existing code.
- A **patch** release fixes bugs.

Removing or renaming anything public, adding a required parameter, removing a
field from a returned record, accepting less input than before, or changing
the exception hierarchy needs a major release. Adding a function, a method, an
optional parameter, an accepted value, or a record field does not. Type hints
count as part of the API: changing the type of a parameter or a record field
is a breaking change too.

## Deprecations { #deprecation-lifecycle }

After v1.0, an API that is going to be removed is:

1. documented as deprecated, with its replacement and the earliest release
   that may remove it;
2. made to emit `DeprecationWarning` where practical;
3. kept for at least two minor releases and six months;
4. removed only in a major release.

`PylopdfWarning` reports problems in PDF files and is never used for
deprecations. A security, legal, or upstream emergency may require a shorter
path; the changelog and release notes will say so clearly.

## Changes in results { #behavior-and-data }

A bug fix can change results when the old result was wrong, for example
reading order, table detection, glyph positions, colors, or how a damaged file
is repaired. Such changes can ship in any release, and notable ones are
listed in the changelog.

A new resource limit can refuse a malicious or unexpectedly expensive file that
an earlier release tried to process. Programs should rely on exception types
and `LimitError.code`, not on message text.

Supported Python versions and platforms are listed for each release. Support
for a Python version may be dropped in a minor release after it reaches its
end of life upstream, and the release notes will say so.

## How changes are tracked { #compatibility-review }

The repository records the public API in
[`api/public-api.json`](https://github.com/yhay81/pylopdf/blob/main/api/public-api.json),
and tests fail when the API changes without the record being updated, so
every change to the public API is reviewed deliberately.
