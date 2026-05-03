# OtomeKairo

OtomeKairo は、HTTPS API サーバとして bootstrap、観測、設定、inspection、event stream を提供する。
設計と現在地は `docs/` を正とする。

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

生成系モデルは `model_presets.roles.*`、埋め込みモデルは `memory_sets.embedding` で管理する。

- 生成系 role
  - 必須項目: `model`, `api_key`, `max_output_tokens`, `web_search_enabled`
  - 任意項目: `api_base`, `reasoning_effort`
- 埋め込み
  - 必須項目: `model`, `api_key`, `embedding_dimension`
  - 任意項目: `api_base`

`model` が `mock` で始まるときは、内蔵の開発用モック経路を使う。
それ以外の生成系 role は LiteLLM を通して呼び出す。
埋め込みは通常も LiteLLM を使うが、provider が `openrouter` のときだけ OpenRouter embeddings API を直接呼ぶ。

`api_base` を省略したとき、`model` の provider prefix が `openrouter` なら既定値 `https://openrouter.ai/api/v1` を使う。

既定の新規 state は OpenRouter 前提で、生成系に `openrouter/google/gemini-3.1-flash-lite-preview`、埋め込みに `openrouter/google/gemini-embedding-001` を入れる。

設定項目の抜粋例は次のとおりである。
実際の `model_preset` 定義では、8 個の必須 role をすべて含める。

```json
{
  "model_preset_id": "model_preset:default",
  "display_name": "Default OpenRouter Gemini Preset",
  "prompt_window": {
    "recent_turn_limit": 30,
    "recent_turn_minutes": 3
  },
  "roles": {
    "decision_generation": {
      "model": "openrouter/google/gemini-3.1-flash-lite-preview",
      "api_key": "",
      "max_output_tokens": 3000,
      "web_search_enabled": false
    }
  }
}
```

```json
{
  "memory_set_id": "memory_set:default",
  "display_name": "Default Memory",
  "embedding": {
    "model": "openrouter/google/gemini-embedding-001",
    "embedding_dimension": 3072,
    "api_key": ""
  }
}
```

通常は `CocoroConsole` の設定画面からこれらを編集する。詳細は `docs/design/07_設定モデル.md` と `docs/design/10_モデルプリセット詳細.md` を参照する。

## 実 LLM smoke

保存済みの `var/otomekairo/server_state.json` に実 LLM 用の API key が入っている場合、隔離データディレクトリへ model/memory 設定だけをコピーして、短い実 LLM smoke を実行できる。

```bash
.venv/bin/python scripts/run_long_smoke.py --profile real-llm-smoke --keep-artifacts
```

この profile は通常会話 1 回、`external.status` の capability request / result follow-up 1 回、memory postprocess drain を確認する。
full smoke と違い、`wake` と `desktop_watch` は無効化する。

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
