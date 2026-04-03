# OtomeKairo

会話 1 サイクルの最小縦切りとして、HTTPS の最小 API サーバを実装している。

## セットアップ

```bash
./scripts/setup_venv.sh
```

このスクリプトは `.venv` を作成する。
あわせて `pyproject.toml` に定義した依存関係をインストールする。

## 実行

```bash
./scripts/run_dev_server.sh
```

このスクリプトは次を行う。

- `.venv` が無ければ停止する
- `var/dev-tls/` にローカル開発用の自己署名証明書を作る
- `var/otomekairo/` をデータ保存先にして HTTPS サーバを起動する
- `PYTHONPATH=src` を付けて `.venv` の Python からサーバを起動する
- 既定ポートは `55601` を使う

データはデフォルトで `var/otomekairo/` に保存される。

## LLM 接続

OtomeKairo は `model_profile.provider` が `mock` のときは内蔵の開発用ロジックを使う。
`mock` 以外の `generation` プロファイルは LiteLLM Python SDK 経由で呼び出す。

最小構成は次のとおりである。

- `provider`
  - 例: `openai`, `openai_compatible`, `azure`, `ollama`
- `model_name`
  - LiteLLM に渡すモデル名
- `base_url`
  - 接続先 API のベース URL
- `auth`
  - `type=none` または `token` を含む設定

通常は `CocoroConsole` の設定画面からこれらを編集する。

## VSCode から起動

VSCode では `F5` で `OtomeKairo: Debug Server` を起動できる。

- 起動前に `.venv` と開発用証明書を自動で準備する
- `PYTHONPATH=src` を付けて `otomekairo.run` をデバッグ実行する

## 手動実行

```bash
OTOMEKAIRO_TLS_CERT_FILE=/path/to/cert.pem \
OTOMEKAIRO_TLS_KEY_FILE=/path/to/key.pem \
OTOMEKAIRO_DATA_DIR=var/otomekairo \
PYTHONPATH=src \
.venv/bin/python -m otomekairo.run
```
