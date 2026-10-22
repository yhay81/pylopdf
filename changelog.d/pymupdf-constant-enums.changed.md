Give pymupdf's constant groups their enum types: `LINK_*` are
now members of the new `LinkKind` IntEnum, which `Page.get_links` reports as
`kind`, and `TEXT_ALIGN_*` of the new `TextAlign` IntEnum, with unchanged
names and values. `Permissions` members take pymupdf's names (`FORM`,
`ACCESSIBILITY`, and `PRINT_HQ` replace `FILL_FORMS`,
`COPY_FOR_ACCESSIBILITY`, and `PRINT_HIGH_QUALITY`), and the `PDF_PERM_*`
module constants are its members.
