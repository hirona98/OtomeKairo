# Tapo C220 connector

この connector は Tapo C220 を OtomeKairo の `VisionSource(kind=camera, source_owner=self)` として登録し、`vision.capture` と `camera.ptz` を実行する。
OtomeKairo server 本体へ C220 固有依存を入れない。

## 責務

- 起動時に `/api/events/stream` へ接続し、`hello.caps` と `vision_sources` を送る
- `vision.capture_request` を受けたときだけ RTSP から still image を 1 枚取得する
- `camera.ptz_request` を受けたときだけ ONVIF `ContinuousMove` と `Stop` を呼ぶ
- `POST /api/capability/result` へ result を返す
- `camera.ptz` result には `completed / failed`、`operation`、`amount`、source context だけを返す
- privacy mode、録画、検知設定、アラーム、再起動を capability として扱わない

## 設定

OtomeKairo 側に `camera_source` を登録する。
通常は CocoroConsole の camera source 設定画面から登録する。
API で登録する場合は `PUT /api/config/camera-sources/{vision_source_id}` を使う。
CocoroConsole では有効/無効、識別名、IP address または hostname、camera account だけを設定する。
RTSP / ONVIF / PTZ の詳細値は connector 実装の既定値として扱う。

connector のローカル設定には OtomeKairo への接続情報と `client_id` だけを置く。
OtomeKairo access token は、明示設定、ローカル server state、bootstrap の順に connector が解決する。

```bash
cd connectors/tapo_c220
python3 -m venv .venv
.venv/bin/pip install -e .
cp config.example.json config.local.json
```

明示 token が必要な場合は、`OTOMEKAIRO_ACCESS_TOKEN` に現行 API の `console_access_token` を設定する。
通常は同一 PC 内の `server_state.json` から `console_access_token` を読み取る。
`console_access_token` が未発行の場合は bootstrap API で初回発行する。
connector を repository 外から起動する場合は、`server.data_dir` または `server.state_path` で OtomeKairo の local state を指定する。
C220 の camera account は OtomeKairo の `camera_source.connection` に保存する。
connector は起動時に `GET /api/config/connectors/{client_id}/runtime-config` から runtime config を取得し、同じ camera account を ONVIF control と RTSP capture に使う。
C220 の ONVIF port は connector 実装の既定値 `2020` とする。
`host`、camera account、OtomeKairo access token は repository、sample、通常ログ、result に保存しない。

`operation_vectors` は ONVIF `PanTilt` velocity へ掛ける向きベクトルである。
`move_up / move_down / move_left / move_right` は現在の映像に対する相対方向である。
初期値の `operation_vectors` は C220 実機で確認した ONVIF `ContinuousMove` の符号に合わせる。
設置向きや ONVIF 座標符号が映像上の向きと合わない場合は、connector 実装の既定値を変更する。
ONVIF へ渡す移動速度は connector 実装で `1.0` に固定する。
`small_move_seconds / medium_move_seconds` は `amount` から連続移動時間へ変換する connector 既定値であり、decision view、inspection、capability request、capability result へ出さない。
C220 は物理ズームと ONVIF Zoom capability を持たないため、`zoom_in / zoom_out` を source metadata に出さない。

## 実行

hello payload を確認する。
このコマンドは OtomeKairo から runtime config を取得する。

```bash
.venv/bin/python -m otomekairo_tapo_c220_connector --config config.local.json --print-hello
```

実機への疎通を確認する。
この確認は ONVIF PTZ capability と RTSP still capture だけを実行し、camera を動かさない。
このコマンドも OtomeKairo から runtime config を取得する。

```bash
.venv/bin/python -m otomekairo_tapo_c220_connector --config config.local.json --check-device
```

connector を起動する。

```bash
.venv/bin/python -m otomekairo_tapo_c220_connector --config config.local.json
```

## VSCode F5 debug

workspace root の VSCode F5 は OtomeKairo server と Tapo C220 connector を compound debug で同時起動する。
停止ボタンは server と connector の両方を停止する。

F5 debug でも通常起動と同じ token 解決を使う。
token は launch 設定、コード、通常ログへ保存しない。

F5 debug でローカル上書きが必要な場合だけ、`connectors/tapo_c220/.env` に token を設定する。
`.env` は repository に含めない。

```bash
OTOMEKAIRO_ACCESS_TOKEN=...
```

F5 debug の connector は `https://127.0.0.1:55601` の server 起動を待ってから runtime config を取得する。
camera source は CocoroConsole の camera source 設定画面で登録し、`enabled=true`、`connector_kind=tapo_c220`、`client_id=tapo-c220-connector-main` にする。

server のローカル開発 TLS 証明書を使う場合、`server.tls_verify=false` のまま使う。
実運用の信頼済み証明書を使う場合、`server.tls_verify=true` にする。

## Wire

hello で送る source metadata は次の形に固定する。

```json
{
  "vision_source_id": "vision_source:tapo_c220_main",
  "capability_id": "vision.capture",
  "kind": "camera",
  "source_owner": "self",
  "label": "C220",
  "aliases": ["C220"],
  "default_for": ["camera"],
  "required_permissions": ["observe_vision", "observe_camera"],
  "supported_controls": {
    "camera.ptz": {
      "operations": ["move_up", "move_down", "move_left", "move_right"],
      "amounts": ["small", "medium"]
    }
  }
}
```

`vision.capture` result の `client_context` は `vision_source_id / source_kind / source_label` を request と同じ値にする。
`camera.ptz` result の `client_context` も `vision_source_id / source_kind / source_label` を request と同じ値にする。
credential、内部 URL、RTSP URL、host、機器固有 payload は result に入れない。
