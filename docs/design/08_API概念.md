# API概念

## 基本前提

OtomeKairo の API は、クライアント都合ではなくサーバ側の概念に沿って切る。
そのため、次の 4 面をそのまま外へ出す。

- 観測を送る面
- 状態を読む面
- 設定を読む / 変える面
- 選択対象を列挙する面

これに加えて、MVP の `desktop_watch` では server-driven の event/control stream を持つ。

ここで重要なのは、`CocoroConsole` が OtomeKairo の判断や設定の正本を持たないことである。
また、ここでいう API 面は、接続確立後の通常 API 面を指す。

## 観測を送る面

MVP でクライアントが送る観測は、次の 2 種類とする。

- 会話観測
  - ユーザー文字入力
  - 軽量クライアント文脈
- 起床観測
  - 定期起床が発生したという事実
  - 軽量クライアント文脈

この段階では、観測はあくまで「その回の判断材料」である。
人格や記憶の切り替えを観測面に混ぜない。

`desktop_watch` は、この通常の観測面とは別に扱う。
OtomeKairo が event/control stream で capture request を出し、クライアントが capture-response を返すことで、server 側が desktop 観測を得る。

## 観測に対して返すもの

観測に対してサーバが返す結果は、MVP では次とする。

- `reply`
  - 会話文を返す
- `noop`
  - 今回は外向きに何もしない
- `internal_failure`
  - 内部失敗により判断サイクルを畳んだ

将来は `act` を外へ露出しうるが、MVP では主対象を会話に置くため、外向きの主結果は `reply / noop / internal_failure` で足りる。
自発判断の内部では `future_act` を取りうるが、これは通常 API の外向き結果ではなく内部保留候補として扱う。

## 状態を読む面

クライアントが読むべき状態は、MVP では次の 2 系統に分ける。

- 設定スナップショット
  - `selected_persona_id`
  - `selected_memory_set_id`
  - `memory_enabled`
  - `desktop_watch`
  - `wake_policy`
  - `selected_model_preset_id`
- ランタイム要約
  - 読み込み済みの人格設定参照と記憶セット参照
  - 読み込み済みのモデルプリセット参照
  - 接続状態
  - 起床スケジューラの稼働状況
  - 進行中の行動有無

ここでは、記憶内部テーブルや想起候補の生データをそのまま返す面は作らない。
MVP の API は、OtomeKairo の運用に必要な要約状態だけを返す。
ここでいう参照は、ID、内部参照の要約、または軽量な反映状態のいずれかで返す。
具体形式は `14_API仕様.md` と `design/api/` 配下で定める。

## event/control stream 面

MVP の `desktop_watch` では、通常の request/response API とは別に、server-driven の event/control stream を持つ。
`events/stream` で event や command を送り、`vision/capture-response` で結果を返す。
これは詳細ログの常時ストリーミングではなく、必要なときだけ往復する運用面である。

## debug stream 面

MVP では、`logs/stream` で判断サイクルの短い段階要約ログを流す。
これは通常 API の代替でも完全な生ログ面でもなく、live 観測のための補助面である。

## 設定を読む / 変える面

設定面では、少なくとも次を扱える必要がある。

- 現在設定の取得と部分更新
- `editor-state` の取得と置換
- `persona` / `memory_set` / `model_preset` / `model_profile` の取得、置換、削除
- 選択中 `persona` / `memory_set` / `model_preset` の切り替え
- `wake_policy` の更新

ここでの通常操作は、できるだけ 1 つの意味単位を 1 操作に対応させる。
ただし `patch_current(current_patch)` と `replace_editor_state(editor_state)` は、`CocoroConsole` の編集 UI のために current / editor bundle をまとめて扱う明示的な補助操作として持つ。

ここでの正本は常に OtomeKairo 側にある。
そのため、クライアントは「現在設定を読み、操作要求を送る」だけに留まる。

`model_profile` の各項目は、他の設定値と同じく設定面で送受信する。
経路は HTTPS を前提にし、利用範囲はローカルネットワーク内に限定する。
通常 API の呼び出しは、接続 bootstrap 完了後の認証済み状態を前提にする。

## 選択対象を列挙する面

人格や記憶を切り替えるには、選択肢を列挙する面が必要である。

MVP では、人格、記憶、モデルプリセット、モデルプロファイルを列挙できるようにする。

これにより、`CocoroConsole` は設定の正本を持たずに、OtomeKairo が管理する選択対象を UI として提示できる。

## MVP で API に載せないもの

MVP では、次は API の主対象にしない。

- 記憶内部テーブルの直接操作
- 外界行動の複雑な実行要求
- クライアント側が正本を持つ同期方式
- リクエストごとの `model` 文字列上書き
- HTTPS 以外での設定 API 通信

ただし、`logs/stream` のような debug 専用の短い段階要約 stream は、この非目標に含めない。
