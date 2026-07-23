# Real-world PDF test corpus

These assets support regressions in `tests/test_real_world.py` against PDFs
produced by real toolchains. Their purpose is to expose lopdf and hayro
limitations early. Every bundled document has a redistributable license.

## Files

| File | Source | License | Coverage |
|---|---|---|---|
| `f1040.pdf` | [irs.gov](https://www.irs.gov/pub/irs-pdf/f1040.pdf) | US government work, public domain | PDF 1.7, AcroForm, tagged PDF, object streams, Adobe Designer output |
| `pdf20-simple.pdf` | [pdf-association/pdf20examples](https://github.com/pdf-association/pdf20examples), “Simple PDF 2.0 file.pdf” | CC BY 4.0 | PDF 2.0 header, minimal uncompressed structure, Type 1 font without `/Encoding` |
| `usrguide.pdf` | [latex-project.org](https://www.latex-project.org/help/documentation/usrguide.pdf) | LPPL, freely redistributable | PDF 1.5, pdfTeX output, subset Type 1 fonts, formulas and ligatures |
| `bill-hr815.pdf` | [govinfo.gov](https://www.govinfo.gov/content/pkg/BILLS-118hr815enr/pdf/BILLS-118hr815enr.pdf), H.R. 815, 118th Congress | US government work, public domain | PDF 1.5, GPO typesetting, medium-size 110-page document |
| `mhlw-doc.pdf` | [mhlw.go.jp](https://www.mhlw.go.jp/content/11201250/001526113.pdf), Study Group on “Workers” under the Labor Standards Act, material 2-1 | [Government Standard Terms of Use 2.0](https://www.digital.go.jp/resources/open_data/), CC BY 4.0 compatible | PDF 1.7, embedded CJK CID fonts, mixed vertical/horizontal layout |
| `patent-us223898.pdf` | [Google Patents](https://patents.google.com/patent/US223898A), Edison's 1880 light-bulb patent | Public-domain US patent | PDF 1.3, scanned CCITTFaxDecode image, OCR text layer; retrieved 2026-07-22 |
| `wdl6812-manuscript.pdf` | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Illuminated_Panel_and_Qur%27anic_Chapter_WDL6812.pdf), illuminated World Digital Library manuscript | Public domain | PDF 1.4, color scan using DCTDecode and JBIG2Decode, no text layer; retrieved 2026-07-22 |

## Previously known limitations, now fixed

- **Text extraction from `pdf20-simple.pdf` returned nothing.** lopdf's content
  parser dropped every operation after a `%` comment followed by an indented
  line, reported upstream as
  [lopdf#535](https://github.com/J-F-Liu/lopdf/issues/535). pylopdf v0.7 replaced
  extraction with the hayro engine. `test_pdf20_comment_streams_extract`
  protects the regression and the same engine also extracts non-embedded CJK
  text using `90ms-RKSJ-H`.

## Covered dimensions

- Encrypted PDFs in `tests/assets/encrypted/`: RC4-40/128, AES-128, and AES-256.
- Non-embedded CJK fonts through synthetic PDFs in `tests/test_cjk.py` and
  `pylopdf[cjk]`.
- Scans using CCITTFaxDecode plus an OCR layer (`patent-us223898.pdf`), and
  DCTDecode plus JBIG2Decode without a text layer
  (`wdl6812-manuscript.pdf`).

Choose additions using three criteria: a redistributable license, a size below
1 MB, and coverage not already represented in the corpus.
