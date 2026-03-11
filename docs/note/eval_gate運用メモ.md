# eval_gate 運用メモ

<!-- Block: Purpose -->
## 目的

- merge 前に最低限通す deterministic な評価を 1 コマンドへまとめる
- `src/otomekairo` 全体の `py_compile` と `chat_behavior_golden` を続けて流し、構文破壊と会話品質破壊を同時に止める
- 日常運用では、個別 CLI を覚える代わりにこの gate だけを実行すればよい状態にする

<!-- Block: Command -->
## 実行コマンド

```bash
PYTHONPATH=src python3 -m otomekairo.boot.run_eval_gate
```

JSON で取りたい場合:

```bash
PYTHONPATH=src python3 -m otomekairo.boot.run_eval_gate --format json
```

golden 用 DB を残したい場合:

```bash
PYTHONPATH=src python3 -m otomekairo.boot.run_eval_gate --keep-db
```

<!-- Block: What It Runs -->
## 何を流すか

- `src/otomekairo` 配下の `*.py` を全件 `py_compile` する
- その後に `chat_behavior_golden` を実行する
- どちらかが失敗した時点で例外にして止める

<!-- Block: Reading -->
## 読み方

- `py_compile` が落ちた場合は、構文レベルの破壊なので即修正する
- `chat_behavior_golden` が落ちた場合は、まず `chat_behavior_golden --keep-db` か `eval_gate --keep-db` で DB を残し、`chat_replay_eval` で cycle ごとの差を確認する
- `eval_gate` は merge 前の入口であり、詳細な原因分析は `memory_write_e2e`、`chat_replay_eval`、`chat_behavior_golden` 側で行う
