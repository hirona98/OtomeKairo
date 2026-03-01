# 作業開始インデックス

<!-- Block: Mission -->
## 最重要目的

- このプロジェクトの目的は、LLMを使用して **人間のように常時稼働する人格中枢を再現すること** である
- 対象はチャットボットではなく、五感を持ち、移動し、外界を観測し、必要に応じて行動できるアンドロイドやロボットに近い存在の中心部分である
- 会話は入出力チャネルの 1 つにすぎず、中心機能ではない
- インターネットアクセスや外部システム利用も、外界へ働きかける正式な行動手段として扱う
- 感覚器の数や処理性能は人間と同一でなくてよく、重要なのは「それらをどう人格として統合し、どう判断し、どう振る舞うか」である
- 実装や設計を読むときは、「その変更が人間らしい観測・判断・行動・記憶の再現にどう効くか」を最優先で判断する

<!-- Block: Purpose -->
## このドキュメントについて
- このドキュメントは **毎回の作業開始時に最初に読む**「索引」
- 詳細な説明は重複させない（必要になったらリンク先を読む）

<!-- Block: Always Read -->
## 毎回読む（最短で現在地に戻る）

- `README.md`: リポジトリの入口
- `docs/00_index.md`: 現在の導線と参照先
- `docs/10_target_architecture.md`: 常時稼働する人格コアの構成と責務境界
- `docs/20_external_interfaces.md`: 決定済みの外部インタフェースと技術選定
- `docs/30_design_breakdown.md`: 実装単位まで分解した詳細設計

<!-- Block: Next Reads -->
## 作業タイプ別: 次に開く

- 構成設計を考えるとき: `docs/10_target_architecture.md`
- 外部接続や採用技術を考えるとき: `docs/20_external_interfaces.md`
- 詳細設計を詰めるとき: `docs/30_design_breakdown.md`
- 実装直前の処理契約を詰めるとき: `docs/31_runtime_detail.md`
- 記憶設計を詰めるとき: `docs/32_memory_detail.md`
- `memory_jobs` の payload 契約を詰めるとき: `docs/33_memory_job_contracts.md`
- 自律行動設計を詰めるとき: `docs/10_target_architecture.md` と `docs/30_design_breakdown.md` と `docs/31_runtime_detail.md`
- 採用済みの設計原則を確認するとき: `docs/10_target_architecture.md` と `docs/30_design_breakdown.md` と `docs/31_runtime_detail.md` と `docs/32_memory_detail.md` と `docs/33_memory_job_contracts.md`
- 記憶設計の背景判断を確認するとき: `docs/note/記憶設計に関する先行研究のメモ.md`
- 自律行動設計の背景判断を確認するとき: `docs/note/自律行動システムの先行研究メモ.md`
- 実装を始めるとき: `docs/10_target_architecture.md` と `docs/30_design_breakdown.md` と `docs/31_runtime_detail.md` と `docs/32_memory_detail.md` と `docs/33_memory_job_contracts.md`
- ドキュメントを直すとき: この `docs/00_index.md` と `docs/10_target_architecture.md` と `docs/20_external_interfaces.md` と `docs/30_design_breakdown.md` と `docs/31_runtime_detail.md` と `docs/32_memory_detail.md` と `docs/33_memory_job_contracts.md`

<!-- Block: Notes -->
## 参考メモ

- 一時的な情報、比較検討、参考資料、最終設計書に不要な補足は `docs/note/` に置く
- 研究メモは比較検討用であり、正本は `docs/10`、`docs/30`、`docs/31`、`docs/32`、`docs/33` だけで読める状態を維持する
- 記憶設計の研究メモ: `docs/note/記憶設計に関する先行研究のメモ.md`
- 自律行動システムの研究メモ: `docs/note/自律行動システムの先行研究メモ.md`
- 類似システムの参考フロー図: `docs/note/CocoroGhost_システムフロー図.md`

<!-- Block: Maintenance -->
## 更新ルール（重要）

- リポ構成/入口/主要導線が変わったら、この `docs/00_index.md` を更新する
- 目標構成/責務分割/永続化方針が変わったら `docs/10_target_architecture.md` を更新する
- 外部インタフェース/採用技術/接続先が変わったら `docs/20_external_interfaces.md` を更新する
- 実装単位/処理順序/状態境界が変わったら `docs/30_design_breakdown.md` を更新する
- 実行単位/入出力契約/保存順序の細部が変わったら `docs/31_runtime_detail.md` を更新する
- 記憶の想起/更新/保存設計が変わったら `docs/32_memory_detail.md` を更新する
- `memory_jobs` の payload 契約や job ごとの責務が変わったら `docs/33_memory_job_contracts.md` を更新する
- 参考メモや一時資料を追加するときは `docs/note/` に置く
