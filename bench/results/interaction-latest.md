# Interaction oracle

pylopdf 0.15.0 against pymupdf 1.28.2, Python 3.15.0 on Darwin arm64.

## Annotations

Each annotation is created by pylopdf on a 220-point page and rendered at 72 dpi by pylopdf (hayro) and by MuPDF from the same saved bytes. A sample differs when a channel differs by more than 64; inked is the share of MuPDF's samples that are not white.

| Annotation | Page rotation | pymupdf type | Same rect | Inked | Differing |
| --- | ---: | --- | --- | ---: | ---: |
| Square | 0 | Square | yes | 13.22% | 0.00% |
| Square, cloudy | 0 | Square | yes | 0.98% | 0.04% |
| Circle | 0 | Circle | yes | 5.55% | 0.01% |
| Line with endings | 0 | Line | yes | 0.31% | 0.00% |
| Polygon | 0 | Polygon | yes | 7.05% | 0.00% |
| PolyLine | 0 | PolyLine | yes | 0.34% | 0.00% |
| Ink | 0 | Ink | yes | 0.66% | 0.00% |
| Highlight | 0 | Highlight | yes | 1.65% | 0.00% |
| Underline | 0 | Underline | yes | 0.33% | 0.00% |
| StrikeOut | 0 | StrikeOut | yes | 0.33% | 0.00% |
| Squiggly | 0 | Squiggly | yes | 0.33% | 0.00% |
| Note | 0 | Text | yes | 0.37% | 0.00% |
| FreeText | 0 | FreeText | yes | 1.35% | 0.03% |
| FreeText with callout | 0 | FreeText | yes | 1.16% | 0.02% |
| Stamp | 0 | Stamp | yes | 6.84% | 0.37% |
| Caret | 0 | Caret | yes | 0.20% | 0.00% |
| FileAttachment | 0 | FileAttachment | yes | 0.18% | 0.00% |
| Redaction | 0 | Redact | yes | 0.92% | 0.01% |
| Square | 90 | Square | yes | 13.22% | 0.00% |
| Square, cloudy | 90 | Square | yes | 0.98% | 0.04% |
| Circle | 90 | Circle | yes | 5.55% | 0.01% |
| Line with endings | 90 | Line | yes | 0.31% | 0.00% |
| Polygon | 90 | Polygon | yes | 7.05% | 0.00% |
| PolyLine | 90 | PolyLine | yes | 0.34% | 0.00% |
| Ink | 90 | Ink | yes | 0.66% | 0.00% |
| Highlight | 90 | Highlight | yes | 1.65% | 0.00% |
| Underline | 90 | Underline | yes | 0.33% | 0.00% |
| StrikeOut | 90 | StrikeOut | yes | 0.33% | 0.00% |
| Squiggly | 90 | Squiggly | yes | 0.33% | 0.00% |
| Note | 90 | Text | no | 0.00% | 0.37% |
| FreeText | 90 | FreeText | yes | 1.35% | 0.03% |
| FreeText with callout | 90 | FreeText | yes | 1.16% | 0.02% |
| Stamp | 90 | Stamp | yes | 6.84% | 0.37% |
| Caret | 90 | Caret | yes | 0.20% | 0.00% |
| FileAttachment | 90 | FileAttachment | yes | 0.18% | 0.00% |
| Redaction | 90 | Redact | yes | 0.92% | 0.01% |

On the turned page MuPDF draws no note: it honours the NoRotate flag that pylopdf sets on notes (ISO 32000-2 §12.5.6.4) by turning the icon and its rectangle about its corner, while hayro ignores the flag (#338). The other differences are anti-aliasing and glyph rasterization along edges and text.

## Links

Each link is created by pylopdf and read by pymupdf's `get_links()`. MuPDF reports a launch action as `LINK_GOTOR` and resolves the named action `NextPage` to a page.

| Target | pymupdf kind | pymupdf reads |
| --- | --- | --- |
| URI | LINK_URI | `https://example.test/` |
| Destination | LINK_GOTO | `1` |
| RemoteDestination | LINK_GOTOR | `other.pdf` |
| Launch | LINK_GOTOR | `notes.txt` |
| NamedAction | LINK_GOTO | `1` |

## Form fields

Fields created by pylopdf, filled by pymupdf, and read back by pylopdf. pymupdf's value of `plan` is that of the first radio button, which is off once pymupdf selects the second.

| Field | pymupdf type | pymupdf value | pylopdf reads |
| --- | --- | --- | --- |
| `name` | Text | `'Grace'` | `'Grace'` |
| `agree` | CheckBox | `'Yes'` | `'Yes'` |
| `plan` | RadioButton | `'Off'` | `'Pro'` |
| `level` | ComboBox | `'high'` | `'high'` |
| `color` | ListBox | `'blue'` | `'blue'` |
| `go` | Button | `''` | `None` |
| `sign` | Signature | `''` | `None` |

## Imported pages

pylopdf imports two pages, the first with an internal link, a URI link, a text field `name`, and a check box, into a one-page document that already has a field `name`, so the imported one becomes `name+1`. pymupdf reads the links of the imported page and fills its fields, and pylopdf reads the filled values back.

| Check | pylopdf | pymupdf |
| --- | --- | --- |
| Internal link page | `2` | `2` |
| URI link | `https://example.test/` | `https://example.test/` |
| Imported field names | `['agree', 'name', 'name+1']` | `['agree', 'name+1']` |
| Values after pymupdf fills them | `'Grace'`, `'Yes'` | `'Grace'`, `'Yes'` |
| Receiving field kept | `'Existing'` | `'Existing'` |

## Flattening

0.00% of samples differ between pylopdf's flatten() and pymupdf's bake(), both rendered by MuPDF.
