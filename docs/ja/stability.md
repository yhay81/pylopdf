---
title: API安定性
description: pylopdfの公開API境界、セマンティックバージョニング、非推奨化のライフサイクル、互換性レビュー手順。
---

# API安定性

pylopdfは[Semantic Versioning 2.0.0](https://semver.org/)に従います。このページでは、
結果がPDF生成元、フォント、renderer、対応runtimeにも依存するPython libraryにおいて、
その方針が何を意味するかを定義します。

## 現在の状態 { #current-status }

0.11 APIは**候補baseline**であり、v1.0の互換性保証そのものではありません。実利用で
検証しながら、加算的な改善とレビュー済みの修正を続けます。一方で、公開面の変更は
今からすべて検出・レビューし、v1.0の境界を偶然ではなく意図して決めます。

[v1.0以降](#after-v1)の保証はv1.0から始まります。それまでも互換性に影響する変更と
移行方法をrelease noteに記載し、1.0未満であることを無言の破壊的変更の理由には
しません。

## 公開APIの境界 { #public-api-boundary }

対応する公開APIは次の範囲です。

- `pylopdf.__all__`がexportする名前と`pylopdf.__version__`
- それらのclassで文書化されたpublic member
- callableのparameter名、種類、default、文書化されたreturn contract
- 文書化された定数とenum値
- TypedDictの必須・任意key、NamedTuple field、public type alias
- 文書化された例外階層と`LimitError.code`などの機械可読属性

`_`で始まる名前、`pylopdf.pylopdf_core`、Rust実装詳細、objectの`repr`、例外messageの
完全一致、warning順序、未文書化属性はprivateです。public objectを実装moduleから
importしても、そのmodule pathはpublicになりません。`pylopdf`からimportしてください。

`save()`が生成するPDF byte列とPNG/SVGの正確なserializationは、byte単位で固定された
formatではありません。文書化された見た目、構造、抽出の意味論がcontractです。

## v1.0以降 { #after-v1 }

stable releaseでは次の規則を適用します。

- **major** releaseはpublic APIを削除または非互換に変更できます。
- **minor** releaseは後方互換なAPIと挙動を追加します。
- **patch** releaseはpublic APIを意図的に変えず不具合を修正します。

public symbol/memberの削除・改名、parameterの必須化、positional/keyword受付の非互換
変更、mapping keyの削除、入力範囲の縮小、文書化された定数の変更、publicな例外継承の
破壊にはmajor releaseが必要です。

任意keyword parameter、新しいsymbol/method、入力として受け付けるenum・Literalの
選択肢、任意の結果keyの追加は通常加算的です。型contractもruntime objectと同じ
互換性レビュー対象です。TypedDict keyの必須・任意間の移動やvalue typeの非互換変更を
「型だけの変更」とは扱いません。

## 非推奨化のライフサイクル { #deprecation-lifecycle }

v1.0以降、削除予定のpublic APIは通常次の順序で扱います。

1. 代替手段と最短削除releaseを示して非推奨と文書化する。
2. 実用的な場合は`DeprecationWarning`を発行する。
3. 少なくとも2回のminor releaseかつ6か月間維持する。
4. major releaseでのみ削除する。

`DeprecationWarning`は開発者の移行用です。`PylopdfWarning`はPDF解釈時の運用warning
に使い続け、非推奨通知には使いません。

security、legal、またはupstream runtimeの緊急事態では期間を短縮することがあります。
その場合は影響を最小にする移行方法とともに、changelogとrelease noteで明示します。

## 挙動とdataの互換性 { #behavior-and-data }

旧挙動が誤っていた場合、bug fixによって出力が変わることがあります。壊れたPDFの
回復、読み順、glyph geometry、table解釈、色変換、renderer差分などが該当します。
文書化されたcontractに近づける修正はmajor releaseを必要としませんが、影響が大きい
場合は明記します。

resource limitにより、以前は処理を試みた攻撃的または想定外に高コストな入力を拒否
することがあります。callerが機械的に判断する箇所には安定した例外型とerror codeを
用い、人間向けmessageの文面はAPIとしません。

対応範囲はreleaseごとに文書化されたPython version、platform、ABI、WebAssembly
runtime matrixで定義します。upstreamのEOL後、または対応継続がsecurity/correctness
修正を妨げる場合、minor releaseでruntimeを外すことがあります。理由と移行期間を
公開します。Pyodide互換性はruntime更新を推測せず、pylopdfのminor lineごとに固定して
検証します。

## 互換性レビュー { #compatibility-review }

[`api/public-api.json`](https://github.com/yhay81/pylopdf/blob/main/api/public-api.json)
は0.11のレビュー済み候補surfaceを記録します。全native Python laneで、export、
signature、mapping key、type alias、enum/定数値、public member、例外継承の変化を
testします。

```console
uv run python tools/check_api_surface.py
```

意図的な変更ではruntime、typing、documentation、SemVerへの影響をレビューしてから
snapshotを更新します。

```console
uv run python tools/check_api_surface.py --update
```

snapshotはreview gateであり、互換性を自動判定するものではありません。意図的な変更
には引き続きtest、4言語のdocumentation、changelog entryが必要です。
