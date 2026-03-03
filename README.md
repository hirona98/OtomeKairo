# OtomeKairo

<!-- Block: Summary -->
身体性を持つ AI 人格コア

<!-- Block: Entry -->
## 入口

- 作業開始: `docs/00_index.md`
- 目標構成: `docs/10_目標アーキテクチャ.md`
- 決定済み外部接続: `docs/20_外部インタフェース.md`
- システム設計: `docs/30_システム設計.md`
- ランタイム処理仕様: `docs/31_ランタイム処理仕様.md`
- 記憶設計: `docs/32_記憶設計.md`
- `memory_jobs` 仕様: `docs/33_記憶ジョブ仕様.md`
- SQLite 論理スキーマ: `docs/34_SQLite論理スキーマ.md`
- WebAPI仕様: `docs/35_WebAPI仕様.md`
- JSONデータ仕様: `docs/36_JSONデータ仕様.md`
- 起動初期化仕様: `docs/37_起動初期化仕様.md`
- 入力ストリーム運用仕様: `docs/38_入力ストリーム運用仕様.md`
- 設定キー運用仕様: `docs/39_設定キー運用仕様.md`
- 人格変化仕様: `docs/40_人格変化仕様.md`
- 人格選択仕様: `docs/41_人格選択仕様.md`
- 設定既定値: `config/default_settings.json`
- 初期 SQL 実装: `sql/core_schema.sql`
- 参考メモ: `docs/note/記憶設計に関する先行研究のメモ.md`

<!-- Block: Current Implementation -->
## 現在の実装状況

- `pyproject.toml`: Python パッケージ定義、`LiteLLM` 依存、`otomekairo-web` / `otomekairo-runtime` の起動入口を持つ
- `src/otomekairo/boot/run_web.py`: `uvicorn` で Web サーバを起動する
- `src/otomekairo/boot/run_runtime.py`: 人格ランタイムの常時ループを起動する
- `src/otomekairo/web/app.py`: FastAPI アプリを構成し、API ルータと例外処理を束ねる
- `src/otomekairo/gateway/cognition_client.py`: 認知処理の外部境界を表す抽象を定義する
- `src/otomekairo/usecase/build_cognition_input.py`: `self_state` などの現在状態から最小の `cognition_input` を組み立てる
- `src/otomekairo/usecase/run_cognition.py`: 認知クライアントが返す `cognition_result` を受け取り、`speech_draft.text` を `token` / `message` / `status` の UI 応答と `action_history` へ変換する
- `src/otomekairo/usecase/validate_action.py`: `cognition_result.action_proposals` から `speak` 候補を検査し、最小の `action validator` として確定候補を選ぶ
- `src/otomekairo/infra/litellm_cognition_client.py`: `LiteLLM` を使って人格断面つきの認知呼び出しを行う
- `src/otomekairo/infra/sqlite_state_store.py`: `core_schema.sql` を読み込む DB 初期化と、状態参照・入力受付・設定反映、短周期確定時の `write_memory` enqueue、`revisions` 記録、`memory_state` と `event` を対象にした `refresh_preview` / `embedding_sync`、`searchable=0` へ落とす `quarantine_memory`、`memory_jobs` の再キュー / `dead_letter`、`ui_outbound_events` の保持窓削除を持つ
- `src/otomekairo/runtime/main_loop.py`: `settings_overrides` と `pending_inputs` を消費し、待機中も応答中も lease heartbeat を維持しながら、失敗時も `claimed` を終端状態へ確定し、`token` の即時追記、進行中 `cancel` の消費、`write_memory` / `refresh_preview` / `embedding_sync` / `quarantine_memory` の最小長周期処理までを行う
- `src/otomekairo/schema/runtime_types.py`: ランタイムの共通データ形を `infra` から切り離して持つ
- `src/otomekairo/schema/settings.py`: 設定キーの検証と `config/default_settings.json` からの既定値読み込みを持つ
- `config/default_settings.json`: `runtime_settings` seed と Web の `effective_settings` に使う設定既定値の正本を持つ
