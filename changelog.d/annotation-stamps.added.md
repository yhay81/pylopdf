`page.annotations.add()` takes `Stamp(rect, icon=StampIcon.APPROVED, ...)`,
drawing one of the fourteen standard stamps with `color`, `opacity`, and a
clockwise `rotate`, and `Caret(at, ...)`; both read upright on a turned
page, and rendering draws them without an appearance. The codemod rewrites
`add_stamp_annot()`, `add_caret_annot()`, and the `STAMP_*`,
`PDF_ANNOT_LE_*`, and `PDF_BM_*` constants.
