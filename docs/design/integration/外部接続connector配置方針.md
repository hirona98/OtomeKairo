# 外部接続 process 配置方針

## 目的

この文書は、外部接続 process（connector / watcher）をどの repository 階層に置き、OtomeKairo 本体とどこで分けるかを正本にする。

capability の意味境界は [../capability/capability_manifest.md](../capability/capability_manifest.md)、wire は [../api/実行連携.md](../api/実行連携.md) を正とする。
各 connector / watcher の起動手順と機器固有の既定値は、対象ディレクトリの `README.md` を正とする。

## 基本方針

- 外部接続 connector は OtomeKairo 本体とは別の実行 client である
- OtomeKairo 本体は capability manifest、判断、状態、記憶、inspection の正本を持つ
- capability connector は接続先を扱い、capability request を実行し result を返す
- microphone connector は capability binding を持たず、PCM を音声入力 stream へ送る
- watcher は軽量外部監視を行い、変化時に `/api/wake` へ参照付き wake を送る。capability request / hello / binding を持たない
- connector / watcher 実装を `src/otomekairo/` に入れない
- 最初の connector 群は `connectors/`、watcher 群は `watchers/` に置く
- connector は capability manifest を定義しない。`hello.caps` と必要な source metadata だけを送る
- camera connector / watcher の host・camera account・監視閾値は OtomeKairo の runtime config を正本とし、ローカル設定の正本にしない

## repository 配置

```text
connectors/<name>/
  pyproject.toml
  README.md
  config.example.json
  src/otomekairo_<name>_connector/

watchers/<name>/
  pyproject.toml
  README.md
  src/otomekairo_<name>_watcher/
```

- connector / watcher ごとに独立した `pyproject.toml` を置く
- 固有依存を repository root の `pyproject.toml` へ入れない
- 2 個以上の connector で同じ処理が継続して必要になった段階で共通 package の要否を判断する
- 共通 package を作る場合も、server 本体 package へ connector 実装依存を入れない

初期対象の配置は次である。

| 種別 | パス | 役割の要約 |
| --- | --- | --- |
| connector | `connectors/microphone/` | OS audio から PCM を `/api/audio/stream` へ送る |
| connector | `connectors/tapo_c220/` | camera source の `vision.capture` / `camera.ptz` |
| connector | `connectors/mcp_client/` | 許可済み MCP tool を `mcp.call_tool` として実行する |
| watcher | `watchers/tapo_c220/` | 軽量 CV 監視し、変化時に参照付き `/api/wake` を送る |

Webカメラは新しい capability id にせず、`vision.capture` の `VisionSource(kind=camera, source_owner=self)` として登録する。
制御可能な pan / tilt / zoom は `camera.ptz` とする（[../capability/camera_ptz.md](../capability/camera_ptz.md)）。
音声入力の意味規則は [../audio/音声入力と話者識別.md](../audio/音声入力と話者識別.md)、wire は [../api/audio_stream.md](../api/audio_stream.md) を正とする。

## 簡易常駐起動

専用 PC では repository を `/opt/OtomeKairo` に固定し、server と connector / watcher を単一の systemd service lifecycle でまとめて起動してよい。
これは運用上の process 管理単位であり、client 境界、hello、capability request / result、runtime config API、wake reference API の意味境界は変えない。

- server は `0.0.0.0:55601` で listen し、同一 PC 上の connector / watcher は `https://127.0.0.1:55601` へ接続する
- どれか 1 つの process が終了した場合は service 全体を終了し、systemd の restart に任せる
- microphone connector は音声入力が無効でも idle process として起動する
- camera source または MCP server の runtime config が未登録の場合、対象 connector は起動しない
- watcher runtime config が未登録または無効の場合、watcher は起動しない

## connector の責務

担うこと:

- server への認証済み接続と `GET /api/events/stream` の維持
- 起動時 hello による capability binding 候補と source metadata の通知
- capability request の受信、対象機器 / 外部サービスの実行
- `POST /api/capability/result` への result 返却
- 接続・権限・デバイス取得失敗の短い error 返却

担わないこと:

- capability manifest の定義
- 判断結果の生成
- `world_state`、記憶、`activity_state` の更新
- raw payload の永続保存
- OtomeKairo server の設定定義編集
- LLM role、API key、記憶集合の管理

microphone connector は加えて VAD、STT、音声起動ワード判定、話者 embedding の生成・保存・照合、`person_ref` / `interaction_ref` の決定、raw 音声の保存を担わない。

## watcher の責務

担うこと:

- 認証済み HTTP 接続と `GET /api/config/watchers/{watcher_id}/runtime-config` による runtime config 取得
- 対象機器からの軽量観測とローカル判定
- wake reference snapshot の保存と、変化時の `POST /api/wake`
- snapshot の世代管理

担わないこと:

- capability manifest の定義
- hello による binding 候補通知
- capability request の受信
- 判断結果の生成
- raw 動画の常時録画
- OtomeKairo server の設定定義編集
- LLM role、API key、記憶集合の管理

## 設定と秘密情報

- connector のローカル設定は server URL、TLS 検証、再接続間隔、`client_id`、token 明示上書きなど接続項目に限定する
- microphone device、物理入力の有効状態、応答先 client は本体の `microphone_settings` に置く
- watcher はローカル設定ファイルを持たず、`config.db` から `console_access_token` と有効な `watcher_id` を読む。server URL 等は環境変数で扱う
- camera connector の host / camera account は本体の `camera_source` で扱う
- watcher の host / camera account / 監視閾値 / snapshot 保存先は本体の `camera_source.watcher` と runtime config で扱う
- MCP client connector の stdio 用 command / args / cwd / env と Streamable HTTP 用 url / headers は本体の `mcp_server` で扱う
- MCP server 子 process へは launcher 用 `PATH` と当該 `mcp_server.env` だけを渡し、connector process の環境を継承しない
- MCP tool の意味と入力境界は connector に固定せず、接続中の `tools/list` catalog を本体の判断文脈へ供給する
- `config.example.json` と repository に秘密値を入れない
- 通常ログ、debug log、inspection 用 result summary、`client_context` に秘密値を出さない

## 別 repository への切り出し基準

次の状態になった connector は別 repository への切り出し対象にする。

- 配布、更新、権限付与の単位が server と分かれる
- OS 固有依存や大型 SDK により server 開発環境から分離する必要がある
- 複数の実行端末へ個別配布する
- release cycle が server と分かれる
- 外部サービス資格や運用手順を connector 単位で管理する

切り出し後も、server 側の capability manifest と wire 契約はこの repository の docs を正本にする。
この repository には参照実装または起動手順へのリンクだけを残す。
