# memory_write_e2e 運用メモ

<!-- Block: Purpose -->
## 目的

- `write_memory -> refresh_preview -> embedding_sync` の長周期チェーンを、LLM なしの deterministic な scripted conversation で毎回再現できるようにする
- `summary`、`fact`、`reflection_note`、`long_mood_state`、`preference_memory`、`event_preview_cache`、`vec_items`、`event_links`、`event_threads`、`state_links`、`event_about_time`、`state_about_time` まで一連で壊れていないかを確認する
- 後続の `write_memory orchestration` 分離や `chat replay eval` の前に、保存系の confidence を先に上げる

<!-- Block: Command -->
## 実行コマンド

```bash
PYTHONPATH=src python3 -m otomekairo.boot.run_memory_write_e2e
```

JSON で取りたい場合:

```bash
PYTHONPATH=src python3 -m otomekairo.boot.run_memory_write_e2e --format json
```

生成された DB を残したい場合:

```bash
PYTHONPATH=src python3 -m otomekairo.boot.run_memory_write_e2e --keep-db
```

<!-- Block: Scenario -->
## シナリオ

- 6 cycle の scripted conversation を使う
- 1, 2 cycle では `complete_browse_task` 成功を重ねて `browse` / `web_search` の preference を確定側へ寄せる
- 3 cycle では `enqueue_browse_task` の `timeout` 失敗を入れて dislike と revoke を発生させる
- 4 cycle では `complete_browse_task` を 2 回成功させて like を restore/confirm し、dislike を revoke する
- 5 cycle では user の明示発話から `topic_keyword` の `展示 like` と `ホラー映画 dislike` を確定させる
- 6 cycle では `dialogue continuation` と `topic_keyword carryover` を含む follow-up 応答を保存する
- すべての cycle で assistant 応答も `external_response` event として保存される

<!-- Block: Success -->
## 期待結果

- `write_memory` は cycle 数と一致し、`refresh_preview` は `events` 件数と一致、`embedding_sync` は `write_memory + refresh_preview` 件数と一致する
- `memory_states` に `summary`、`fact`、`reflection_note`、`long_mood_state` が入る
- `preference_memory` は `action_type` / `observation_kind` に加えて `topic_keyword` も持ち、最終的に `confirmed >= 4`、`revoked >= 2` になる
- `event_preview_cache` 件数は `events` 件数と一致する
- `vec_items` に `event`、`memory_state`、`event_affect` の各 entity_type が入る
- `event_links`、`event_threads`、`state_links`、`event_about_time`、`state_about_time` が非ゼロで入る
- `event_links.label` は `reply_to`、`same_topic`、`continuation`、`caused_by` の 4 種が materialize される

<!-- Block: Role -->
## 位置づけ

- これは unit test ではなく、保存チェーン全体の deterministic verification tool である
- runtime loop や LLM を起動しなくても、`pending_input finalize -> memory_jobs drain` の実運用に近い流れを確認できる
- ここで壊れるなら、`write_memory` の設計変更を merge する前に止めるべきである
