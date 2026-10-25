# Third-party notices

pylopdf includes krilla 0.8.2 in its compiled extension for OpenType text
generation, font subsetting, and new documents. krilla is available under MIT
OR Apache-2.0:

- Source: <https://github.com/LaurenzV/krilla>
- Package: <https://crates.io/crates/krilla/0.8.2>

krilla's image support reads images with png, zune-jpeg, gif 0.14 (MIT OR
Apache-2.0), image-webp 0.2 (MIT OR Apache-2.0), weezl 0.1 (MIT OR
Apache-2.0), imagesize 0.14 (MIT), color_quant 1.1 (MIT), and quick-error 2
(MIT OR Apache-2.0).

pylopdf uses HarfRust 0.15 for OpenType shaping. HarfRust is available under
the MIT license:

- Source: <https://github.com/harfbuzz/harfrust>
- Package: <https://crates.io/crates/harfrust/0.15.0>

pylopdf uses `pdf-base14-metrics` 0.0.2 for canonical Core 14 font widths.
Its Rust code is MIT and its Adobe Font Metrics data is licensed under
APAFML. The required Adobe license text is distributed in
`LICENSES/APAFML.txt`.

- Source: <https://github.com/kjanat/mosaic>
- Package: <https://crates.io/crates/pdf-base14-metrics/0.0.2>

pylopdf uses read-fonts 0.46 (MIT OR Apache-2.0) with its `agl` feature,
which includes the Adobe Glyph List and the ITC Zapf Dingbats glyph list for
`pylopdf.glyph_name` and `pylopdf.glyph_text`. The lists are copyright Adobe
and available under the BSD 3-Clause license reproduced in
`LICENSES/AGL-BSD-3-Clause.txt`.

- Source: <https://github.com/googlefonts/fontations> and
  <https://github.com/adobe-type-tools/agl-aglfn>
- Package: <https://crates.io/crates/read-fonts/0.46.0>

pylopdf uses `unicode-linebreak` 0.1.5 (Apache-2.0) and
`unicode-segmentation` 1.13 (MIT OR Apache-2.0) for UAX #14 wrapping and
grapheme-safe emergency breaks. It uses `unicode-bidi` 0.3.18 (MIT OR
Apache-2.0) to classify extracted pure RTL lines before restoring logical
Unicode order.

pylopdf uses zune-jpeg 0.5.15 to decode JPEG image XObjects. zune-jpeg is
available under MIT OR Apache-2.0 OR Zlib:

- Source: <https://github.com/etemesi254/zune-image>
- Package: <https://crates.io/crates/zune-jpeg/0.5.15>

pylopdf uses jpeg-encoder 0.7.0 to encode JPEG image XObjects with optimized
Huffman tables. jpeg-encoder is available under (MIT OR Apache-2.0) AND IJG:

- Source: <https://github.com/vstroebel/jpeg-encoder>
- Package: <https://crates.io/crates/jpeg-encoder/0.7.0>

This software is based in part on the work of the Independent JPEG Group.

pylopdf uses self_cell 1.3.0 to own one immutable hayro rendering snapshot
together with the render cache that borrows it. self_cell is available under
Apache-2.0 OR GPL-2.0-only; pylopdf uses it under Apache-2.0, whose license is
distributed in `LICENSES/Apache-2.0.txt`.

- Source: <https://github.com/Voultapher/self_cell>
- Package: <https://crates.io/crates/self_cell/1.3.0>

pylopdf uses RTen 0.27 and its companion `rten-*` crates for local PP-OCR
inference. RTen is available under MIT OR Apache-2.0. Its RTen model reader
uses FlatBuffers 24.12.23, available under Apache-2.0. The Apache license is
distributed in `LICENSES/Apache-2.0.txt`.

- RTen source: <https://github.com/robertknight/rten>
- RTen package: <https://crates.io/crates/rten/0.27.0>
- FlatBuffers source: <https://github.com/google/flatbuffers>

The optional `pylopdf-ocr-models` distribution contains PP-OCRv6 small model
data separately from the core wheel. Model copyright is held by Baidu;
provenance, source hashes, and Apache-2.0 notices are included in that
distribution.

The interoperability corpus includes four minimal PDFium test fixtures.
PDFium is copyright The PDFium Authors and available under the BSD 3-Clause
license reproduced in `LICENSES/PDFium-BSD-3-Clause.txt`.

- Source: <https://github.com/chromium/pdfium>
- Pinned revision: `a84323421e94f484faca52dd9d027934eba42ab8`

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
