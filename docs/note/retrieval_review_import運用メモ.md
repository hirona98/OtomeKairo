# retrieval review import 運用メモ

<!-- Block: Purpose -->
## 役割

- このメモは、編集済み `retrieval_triage_report` を `quarantine_memory` へ取り込むための開発者向け運用メモである
- 誤想起や stale linkage を自動判定せず、人手確認後にだけ隔離ジョブを enqueue する

<!-- Block: Command -->
## 実行コマンド

```bash
PYTHONPATH=src python3 -m otomekairo.boot.run_retrieval_review_import --input /path/to/retrieval-triage-reviewed.json
```

- JSON で結果を見たいときは `--format json` を付ける
- DB を切り替えるときは `--db-path /path/to/core.sqlite3` を付ける

<!-- Block: Input -->
## 入力前提

- 入力は `retrieval_triage_report` の JSON を人手で編集したものとする
- import 対象にしたい packet は `annotation_template.review_status=\"confirmed\"` にする
- `annotation_template.reason_code` は、`misretrieval_confirmed`、`stale_linkage`、`manual_quarantine` のいずれかにする
- `annotation_template.reason_note` は空にしない
- `annotation_template.selected_targets[]` は、`candidate_targets[]` の部分集合にする
- `resolved_event_ids` が空の packet は import できない

<!-- Block: Output -->
## 何が起こるか

- `confirmed` packet ごとに 1 件の `quarantine_memory` job を enqueue する
- `selected_targets` は、`event` / `memory_state` だけをそのまま `targets` に落とす
- import 結果は、`source_cycle_id`、`reason_code`、`queued_cycle_id`、`job_ids` を返す

<!-- Block: Scope -->
## この CLI がやらないこと

- `confirmed` 以外の packet を自動補完しない
- review packet を DB に保存しない
- `quarantine_memory` の即時実行までは行わない
