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
- `src/otomekairo/usecase/run_cognition.py`: 認知クライアントのストリーム出力を `token` / `message` / `status` の UI 応答と `action_history` へ変換する
- `src/otomekairo/infra/litellm_cognition_client.py`: `LiteLLM` を使って人格断面つきの認知呼び出しを行う
- `src/otomekairo/infra/sqlite_state_store.py`: `core_schema.sql` を読み込む DB 初期化と、状態参照・入力受付・設定反映の最小実装を持つ
- `src/otomekairo/runtime/main_loop.py`: `settings_overrides` と `pending_inputs` を消費し、待機中も応答中も lease heartbeat を維持しながら、`token` の即時追記と進行中 `cancel` の消費を含めて、`input_journal`、`action_history`、`events`、`ui_outbound_events`、`commit_records` まで閉じる
- `src/otomekairo/schema/runtime_types.py`: ランタイムの共通データ形を `infra` から切り離して持つ
- `src/otomekairo/schema/settings.py`: 設定キーの検証と有効設定の初期値を定義する
