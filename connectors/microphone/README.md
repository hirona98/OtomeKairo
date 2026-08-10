# microphone connector

この connector は Ubuntu の PortAudio / ALSA input device を OtomeKairo のローカル音声入力へ接続し、選択時は合成済み音声を明示設定された output device で再生する。
VAD、STT、音声起動ワード判定、話者識別は実行しない。

## OS準備

```bash
sudo apt install libportaudio2
sudo usermod -aG audio "$USER"
```

group追加後はログインし直す。
daemon構成では `scripts/install_simple_system_service.sh` がservice userを`audio` groupへ追加する。

## セットアップ

```bash
cd connectors/microphone
python3 -m venv .venv
.venv/bin/pip install -e .
```

OtomeKairo serverと同じPCで実行する場合、tokenは`OTOMEKAIRO_DATA_DIR/config.db`から取得する。
明示tokenを使う場合は`OTOMEKAIRO_ACCESS_TOKEN`へ設定する。
実tokenを`config.example.json`へ書かない。

ローカル設定を上書きする場合は`config.example.json`を`config.local.json`へコピーする。
通常入力元、input device、音声出力先、output device は OtomeKairo の音声設定で管理する。

## 実行

```bash
.venv/bin/python -m otomekairo_microphone_connector
```

`config.local.json`を使う場合は次で起動する。

```bash
.venv/bin/python -m otomekairo_microphone_connector --config config.local.json
```

connectorは無効設定中も起動を維持し、input / output device catalog と設定変更を監視する。
選択deviceが消えた場合はOS defaultへ切り替えず、同じALSA host API名とdevice名を5秒間隔で探索する。出力は `destination=otomekairo` または `destination=cocoro_console` で、保存済み output device が一意に存在するときだけ event stream を購読する。`destination=cocoro_console` では、CocoroConsoleが未接続の発話だけOtomeKairoから再生する。
