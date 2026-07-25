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

pylopdf uses zune-jpeg 0.5.15 to decode JPEG image XObjects. zune-jpeg is
available under MIT OR Apache-2.0 OR Zlib:

- Source: <https://github.com/etemesi254/zune-image>
- Package: <https://crates.io/crates/zune-jpeg/0.5.15>

pylopdf uses jpeg-encoder 0.7.0 to encode JPEG image XObjects with optimized
Huffman tables. jpeg-encoder is available under (MIT OR Apache-2.0) AND IJG:

- Source: <https://github.com/vstroebel/jpeg-encoder>
- Package: <https://crates.io/crates/jpeg-encoder/0.7.0>

This software is based in part on the work of the Independent JPEG Group.

pylopdf uses RTen 0.24 and its companion `rten-*` crates for local PP-OCR
inference. RTen is available under MIT OR Apache-2.0. Its RTen model reader
uses FlatBuffers 24.12.23, available under Apache-2.0. The Apache license is
distributed in `LICENSES/Apache-2.0.txt`.

- RTen source: <https://github.com/robertknight/rten>
- RTen package: <https://crates.io/crates/rten/0.24.0>
- FlatBuffers source: <https://github.com/google/flatbuffers>

The optional `pylopdf-ocr-models` distribution contains PP-OCRv6 small model
data separately from the core wheel. Model copyright is held by Baidu;
provenance, source hashes, and Apache-2.0 notices are included in that
distribution.

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
