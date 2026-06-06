# 設計地図

この階層は OtomeKairo の設計正本を置く。
API wire、記憶詳細、LLM 補助契約、capability 境界を分け、同じ仕様を複数箇所に正本として置かない。

## フォルダ

| フォルダ | 責務 | 入口 |
| --- | --- | --- |
| [foundation/](foundation/) | 全体構成、人格設定、記憶、現在の個の上位関係 | [foundation/アーキテクチャ.md](./foundation/アーキテクチャ.md) |
| [runtime/](runtime/) | 判断ループ、状態、感情、時刻、自律判断、デバッグ | [runtime/判断と行動.md](./runtime/判断と行動.md) |
| [configuration/](configuration/) | 設定定義、設定変更、人格設定、モデルプリセット | [configuration/設定モデル.md](./configuration/設定モデル.md) |
| [llm/](llm/) | LLM を使う判断と補助処理の契約 | [llm/LLM判断優先方針.md](./llm/LLM判断優先方針.md) |
| [capability/](capability/) | capability manifest、視覚機能、能力由来の source pack | [capability/capability_manifest.md](./capability/capability_manifest.md) |
| [integration/](integration/) | 外部接点、権限境界、connector 配置 | [integration/外部接点とAPI概念.md](./integration/外部接点とAPI概念.md) |
| [api/](api/) | HTTP / WebSocket API の wire 契約 | [api/README.md](./api/README.md) |
| [memory/](memory/) | 記憶 subsystem のデータ、想起、更新、管理 | [memory/README.md](./memory/README.md) |

## 正本境界

- 意味境界、状態遷移、判断責務は `foundation/`、`runtime/`、`configuration/`、`llm/`、`capability/`、`integration/` に置く
- HTTP / WebSocket の path、method、認証、request / response、error code は `api/` に置く
- 記憶 subsystem の内部契約は `memory/` に置く
- 感情モデルは [runtime/感情モデル.md](./runtime/感情モデル.md) を正とし、memory 文書はそこへリンクする
- 現行実装の状態は `src/` と smoke 結果を正とする

## 目的別の読む順

### 全体像

1. [foundation/アーキテクチャ.md](./foundation/アーキテクチャ.md)
2. [foundation/人格と記憶.md](./foundation/人格と記憶.md)
3. [runtime/判断と行動.md](./runtime/判断と行動.md)
4. [runtime/状態モデル.md](./runtime/状態モデル.md)

### 判断と状態

1. [runtime/判断と行動.md](./runtime/判断と行動.md)
2. [runtime/状態モデル.md](./runtime/状態モデル.md)
3. [runtime/world_state.md](./runtime/world_state.md)
4. [runtime/activity_state.md](./runtime/activity_state.md)
5. [runtime/自律initiative_loop.md](./runtime/自律initiative_loop.md)
6. [runtime/デバッグ可能性.md](./runtime/デバッグ可能性.md)

### 記憶

1. [foundation/人格と記憶.md](./foundation/人格と記憶.md)
2. [memory/README.md](./memory/README.md)
3. [runtime/感情モデル.md](./runtime/感情モデル.md)
4. [memory/データモデル.md](./memory/データモデル.md)
5. [memory/想起と判断.md](./memory/想起と判断.md)
6. [memory/記憶更新と再整理.md](./memory/記憶更新と再整理.md)

### API と接続

1. [integration/外部接点とAPI概念.md](./integration/外部接点とAPI概念.md)
2. [integration/接続と権限境界.md](./integration/接続と権限境界.md)
3. [api/README.md](./api/README.md)
4. [capability/capability_manifest.md](./capability/capability_manifest.md)
5. [integration/外部接続connector配置方針.md](./integration/外部接続connector配置方針.md)

### LLM 境界

1. [llm/LLM判断優先方針.md](./llm/LLM判断優先方針.md)
2. [llm/LLM補助契約共通.md](./llm/LLM補助契約共通.md)
3. [llm/プロンプト文脈分離方針.md](./llm/プロンプト文脈分離方針.md)

個別の LLM 補助処理は、対象領域の文書からリンクされた文書を読む。
