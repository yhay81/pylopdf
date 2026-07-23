# Documentation language policy

Last reviewed: 2026-07-24

## Important languages

| Priority | Language | URL | Role |
|---|---|---|---|
| P0 | English (`en`) | `/` | Canonical meaning; specifications, APIs, and security information are updated here first |
| P1 | Japanese (`ja`) | `/ja/` | The maintainer's primary language and a core CJK workload; every page ships with English |
| P1 | Simplified Chinese (`zh-cn`) | `/zh-cn/` | Directly relevant to CJK PDFs, font fallback, and OCR; every page is maintained |
| P1 | Korean (`ko`) | `/ko/` | Important to both CJK PDF users and the Python community; every page is maintained |

Only English, Japanese, Simplified Chinese, and Korean appear in the language
switcher. A locale is not published while its translation is incomplete or its
strict build produces warnings.

## Selection criteria

1. Python's official documentation has an active coordination team and a
   published translation for the language.
2. The language overlaps with pylopdf's differentiators: CJK handling, OCR,
   lightweight wheels, and production PDF workflows.
3. Every page, including API, security, and benchmark content, can be maintained
   in the same release.
4. URLs use lowercase IETF-style language tags; English remains at the root URL.

As of 2026-07-22, Python's official translation dashboard reported 91.26%
overall completion for Simplified Chinese, 45.67% for Korean, and 39.17% for
Japanese, with coordination teams for all three. Python's translation guidance
also prioritizes the latest non-alpha branch and natural translation of meaning.

- [Python Docs Translation Dashboard](https://translations.python.org/)
- [Python Developer's Guide: Translating](https://devguide.python.org/documentation/translations/translating/)
- [PEP 545 – Python Documentation Translations](https://peps.python.org/pep-0545/)

## Repository language

English is canonical for all repository-facing prose:

- project documentation and policies;
- source comments, docstrings, type stubs, and user-facing messages;
- tests, fixture descriptions, benchmark output, configuration comments, and
  automation;
- new commit messages, issues, and pull requests.

Non-English text is allowed only in localized documentation and data required to
test Unicode or CJK behavior. Language self-names may be used in a language
switcher. Existing Git history is not rewritten.

## Translation rules

- English is the semantic source of truth. Do not translate API names, class
  names, parameter names, file names, commands, or code.
- Prefer technical meaning and natural expression over literal translation.
- Keep heading IDs identical across languages so same-page switching and
  external links remain stable.
- Synchronize numbers, supported Python versions, security guidance, and
  benchmark environments across all languages.
- Build every P1 locale in Zensical strict mode and validate internal links.
- Translation-only fixes are welcome. When meaning must change, update English
  first and then synchronize P1 locales.

## Candidates

Spanish (`es`), Brazilian Portuguese (`pt-br`), French (`fr`), and Traditional
Chinese (`zh-tw`) are on the watchlist. Promote a language to P1 when ongoing
full-page maintenance has an owner and at least one of these conditions is met:

- privacy-conscious analytics show at least 5% of sessions for two consecutive
  months;
- issues, discussions, or adoption requests in the language recur;
- at least two recurring contributors volunteer to maintain the translation.
