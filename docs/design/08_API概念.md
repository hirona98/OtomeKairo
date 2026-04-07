# API概念

<!-- Block: Role -->
## この文書の役割

この文書は、OtomeKairo 専用 API がどの概念を外へ露出するかを定める正本である。

ここで固定したいのは次の内容である。

- クライアントが何を送るか
- サーバが何を返すか
- 設定操作をどの操作面に載せるか
- `CocoroConsole` と OtomeKairo の責務境界を API 上でどう表すか

ここでは API パスや JSON の詳細形式そのものは扱わない。
厳密な API 仕様は `14_API仕様.md` と `design/api/` 配下を正とする。
OtomeKairo に自然な操作面だけを固定する。

接続 bootstrap と認証の前段は、この文書ではなく `11_接続と認証.md` を正とする。
また、デバッグ記録の inspection 面は、この文書ではなく `12_デバッグ可能性.md` を正とする。

<!-- Block: Premise -->
## 基本前提

OtomeKairo の API は、クライアント都合ではなくサーバ側の概念に沿って切る。
そのため、次の 4 面をそのまま外へ出す。

- 観測を送る面
- 状態を読む面
- 設定を読む / 変える面
- 選択可能な対象を列挙する面

これに加えて、MVP の `desktop_watch` では server-driven の event/control stream を持ってよい。

ここで重要なのは、`CocoroConsole` が OtomeKairo の判断や設定の正本を持たないことである。
また、ここでいう API 面は、接続確立後の通常 API 面を指す。

<!-- Block: ObservationSurface -->
## 観測を送る面

MVP でクライアントが送る観測は、次の 2 種類で十分である。

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

<!-- Block: ObservationResult -->
## 観測に対して返すもの

観測に対してサーバが返す結果は、MVP では次でよい。

- `reply`
  - 会話文を返す
- `noop`
  - 今回は外向きに何もしない
- `internal_failure`
  - 内部失敗により判断サイクルを畳んだ

将来は `act` を外へ露出しうるが、MVP では主対象を会話に置くため、外向きの主結果は `reply / noop / internal_failure` で足りる。
自発判断の内部では `future_act` を取りうるが、これは通常 API の外向き結果ではなく内部保留候補として扱う。

<!-- Block: StatusSurface -->
## 状態を読む面

クライアントが読むべき状態は、MVP では次の 2 系統に分ける。

- 設定スナップショット
  - `selected_persona_id`
  - `selected_memory_set_id`
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
ここでいう参照は、ID、内部参照の要約、または軽量な反映状態のいずれかで返してよい。
具体形式は `14_API仕様.md` と `design/api/` 配下で定める。

<!-- Block: EventControlSurface -->
## event/control stream 面

MVP の `desktop_watch` では、通常の request/response API とは別に、server-driven の event/control stream を持ってよい。

ここで扱うのは次である。

- `events/stream`
  - OtomeKairo からクライアントへ event や command を送る
- `vision/capture-response`
  - クライアントが capture request の結果を返す

この面は、詳細ログの常時ストリーミングではない。
OtomeKairo が必要なときだけ command を出し、クライアントが必要最小の応答を返すための運用面である。

<!-- Block: DebugStreamSurface -->
## debug stream 面

MVP では、`CocoroConsole` のログビューアー向けに、inspection 面とは別の debug stream を持ってよい。

ここで扱うのは次である。

- `logs/stream`
  - OtomeKairo が判断サイクルの短い段階要約ログを流す

この面は、通常 API の代替ではない。
また、完全な生ログや LLM の長い思考過程を流す面でもない。
`recall`、`decision`、`memory` の採用結果を短い構造化ログとして live に観測するための補助面である。

<!-- Block: ConfigSurface -->
## 設定を読む / 変える面

設定面では、少なくとも次を扱える必要がある。

- 現在設定の取得
- 現在設定の部分更新
- `editor-state` の一括取得
- `editor-state` の一括置換
- `persona` の詳細取得
- `memory_set` の詳細取得
- 編集対象となる `model_preset` と `model_profile` の詳細取得
- `select_persona(persona_id)`
- `select_memory_set(memory_set_id)`
- `update_wake_policy(wake_policy)`
- `replace_persona(persona_id, persona_definition)`
- `replace_memory_set(memory_set_id, memory_set_definition)`
- `delete_persona(persona_id)`
- `delete_memory_set(memory_set_id)`
- `select_model_preset(model_preset_id)`
- `replace_model_preset(model_preset_id, model_preset_definition)`
- `replace_model_profile(model_profile_id, model_profile_definition)`
- `delete_model_preset(model_preset_id)`
- `delete_model_profile(model_profile_id)`

ここでの正本は常に OtomeKairo 側にある。
そのため、クライアントは「現在設定を読み、操作要求を送る」だけに留まる。

`model_profile` の各項目は、他の設定値と同じく設定面で送受信してよい。
経路は HTTPS を前提にし、利用範囲はローカルネットワーク内に限定する。
通常 API の呼び出しは、接続 bootstrap 完了後の認証済み状態を前提にする。

<!-- Block: CatalogSurface -->
## 選択可能な対象を列挙する面

人格や記憶を切り替えるには、選択肢を列挙する面が必要である。

MVP では、少なくとも次を列挙できればよい。

- 利用可能な人格一覧
- 利用可能な記憶一覧
- 利用可能なモデルプリセット一覧
- 利用可能なモデルプロファイル一覧

これにより、`CocoroConsole` は設定の正本を持たずに、OtomeKairo が管理する選択対象を UI として提示できる。

<!-- Block: Separation -->
## 操作面を分ける理由

観測面、状態面、設定面、列挙面を分ける理由は次のとおりである。

- 観測は、その場の判断材料を送る責務である
- 状態取得は、現在の正本や稼働状況を確認する責務である
- 設定変更は、明示的な意味操作である
- 列挙は、選択可能対象を知るための補助面である

これらを 1 つに混ぜると、会話入力と設定変更の責務が曖昧になる。

<!-- Block: NonGoals -->
## MVP で API に載せないもの

MVP では、次は API の主対象にしない。

- 記憶内部テーブルの直接操作
- 外界行動の複雑な実行要求
- クライアント側が正本を持つ同期方式
- リクエストごとの `model` 文字列上書き
- HTTPS 以外での設定 API 通信

ただし、`logs/stream` のような debug 専用の短い段階要約 stream は、この非目標に含めない。

これらは最初の API 概念には含めない。

<!-- Block: DesignDecision -->
## ここで固定したい設計判断

この文書で固定したいのは次である。

- API は観測面、状態面、設定面、列挙面に分ける
- 観測面では会話観測と起床観測を主に扱う
- 外向きの主結果は MVP では `reply / noop / internal_failure` に置く
- 設定変更は `07_設定操作契約.md` の意味単位で露出する
- `CocoroConsole` の一括編集用に `editor-state` の補助面を持ってよい
- モデルプリセットとモデルプロファイルは設定面と列挙面から扱えるようにする
- `model_profile` の各項目は他の設定値と同じく設定面で送受信する
- 設定 API の経路は HTTPS に固定し、利用範囲はローカルネットワーク内を前提にする
- 厳密な API 仕様は `14_API仕様.md` と `design/api/` 配下で定める
- 接続 bootstrap と認証の前段は `11_接続と認証.md` で別に扱う
- デバッグ記録の inspection 面は `12_デバッグ可能性.md` で別に扱う
- 自発判断の `future_act` は `13_自発判断と自発行動.md` で別に扱い、通常 API の外向き結果にはしない
- `CocoroConsole` は API を通じて入出力と編集を行うが、正本や判断主体にはならない
