# Changelog fragments

Each pull request that changes what pylopdf ships (`src/`, `rust/`, `fonts/`,
`models/`, or `pyproject.toml`) records every user-visible change as one
fragment in this directory. At release time, `tools/changelog.py assemble`
folds the fragments into `CHANGELOG.md` and deletes them, so pull requests
never edit the same lines of the changelog.

Name a fragment `<slug>.<type>.md`. The slug is lowercase and hyphenated,
such as `garbage-levels`; the type names the changelog section:

| Type | Section |
| --- | --- |
| `added` | Added |
| `changed` | Changed |
| `deprecated` | Deprecated |
| `removed` | Removed |
| `fixed` | Fixed |
| `security` | Security |
| `performance` | Performance |
| `documentation` | Documentation |

Write the entry's Markdown text without the leading `- ` of the list item, in
English, wrapped at about 78 columns. Continuation lines need no indentation.
A pull request with several user-visible changes adds several fragments.

```text
changelog.d/garbage-levels.added.md
```

```markdown
`Document.save` and `Document.tobytes` take pymupdf's cumulative `garbage`
levels 0 to 4.
```

A pull request that changes shipped files without changing anything for users,
such as a refactor, takes the `no-changelog` label instead. Dependency updates
labeled `dependencies` are exempt as well. The `Changelog` workflow runs
`python tools/changelog.py check` against the base branch to enforce this, and
`python tools/changelog.py preview` prints the next release's section.
