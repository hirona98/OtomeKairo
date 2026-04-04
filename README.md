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

OtomeKairo は `model_profile.model` が `mock` のときは内蔵の開発用ロジックを使う。
`mock` 以外の `generation` プロファイルは LiteLLM Python SDK へ、そのまま `model` を渡して呼び出す。

新規 state のサンプル値は OpenRouter 前提で、生成系は `openrouter/google/gemini-3.1-flash-lite-preview`、埋め込みは `openrouter/google/gemini-embedding-001` を使う。

最小構成は次のとおりである。

- `model`
  - LiteLLM にそのまま渡すモデル文字列
  - 例: `openrouter/google/gemini-3.1-flash-lite-preview`, `xai/grok-2-latest`, `openrouter/openai/gpt-5`, `ollama/llama3.1`
- `base_url`
  - 省略可能
  - ローカルや OpenAI 互換 API を明示したいときだけ使う
- `auth`
  - 省略可能
  - `type=none` または `token` を含む設定

OpenRouter 経由の Gemini サンプルは次のとおりである。

```yaml
model: openrouter/google/gemini-3.1-flash-lite-preview
auth:
  type: bearer
  token: ""
```

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
