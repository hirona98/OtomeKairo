# chat_replay_eval 運用メモ

<!-- Block: Purpose -->
## 目的

- 直近の `chat_message` cycle を時系列で replay し、`dialogue thread` と `返答のふるまい` の効き方を確認する
- retrieval 指標ではなく、会話の継続性に寄った replay 観測を用意する
- `memory_write_e2e` や実 DB に対して、保存後の状態が次 cycle にどう残っているかを数で見る

<!-- Block: Command -->
## 実行コマンド

実 DB に対して:

```bash
PYTHONPATH=src python3 -m otomekairo.boot.run_chat_replay_eval --limit 50
```

JSON で取りたい場合:

```bash
PYTHONPATH=src python3 -m otomekairo.boot.run_chat_replay_eval --limit 50 --format json
```

`memory_write_e2e --keep-db` の生成 DB に対して:

```bash
PYTHONPATH=src python3 -m otomekairo.boot.run_chat_replay_eval --db-path /tmp/otomekairo-memory-write-e2e-xxxx/core.sqlite3
```

<!-- Block: What It Reads -->
## 何を読むか

- `commit_records` から `processed_input_kind=chat_message` の cycle を抽出する
- 各 cycle の `event_ids` から user/assistant の発話を復元する
- `event_threads` から `dialogue:*` thread の再利用を確認する
- `action_history` から、その cycle の行動種別と失敗種別を取り出し、assistant 応答の説明性と照合する
- assistant 応答文を deterministic な cue で見て、行動説明、失敗説明、日付想起を集計する

<!-- Block: Main Metrics -->
## 主な指標

- `dialogue_thread_reuse_rate_percent`: 既存の `dialogue:*` thread を引き継いだ cycle の割合
- `response_date_recall_rate_percent`: user 発話の ISO 日付を assistant 応答も保持した割合
- `response_action_transparency_rate_percent`: action 実行があった cycle のうち、assistant 応答が `調べる` / `確認する` などの cue で行動を言語化した割合
- `response_failure_explanation_rate_percent`: failed action があった cycle のうち、assistant 応答が `timeout` / `待つ` / `やり直す` などで失敗を説明した割合

<!-- Block: Reading -->
## 読み方

- `dialogue_thread_reuse` が低い場合は、`event_threads` の継続 key が切れている
- `response_date_recall` が低い場合は、時間文脈が応答へ残っていない
- `response_action_transparency` が低い場合は、実行した行動を応答文が説明していない
- `response_failure_explanation` が低い場合は、失敗時の案内が弱い
