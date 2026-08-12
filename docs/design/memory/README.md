# 記憶設計方針

## なぜ記憶を重く扱うか

OtomeKairo では、人格設定と記憶を操作上は独立に扱う。
ただし、実際の応答や行動判断は人格設定だけで決まらない。

- 人格設定は基本姿勢を与える
- 記憶は自己像、他者理解、関係、未完了、感情傾向を育てる
- 応答や行動判断は、その時点の人格設定と記憶から構成される現在の個で決まる

そのため、記憶は検索補助ではなく、現在の個を構成する基盤として設計する。

## `design/` 直下との関係

このフォルダは記憶 subsystem の内部構造、想起、更新、管理境界を正本にする。
[../foundation/人格と記憶.md](../foundation/人格と記憶.md) は、人格設定、記憶、現在の個の上位関係を定める。
この `memory/` 配下は、その前提を変えずに記憶 subsystem の詳細だけを定める。

人格設定そのものの定義や設定変更全体の意味は [../foundation/人格と記憶.md](../foundation/人格と記憶.md)、[../configuration/設定モデル.md](../configuration/設定モデル.md)、[../configuration/設定変更.md](../configuration/設定変更.md) を正とする。

## 基本構造

記憶本体は次の 4 領域で構成する。

1. `events` — 観測、返答、判断、実行結果の生ログ（根拠層）
2. `episodes` — その場で何が起き、何が残ったかを束ねる経験単位
3. `memory_units` — 経験から育った継続理解の単位
4. 感情モデル — `episode_affect / mood_state / affect_state`（正本は [../runtime/感情モデル.md](../runtime/感情モデル.md)）

`memory_links`、`entity_registry`、`relation_index`、`revisions`、`retrieval_runs`、`vector_index_entries` は想起・監査・索引の補助構造であり、現在の個を直接構成する記憶層ではない。

循環は次のとおり。

1. 現在入力を受け取る
2. `RecallHint` を作る
3. `RecallPack` を組む
4. 判断系が結果種別を選ぶ
5. 結果を `events` に残す
6. `turn consolidation` で `episodes` と `memory_units` を更新する
7. 条件を満たしたときに `reflective consolidation` で再整理する

## 各文書の責務

| 文書 | 責務 |
| --- | --- |
| [../runtime/感情モデル.md](../runtime/感情モデル.md) | `episode_affect / mood_state / affect_state` と `AffectContext` |
| [データモデル.md](データモデル.md) | `events / episodes / memory_units` と補助構造の契約 |
| [想起と判断.md](想起と判断.md) | `RecallHint`、構造レーン、連想レーン、`RecallPack` |
| [記憶更新と再整理.md](記憶更新と再整理.md) | `turn consolidation` と `reflective consolidation` |
| [記憶管理と削除.md](記憶管理と削除.md) | 個別記憶行に対する外部操作と管理操作を採用しない境界 |
| [RecallPackのLLM選別.md](RecallPackのLLM選別.md) | RecallPack 選別の LLM 契約 |
| [event_evidenceのLLM圧縮.md](event_evidenceのLLM圧縮.md) | event_evidence 圧縮の LLM 契約 |
| [内省要約のLLM生成.md](内省要約のLLM生成.md) | 内省要約生成の LLM 契約 |

## 意図的に採らないもの

- ベクトル検索だけで記憶を成立させること
- なんでも 1 本の自由文 summary に潰すこと
- 生ログを捨てて派生記憶だけ残すこと
- 1 ターンごとに自己像全体を書き直すこと
- 最初から大規模知識グラフを前提にすること

ただし、compare key が一致した `memory_units` の自由文比較では、
`object_ref_or_value`、`summary_text`、意味を持つ `qualifiers` の表現差を吸収するために
埋め込み類似度を補助的に使う。
compare key 自体の決定、状態遷移、明示訂正の扱いは構造ルールを正とする。
