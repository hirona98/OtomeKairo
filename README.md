# OtomeKairo

OtomeKairo は、LLM を用いて人間を模した自立可能な個を作るためのプロジェクトである。
会話 AI ではなく、継続的に存在し、観測し、理解し、判断する個を目指す。

現行実装は HTTPS API サーバとして動作する。
bootstrap、対話入力、API起床、観測能力、設定、inspection、event stream、log stream を扱う。

## 文書

設計、API 仕様、記憶設計、用語表は [docs/README.md](docs/README.md) から読む。
設計判断は `docs/` を正とする。
現行実装の状態は `src/` と smoke 結果を正とする。

## セットアップ

```bash
./scripts/setup_venv.sh
```

このスクリプトは `.venv` を作成し、`pyproject.toml` に定義した依存関係をインストールする。

## 実行

```bash
./scripts/run_dev_server.sh
```

このスクリプトは次を行う。

- `.venv` が無ければ停止する
- `var/dev-tls/` にローカル開発用の自己署名証明書を作る
- `var/otomekairo/` をデータ保存先にして HTTPS サーバを起動する
- `PYTHONPATH=src` を付けて `.venv` の Python からサーバを起動する
- 既定ポート `55601` を使う

データはデフォルトで `var/otomekairo/` に保存する。
デバッグログは `var/otomekairo/server.log` に保存する。
ログは既定で 5MiB を超えるとローテーションし、`server.log` 本体と 3 世代を合わせて最大約 20MiB 保持する。

## LLM 接続

生成系モデルは `model_presets.roles.*`、埋め込みモデルは `memory_sets.embedding` で管理する。
通常は `CocoroConsole` の設定画面から編集する。

`model` が `mock` で始まるときは、内蔵の開発用モック経路を使う。
それ以外の生成系 role は LiteLLM を通して呼び出す。
埋め込みは通常も LiteLLM を使い、provider が `openrouter` のときだけ OpenRouter embeddings API を直接呼ぶ。

設定定義の意味は `docs/` を正とする。
API key は設定値として扱い、コード、ログ、サンプルへ書かない。

## 検証

通常検証は 1 分以内で終わる fast test を標準にする。

```bash
.venv/bin/python -m pytest tests
```

fast test は契約、validation、境界処理を確認する。
サーバ起動、scheduler、memory worker、実 LLM 呼び出しを含む検証は通常検証へ入れない。

## 重い検証

mock 経路の smoke は次で実行する。

```bash
.venv/bin/python scripts/run_long_smoke.py --profile smoke
```

この profile は隔離データディレクトリでサーバを起動し、通常会話、定期起床、capability request / result follow-up、memory worker、vision capture、記憶と想起の代表ケースを確認する。
大きな変更前後に手動で実行する。

実 LLM 用の API key を保存済みの `var/otomekairo/server_state.json` に設定している場合、隔離データディレクトリへ model / memory 設定だけをコピーして短い実 LLM smoke を実行する。

```bash
.venv/bin/python scripts/run_long_smoke.py --profile real-llm-smoke --keep-artifacts
```

詳細な検証層と合否基準は [docs/design/verification/検証基盤.md](docs/design/verification/検証基盤.md) を正とする。
