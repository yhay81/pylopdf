---
title: API stability
description: pylopdf's public API boundary, semantic-versioning guarantees, deprecation lifecycle, and compatibility review process.
---

# API stability

pylopdf follows [Semantic Versioning 2.0.0](https://semver.org/). This page
defines what that means for a Python library whose results also depend on PDF
producers, fonts, renderers, and supported runtimes.

## Current status { #current-status }

The 0.13 API is a **candidate baseline**, not the v1.0 compatibility promise.
It is being exercised in the field while additive work and reviewed corrections
remain possible. Every public-surface change is nevertheless detected and
reviewed now so the eventual v1.0 boundary is deliberate rather than accidental.

The guarantees under [After v1.0](#after-v1) begin with v1.0. Before then,
release notes identify incompatible changes and migration paths. The project
does not use the pre-1.0 version number as permission for silent breakage.

## Public API boundary { #public-api-boundary }

The supported public API consists of:

- names exported by `pylopdf.__all__`, plus `pylopdf.__version__`;
- documented public members of those classes;
- callable parameter names, kinds, defaults, and documented return contracts;
- documented constants and enum values;
- TypedDict required and optional keys, NamedTuple fields, and public type
  aliases;
- the documented exception hierarchy and machine-readable attributes such as
  `LimitError.code`.

Names beginning with `_`, `pylopdf.pylopdf_core`, Rust implementation details,
object `repr` output, exact exception messages, warning order, and undocumented
attributes are private. Importing a public object from its implementation
module does not make that module path public; import it from `pylopdf`.

The PDF bytes produced by `save()` and the exact PNG/SVG serialization are not
stable byte-for-byte formats. The documented visual, structural, and extraction
semantics are the contract.

## After v1.0 { #after-v1 }

For stable releases:

- a **major** release may remove or incompatibly change public API;
- a **minor** release adds backward-compatible API and behavior;
- a **patch** release fixes defects without intentionally changing the public
  API.

Removing or renaming a public symbol or member, making a parameter required,
changing positional or keyword acceptance incompatibly, removing a mapping key,
narrowing accepted input, changing a documented constant, or breaking the
public exception inheritance requires a major release.

Adding an optional keyword parameter, a new symbol, a new method, a new enum or
Literal choice accepted as input, or an optional result key is normally
additive. Typed contracts receive the same compatibility review as runtime
objects: moving a TypedDict key between required and optional, or incompatibly
changing its value type, is not treated as a typing-only detail.

## Deprecation lifecycle { #deprecation-lifecycle }

After v1.0, a public API scheduled for removal is normally:

1. documented as deprecated with its replacement and earliest removal release;
2. made to emit `DeprecationWarning` when practical;
3. retained for at least two minor releases and six months;
4. removed only in a major release.

`DeprecationWarning` is for developer migration. `PylopdfWarning` remains for
operational PDF interpretation warnings and is not a deprecation channel.

Security, legal, or upstream-runtime emergencies may require a shorter path.
Such an exception is called out prominently in the changelog and release notes,
with the least disruptive migration available.

## Behavior and data compatibility { #behavior-and-data }

Bug fixes may change output when the old behavior was incorrect. Examples
include malformed-PDF recovery, reading order, glyph geometry, table
interpretation, color conversion, and renderer differences. These changes do
not require a major release when they move behavior toward the documented
contract, but material effects are reported.

Resource limits may reject an adversarial or unexpectedly expensive input that
an earlier release attempted to process. Stable exception types and error codes
are used where callers need machine decisions; human-readable messages are not
an API.

Support is defined by the Python versions, platforms, ABIs, and WebAssembly
runtime matrix documented for each release. A runtime may be dropped in a minor
release after its upstream end of life or when maintaining it would prevent a
security or correctness fix. The reason and migration window are published.
Pyodide compatibility is pinned and tested per pylopdf minor line rather than
assumed across runtime upgrades.

## Compatibility review { #compatibility-review }

[`api/public-api.json`](https://github.com/yhay81/pylopdf/blob/main/api/public-api.json)
records the reviewed 0.13 candidate surface. Tests compare it on every native
Python lane and detect changes to exports, signatures, mapping keys, type
aliases, enum and constant values, public members, and exception inheritance.

Run:

```console
uv run python tools/check_api_surface.py
```

An intentional change is reviewed for runtime, typing, documentation, and
SemVer impact before refreshing the snapshot:

```console
uv run python tools/check_api_surface.py --update
```

The snapshot is a review gate, not an automatic compatibility verdict. Every
intentional change still needs tests, documentation in all four supported
languages, and a changelog entry.
