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
| `bunka-kokugo-series-019-p4.pdf` | [Agency for Cultural Affairs](https://www.bunka.go.jp/kokugo_nihongo/sisaku/joho/joho/series/19/19.html), *Kokugo Series No. 19: Questions and Answers on Japanese Language, Vol. 2*, source PDF page 4 | [Government Standard Terms of Use 2.0](https://www.digital.go.jp/resources/open_data/), CC BY 4.0 compatible | Pixel-identical, single-page derivative of a 2007 image-only scan; 3311×4681 Japanese body text, historical type, dust, punctuation, and ruby |
| `patent-us223898.pdf` | [Google Patents](https://patents.google.com/patent/US223898A), Edison's 1880 light-bulb patent | Public-domain US patent | PDF 1.3, scanned CCITTFaxDecode image, OCR text layer; retrieved 2026-07-22 |
| `wdl6812-manuscript.pdf` | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Illuminated_Panel_and_Qur%27anic_Chapter_WDL6812.pdf), illuminated World Digital Library manuscript | Public domain | PDF 1.4, color scan using DCTDecode and JBIG2Decode, no text layer; retrieved 2026-07-22 |
| `nics-background-checks-2015-11.pdf` | FBI NICS monthly report, [pdfplumber test-corpus mirror](https://github.com/jsvine/pdfplumber/blob/stable/tests/pdfs/nics-background-checks-2015-11.pdf) | US government work, public domain; mirror repository is MIT licensed | PDF 1.3, Quartz output, dense 25-column records with internal row rules omitted; retrieved 2026-07-25 |
| `senate-expenditures.pdf` | US Senate expenditure-report excerpt, [pdfplumber test-corpus mirror](https://github.com/jsvine/pdfplumber/blob/stable/tests/pdfs/senate-expenditures.pdf) | US government work, public domain; mirror repository is MIT licensed | PDF 1.3, iText/pdftk output, 90-degree page rotation, merged bordered header, and borderless data rows; retrieved 2026-07-25 |

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
  (`wdl6812-manuscript.pdf`). `bunka-kokugo-series-019-p4.pdf` adds a clean
  Japanese archival scan with no text layer and a manually verified OCR
  transcript in `bench/ground_truth/`.
- Independent table layouts from FBI and US Senate reports: sparse internal
  vector dividers, dense numeric columns, merged headers, rotated pages, and
  borderless aligned rows.

Choose additions using three criteria: a redistributable license, a size below
1 MB, and coverage not already represented in the corpus.

`bunka-kokugo-series-019-p4.pdf` was prepared on 2026-07-25 from
`kokugo_series_019_02.pdf` (source SHA-256
`b5eb4fe45cb7db1dfb6ad25255ffc685a1e4409e6a0487983eed440db0f20e43`).
The first page's decoded image was re-embedded losslessly in a one-page PDF to
avoid retaining the source document's shared 22-page image resources. Its
scale-1 RGBA rendering is byte-identical to the source page (SHA-256
`3f1ca966b541d3c7963dee0de82af8d55734f2edad997eb2e746980731cddf25`);
the fixture SHA-256 is
`f8d9d89a8b738ac2fc3108d01d1974ca413d3fa8dd32d4f812f075e3337ab9a3`.
The ground truth transcribes base text and page numbers but deliberately omits
the small `もんじょうはかせ` ruby above `文章博士`, because ruby association is
outside the first native OCR engine's documented contract.
