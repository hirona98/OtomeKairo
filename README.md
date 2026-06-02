# OtomeKairo

OtomeKairo は、LLM を用いて人間を模した自立可能な個を作るためのプロジェクトである。
会話 AI ではなく、継続的に存在し、観測し、理解し、判断する個を目指す。

人格設定と記憶を基盤に、その時点で成立する判断主体を「現在の個」として扱う。
実際の挙動は、人格設定だけでも記憶だけでも決まらず、人格設定、記憶、そこから派生する内部状態を含む文脈で決まる。

現行実装は HTTPS API サーバとして動作する。
bootstrap、対話入力、自律起床、観測能力、設定、inspection、event stream、log stream を扱う。
設計は `docs/` を正とする。
現行実装の状態は `src/` と smoke 結果を正とする。

## 文書の入口

- 目的と設計の入口: `docs/00_はじめに.md`
- 上位設計: `docs/design/`
- HTTP / WebSocket API 仕様: `docs/design/api/`
- 記憶 subsystem: `docs/design/memory/`

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
デバッグログは `var/otomekairo/server.log` に保存される。
ログは既定で 5MiB を超えるとローテーションし、`server.log.1` から `server.log.3` まで保持する。
既定の総保持量は `server.log` 本体と 3 世代を合わせて最大約 20MiB である。

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
    "recent_turn_minutes": 30
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

## smoke

mock 経路の smoke は次で実行する。

```bash
.venv/bin/python scripts/run_long_smoke.py --profile smoke
```

この profile は隔離データディレクトリでサーバを起動し、通常会話、バックグラウンド自律起床（`background_wake`）、capability request / result follow-up、memory worker、vision capture、記憶と想起の代表ケースを確認する。

## 実 LLM smoke

保存済みの `var/otomekairo/server_state.json` に実 LLM 用の API key が入っている場合、隔離データディレクトリへ model/memory 設定だけをコピーして、短い実 LLM smoke を実行できる。

```bash
.venv/bin/python scripts/run_long_smoke.py --profile real-llm-smoke --keep-artifacts
```

この profile は通常会話、status capability の request / result follow-up、手動自律起床（manual wake）の自律判断 matrix、`vision.capture` result follow-up、バックグラウンド自律起床（`background_wake`）制御 matrix、memory postprocess drain を確認する。
詳細な確認観点は `docs/design/21_自律initiative_loop.md` と `scripts/run_long_smoke.py` を正とする。
full smoke と違い、`wake_policy` は各 matrix の実行中だけ一時的に有効化する。
