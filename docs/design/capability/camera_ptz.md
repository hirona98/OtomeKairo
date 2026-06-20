# camera.ptz

## 目的

`camera.ptz` は、OtomeKairo が所有する camera source の向きや画角を、外部 connector 経由で調整する action capability である。
camera source は OtomeKairo の視覚であり、ユーザー明示指示、API起床、定期起床、capability result follow-up、自律判断のいずれでも利用できる。
この文書は `camera.ptz` の意味境界、判断利用、source metadata、結果の扱いを正本として定める。

capability manifest、availability、decision view の共通規則は [capability_manifest.md](capability_manifest.md) を正とする。
API wire は [../api/実行連携.md](../api/実行連携.md)、event stream は [../api/event_stream.md](../api/event_stream.md)、connector 配置は [../integration/外部接続connector配置方針.md](../integration/外部接続connector配置方針.md) を正とする。
視覚観測そのものは [視覚機能.md](視覚機能.md) を正とする。

## 境界

`camera.ptz` に入れるものは次である。

- camera source の pan / tilt 操作
- camera source の zoom 操作
- 汎用操作から機器固有 API への connector 側変換
- 操作成否の短い結果
- 操作対象の `vision_source_id`

次は `camera.ptz` に入れない。

- 静止画取得
- 動画、音声、録画、常時監視
- privacy mode の切替
- reboot、format、録画設定、検知設定、アラーム、サイレン
- 機器固有の raw API 名、角度、内部 URL、credential
- 観測目的と結びつかない無目的な反復 pan / tilt

## 基本方針

`camera.ptz` は `VisionSource(kind=camera)` を対象にする source-targeted capability である。
server は `camera.ptz.input.vision_source_id` から `VisionSource` を引き、実行先 client を一意に決める。
source が存在しない、source が camera ではない、source が複数候補に分かれる、source が操作を advertised していない場合、server は dispatch しない。

camera source は OtomeKairo の視覚 source として採用する。
採用した camera source は `camera_source.enabled=true` のとき定期起床処理の観測対象に含める。
camera source の観測結果は `source_owner=self` として扱い、OtomeKairo の視覚根拠として判断へ渡す。
desktop や virtual source の観測とは所有境界を分ける。

OtomeKairo は現在の映像に対する相対方向と汎用操作だけを判断する。
connector は現在の映像に対する相対方向を機器固有 API、座標符号、角度、速度、ズーム方式へ変換する。
設置向きや機器差分は connector 実装の既定値で吸収する。

server は `camera.ptz` を全ての判断起点の decision view に出す。
対象 source が available で、権限、binding、`supported_controls`、`capability_state` を満たす限り、OtomeKairo はユーザー確認を挟まずに `camera.ptz` を使える。
ここでの「自由に使える」は所有境界と判断権限の話であり、dispatch 境界の schema 検証、busy、timeout、同一 cycle の反復抑制は通常の capability と同じく適用する。

## 操作

`camera.ptz` は次の operation を扱う。
方向系 operation は、camera 本体や ONVIF 座標の方向ではなく、現在の映像内で次に視覚中心を移したい方向を表す。

| operation | 意味 |
|------|------|
| `move_up` | 次の視覚中心を現在の映像の上側へ移す |
| `move_down` | 次の視覚中心を現在の映像の下側へ移す |
| `move_left` | 次の視覚中心を現在の映像の左側へ移す |
| `move_right` | 次の視覚中心を現在の映像の右側へ移す |
| `zoom_in` | camera source の画角を狭める |
| `zoom_out` | camera source の画角を広げる |

`amount` は `small / medium` のいずれかにする。
通常のカメラ移動は `medium` にする。
少し、すこし、ちょっと、微調整などの小さい移動の意図が明示されている場合だけ `small` にする。
connector は実機 API が連続値を要求する場合でも、server には連続値を出さない。
connector は `operation / amount` を実機の座標、符号、速度、継続時間へ変換する。
server、decision view、inspection は実機座標、符号、角度、速度、継続時間を扱わない。

source が対応しない operation は source metadata に出さない。
LLM decision view には source ごとの対応 operation だけを渡す。
server は `input.operation` が対象 source の対応 operation に含まれない場合、dispatch しない。

## Source Metadata

camera source は `vision_sources[]` の一部として登録する。
採用済み camera source の永続定義は `camera_source` 設定定義として OtomeKairo 本体に保存する。
connector は起動時に自分の `client_id` に紐づく runtime config を server から取得し、その内容を hello の source metadata として通知する。
`camera.ptz` を提供する source は、少なくとも次を追加で持つ。

```json
{
  "vision_source_id": "vision_source:tapo_c220_main",
  "kind": "camera",
  "label": "C220",
  "aliases": ["C220"],
  "default_for": ["camera"],
  "capability_id": "vision.capture",
  "required_permissions": ["observe_vision", "observe_camera"],
  "source_owner": "self",
  "supported_controls": {
    "camera.ptz": {
      "operations": ["move_up", "move_down", "move_left", "move_right"],
      "amounts": ["small", "medium"]
    }
  }
}
```

`supported_controls` は source metadata であり、capability manifest の正本ではない。
server は manifest、binding、接続主体の権限、source metadata を照合して availability を導出する。
`supported_controls` は credential、内部 URL、機器 API 名、角度、設置場所の秘匿情報を含めない。
`camera.ptz` が advertised された camera source は、同じ `vision_source_id` の `camera_source.enabled=true` を持つ。
`camera_source.enabled=false` の camera source は `camera.ptz` の対象にしない。

## 判断利用

`camera.ptz` を使う条件は次である。

- ユーザーが camera source の向きや画角変更を明示した
- ユーザーが「上」「左」「少し右」「ズームして」のように camera source 操作を依頼した
- API起床または定期起床で、現在の camera 視覚前景だけでは判断対象が見切れている、低信頼、または明らかに視野外である
- capability result follow-up で、同じ camera source の観測前に向きや画角を変える必要がある
- 自律判断で、進行中の目的や保留意図に対して camera source の視野調整が必要である
- 対象 camera source が一意に定まる
- 対象 source が requested operation を advertised している

`camera.ptz` を使わない条件は次である。

- 現在の判断に camera source の視野調整が不要
- ユーザーが privacy mode、録画、アラーム、再起動、検知設定を求めている
- source が camera ではない
- source が未接続、権限不足、busy、一時 unavailable である
- operation が対象 source の対応 operation に含まれない
- 同じ判断 cycle 内で、同じ `vision_source_id / operation / amount` の PTZ をすでに実行していて、追加観測なしに再実行しようとしている

操作と観測が同じ目的に属する場合、server は `camera.ptz` を先に実行し、`camera.ptz` result follow-up で同じ `vision_source_id` の `vision.capture` を発行できる。
この連鎖は `camera.ptz -> vision.capture` の同一 source に限定する。
`camera.ptz` result から別 source や別 capability family へ広げる場合は、通常の decision validation と repair 対象にする。
`vision.capture` result で新しい `world_state.visual_context` を作り、その後の判断や発話はその観測結果を根拠にする。

## Privacy Mode

privacy mode は `camera.ptz` に含めない。
`camera.privacy` capability は定義しない。
connector は privacy mode を `hello.caps`、`supported_controls`、inspection、capability result に出さない。
OtomeKairo 側の camera availability は source availability、connector の接続状態、`camera_source.enabled`、capability 権限で扱う。
機器側に privacy mode 相当の機能があっても、OtomeKairo の capability request として設計しない。

## 結果

`camera.ptz` result は操作成否の短い結果だけを返す。
raw device response、内部 URL、credential、角度、機器固有 payload は返さない。

result は少なくとも次を持つ。

```json
{
  "status": "completed",
  "operation": "move_up",
  "amount": "small",
  "client_context": {
    "vision_source_id": "vision_source:tapo_c220_main",
    "source_kind": "camera",
    "source_label": "C220"
  },
  "error": null
}
```

`status` は `completed / rejected / failed` のいずれかにする。
`operation` と `amount` は request と同じ値を返す。
`error` は失敗時だけ短い理由を入れる。
server は result を capability audit と inspection に反映する。
`camera.ptz` result だけから `world_state.visual_context` を更新しない。
新しい視覚前景は次の `vision.capture` result から作る。

## C220

C220 connector は、画像取得 backend と制御 backend を分ける。
画像取得 backend は source 設定で固定し、実行中に別 backend へ切り替えない。
C220 では画像取得を `rtsp`、制御を `onvif` とする。
ONVIF port は connector 実装の既定値 `2020` とする。
connector は `camera.ptz_request` の `operation / amount` を ONVIF `ContinuousMove` と `Stop` へ変換する。
ONVIF へ渡す pan / tilt velocity は `1.0` に固定する。

C220 の host と camera account は OtomeKairo の `camera_source` 設定定義で保持する。
OtomeKairo access token は API の `console_access_token` とする。
connector は明示設定または環境変数の token を優先し、未設定の場合は同一 PC 内の `server_state.json` から `console_access_token` を読む。
`console_access_token` が未発行の場合は bootstrap API で初回発行する。
host、camera account、OtomeKairo access token を repository、docs のサンプル、debug log、inspection、capability result に保存しない。

C220 connector は `move_up / move_down / move_left / move_right` を advertised する。
C220 は物理ズームと ONVIF Zoom capability を持たない。
C220 connector は `zoom_in / zoom_out` を advertised しない。
C220 connector は privacy mode を実装対象にしない。
