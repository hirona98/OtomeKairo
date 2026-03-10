# retrieval eval 運用メモ

<!-- Block: Purpose -->
## 役割

- このメモは、`retrieval_runs` から直近の想起傾向を確認するための開発者向け運用メモである
- 正本ではなく、Phase 6 の初期実装を使うときの入口だけを置く

<!-- Block: Command -->
## 実行コマンド

```bash
PYTHONPATH=src python3 -m otomekairo.boot.run_retrieval_eval --limit 200
```

- JSON が必要なら `--format json` を付ける
- DB を切り替えるときは `--db-path /path/to/core.sqlite3` を付ける

<!-- Block: Output -->
## 何が見えるか

- `window`: 何件の `retrieval_runs` を、どの時刻範囲で集計したか
- `overview`: empty run 率、raw / merged / selector input / selected item の平均件数
- `selector`: `LLM` selector の返却率、採用率、duplicate、skip、reserve の平均
- `coverage`: `explicit_time`、`reply_chain` / `context_threads`、`relationship_items` の run 単位カバレッジ
- `top_*`: selected collector / reason / slot と selector input collector の上位件数

<!-- Block: Reading Guide -->
## 見方

- `empty_run_rate_percent` が高すぎる場合は、collector 追加より先に query / time hint / anchor 抽出を疑う
- `explicit_time_input_run_rate_percent` は高いのに `explicit_time_selected_run_rate_percent` が低い場合は、selector か slot 上限の圧縮を疑う
- `thread_input_run_rate_percent` は高いのに `thread_selected_run_rate_percent` が低い場合は、reply chain / context thread の重みづけを疑う
- `relationship_selected_run_rate_percent` が低すぎる場合は、対人記憶の recall が会話へ効いていない可能性がある

<!-- Block: Scope -->
## 今はまだ測らないもの

- 嗜好再現率そのもの
- 冗長注入率そのもの
- 人手ラベル付きの正解 recall

- これらは `retrieval_runs` だけでは確定できないので、次段の replay / annotation で扱う
