# OtomeKairo docs

この docs は、OtomeKairo の設計正本を置く。
実装手順と smoke は [../README.md](../README.md) を入口にする。

OtomeKairo は、人格設定と記憶を基盤に、その時点で成立する判断主体を「現在の個」として扱う。
実際の挙動は、人格設定だけでも記憶だけでも決まらず、人格設定、記憶、そこから派生する内部状態を含む文脈で決まる。

## 読み方

最初に [reference/用語表.md](reference/用語表.md) を確認し、目的に対応する「目的別の読む順」へ進む。

## 構成

| 場所 | 役割 |
| --- | --- |
| [reference/](reference/) | 用語の正規語、wire/code 表記、避ける表記、参考資料の整理 |
| [design/foundation/](design/foundation/) | 全体構成、人格設定、記憶、現在の個の上位関係 |
| [design/runtime/](design/runtime/) | 判断ループ、状態、感情、時刻、自律判断、デバッグ |
| [design/configuration/](design/configuration/) | 設定定義、設定変更、人格設定、モデルプリセット |
| [design/llm/](design/llm/) | LLM を使う判断と補助処理の契約 |
| [design/capability/](design/capability/) | capability manifest、視覚機能、能力由来の source pack |
| [design/integration/](design/integration/) | 外部接点、権限境界、connector 配置 |
| [design/api/](design/api/) | HTTP / WebSocket の path、method、認証、request / response、error code |
| [design/memory/](design/memory/) | 記憶 subsystem の内部構造、想起、更新、管理境界 |
| [design/verification/](design/verification/) | 検証層、通常検証、重い検証、合否基準 |

## 正本境界

- 検証層と合否基準は [design/verification/検証基盤.md](design/verification/検証基盤.md) に置く
- 意味境界、状態遷移、判断責務は `design/foundation/`、`design/runtime/`、`design/configuration/`、`design/llm/`、`design/capability/`、`design/integration/` に置く
- HTTP / WebSocket の path、method、認証、request / response、error code は `design/api/` に置く
- 記憶 subsystem の内部契約は `design/memory/` に置く
- 感情モデルは [design/runtime/感情モデル.md](design/runtime/感情モデル.md) を正本とし、memory 文書はそこへリンクする
- 例示 JSON は shape を示す例とし、意味規則は対応する設計文書に置く

同じ仕様を複数の docs に正本として書かない。
仕様を追加または変更するときは、最初に正本にする文書を決め、その文書だけに意味規則、状態遷移、上限値、失敗条件の詳細を書く。
周辺文書には、必要な短い要約と正本へのリンクだけを置く。

## 目的別の読む順

### 全体像

1. [design/foundation/アーキテクチャ.md](design/foundation/アーキテクチャ.md)
2. [design/foundation/人格と記憶.md](design/foundation/人格と記憶.md)
3. [design/runtime/判断と行動.md](design/runtime/判断と行動.md)
4. [design/runtime/状態モデル.md](design/runtime/状態モデル.md)

### 判断と状態

1. [design/runtime/判断と行動.md](design/runtime/判断と行動.md)
2. [design/runtime/状態モデル.md](design/runtime/状態モデル.md)
3. [design/runtime/world_state.md](design/runtime/world_state.md)
4. [design/runtime/activity_state.md](design/runtime/activity_state.md)
5. [design/runtime/自律initiative_loop.md](design/runtime/自律initiative_loop.md)
6. [design/runtime/デバッグ可能性.md](design/runtime/デバッグ可能性.md)

### 記憶

1. [design/foundation/人格と記憶.md](design/foundation/人格と記憶.md)
2. [design/memory/README.md](design/memory/README.md)
3. [design/runtime/感情モデル.md](design/runtime/感情モデル.md)
4. [design/memory/データモデル.md](design/memory/データモデル.md)
5. [design/memory/想起と判断.md](design/memory/想起と判断.md)
6. [design/memory/記憶更新と再整理.md](design/memory/記憶更新と再整理.md)

### API と接続

1. [design/integration/外部接点とAPI概念.md](design/integration/外部接点とAPI概念.md)
2. [design/integration/接続と権限境界.md](design/integration/接続と権限境界.md)
3. [design/api/README.md](design/api/README.md)
4. [design/capability/capability_manifest.md](design/capability/capability_manifest.md)
5. [design/integration/外部接続connector配置方針.md](design/integration/外部接続connector配置方針.md)

### LLM 境界

1. [design/llm/LLM判断優先方針.md](design/llm/LLM判断優先方針.md)
2. [design/llm/LLM補助契約共通.md](design/llm/LLM補助契約共通.md)
3. [design/llm/プロンプト文脈分離方針.md](design/llm/プロンプト文脈分離方針.md)

個別の LLM 補助処理は、対象領域の文書からリンクされた文書を読む。

### 設定とモデル

1. [design/configuration/設定モデル.md](design/configuration/設定モデル.md)
2. [design/configuration/設定変更.md](design/configuration/設定変更.md)
3. [design/configuration/人格設定詳細.md](design/configuration/人格設定詳細.md)
4. [design/configuration/モデルプリセット詳細.md](design/configuration/モデルプリセット詳細.md)

検証を行う場合は [design/verification/検証基盤.md](design/verification/検証基盤.md) を読む。

## 設計方針

- シンプルさを優先する
- 判断の中心は LLM に置く
- 実行の入口と境界はコード側で明示的に管理する
- 長く残る契約だけ docs に残し、内部フローはコードを正とする
