# retrieval eval 運用メモ

<!-- Block: Purpose -->
## 役割

- このメモは、`retrieval_runs` から直近の想起傾向を確認するための開発者向け運用メモである
- 正本ではなく、Phase 6 の current 実装を使うときの入口だけを置く

<!-- Block: Command -->
## 実行コマンド

```bash
PYTHONPATH=src python3 -m otomekairo.boot.run_retrieval_eval --limit 200
```

- JSON が必要なら `--format json` を付ける
- DB を切り替えるときは `--db-path /path/to/core.sqlite3` を付ける

<!-- Block: Output -->
## 何が見えるか

- `report_schema_version`: JSON report の schema version
- `window`: 何件の `retrieval_runs` を、どの時刻範囲で集計したか
- `overview`: empty run 率、raw / merged / selector input / selected item の平均件数
- `selector`: `LLM` selector の返却率、採用率、duplicate、skip、reserve の平均
- `coverage`: `explicit_time`、`reply_chain` / `context_threads`、`relationship_items` の run 単位カバレッジ
- `preference`: preference 候補が input に入り、最終採用まで残った割合
- `redundancy`: `recent_event_window` と長期記憶の text overlap による冗長注入の疑い件数
- `top_*`: selected collector / reason / slot と selector input collector の上位件数
- `mode_names`: `mode_breakdown` を読む順序
- `mode_breakdown`: `mode` ごとに `window / overview / selector / coverage / top_*` を同じ shape で持つ

<!-- Block: Reading Guide -->
## 見方

- まず global の `overview / selector / coverage` で全体の偏りを見る
- 次に `mode_breakdown` で `explicit_about_time`、`reflection_recall`、`task_targeted`、`associative_recent` のどこで崩れているかを切り分ける
- `empty_run_rate_percent` が高すぎる場合は、collector 追加より先に query / time hint / anchor 抽出を疑う
- `explicit_time_input_run_rate_percent` は高いのに `explicit_time_selected_run_rate_percent` が低い場合は、selector か slot 上限の圧縮を疑う
- `thread_input_run_rate_percent` は高いのに `thread_selected_run_rate_percent` が低い場合は、reply chain / context thread の重みづけを疑う
- `relationship_selected_run_rate_percent` が低すぎる場合は、対人記憶の recall が会話へ効いていない可能性がある
- `preference_input_run_count` はあるのに `preference_carryover_rate_percent` が低い場合は、preference が会話文脈へ効く前に selector や slot 上限で落ちている
- `redundant_selected_run_rate_percent` が高い場合は、`recent_event_window` と長期記憶の text 重複を抑える調整を先に見る
- `task_targeted` だけ empty が高いなら task anchor 系、`explicit_about_time` だけ explicit selected が低いなら time recall か selector 圧縮を先に疑う

<!-- Block: Scope -->
## 今はまだ測らないもの

- 人手ラベル付きの正解 recall

- 人手ラベル付きの正解 recall は `retrieval_runs` だけでは確定できないので、review / annotation を併用して扱う
