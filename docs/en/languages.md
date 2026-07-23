---
title: Language policy
description: The languages pylopdf treats as first-class documentation and how translations stay trustworthy.
---

# Language policy

pylopdf is built for the global Python community, with an especially strong
commitment to CJK documents. Language support is therefore part of the product,
not a decorative machine-translated layer.

## Important languages { #important-languages }

| Priority | Language | URL | Why it matters |
|---|---|---|---|
| P0 | English | `/` | Canonical technical meaning and the shared language of the Python ecosystem |
| P1 | Japanese | `/ja/` | The project's home community and a core CJK document workload |
| P1 | Simplified Chinese | `/zh-cn/` | A large, active Python translation community and a core CJK workload |
| P1 | Korean | `/ko/` | An active Python translation community and a core CJK workload |

English is the semantic source of truth. All four languages are nevertheless
built as complete sites with the same navigation, page set, heading anchors and
strict link checks.

## Translation principles { #translation-principles }

- Translate meaning and examples in context; do not translate API names, code,
  file names or command-line options.
- Update versions, security guidance and benchmark data in every P1 language in
  the same change.
- Preserve shared heading IDs so links continue to work across the language
  switcher.
- Prefer a maintained, complete translation over adding many shallow locales.
- Promote another language when usage, sustained demand or a maintainership
  team justifies first-class support.

Spanish, Brazilian Portuguese, French and Traditional Chinese are tracked as
candidates. The full decision record and promotion criteria live in
[`LANGUAGES.md`](https://github.com/yhay81/pylopdf/blob/main/LANGUAGES.md).
