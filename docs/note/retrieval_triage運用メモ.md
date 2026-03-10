# retrieval triage 運用メモ

<!-- Block: Purpose -->
## 役割

- このメモは、`retrieval_runs` から manual review 対象を抜き出すための開発者向け運用メモである
- `retrieval_eval` が全体傾向を見る入口であるのに対し、`retrieval_triage` は個別 run の中身を見る入口である
- 正本ではなく、Phase 6 の current 実装を使うときの入口だけを置く

<!-- Block: Command -->
## 実行コマンド

```bash
PYTHONPATH=src python3 -m otomekairo.boot.run_retrieval_triage --limit 200 --max-packets 20
```

- JSON が必要なら `--format json` を付ける
- flag がない run も見たいときは `--include-all` を付ける
- DB を切り替えるときは `--db-path /path/to/core.sqlite3` を付ける

<!-- Block: Output -->
## 何が見えるか

- `report_schema_version`: triage report の schema version
- `flagged_run_count`: review 候補として flag が立った run 数
- `triage_flag_counts`: flag ごとの件数
- `mode_counts`: `mode` ごとの run 件数
- `review_packets[]`: `cycle_id`、`queries`、flag、summary、selected / skipped / reserve の trace preview
- `review_packets[].annotation_template`: 次段の annotation へ持ち込むための固定 shape
- `annotation_template.candidate_targets[]`: `event` / `memory_state` と `source_trace` を持つ review 候補
- `annotation_template.selected_targets[]`: 実際に隔離対象として選んだ target 列

<!-- Block: Flag Meaning -->
## flag の意味

- `empty_selection`: 最終採用が 0 件
- `explicit_time_dropped`: `explicit_time` が selector input にあるのに最終採用へ残っていない
- `thread_dropped`: `reply_chain` / `context_threads` が input にあるのに最終採用へ残っていない
- `relationship_dropped`: `relationship_focus` 候補が input にあるのに `relationship_items` が 0 件
- `low_adopt_ratio`: selector input に対して採用率が低い
- `slot_pressure`: slot 上限で見送りが多い
- `reserve_heavy`: reserve 候補が多い
- `duplicate_heavy`: collector 重複で merge 圧縮が大きい

<!-- Block: Reading Guide -->
## 見方

- まず `retrieval_eval` で `mode` 単位の偏りを見る
- 次に `retrieval_triage` で、偏っている `mode` の review packet を読む
- `selected_items` と `reserve_items` を見比べて、「本来選ばれるべきだった候補」があるかを確認する
- `annotation_template.candidate_targets` は、その run を `quarantine_memory` へ繋ぐときの候補一覧として使う
- `annotation_template.review_status` を `confirmed` にし、`reason_code`、`reason_note`、`selected_targets` を埋めると import できる
- `explicit_time_dropped` や `thread_dropped` が多い run は、collector 不足ではなく selector 圧縮や slot 設計を疑う
- `duplicate_heavy` が多い run は、collector の重複寄与が大きすぎないかを見る

<!-- Block: Scope -->
## 今はまだやらないこと

- triage 結果を DB へ保存すること
- `misretrieval_confirmed` や `stale_linkage` を自動確定すること

- review した triage report の import と `quarantine_memory` enqueue は、current 実装で別 CLI に分離して扱う
