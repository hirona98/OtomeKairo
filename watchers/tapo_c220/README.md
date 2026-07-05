# Tapo C220 watcher

この watcher は Tapo C220 の RTSP 映像を軽量に監視し、画像差分が閾値を超えたときだけ OtomeKairo の `/api/wake` へ参照付き wake を送る。
`vision.capture` や `camera.ptz` を実行する connector ではない。

## 責務

- OtomeKairo から watcher runtime config を取得する
- RTSP からフレームを取得する
- resize + grayscale + absdiff で画像差分を判定する
- 変化時に snapshot JPEG を保存する
- `/api/wake` へ `reference.uri` として snapshot のローカルパスを渡す

## 設定

OtomeKairo 側の `camera_source.watcher` を正本にする。
watcher のローカル設定には OtomeKairo への接続情報と `watcher_id` だけを置く。

```bash
cd watchers/tapo_c220
python3 -m venv .venv
.venv/bin/pip install -e .
cp config.example.json config.local.json
```

明示 token が必要な場合は、`OTOMEKAIRO_ACCESS_TOKEN` に現行 API の `console_access_token` を設定する。
通常は同一 PC 内の `config.db` から `console_access_token` を読み取る。
`console_access_token` が未発行の場合は bootstrap API で初回発行する。

## 実行

```bash
.venv/bin/python -m otomekairo_tapo_c220_watcher --config config.local.json
```

`host`、camera account、OtomeKairo access token、RTSP URL は repository、sample、通常ログ、wake payload に保存しない。
