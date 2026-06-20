# 外部接続 connector 配置方針

## 目的

この文書は、Webカメラ、各種センサ、外部サービス、物理デバイスを OtomeKairo へ接続する外部接続 connector の配置方針を定める。

capability の意味境界、manifest、availability、decision view は [../capability/capability_manifest.md](../capability/capability_manifest.md) を正とする。
capability request / result の wire 契約は [../api/実行連携.md](../api/実行連携.md) を正とする。
この文書は、connector 実装をどの repository 階層に置き、OtomeKairo 本体とどこで分けるかを正本にする。

## 基本方針

外部接続 connector は、OtomeKairo 本体とは別の実行 client として扱う。
OtomeKairo 本体は capability manifest、判断、状態、記憶、inspection の正本を持つ。
connector は接続先の機器、サービス、OS API を扱い、capability request を実行し、result を返す。

最初の connector 群はこの repository 内の `connectors/` 配下に置く。
connector 実装を `src/otomekairo/` 配下へ入れない。
`src/otomekairo/` は OtomeKairo server 本体の package とする。

外部接続 connector は capability manifest を定義しない。
connector は `hello.caps` と必要な source metadata を送る。
server は既知の manifest、binding、state、権限から availability と dispatch 先を決める。
camera connector は機器接続に必要な host と camera account を OtomeKairo の runtime config API から取得する。
camera connector は host と camera account をローカル設定の正本として持たない。

## repository 配置

外部接続 connector は次の配置を基準にする。

```text
connectors/
  webcam/
    pyproject.toml
    README.md
    config.example.json
    src/
      otomekairo_webcam_connector/
        __main__.py
        config.py
        stream.py
        capture.py
```

connector ごとに独立した `pyproject.toml` を置く。
connector 固有の依存関係を repository root の `pyproject.toml` へ入れない。
OpenCV、デバイス SDK、外部サービス SDK、OS 固有ライブラリは対象 connector の package 依存に閉じる。

2 個以上の connector で同じ処理が継続して必要になった段階で、共通 package の要否を判断する。
共通 package を作る場合も、OtomeKairo server 本体 package へ connector 実装依存を入れない。

## Webカメラ connector

Webカメラは新しい capability id として定義しない。
Webカメラは `vision.capture` の `VisionSource(kind=camera)` として登録する。
camera source は OtomeKairo の視覚なので、採用した camera source は `source_owner=self` とし、`camera_source.enabled=true` のとき定期起床処理の観測対象に含める。

Webカメラ connector は、server から `vision.capture_request` を受けたときだけ still image を 1 枚取得する。
connector は常時録画、常時監視、独自周期での撮影を行わない。
定期観測は server の定期起床処理が有効な camera source を解決し、`vision.capture` として発行する。
制御可能な camera の pan / tilt / zoom は `camera.ptz` として扱う。
`camera.ptz` の意味境界は [../capability/camera_ptz.md](../capability/camera_ptz.md) を正とする。
privacy mode は connector capability として実装しない。

Webカメラ connector の hello は次の形を基準にする。

```json
{
  "type": "hello",
  "client_id": "webcam-connector-main",
  "caps": [
    { "id": "vision.capture", "version": "1" }
  ],
  "vision_sources": [
    {
      "vision_source_id": "vision_source:webcam_main",
      "kind": "camera",
      "label": "Webカメラ",
      "aliases": ["カメラ", "Webカメラ", "部屋のカメラ"],
      "default_for": ["camera"],
      "capability_id": "vision.capture",
      "required_permissions": ["observe_vision", "observe_camera"],
      "source_owner": "self"
    }
  ]
}
```

`vision_source_id` は server 内で一意にする。
複数の Webカメラを扱う場合は、`vision_source_id` と `label` を `camera_source` 設定定義で明示的に分ける。
source が一意に定まらない状態で connector は登録しない。
固定 Webカメラのように向きや画角を制御できない source は `supported_controls` を出さない。

## Tapo C220 connector

Tapo C220 connector は、制御可能な camera connector の初期対象である。
C220 は `vision.capture` の `VisionSource(kind=camera, source_owner=self)` として登録し、pan / tilt は同じ source の `camera.ptz` として登録する。
同じ物理 camera に対して、観測用 source id と制御用 source id を分けない。
この repository 内の初期実装は `connectors/tapo_c220/` に置く。

C220 connector の hello は次の形を基準にする。

```json
{
  "type": "hello",
  "client_id": "tapo-c220-connector-main",
  "caps": [
    { "id": "vision.capture", "version": "1" },
    { "id": "camera.ptz", "version": "1" }
  ],
  "vision_sources": [
    {
      "vision_source_id": "vision_source:tapo_c220_main",
      "kind": "camera",
      "label": "C220",
      "aliases": ["カメラ", "部屋のカメラ", "C220"],
      "default_for": ["visual", "camera"],
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
  ]
}
```

C220 の capture backend は `rtsp`、control backend は `onvif` を初期基準にする。
connector は `vision.capture_request` では RTSP から still image を 1 枚取得し、`camera.ptz_request` では `operation / amount` を ONVIF `ContinuousMove` と `Stop` へ変換する。
`operation` は現在の映像に対する相対方向として扱い、ONVIF の座標符号と設置向きの対応は connector 実装の既定値で吸収する。
C220 connector の初期 `operation_vectors` は実機で確認した ONVIF `ContinuousMove` の符号に合わせる。
ONVIF port、移動時間、設置向きの対応は connector 実装の既定値で扱う。
pan / tilt velocity は `1.0` に固定する。
server、decision view、inspection へ角度や生 API 名を出さない。
C220 は物理ズームと ONVIF Zoom capability を持たないため、zoom 操作を `supported_controls` に含めない。

C220 の host と camera account は OtomeKairo の `camera_source` 設定定義で保持する。
connector は起動時に `GET /api/config/connectors/{client_id}/runtime-config` を呼び、自分に割り当てられた C220 の runtime config を取得する。
OtomeKairo access token は API の `console_access_token` とする。
connector は明示設定または環境変数の token を優先し、未設定の場合は同一 PC 内の `server_state.json` から `console_access_token` を読む。
`console_access_token` が未発行の場合は bootstrap API で初回発行する。
host、camera account、OtomeKairo access token を repository、docs のサンプル、debug log、inspection、capability result に保存しない。
privacy mode、録画、検知設定、アラーム、再起動は C220 connector の OtomeKairo capability として実装しない。
失敗時は `camera.ptz` result に `status=failed` と短い `error` を返す。

## connector の責務

connector は少なくとも次を担う。

- server への認証済み接続
- `GET /api/events/stream` への接続維持
- 起動時 hello による capability binding 候補の通知
- source metadata の通知
- server から届く capability request の受信
- 対象機器または外部サービスの実行
- `POST /api/capability/result` への result 返却
- 接続、権限、デバイス取得失敗の短い error 返却

connector は次を担わない。

- capability manifest の定義
- 判断結果の生成
- `world_state`、記憶、`activity_state` の更新
- raw payload の永続保存
- OtomeKairo server の設定定義編集
- LLM role、API key、記憶集合の管理

## 設定と秘密情報

connector のローカル設定は server URL、TLS 検証、再接続間隔、`client_id`、token 明示上書きなど、OtomeKairo へ接続するための項目に限定する。
camera connector の host と camera account は OtomeKairo 本体の `camera_source` 設定定義で扱う。
`config.example.json` には秘密値を入れない。
実 token、API key、password、内部 URL の秘密部分を repository に保存しない。

connector は通常ログ、debug log、inspection 用 result summary に秘密値を出さない。
server へ返す `client_context` には、判断と inspection に必要な短い状態だけを入れる。
credential、内部 URL、token、raw device path のうち秘匿が必要な値を `client_context` に入れない。

## 別 repository への切り出し基準

connector は、この repository 内で server との契約整合を確認する段階から始める。
次の状態になった connector は別 repository への切り出し対象にする。

- 配布、更新、権限付与の単位が OtomeKairo server と分かれる
- OS 固有依存や大型 SDK により server 開発環境から分離する必要がある
- 複数の実行端末へ個別配布する
- connector の release cycle が server と分かれる
- 外部サービス資格や運用手順を connector 単位で管理する

別 repository へ切り出した後も、server 側の capability manifest と wire 契約はこの repository の docs を正本にする。
この repository には、参照実装または起動手順へのリンクだけを残す。
