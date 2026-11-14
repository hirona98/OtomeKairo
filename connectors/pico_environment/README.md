# Pico環境センサー

Pico 2 Wへ接続したSHT41、VEML7700、SCD4xをUSBで読み、OtomeKairoへ現在の測定値を報告する。同じPC上で動かすproducerであり、capability bindingやhelloは持たない。
観測の鮮度・保存・LLMへの転送は[周辺環境](../../docs/design/runtime/周辺環境.md)、登録と送信のHTTP契約は[観測入力API](../../docs/design/api/観測入力.md)を正とする。

## 配線と測定

PicoのGP0をSDA、GP1をSCL、3V3(OUT)を電源、GNDを共通接地とし、I²C0を100 kHzで使う。VEML7700とSHT41の3Voは出力端子なので、電源入力にはVINを使う。配線を変更するときはUSBを抜く。

| 機器 | 7ビットアドレス | channel_id | 単位 |
| --- | --- | --- | --- |
| SHT41 | `0x44` | `temperature` / `humidity` | `degC` / `%` |
| VEML7700 | `0x10` | `illuminance` | `lux` |
| SCD4x | `0x62` | `co2` | `ppm` |

室温と湿度はSHT41から取得する。SHT41はヒーターなしの高精度単発測定、SCD4xは5秒周期の連続測定を使い、CRCを照合する。CO₂の開始には約6秒かかる。校正設定は変更しない。
VEML7700はgain 1/8・積分100 msとし、Vishayの換算係数0.5376 lux/countと1000 lux超の補正式を使う。飽和は取得失敗として報告する。

機器コマンドと数値換算の参照資料:

- [Sensirion SHT4xデータシート](https://sensirion.com/products/catalog/SHT41)
- [Sensirion SCD4xデータシート](https://sensirion.com/products/catalog/SCD41)
- [Vishay VEML7700アプリケーションノート](https://www.vishay.com/docs/84323/designingveml7700.pdf)

## 初回のPico書き込み

BOOTSELを押しながらUSB接続し、[Pico 2 W用MicroPython](https://micropython.org/download/RPI_PICO2_W/)の通常版UF2を、`RP2350`ドライブへコピーする。CLIでmountする場合は、`lsblk`でラベルと対象デバイスを確認する。書き込み後はUSBシリアル機器として再起動する。

以下はrepository rootから実行する。

```bash
python3 -m venv /tmp/otomekairo-pico-setup
/tmp/otomekairo-pico-setup/bin/pip install mpremote
/tmp/otomekairo-pico-setup/bin/mpremote connect list
```

`/dev/serial/by-id/`の対象を選び、以下の`<PICO_PORT>`に指定する。OSのアクセス権も必要になる。例として、確認した当該PicoのUSB serialを使い、`/etc/udev/rules.d/70-otomekairo-pico.rules`に次の1行を置ける。実行ユーザーはplugdev groupへ所属させ、udev ruleをreloadして対象ttyへ適用する。

```udev
SUBSYSTEM=="tty", ATTRS{idVendor}=="2e8a", ATTRS{idProduct}=="0005", ATTRS{serial}=="<PICO_USB_SERIAL>", GROUP="plugdev", MODE="0660"
```

認識確認:

```bash
/tmp/otomekairo-pico-setup/bin/mpremote connect <PICO_PORT> exec 'from machine import I2C, Pin; print([hex(a) for a in I2C(0, sda=Pin(0), scl=Pin(1), freq=100_000).scan()])'
```

3つのアドレスを確認したら、次の3ファイルをPicoのrootへコピーし、再起動する。更新時はUSBを使用しているproducerを先に停止する。

```bash
/tmp/otomekairo-pico-setup/bin/mpremote connect <PICO_PORT> cp connectors/pico_environment/src/otomekairo_pico_environment_connector/protocol.py connectors/pico_environment/firmware/sensors.py connectors/pico_environment/firmware/main.py : + reset
```

## OtomeKairoの登録と起動

観測入力APIで上表の4つのnumber型measurement channelを持つsourceを登録する。既定のsource IDは`sensor_source:pico_environment`、producer clientは`pico-environment-main`。自身の観測として採用する場合は`source_owner=self`とする。場所は実際の設置場所を登録し、未設定なら`place_ref=null`を保持する。運用例では`max_age_seconds=120`とする。

```bash
./scripts/ensure_editable_install.sh connectors/pico_environment/.venv connectors/pico_environment otomekairo_pico_environment_connector
cp connectors/pico_environment/config.example.json connectors/pico_environment/config.local.json
```

`config.local.json`の`serial_port`を確認済みの`/dev/serial/by-id/`へ変更する。各fieldは必須。`config_db_path`は設定ファイルからの相対パス、または絶対パスで指定する。共有tokenはそのDBから読み取り専用で取得し、別ファイルへ複製しない。`tls_verify=false`の例は同一PCの自己署名HTTPS用である。

```bash
connectors/pico_environment/.venv/bin/python -m otomekairo_pico_environment_connector --config connectors/pico_environment/config.local.json --once
connectors/pico_environment/.venv/bin/python -m otomekairo_pico_environment_connector --config connectors/pico_environment/config.local.json
```

`--once`は1回分の報告を表示し、失敗channelがあれば終了コード2を返す。通常起動は既定の設定例で10秒間隔。sourceを無効にすると取得を休止し、有効化すると再開する。未登録・producerやchannelの不一致・設定不正は明示失敗とする。
USB切断や不正な応答は全channelの失敗を報告する。機器単位の測定失敗は該当channelだけを失敗にし、同じ機器を次の周期で再試行する。HTTPの接続失敗・5xxは同じserverを再試行し、4xxは終了する。

`scripts/prepare_service_env.sh`はこのproducer用venvも準備する。`config.local.json`がある場合、`scripts/run_service_all.sh`が既存process群と同じlifecycleで起動する。現在値は`GET /api/inspection/current-state`の`surroundings`で確認できる。観測APIへの報告はruntimeだけを更新する。温度急変の通知は次の条件でAPI起床を呼び出す。

## 変化分類と温度急変の通知

PC側producerは各channelの前回値・取得時点だけを保持し、送信するmeasurementへ`change`を添える。時系列の蓄積は行わない。分類と比較値のwireは[観測入力API](../../docs/design/api/観測入力.md)、LLMとの意味境界は[測定上の変化情報](../../docs/design/runtime/周辺環境.md#測定上の変化情報)を正とする。

`change_detection`の全項目は必須とする。比較間隔はmonotonic clockで測り、0秒以下または`max_gap_seconds`を超えた場合、初回、取得失敗後の復帰、有効化後は`initial`とする。比較できる場合、前回との差の絶対値が`steady_delta`以下なら`steady`、`rapid_delta`以上なら`rapid_change`、中間は`changing`とする。境界値には浮動小数点の比較許容差（相対・絶対とも`1e-9`）を使う。

設定例は`max_gap_seconds=30`、次の値とする。数値条件は`config.local.json`で変更できる。

| channel | ほぼ同じ（steady_delta以下） | 急変動（rapid_delta以上） |
| --- | --- | --- |
| temperature | 0.1 degC | 0.5 degC |
| humidity | 1 % | 5 % |
| illuminance | 10 lux | 100 lux |
| co2 | 30 ppm | 300 ppm |

温度が`rapid_change`へ入ったとき、または急変の方向が反転したとき、観測の送信成功後に既存の`POST /api/wake`へ参照付き起床を送る。同じ方向の急変が連続する間は通知を増やさず、`initial / steady / changing`を挟むと次の急変を新しい通知機会にする。他のchannelの分類は測定報告だけに添える。

`temperature_wake`の全項目は必須とする。`enabled`で通知を有効化し、`min_interval_seconds`で起床要求の開始間隔を制限する。設定例では120秒。実行中の起床は最大1件、待機する通知も最新1件にまとめる。待機中に温度が元に戻っても、急変時の値・時刻・比較情報を保持する。sourceを無効にすると未送信の通知を破棄する。

起床のHTTP応答待ちは別threadで行い、通常の測定送信を続ける。`request_timeout_seconds`の設定例は120秒。起床時だけ測定報告を一時的なJSON参照ファイルへ固定し、その起床が終了すると削除する。`message`や対象人物を指定せず、自律判断の機会と測定根拠を渡す。発話・行動・見送りはOtomeKairoが決める。

起床の失敗はログに残し、処理済みの起床を重複させないよう同じ要求を自動再送しない。通常の測定・分類は継続する。`--once`は分類の初回報告だけを送り、起床を行わない。

## USBプロトコル

プロトコル名は`otomekairo.pico-environment.v1`。USBシリアル上の改行区切りJSONを使う。hostは`{"command":"measure","request_id":"<UUID>"}`を送り、Picoは`protocol / request_id / samples`を返す。`samples`は全4channelを持ち、各項目は観測入力APIの`channel_id / status / payload / error_code`と同じshapeである。`sample_id`と取得時刻はhostが付ける。

Picoは要求時だけ測定し、未送信の定期サンプルを蓄積しない。hostは応答のrequest ID、全channel、値型、単位を照合し、別要求の応答を現在値へ採用しない。改行を含め8192 bytes以下の応答を受理する。不正要求には`protocol / request_id=null / error=invalid_request`を返す。
