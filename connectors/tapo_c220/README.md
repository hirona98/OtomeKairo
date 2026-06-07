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

`config.example.json` を元にローカル設定を作る。
秘密値は環境変数で渡す。

```bash
cd connectors/tapo_c220
python3 -m venv .venv
.venv/bin/pip install -e .
cp config.example.json config.local.json

export OTOMEKAIRO_ACCESS_TOKEN="..."
export TAPO_C220_HOST="192.168.1.52"
export TAPO_C220_CAMERA_USERNAME="..."
export TAPO_C220_CAMERA_PASSWORD="..."
```

`TAPO_C220_CAMERA_USERNAME / TAPO_C220_CAMERA_PASSWORD` は C220 の camera account である。
connector は同じ camera account を ONVIF control と RTSP capture に使う。
C220 の ONVIF port は `camera.onvif_port` で指定し、初期値は `2020` とする。
`host`、camera account、connector token は repository、sample、通常ログ、result に保存しない。

`operation_vectors` は ONVIF `PanTilt` velocity へ掛ける向きベクトルである。
`move_up / move_down / move_left / move_right` は現在の映像に対する相対方向である。
`config.example.json` の `operation_vectors` は C220 実機で確認した ONVIF `ContinuousMove` の符号に合わせる。
設置向きや ONVIF 座標符号が映像上の向きと合わない場合は、`config.local.json` の `operation_vectors` を変更する。
ONVIF へ渡す移動速度は connector 実装で `1.0` に固定する。
`small_move_seconds / medium_move_seconds` は `amount` から連続移動時間へ変換する connector 内部設定であり、server、decision view、inspection へ出さない。
C220 は物理ズームと ONVIF Zoom capability を持たないため、`zoom_in / zoom_out` を source metadata に出さない。

## 実行

hello payload を確認する。

```bash
.venv/bin/python -m otomekairo_tapo_c220_connector --config config.local.json --print-hello
```

実機への疎通を確認する。
この確認は ONVIF PTZ capability と RTSP still capture だけを実行し、camera を動かさない。

```bash
.venv/bin/python -m otomekairo_tapo_c220_connector --config config.local.json --check-device
```

connector を起動する。

```bash
.venv/bin/python -m otomekairo_tapo_c220_connector --config config.local.json
```

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
  "aliases": ["カメラ", "部屋のカメラ", "C220"],
  "default_for": ["visual", "camera"],
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
