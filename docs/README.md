# OtomeKairo docs

この docs は、OtomeKairo の設計正本を置く。
実装手順と smoke は [../README.md](../README.md) を入口にする。

OtomeKairo は、人格設定と記憶を基盤に、その時点で成立する判断主体を「現在の個」として扱う。
実際の挙動は、人格設定だけでも記憶だけでも決まらず、人格設定、記憶、そこから派生する内部状態を含む文脈で決まる。

用語の正規語は [用語表.md](用語表.md) を参照する。
全体像は [design/foundation/アーキテクチャ.md](design/foundation/アーキテクチャ.md) から入る。

## 構成

| 場所 | 役割 |
| --- | --- |
| [用語表.md](用語表.md) | 用語の正規語、wire/code 表記、避ける表記 |
| [design/foundation/](design/foundation/) | 全体構成、人格設定、記憶、現在の個の上位関係 |
| [design/runtime/](design/runtime/) | 判断ループ、状態、感情、時刻、自律判断、デバッグ |
| [design/configuration/](design/configuration/) | 設定定義、設定変更、人格設定、モデルプリセット |
| [design/llm/](design/llm/) | LLM を使う判断と補助処理の契約 |
| [design/capability/](design/capability/) | capability manifest、視覚機能、能力由来の source pack |
| [design/audio/](design/audio/) | 音声入力、VAD、STT、音声起動ワード、話者識別、音声人物 |
| [design/integration/](design/integration/) | 外部接点、権限境界、connector 配置、Cocoro 連携、ブラウザ UI 規約 |
| [design/api/](design/api/) | HTTP / WebSocket の path、method、認証、request / response、error code |
| [design/memory/](design/memory/) | 記憶 subsystem の内部構造、想起、更新、管理境界 |
| [design/verification/](design/verification/) | 検証層、通常検証、重い検証、合否基準 |

## 正本境界

- 意味境界、状態遷移、判断責務は `design/foundation/`、`design/runtime/`、`design/configuration/`、`design/llm/`、`design/capability/`、`design/integration/` に置く
- 音声の意味規則は `design/audio/` に置く
- HTTP / WebSocket の wire 契約は `design/api/` に置く
- 記憶 subsystem の内部契約は `design/memory/` に置く
- 感情モデルは [design/runtime/感情モデル.md](design/runtime/感情モデル.md) を正本とし、memory 文書はそこへリンクする
- 検証層と合否基準は [design/verification/検証基盤.md](design/verification/検証基盤.md) に置く
- ブラウザ UI 見た目規約は [design/integration/WebUI設定フォーム規約.md](design/integration/WebUI設定フォーム規約.md) と [design/integration/WebUI通常画面規約.md](design/integration/WebUI通常画面規約.md) を正本とする
- Agent Skills の発見、選択、resource 読込、script 信頼境界は [design/integration/AgentSkills統合.md](design/integration/AgentSkills統合.md) を正本とする
- 例示 JSON は shape を示す例とし、意味規則は対応する設計文書に置く

同じ仕様を複数の docs に正本として書かない。
仕様を追加または変更するときは、最初に正本にする文書を決め、その文書だけに意味規則、状態遷移、上限値、失敗条件の詳細を書く。
周辺文書には、必要な短い要約と正本へのリンクだけを置く。

## 設計方針

- シンプルさを優先する
- 判断の中心は LLM に置く
- 実行の入口と境界はコード側で明示的に管理する
- 長く残る契約だけ docs に残し、内部フローはコードを正とする
