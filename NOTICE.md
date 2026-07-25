# Third-party notices

pylopdf includes krilla 0.8.2 in its compiled extension for OpenType text
generation and font subsetting. krilla is available under MIT OR Apache-2.0:

- Source: <https://github.com/LaurenzV/krilla>
- Package: <https://crates.io/crates/krilla/0.8.2>

pylopdf uses HarfRust 0.12 for OpenType shaping. HarfRust is available under
the MIT license:

- Source: <https://github.com/harfbuzz/harfrust>
- Package: <https://crates.io/crates/harfrust/0.12.0>

pylopdf uses `pdf-base14-metrics` 0.0.1 for canonical Core 14 font widths.
Its Rust code is MIT and its Adobe Font Metrics data is licensed under
APAFML. The required Adobe license text is distributed in
`LICENSES/APAFML.txt`.

- Source: <https://github.com/kjanat/mosaic>
- Package: <https://crates.io/crates/pdf-base14-metrics/0.0.1>

pylopdf uses `unicode-linebreak` 0.1.5 (Apache-2.0) and
`unicode-segmentation` 1.13 (MIT OR Apache-2.0) for UAX #14 wrapping and
grapheme-safe emergency breaks.

The following acknowledgements are reproduced from krilla's `NOTICE.md`.

## krilla acknowledgements

Some parts of krilla build upon code copied from other crates. In particular:

### resvg

The following code snippets have been taken or adapted from
[resvg](https://github.com/RazrFalcon/resvg), available under the
[Mozilla Public License 2.0](https://github.com/RazrFalcon/resvg/blob/master/LICENSE.txt):

- The contents of the `content_draw_path` method.
- The resvg test suite in `assets/svgs`.

### typst

The following code snippets have been taken or adapted from
[typst](https://github.com/typst/typst), available under the
[Apache License 2.0](https://github.com/typst/typst/blob/main/LICENSE):

- The `GroupByKey` struct.
- The `SliceExt` trait.
- The `Prehashed` struct.
- The implementation of `SipHashable`.
- The implementation of writing CID-keyed fonts.
- The implementation of writing PDF metadata.

### svg2pdf

The SVG conversion implementation has been taken or adapted from
[svg2pdf](https://github.com/typst/svg2pdf), available under the
[Apache License 2.0](https://github.com/typst/svg2pdf/blob/main/LICENSE-APACHE).

### vello

The bitmap-glyph logic in `bitmap.rs` has been taken or adapted from
[vello](https://github.com/linebender/vello), available under the
[Apache License 2.0](https://github.com/linebender/vello/blob/main/LICENSE-APACHE).
