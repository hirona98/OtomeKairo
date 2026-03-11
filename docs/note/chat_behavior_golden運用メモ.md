# chat_behavior_golden 運用メモ

<!-- Block: Purpose -->
## 目的

- `memory_write_e2e` と `chat_replay_eval` を 1 回で流し、会話品質の golden pack を deterministic に確認する
- 保存チェーンが壊れていないことに加えて、`dialogue thread`、`preference restore`、`action transparency`、`failure explanation`、`mood tone hint` が最低限維持されているかを見る
- merge 前の回帰確認で、`chat` 品質に直結する破壊を早めに止める

<!-- Block: Command -->
## 実行コマンド

```bash
PYTHONPATH=src python3 -m otomekairo.boot.run_chat_behavior_golden
```

JSON で取りたい場合:

```bash
PYTHONPATH=src python3 -m otomekairo.boot.run_chat_behavior_golden --format json
```

生成 DB を残したい場合:

```bash
PYTHONPATH=src python3 -m otomekairo.boot.run_chat_behavior_golden --keep-db
```

<!-- Block: What It Runs -->
## 何を流すか

- まず `memory_write_e2e` の 10 cycle scripted conversation を実行する
- その生成 DB に対して `chat_replay_eval` を流し、保存済み `preference`、`long_mood_state`、`dialogue thread` が次 cycle へどう効いたかを集計する
- 2 つの report から golden check を作り、1 つでも落ちたら例外で止める

<!-- Block: Main Checks -->
## 主なチェック

- `memory_chain_intact`: `memory_write_e2e.checks` が全件 true
- `scenario_action_mix_visible`: `look` と `notify` が少なくとも 1 回ずつあり、`network_unavailable` 失敗も 1 回以上ある
- `dialogue_thread_reuse_visible`: `dialogue_thread_reuse_cycle_count >= 4`
- `preference_restore_visible`: `preference_restore_cycle_count >= 1`
- `action_transparency_visible`: `response_action_transparency_cycle_count >= 5`
- `failure_explanation_visible`: `response_failure_explanation_cycle_count >= 2`
- `preference_reference_visible`: `response_preference_reference_cycle_count >= 4`
- `controlled_preference_violation`: `response_preference_violation_cycle_count <= 1`
  - current 実装では `action_type` の cue は violation から外し、`topic_keyword` と `observation_kind` だけを見る
- `mood_tone_hint_visible`: `response_mood_tone_hint_cycle_count >= 4`

<!-- Block: Reading -->
## 読み方

- ここで落ちるなら、`write_memory`、`selection_profile`、`reply render`、`chat_replay_eval` のどこかが会話品質を壊している
- `controlled_preference_violation` を 1 件まで許しているのは、scripted scenario に反転する topic preference cycle が含まれ、文面上の topic 参照が 1 件だけ残る可能性を許容しているためである
- 失敗時は、まず `--keep-db` で DB を残し、同じ DB に対して `run_chat_replay_eval` を流して cycle 単位で確認する
