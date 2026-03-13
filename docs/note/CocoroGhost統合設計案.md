# CocoroGhost 統合完了メモ

<!-- Block: Purpose -->
## このメモの役割

- このファイルは、CocoroGhost の強みを OtomeKairo へ取り込んだ作業の履歴メモである
- current の設計判断を読む正本ではなく、統合で何を採用し、どこへ昇格したかを確認するために残す
- 現在の設計判断は `docs/10_目標アーキテクチャ.md`、`docs/30_システム設計.md`、`docs/31_ランタイム処理仕様.md`、`docs/32_記憶設計.md`、`docs/33_記憶ジョブ仕様.md`、`docs/34_SQLite論理スキーマ.md` を正本とする

<!-- Block: Final Result -->
## 統合で固定した結論

- OtomeKairo は chat-first に寄せず、runtime 中心設計を維持したまま会話記憶能力を強化した
- 想起は `retrieval planner -> candidate collectors -> LLM selector -> context assembler` の 4 段に固定した
- 会話入力は `recent_dialog`、`selected_memory_pack`、`stable_preferences`、`long_mood_state`、`reply_render_input`、`action_selection_context` の別断面で扱う
- `cognition planner` と `reply renderer` を分離し、返答生成は render 専用断面で行う
- `write_memory` は `generate -> validate -> apply` の orchestration を分離し、`refresh_preview`、`embedding_sync`、`quarantine_memory`、`tidy_memory` を followup job として扱う
- retrieval の観測と review は `retrieval_runs`、`retrieval_eval`、`retrieval_triage`、`retrieval_review_import` に固定した
- 会話品質の deterministic gate は `memory_write_e2e`、`chat_replay_eval`、`chat_behavior_golden`、`eval_gate` で回す

<!-- Block: Promoted Docs -->
## 正本へ昇格した更新先

- `docs/10_目標アーキテクチャ.md`
  - 会話記憶統合の固定方針
  - runtime 中心、single writer、no fallback の原則
- `docs/30_システム設計.md`
  - retrieval planner / collectors / selector の責務
  - chat memory subsystem と `reply renderer` 分離
  - `run_write_memory_job.py` を中心にした orchestration
- `docs/31_ランタイム処理仕様.md`
  - `browser_chat` の短周期手順
  - `reply_render_input` / `action_selection_context` / `retrieval_runs` の保存順
- `docs/32_記憶設計.md`
  - `retrieval_runs`、preview cache、`long_mood_state`、`preference_memory`、graph enriched memory
- `docs/33_記憶ジョブ仕様.md`
  - `write_memory`、`refresh_preview`、`embedding_sync`、`quarantine_memory`、`tidy_memory`
- `docs/34_SQLite論理スキーマ.md`
  - `retrieval_runs` と side table 群の責務

<!-- Block: Closed Phases -->
## 閉じた作業

- Phase 1: retrieval 全面置換
  - collector 群、`LLM selector`、`event_preview_cache`、`retrieval_runs`
- Phase 2: context assembly 再設計
  - `recent_dialog`、`selected_memory_pack`、`stable_self_state`、`stable_preferences`、`long_mood_state`
- Phase 3: cognition と reply の分離
  - `reply_render_input` と `reply_render_plan`
- Phase 4: write_memory 強化
  - `generate -> validate -> apply` と side table 更新
- Phase 5: affect / preference / graph 完成
  - `long_mood_state`、preference lifecycle、dialogue thread continuity
- Phase 6: tidy と評価
  - `retrieval_eval`、`retrieval_triage`、`retrieval_review_import`、`chat_behavior_golden`、`eval_gate`

<!-- Block: Use Rule -->
## いまの使い方

- 現在の仕様や実装を決めるときは、このメモを読まずに正本 docs を読む
- 過去に何を置き換えたか、どの観点を採用したかを追うときだけ、このメモを参照する
- 追加の比較検討が必要なら、新しい一時メモを `docs/note/` に作り、正本 docs へ直接混ぜない
