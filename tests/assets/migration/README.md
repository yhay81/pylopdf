# Migration corpus

pymupdf programs written for this repository, not copied from elsewhere, and
their migrations by `python -m pylopdf.migrate`.
`tests/test_migration.py` requires each program in `pymupdf/` to migrate
exactly to its namesake in `pylopdf/`, and every migrated program except
`needs_review.py` to run and print `<name> ok`. `needs_review.py` uses pymupdf
features that a person has to migrate; the test pins the TODO comments that
the codemod adds to it. Regenerate the expected files with:

```bash
cp tests/assets/migration/pymupdf/*.py tests/assets/migration/pylopdf/
uv run python -m pylopdf.migrate tests/assets/migration/pylopdf
```
