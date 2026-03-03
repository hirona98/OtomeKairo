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

- `pyproject.toml`: Python パッケージ定義、`LiteLLM` と `sqlite-vec` 依存、`otomekairo` / `otomekairo-web` / `otomekairo-runtime` の起動入口を持つ
- `run_otomekairo.sh`: Web サーバと人格ランタイムを引数なしで同時起動する最短の実行スクリプト
- `src/otomekairo/boot/run_web.py`: `uvicorn` で Web サーバを起動し、既定では `0.0.0.0:8000` に bind する
- `src/otomekairo/boot/run_runtime.py`: 人格ランタイムの常時ループを起動する
- `src/otomekairo/boot/run_all.py`: Web サーバと人格ランタイムを同じ親プロセスで起動し、終了シグナル時に両方を停止する
- `src/otomekairo/web/app.py`: FastAPI アプリを構成し、API ルータ、最小ブラウザ UI (`GET /`)、例外処理を束ねる
- `src/otomekairo/web/static/`: `tmp/CocoroGhost/static/` の見た目を簡略流用した最小チャット UI を持ち、同一オリジンで `POST /api/chat/input` と `GET /api/chat/stream` を使い、`message` 到着時は `output.tts.enabled=true` ならブラウザの `SpeechSynthesis` で音声化し、`Mic` は標準 `SpeechRecognition` で音声入力し、設定パネルでは主要な一部設定を `POST /api/settings/overrides` へ保存できる
- `src/otomekairo/gateway/cognition_client.py`: 認知処理の外部境界を表す抽象を定義する
- `src/otomekairo/usecase/build_cognition_input.py`: `self_state` などの現在状態から最小の `cognition_input` を組み立て、`task_state` の進行中 / 外部待ちタスク、`sqlite-vec` で補強した直近の `summary` / `fact` 記憶、直近イベント列を `current_observation` と照合して絞り込み、`memory_bundle` として渡す
- `src/otomekairo/usecase/run_cognition.py`: 認知クライアントが返す `cognition_result` を受け取り、`action_command` を使って `speak` は `token` / `message`、`notify` は `notice`、`browse` は `waiting_external` の検索タスクとして実行し、`action_history` へ変換する
- `src/otomekairo/usecase/run_browse_task.py`: `task_state(waiting_external)` の `browse` タスクを外部検索へ通し、検索結果を内部入力 `network_result` として次周期へ戻し、`action_history` へ変換する
- `src/otomekairo/usecase/validate_action.py`: `cognition_result.action_proposals` から `speak` / `browse` / `notify` / `wait` を比較し、`selection_profile` の trait / style / relationship / emotion / drive、`memory_bundle`、`task_snapshot` を使って `execute / hold / reject` と構造化した `action_command` を確定する
- `src/otomekairo/infra/litellm_cognition_client.py`: `LiteLLM` を使って人格断面つきの認知呼び出しを行い、`cognition_result.action_proposals` の最小形も厳密に検証する
- `src/otomekairo/gateway/search_client.py`: 外部検索の境界を表す抽象を定義する
- `src/otomekairo/gateway/notification_client.py`: 外部通知の境界を表す抽象を定義する
- `src/otomekairo/infra/duckduckgo_search_client.py`: DuckDuckGo Instant Answer API を使う最小の外部検索アダプタを持つ
- `src/otomekairo/infra/line_notification_client.py`: `OTOMEKAIRO_LINE_CHANNEL_ACCESS_TOKEN` と `OTOMEKAIRO_LINE_TO_USER_ID` を使って LINE Messaging API の push を行う最小の通知アダプタを持つ
- `src/otomekairo/infra/sqlite_state_store.py`: `core_schema.sql` を読み込む DB 初期化と、`sqlite-vec` の `vec0` 仮想表初期化、状態参照・入力受付・設定反映、短周期確定時の `write_memory` enqueue、`revisions` 記録、`network_result` を伴う `browse` では `summary` に加えて `fact` の `memory_state` も作成し、`memory_state` と `event` を対象にした `refresh_preview` / `embedding_sync`、`searchable=0` へ落とす `quarantine_memory`、派生索引とジョブ履歴を掃除する `tidy_memory`、`memory_jobs` の再キュー / `dead_letter`、`ui_outbound_events` の保持窓削除を持つ
- `src/otomekairo/runtime/main_loop.py`: `settings_overrides`、`pending_inputs`、`task_state(waiting_external)` を消費し、待機中も応答中も lease heartbeat を維持しながら、失敗時も `claimed` を終端状態へ確定し、`token` の即時追記、進行中 `cancel` の消費、`browse` の外部検索と `network_result` の再認知、`notify` の LINE push、短周期と長周期を交互に管理しつつ `runtime.long_cycle_min_interval_ms` で間隔制御したうえで、`write_memory` / `refresh_preview` / `embedding_sync` / `quarantine_memory` / `tidy_memory` の最小長周期処理までを行う
- `src/otomekairo/schema/runtime_types.py`: ランタイムの共通データ形を `infra` から切り離して持つ
- `src/otomekairo/schema/settings.py`: 設定キーの検証と `config/default_settings.json` からの既定値読み込みを持つ
- `config/default_settings.json`: `runtime_settings` seed と Web の `effective_settings` に使う設定既定値の正本を持つ

<!-- Block: Startup Guide -->
## 起動と確認

1. リポジトリ直下で起動スクリプトを実行する  
   `./run_otomekairo.sh`
2. ブラウザで `http://127.0.0.1:8000/` を開く
3. テキスト入力、`Mic`、設定パネル、`browse` を含む応答経路を確認する

- `LINE` 通知を使うときは、起動前に `OTOMEKAIRO_LINE_CHANNEL_ACCESS_TOKEN` と `OTOMEKAIRO_LINE_TO_USER_ID` を環境変数で渡す
- `LINE` を使わないときは、設定パネルで `integrations.line.enabled=false` のまま使う
- `otomekairo` の console script を使う場合も、`./run_otomekairo.sh` と同じく Web とランタイムを同時に起動する
- 手動で分けて起動したいときは、`otomekairo-web` と `otomekairo-runtime` を別ターミナルで順に起動してよい
- 既定の bind 先は `0.0.0.0:8000` だが、ブラウザからは `http://127.0.0.1:8000/` を開いてよい
- `Mic` はブラウザ標準の `SpeechRecognition` がある環境だけで使える
- `browse` は、UI 上では `検索タスク` と `検索結果` の通知を経てから最終応答へ進む
