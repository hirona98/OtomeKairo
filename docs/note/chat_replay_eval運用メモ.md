# chat_replay_eval 運用メモ

<!-- Block: Purpose -->
## 目的

- 直近の `chat_message` cycle を時系列で replay し、`dialogue thread`、`preference carryover`、`long_mood carryover`、`返答のふるまい` の効き方を確認する
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
- `commit_records` と保存済み記憶断面から、cycle 時点の `preference_memory` と `long_mood_state` の残り方を追う
- `action_history` から、その cycle の行動種別と失敗種別を取り出して prior preference と照合する
- assistant 応答文を deterministic な cue で見て、行動説明、失敗説明、preference 参照、mood tone hint を集計する

<!-- Block: Main Metrics -->
## 主な指標

- `dialogue_thread_reuse_rate_percent`: 既存の `dialogue:*` thread を引き継いだ cycle の割合
- `preference_alignment_rate_percent`: 前 cycle までに confirmed だった `action_type` preference と、当 cycle の行動が一致した割合
- `preference_restore_rate_percent`: 直前まで revoked だった preference が当 cycle で confirmed に戻った割合
- `long_mood_carryover_rate_percent`: 前 cycle の `long_mood_state` が当 cycle でも続いている割合
- `long_mood_same_label_rate_percent`: `long_mood_state.primary_label` が前 cycle と同じ割合
- `response_date_recall_rate_percent`: user 発話の ISO 日付を assistant 応答も保持した割合
- `response_action_transparency_rate_percent`: action 実行があった cycle のうち、assistant 応答が `調べる` / `確認する` などの cue で行動を言語化した割合
- `response_failure_explanation_rate_percent`: failed action があった cycle のうち、assistant 応答が `timeout` / `待つ` / `やり直す` などで失敗を説明した割合
- `response_preference_reference_rate_percent`: confirmed `like` preference を持つ cycle のうち、assistant 応答か action がその preference を参照した割合
- `response_preference_violation_rate_percent`: confirmed `dislike` または revoked preference を持つ cycle のうち、assistant 応答か action がそれに逆行した割合
- `response_mood_tone_hint_rate_percent`: `long_mood_state.primary_label` から導かれる tone hint と、assistant 応答の cue が一致した割合

<!-- Block: Reading -->
## 読み方

- `dialogue_thread_reuse` が低い場合は、`event_threads` の継続 key が切れている
- `preference_alignment` が低い場合は、保存された好悪が次 cycle の行動へ効いていない
- `preference_restore` が低い場合は、revoke 後の回復導線が弱い
- `long_mood_same_label` が極端に低い場合は、感情状態が揺れすぎている可能性がある
- `response_date_recall` が低い場合は、時間文脈が応答へ残っていない
- `response_action_transparency` が低い場合は、実行した行動を応答文が説明していない
- `response_failure_explanation` が低い場合は、失敗時の案内が弱い
- `response_preference_reference` が低い場合は、保存済み preference が返答文へ効いていない
- `response_preference_violation` が高い場合は、保存済み aversion や revoke が無視されている
- `response_mood_tone_hint` は heuristic 指標であり、厳密判定ではなく口調 drift の早期検知に使う
