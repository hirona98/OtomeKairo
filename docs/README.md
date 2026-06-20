# OtomeKairo docs

この docs は、OtomeKairo の設計正本を置く。
実装手順と smoke は [../README.md](../README.md) を入口にする。

OtomeKairo は、人格設定と記憶を基盤に、その時点で成立する判断主体を「現在の個」として扱う。
実際の挙動は、人格設定だけでも記憶だけでも決まらず、人格設定、記憶、そこから派生する内部状態を含む文脈で決まる。

## 初見の読み順

1. [design/README.md](design/README.md)
2. [reference/用語表.md](reference/用語表.md)
3. [design/foundation/アーキテクチャ.md](design/foundation/アーキテクチャ.md)
4. [design/foundation/人格と記憶.md](design/foundation/人格と記憶.md)
5. 触る領域に応じて [design/api/README.md](design/api/README.md) または [design/memory/README.md](design/memory/README.md) を読む。

## 構成

| 場所 | 役割 |
| --- | --- |
| [reference/](reference/) | 用語の正規語、wire/code 表記、避ける表記 |
| [design/](design/) | 設計地図と領域別の意味境界 |
| [design/api/](design/api/) | HTTP / WebSocket の path、method、認証、request / response、error code |
| [design/memory/](design/memory/) | 記憶 subsystem の内部構造、想起、更新、管理境界 |

## 正本境界

- API 文書は通信仕様を正本にする
- 設定値、状態、capability、記憶、LLM role の意味規則は対応する design 文書を正本にする
- 例示 JSON は shape を読むための例として扱い、意味規則の正本にしない

同じ仕様を複数の docs に正本として書かない。
仕様を追加または変更するときは、最初に正本にする文書を決め、その文書だけに意味規則、状態遷移、上限値、失敗条件の詳細を書く。
周辺文書には、必要な短い要約と正本へのリンクだけを置く。

## 目的別の入口

- 今後の設計実装課題: [design/今後の設計実装課題.md](design/今後の設計実装課題.md)
- 検証基盤: [design/verification/検証基盤.md](design/verification/検証基盤.md)
- 全体構成: [design/foundation/アーキテクチャ.md](design/foundation/アーキテクチャ.md)
- 人格設定と記憶: [design/foundation/人格と記憶.md](design/foundation/人格と記憶.md)
- 判断と行動: [design/runtime/判断と行動.md](design/runtime/判断と行動.md)
- 状態モデル: [design/runtime/状態モデル.md](design/runtime/状態モデル.md)
- world_state: [design/runtime/world_state.md](design/runtime/world_state.md)
- activity_state: [design/runtime/activity_state.md](design/runtime/activity_state.md)
- 自律判断: [design/runtime/自律initiative_loop.md](design/runtime/自律initiative_loop.md)
- デバッグと inspection: [design/runtime/デバッグ可能性.md](design/runtime/デバッグ可能性.md)
- 設定とモデル: [design/configuration/設定モデル.md](design/configuration/設定モデル.md)
- API wire: [design/api/README.md](design/api/README.md)
- 外部接点と権限: [design/integration/外部接点とAPI概念.md](design/integration/外部接点とAPI概念.md)
- capability: [design/capability/capability_manifest.md](design/capability/capability_manifest.md)
- 記憶: [design/memory/README.md](design/memory/README.md)
- LLM 補助契約: [design/llm/LLM補助契約共通.md](design/llm/LLM補助契約共通.md)

## 設計方針

- シンプルさを優先する
- 判断の中心は LLM に置く
- 実行の入口と境界はコード側で明示的に管理する
- 長く残る契約だけ docs に残し、内部フローはコードを正とする
