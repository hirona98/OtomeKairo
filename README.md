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

## daemon 実行

専用 PC で常時起動する場合は、repository を `/opt/OtomeKairo` に置き、単一の systemd service として起動する。
この構成では OtomeKairo server、Tapo C220 connector、MCP client connector を 1 つの service lifecycle でまとめて扱う。

```bash
sudo mkdir -p /opt
sudo chown hirona:hirona /opt
mv /home/hirona/app/OtomeKairo /opt/OtomeKairo
cd /opt/OtomeKairo
./scripts/prepare_service_env.sh
sudo ./scripts/install_simple_system_service.sh
sudo systemctl enable --now otomekairo
```

本体は daemon 実行時に `0.0.0.0:55601` で listen する。
外部端末からは次で接続する。

```text
https://<このPCのIPアドレス>:55601
```

操作は通常の systemd コマンドを使う。

```bash
sudo systemctl status otomekairo
sudo systemctl restart otomekairo
sudo journalctl -u otomekairo -f
```

コードを更新した場合は、同じ場所の checkout を更新して service を再起動する。
依存関係が変わった場合は、再起動前に `./scripts/prepare_service_env.sh` を再実行する。

```bash
cd /opt/OtomeKairo
git pull
./scripts/prepare_service_env.sh
sudo systemctl restart otomekairo
```

Tapo C220 connector と MCP client connector は起動時に OtomeKairo server から runtime config を取得する。
camera source または MCP server が未登録の場合、service 全体を起動失敗として扱う。
connector を有効にする前に、CocoroConsole または設定 API で camera source と MCP server を登録する。
初回登録がまだの場合は、daemon 有効化前に `OTOMEKAIRO_HOST=0.0.0.0 ./scripts/run_dev_server.sh` で server だけを起動して設定する。

## LLM 接続

生成系モデルは `model_presets.roles.*`、埋め込みモデルは `memory_sets.embedding` で管理する。
通常は `CocoroConsole` の設定画面から編集する。

`model` が `mock` で始まるときは、内蔵の開発用モック経路を使う。
それ以外の生成系 role は LiteLLM を通して呼び出す。
埋め込みは通常も LiteLLM を使い、provider が `openrouter` のときだけ OpenRouter embeddings API を直接呼ぶ。

設定定義の意味は `docs/` を正とする。
API key は設定値として扱い、コード、ログ、サンプルへ書かない。

## smoke

mock 経路の smoke は次で実行する。

```bash
.venv/bin/python scripts/run_long_smoke.py --profile smoke
```

この profile は隔離データディレクトリでサーバを起動し、通常会話、定期起床、capability request / result follow-up、memory worker、vision capture、記憶と想起の代表ケースを確認する。

実 LLM 用の API key を保存済みの `var/otomekairo/server_state.json` に設定している場合、隔離データディレクトリへ model / memory 設定だけをコピーして短い実 LLM smoke を実行する。

```bash
.venv/bin/python scripts/run_long_smoke.py --profile real-llm-smoke --keep-artifacts
```

詳細な確認観点は `scripts/run_long_smoke.py` と `docs/` を正とする。
