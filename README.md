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
- 人格選択仕様: `docs/41_人格選択仕様.md`
- 設定UI仕様: `docs/42_設定UI仕様.md`
- 開発者設定仕様: `docs/43_開発者設定仕様.md`
- 設定既定値: `config/default_settings.json`
- 開発者用起動設定: `config/developer.toml`
- 初期 SQL 実装: `sql/core_schema.sql`
- 参考メモ: `docs/note/記憶設計に関する先行研究のメモ.md`

<!-- Block: Current Implementation -->
## 現在の実装状況

- `pyproject.toml`: Python パッケージ定義、`LiteLLM` と `sqlite-vec` と `onvif-zeep` 依存、`otomekairo` / `otomekairo-web` / `otomekairo-runtime` の起動入口を持つ
- `run_otomekairo.sh`: Web サーバと人格ランタイムを引数なしで同時起動する最短の実行スクリプト
- `src/otomekairo/boot/run_web.py`: `uvicorn` で Web サーバを起動し、既定では `0.0.0.0:8000` に bind する
- `src/otomekairo/boot/run_runtime.py`: 人格ランタイムの常時ループを起動する
- `src/otomekairo/boot/run_all.py`: Web サーバと人格ランタイムを同じ親プロセスで起動し、既存のランタイム lease が生きていればそれを再利用し、終了シグナル時はまず `SIGINT` で子プロセスを graceful shutdown させる
- `src/otomekairo/boot/compose_sqlite.py`: 共通の SQLite backend 初期化と adapter bundle の組み立てを担当し、`run_all.py`、`compose_runtime.py`、`compose_web.py`、deterministic eval runner から再利用する
- `src/otomekairo/boot/compose_web.py`: Web サーバ用の composition root として `compose_sqlite.py` から受けた SQLite adapter 群、カメラ入力、STT を組み立て、`build_app(...)` へ渡す
- `src/otomekairo/boot/compose_runtime.py`: ランタイム用の composition root として `compose_sqlite.py` から受けた port bundle、TTS、検索、LiteLLM、カメラ制御を組み立て、`RuntimeLoop` へ渡す
- `src/otomekairo/infra/logging_setup.py`: `config/developer.toml` を前提に、`launcher / web / runtime` の root / handler / library logger level を起動時に決め、`log/otomekairo.log` への通常テキストログ整形と秘密情報マスクと BASE64 本文の省略を行い、`LiteLLM` の自前コンソール handler も共通ログ経路へ統一し、共有ロックつきで約 1MiB ごとにローテーションする共通ロギング設定を持つ
- `src/otomekairo/web/app.py`: `AppServices` bundle を受け取り、API ルータ、静止画配信用の `/captures`、最小ブラウザ UI (`GET /`)、例外処理だけを束ねる
- `src/otomekairo/web/camera_api.py`: `POST /api/camera/capture` でカメラ静止画を取得し、`POST /api/camera/observe` でその画像を自発観測入力として認知キューへ積む
- `src/otomekairo/web/static/`: `tmp/CocoroConsole` ベースの設定ウインドウを持つ最小チャット UI を持ち、同一オリジンで `POST /api/chat/input`、`POST /api/camera/capture`、`GET /api/chat/stream` を使い、`message` 到着時はチャット表示へ反映し、`audio_url` があればサーバ生成の TTS 音声を再生し、`Cam` は静止画をサムネイル表示して次のチャット入力へ添付し、設定パネルでは `キャラクター / 振る舞い / 会話 / 記憶 / モーション / システム` の 6 タブで 5 種のプリセットとシステム設定・カメラ接続をまとめて編集し、カメラ接続は表内で `使用` の有効化、行追加、行削除、接続情報編集を行う
- `src/otomekairo/gateway/cognition_client.py`: 認知処理の外部境界を表す抽象を定義する
- `src/otomekairo/usecase/build_cognition_input.py`: `self_state` などの現在状態から最小の `cognition_input` を組み立て、`task_state` の進行中 / 外部待ちタスク、`sqlite-vec` で補強した直近の `summary` / `fact` 記憶、直近イベント列を `current_observation` と照合して絞り込み、`memory_bundle` として渡す
- `src/otomekairo/usecase/cognition_prompt_messages.py`: planner / retrieval selector / reply renderer で共有する prompt message 構築を持ち、deterministic smoke と LiteLLM adapter の両方から再利用する
- `src/otomekairo/usecase/run_cognition.py`: 認知クライアントが返す `cognition_result` を受け取り、`action_command` を使って `speak` は `token` / `message`、`notify` はユーザー通知イベント (`notice`)、`look` は ONVIF 経由のカメラ視点操作、`browse` は `waiting_external` の検索タスクとして実行し、`action_history` へ変換する
- `src/otomekairo/usecase/run_browse_task.py`: `task_state(waiting_external)` の `browse` タスクを外部検索へ通し、検索結果を内部入力 `network_result` として次周期へ戻し、`action_history` へ変換する
- `src/otomekairo/usecase/validate_action.py`: `cognition_result.action_proposals` から `speak` / `browse` / `notify` / `look` / `wait` を比較し、`selection_profile` の trait / style / relationship / emotion / drive、`memory_bundle`、`task_snapshot`、カメラ可用性を使って `execute / hold / reject` と構造化した `action_command` を確定する
- `src/otomekairo/infra/litellm_cognition_client.py`: `LiteLLM` を使って認知呼び出しを行い、`usecase/cognition_prompt_messages.py` が作った message を transport に流し、`response_format={"type":"json_schema"}` と厳密な validation で `cognition_result` を構造化させる
- `src/otomekairo/gateway/search_client.py`: 外部検索の境界を表す抽象を定義する
- `src/otomekairo/gateway/camera_controller.py`: カメラ視点操作の外部境界を定義する
- `src/otomekairo/gateway/camera_sensor.py`: カメラ静止画取得の外部境界を定義する
- `src/otomekairo/infra/duckduckgo_search_client.py`: DuckDuckGo Instant Answer API を使う最小の外部検索アダプタを持つ
- `src/otomekairo/infra/wifi_camera_common.py`: ONVIF 接続に使う Wi-Fi カメラ設定の正規化と共通クライアント生成をまとめる
- `src/otomekairo/infra/wifi_camera_controller.py`: 設定UIで `使用` が有効なカメラ接続を読み、先頭の有効接続に対して ONVIF 経由で `Tapo C220` などの視点操作を行う
- `src/otomekairo/infra/wifi_camera_sensor.py`: 設定UIで `使用` が有効なカメラ接続を読み、先頭の有効接続から ONVIF で RTSP stream URI を取得し、`ffmpeg` で 1 フレームを `data/camera/` の JPEG として保存する
- `src/otomekairo/infra/sqlite/backend.py`: `core_schema.sql` 適用判定、schema/meta 検証、singleton seed の入口、共通 connection を束ねる bootstrap facade であり、実処理は `bootstrap_connection_impl.py`、`bootstrap_meta_impl.py`、`bootstrap_settings_editor_impl.py`、`bootstrap_singleton_seed_impl.py` に分離している。`bootstrap_singleton_seed_impl.py` 自体も `bootstrap_core_singleton_seed_impl.py` と `bootstrap_live_state_seed_impl.py` の集約にしている
- `src/otomekairo/infra/sqlite/`: `runtime_query_impl.py`、`cycle_commit_impl.py`、`settings_impl.py`、`runtime_live_state_impl.py`、`event_writer_impl.py`、`ui_event_impl.py`、`runtime_lease_impl.py`、`memory_job_impl.py` が責務別の SQLite 実装を持ち、`*_store.py` は gateway port を包む薄い adapter である。`runtime_query_impl.py` は `runtime_status_query_impl.py` / `runtime_cognition_query_impl.py` / `runtime_settings_editor_query_impl.py` の集約であり、`runtime_cognition_query_impl.py` も `runtime_cognition_base_query_impl.py` / `runtime_memory_snapshot_query_impl.py` を組み合わせる coordinator にしている。`cycle_commit_impl.py` は `cycle_enqueue_impl.py` / `cycle_pending_input_impl.py` / `cycle_task_commit_impl.py` の集約、`settings_impl.py` は `settings_override_impl.py` / `settings_editor_persistence_impl.py` / `settings_change_set_impl.py` の集約、`runtime_live_state_impl.py` は `runtime_state_replace_impl.py` と `runtime_mutation_apply_impl.py` の集約、`event_writer_impl.py` は `input_journal_impl.py` / `event_record_insert_impl.py` / `cycle_event_insert_impl.py` の集約、`memory_job_impl.py` は `memory_job_claim_impl.py` / `memory_job_enqueue_impl.py` の集約である。`write_memory_execution_store.py` は `write_memory_load_impl.py`、`write_memory_state_impl.py`、`write_memory_preference_impl.py`、`write_memory_context_impl.py` を束ねる `write_memory` 専用 execution adapter である。`write_memory_state_impl.py` は `write_memory_state_update_impl.py` / `write_memory_self_state_sync_impl.py`、`write_memory_state_update_impl.py` は `write_memory_state_insert_impl.py` / `write_memory_state_existing_update_impl.py`、`write_memory_context_impl.py` は `write_memory_event_affect_impl.py` / `write_memory_context_relation_impl.py` へさらに分けている
- `src/otomekairo/runtime/main_loop.py`: `RuntimeStores` bundle 越しに `settings_overrides`、`settings_change_sets`、`pending_inputs`、`task_state(waiting_external)` を消費し、待機中も応答中も lease heartbeat を維持しながら、失敗時も `claimed` を終端状態へ確定しつつ `logger.exception(...)` で stderr にスタックトレースも出し、`token` の即時追記、進行中 `cancel` の消費、`browse` の外部検索と `network_result` の再認知、`notify` のユーザー通知発行、設定UI保存結果からの `runtime_settings` materialize、短周期と長周期を交互に管理しつつ `runtime.long_cycle_min_interval_ms` で間隔制御したうえで、`write_memory` / `embedding_sync` の最小長周期処理までを行う
- `src/otomekairo/schema/runtime_types.py`: ランタイムの共通データ形を `infra` から切り離して持つ
- `src/otomekairo/schema/settings.py`: 設定キーの検証と `config/default_settings.json` からの既定値読み込みを持つ
- `config/default_settings.json`: `runtime_settings` seed と Web の `effective_settings` に使う設定既定値の正本を持つ
- `config/developer.toml`: `launcher / web / runtime` のログ level と `LiteLLM` の開発者向けログ出力を起動時に固定する正本を持つ

<!-- Block: Startup Guide -->
## 起動と確認

1. リポジトリ直下で起動スクリプトを実行する  
   `./run_otomekairo.sh`
2. ブラウザで `http://127.0.0.1:8000/` を開く
3. テキスト入力、`Cam`、設定パネル、`browse` を含む応答経路を確認する

- VSCode では、ワークスペース直下の `.vscode/launch.json` に `OtomeKairo` 起動構成を用意しているので、`F5` で `otomekairo.boot.run_all` をそのまま起動できる

- `look` と `Cam` を使うときは、設定画面の `システム` タブで `追加` から行を増やし、表形式の接続一覧で IP アドレス・アカウント・パスワードを編集したうえで、AI に使わせる接続だけ `使用` をオンにして保存する
- `Cam`、`POST /api/camera/observe`、`look` 後の追跡観測を使うときは、実行環境に `ffmpeg` が入っている必要がある
- 設定画面の API キー、トークン、パスワード欄は、確認しやすさを優先して通常の文字列入力欄で表示する
- `Cam` で撮った画像は、次の `POST /api/chat/input` に添付され、テキストなしでも送信できる
- 自発観測を起こしたいときは、`POST /api/camera/observe` で撮影と認知キュー投入を一度に行える
- `otomekairo` の console script を使う場合も、`./run_otomekairo.sh` と同じく Web とランタイムを同時に起動する
- 既に別プロセスで人格ランタイムが稼働中なら、`./run_otomekairo.sh` はそのランタイムを再利用し、Web だけを追加起動する
- 手動で分けて起動したいときは、`otomekairo-web` と `otomekairo-runtime` を別ターミナルで順に起動してよい
- 既定の bind 先は `0.0.0.0:8000` だが、ブラウザからは `http://127.0.0.1:8000/` を開いてよい
- `./run_otomekairo.sh` は、通常は `Ctrl+C` 1 回で Web とランタイムを順に停止し、ランタイム lease も解放する
- 通常終了では、signal handler が例外を投げず、Web も `SSE` 切断を専用レスポンスで閉じるため、想定内の `KeyboardInterrupt` や `CancelledError` がエラートレースとして出ない構成にしている
- 初期実装では、端末には `INFO` 以上だけを表示しつつ、単体の JSON だけでなく複数行メッセージ内や行末に埋め込まれた JSON / Python 辞書形式の構造化データや `context` の辞書も見やすく整形して出し、`log/otomekairo.log` に `DEBUG` の通常テキストログをまとめて残し、約 1MiB ごとに 5 世代ローテーションする
- `config/developer.toml` を編集すると、`launcher / web / runtime` ごとの root / handler / library logger level をコード変更なしで切り替えられる
- 初期実装では、Uvicorn のアクセスログは基本的に表示しつつ、`/api/status` と `/api/chat/stream` の定期アクセスだけ抑止する
- `LiteLLM` の log level は `config/developer.toml` の `integrations.litellm.log_level` で切り替える
- ログ内の `api_key`、`Authorization: Bearer ...`、`token`、`password`、`bot_token` は常に mask し、BASE64 本文は出力しない
- `browse` は、UI 上では `検索タスク` と `検索結果` の通知を経てから最終応答へ進む
