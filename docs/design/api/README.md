# API 仕様

## API仕様ファミリーの構成

このフォルダは HTTP / WebSocket API の wire 契約を正本にする。
path、method、認証、request / response、error code はこのフォルダで定める。
意味境界、状態遷移、capability、記憶、LLM role の規則は対応する design 文書を正とする。

API 仕様は次のように分ける。

- [共通ルール.md](共通ルール.md)
  - 共通ルール
  - 認証の基本
  - 共通エラー
- [bootstrapと入力.md](bootstrapと入力.md)
  - bootstrap
  - 会話入力
  - `wake` API起床要求
- [event_stream.md](event_stream.md)
  - `events/stream`
  - 接続 client の capability binding 提示
- [audio_stream.md](audio_stream.md)
  - microphone connector、CocoroConsole、Web microphone の PCM stream
  - Web 入力 session と実効入力状態
  - input device catalog
  - 話者登録と話者管理
- [状態と設定.md](状態と設定.md)
  - `status`
  - `config`
  - 設定定義の read / replace / delete
- [列挙とinspection.md](列挙とinspection.md)
  - `catalog`
  - `docs`
  - `inspection`
  - capability availability の確認
  - `logs/stream`
- [実行連携.md](実行連携.md)
  - capability 実行要求
  - capability 実行結果
  - capability binding と HTTP / WebSocket 通信仕様
  - capability state 操作

## ブラウザUI配信面

`GET /ui/` とその静的 asset は、同一 HTTPS server から配信するブラウザ UI である。
`/ui/` は API wire 契約の正本ではなく、既存 `/api/...` endpoint を呼び出す client 実装として扱う。
設定パネルの見た目とフォームマークアップ規約は [../integration/WebUI設定フォーム規約.md](../integration/WebUI設定フォーム規約.md) を正とする。
通常画面の見た目規約は [../integration/WebUI通常画面規約.md](../integration/WebUI通常画面規約.md) を正とする。
`GET /` は `/ui/` へリダイレクトする。
`/ui/api/...` はブラウザ UI 専用の同一 server 内部呼び出し面であり、外部接点向け API として扱わない。
ブラウザ UI は `/ui/api/conversation` を通じて既存の会話入力処理を呼び出す。
ブラウザ UI はブラウザstorageへ永続化した `person_ref / interaction_ref` と、
OtomeKairo設定の `selected_conversation_display_name_id` から解決した表示名を `interaction_context` として送る。
ブラウザ UI は `/ui/api/config/...` を通じて既存設定操作を呼び出す。
`GET /ui/logs` は会話 UI とは別のログ専用画面であり、`debug_log` を `GET /ui/api/logs/stream` 経由でリアルタイム購読する。
`GET /ui/api/logs/stream` は server が保持する `console_access_token` で認可し、token をブラウザへ返さない。
`GET /ui/api/logs/stream` は `Origin` と `Host` が一致する同一 origin の接続だけを受理する。
ログ画面の wire は `GET /api/logs/stream` と同じであり、正本は [列挙とinspection.md](列挙とinspection.md) とする。
メイン画面 topbar からログ画面を新しいタブで開ける。
ナビは次の分類とする。

- 表現: `表示`、`アバター`、`モーション`
- 入力: `会話入力`
- 人格と記憶: `人格設定`、`モデル`、`記憶`
- 自律動作: `定期思考`（本体とデスクトップ観測・カメラ観測）、`Watcher`
- 接続: `デスクトップ`、`カメラ`、`MCP`
- 情報: `API説明`

設定編集の役割は次のように分ける。

- Web UI: 人格（本文・表現補助・音声起動ワード）、モデル、記憶、会話入力（呼ばれ方・STT 詳細）、定期思考（思考前観測を含む）、Watcher、デスクトップ取得方針、カメラ接続、MCP、API説明、アバター音声を含む本体設定。表示・VRM・モーションは最終接続端末の現在値を読み取り専用で表示する
- CocoroConsole: 表示、アバター（プリセットと VRM）、モーション、マイク（入力元と Console デバイス）、ライセンス。メイン画面に STT / TTS / デスクトップウォッチの運用トグルを持つ。音声合成と音声起動ワードは Web UI で編集する

`表示` と `モーション` とアバターの VRM は、OtomeKairo が保持する `console_client_settings` の現在値を Web UI に disabled で表示する。編集は CocoroConsole から端末設定 API へ保存する。
`定期思考` は判断機会の有効化・間隔・発話頻度に加え、思考前のデスクトップ観測とカメラ観測の on/off を持つ。カメラの host や account など接続定義は `接続 → カメラ` に置く。
ブラウザ UI は `/ui/api/docs` を通じて `GET /api/docs` と同じAPI説明を表示し、`console_access_token` をブラウザへ返さない。
デスクトップ取得は最後に接続したCocoroConsole端末設定を編集する。
モデル指定値、VRM、表示、モーションは Web UI へ現在値を無効表示する。
VRM、表示、モーションの通常編集はCocoroConsoleから端末設定APIへ保存する。
Web UI のアバター複製では、最終接続端末の `avatar_presentations` も同じ内容で複製し、設定保存時に `PATCH /ui/api/config/console-clients/{client_id}` へ含めて永続化する。
最後に接続した端末が存在しない場合、端末依存の設定欄を無効にし、VRM 表示設定の複製対象も持たない。
記憶の `記憶複製` は client 下書きとして保持し、適用時に `POST /ui/api/config/memory-sets/clone` を呼んでから `editor-state` を保存する。
ブラウザ UI は `/ui/api/config/avatar-speech/editor-state` を通じて、アバターごとの STT / TTS と通常のマイク入力元を編集する。
ブラウザ UI は入力欄でローカルマイクまたはWebマイクを選択し、マイクアイコンのトグルで音声入力を開始/停止する。Webマイク選択時だけ `/ui/api/audio/stream` へ取得音声を送る。音声状態は statusbar に表示する。
ブラウザ UI はVAD、STT、音声起動ワード判定、話者識別を実行しない。
ブラウザ UI は `/ui/api/audio/...` を通じて input device 確認と話者管理を行う。
ブラウザ UI は TTS providerへ接続せず、OtomeKairoから受信した`assistant_audio`のWAVをブラウザ音声出力で直接再生する。
ブラウザの直接会話入力はOtomeKairoの選択中 `conversation_display_name` 定義の表示名を
`participants[].display_name` に使用し、呼び名をブラウザstorageへ保存しない。
ブラウザ UI は「内部状態」パネルを常設し、`/ui/api/inspection/current-state`、`/ui/api/inspection/cycle-summaries`、`/ui/api/inspection/memory-snapshot` を 5 秒周期で読み取る。
「内部状態」パネルは設計語の現在の個に対応する UI 表示であり、パネル見出しは置かず、いま動いていること、内面（動機・気分・感情）、外界前景、記憶要約、直近の判断、接続と能力・健全性を表示する。
気分は意味表示と VAD 生値を併記する。記憶は読み取り専用の要約表示であり、行単位の編集面ではない。
自律実行の pause / resume / cancel は `/ui/api/autonomous-runs/{run_id}/{operation}` を通じて既存の autonomous run 操作を呼び出す。
ブラウザ UI は対話入力と同じ session-scoped `client_id` で `/ui/api/events/stream` へ接続し、`conversation_input`、`assistant_message`、`assistant_audio`、`audio_runtime_state` を受信する。
`/ui/api/events/stream` は server が保持する `console_access_token` で認可し、token をブラウザへ返さない。
`/ui/api/events/stream` は `Origin` と `Host` が一致する同一 origin の接続だけを受理する。
ブラウザ UI は画面上で `console_access_token` の入力を要求しない。
`/ui/` と `/ui/api/...` の追加は `/api/...` の path、method、認証、request / response 形式を変更しない。

## 更新ルール

API を実装または変更する場合は、少なくとも次を同じ変更内で更新する。

- 影響を受けるこのフォルダの詳細文書
- API 面の責務境界が変わるなら [../integration/外部接点とAPI概念.md](../integration/外部接点とAPI概念.md)
- 接続や権限の意味が変わるなら [../integration/接続と権限境界.md](../integration/接続と権限境界.md)
- inspection 面の保証が変わるなら [../runtime/デバッグ可能性.md](../runtime/デバッグ可能性.md)
- 判断結果の外向き露出や内部保留の扱いが変わるなら [../runtime/判断と行動.md](../runtime/判断と行動.md)
- timestamp 表現が変わるなら [../runtime/時刻モデル.md](../runtime/時刻モデル.md)
- 実装確認手順が変わるなら [../../../README.md](../../../README.md) または `scripts/run_long_smoke.py`

## 境界

この API 仕様ファミリーで正本として定めるのは、`docs/design/api/` 配下の path、method、認証、request / response 形式である。

一方で、上位の責務境界は次の文書を正とする。

- [../integration/外部接点とAPI概念.md](../integration/外部接点とAPI概念.md)
- [../integration/接続と権限境界.md](../integration/接続と権限境界.md)
- [../runtime/デバッグ可能性.md](../runtime/デバッグ可能性.md)
- [../runtime/判断と行動.md](../runtime/判断と行動.md)
- [../capability/capability_manifest.md](../capability/capability_manifest.md)
- [../runtime/時刻モデル.md](../runtime/時刻モデル.md)

内部フローや保存先の exact な shape は、この API 仕様ファミリーの対象に含めない。
