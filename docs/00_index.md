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
- `docs/10_目標アーキテクチャ.md`: 常時稼働する人格コアの構成と責務境界
- `docs/20_外部インタフェース.md`: 決定済みの外部インタフェースと技術選定
- `docs/30_システム設計.md`: 実装単位まで分解したシステム設計

<!-- Block: Next Reads -->
## 正本の役割分担

- `docs/10_目標アーキテクチャ.md`: 何を目指すか、どの責務に分けるか
- `docs/20_外部インタフェース.md`: どの外部面と接続するか、採用技術は何か
- `docs/30_システム設計.md`: どのモジュールが何を担当するか
- `docs/31_ランタイム処理仕様.md`: ランタイムがどの順で何を受け渡し、どう保存するか
- `docs/32_記憶設計.md`: 記憶をどう想起し、どう更新し、どう意味づけるか
- `docs/33_記憶ジョブ仕様.md`: `memory_jobs` の payload と job ごとの責務
- `docs/34_SQLite論理スキーマ.md`: 何をどの保存単位で持つか
- `docs/35_WebAPI仕様.md`: HTTP path、method、受付条件、主要応答
- `docs/36_JSONデータ仕様.md`: JSON のキー、型、必須項目、固定語彙
- `docs/37_起動初期化仕様.md`: seed、schema version、起動前提、排他起動
- `docs/38_入力ストリーム運用仕様.md`: 入力重複、`cancel`、`SSE` 再接続、保持期間
- `docs/39_設定キー運用仕様.md`: scalar 設定キーの一覧、型、`apply_scope`
- `docs/40_人格変化仕様.md`: 経験から人格がどう変わるか
- `docs/41_人格選択仕様.md`: 人格に基づいて何を選ぶか
- `docs/42_設定UI仕様.md`: 設定 UI の保存モデルと編集フロー
- `docs/43_開発者設定仕様.md`: `config/developer.toml` の固定 schema

<!-- Block: Reading Paths -->
## 作業タイプ別の最短導線

- 構成設計を考えるとき: `docs/10_目標アーキテクチャ.md` -> `docs/30_システム設計.md`
- ランタイム処理を直すとき: `docs/31_ランタイム処理仕様.md` -> `docs/36_JSONデータ仕様.md`
- 記憶を直すとき: `docs/32_記憶設計.md` -> `docs/33_記憶ジョブ仕様.md` -> `docs/34_SQLite論理スキーマ.md`
- API を直すとき: `docs/35_WebAPI仕様.md` -> `docs/36_JSONデータ仕様.md` -> `docs/38_入力ストリーム運用仕様.md`
- 設定を直すとき: `docs/39_設定キー運用仕様.md` -> `docs/42_設定UI仕様.md` -> `docs/43_開発者設定仕様.md`
- 人格を直すとき: `docs/40_人格変化仕様.md` -> `docs/41_人格選択仕様.md` -> `docs/31_ランタイム処理仕様.md`
- 起動や DB 初期化を直すとき: `docs/37_起動初期化仕様.md` -> `docs/34_SQLite論理スキーマ.md`
- ブラウザUIを直すとき: `src/otomekairo/web/static/` -> `docs/35_WebAPI仕様.md` -> `docs/42_設定UI仕様.md`
- 設定既定値を見るとき: `config/default_settings.json`
- 開発者用起動設定を見るとき: `config/developer.toml`
- 実際の初期 SQL を見るとき: `sql/core_schema.sql`
- 最短で起動するとき: `./run_otomekairo.sh`
- VSCode の `F5` で起動するとき: `.vscode/launch.json` の `OtomeKairo`

<!-- Block: Documentation Rules -->
## ドキュメント整理ルール

- 1 つの事実は 1 つの正本だけで固定し、他の文書では意味だけを書いて shape や path を重複させない
- API の意味と HTTP 挙動は `docs/35_WebAPI仕様.md` に書き、JSON のキーや型は `docs/36_JSONデータ仕様.md` に書く
- ランタイムの処理順、保存順、判断材料の意味は `docs/31_ランタイム処理仕様.md` に書き、テーブルやカラムの物理名は `docs/34_SQLite論理スキーマ.md` に書く
- 入力重複、`cancel`、`SSE` の保持と再接続は `docs/38_入力ストリーム運用仕様.md` に書き、個別 API 節へ同じ規則を重ね書きしない
- 設定キーの一覧と型は `docs/39_設定キー運用仕様.md` に書き、設定 UI の画面都合は `docs/42_設定UI仕様.md` に分ける
- 実装済みでも、将来の判断に効かない一時的な補足、比較検討、運用メモは `docs/note/` へ逃がし、正本へ残しすぎない

<!-- Block: Notes -->
## 参考メモ

- 一時的な情報、比較検討、参考資料、最終設計書に不要な補足は `docs/note/` に置く
- 研究メモは比較検討用であり、正本は `docs/10`、`docs/30`、`docs/31`、`docs/32`、`docs/33`、`docs/34`、`docs/35`、`docs/36`、`docs/37`、`docs/38`、`docs/39`、`docs/40`、`docs/41`、`docs/42` だけで読める状態を維持する
- 設定UIの目標仕様は `docs/42_設定UI仕様.md` を正本とする
- 記憶設計の研究メモ: `docs/note/記憶設計に関する先行研究のメモ.md`
- 自律行動システムの研究メモ: `docs/note/自律行動システムの先行研究メモ.md`
- 類似システムの参考フロー図: `docs/note/ココロゴースト_システムフロー図.md`
- CocoroGhost 統合の完了履歴メモ: `docs/note/CocoroGhost統合設計案.md`
- `write_memory -> refresh_preview -> embedding_sync` の deterministic e2e 運用メモ: `docs/note/memory_write_e2e運用メモ.md`
- chat cycle を replay して継続性を測る運用メモ: `docs/note/chat_replay_eval運用メモ.md`
- deterministic な会話品質 golden pack の運用メモ: `docs/note/chat_behavior_golden運用メモ.md`
- merge 前の deterministic eval gate の運用メモ: `docs/note/eval_gate運用メモ.md`
- retrieval_runs から直近傾向を確認する運用メモ: `docs/note/retrieval_eval運用メモ.md`
- retrieval_runs の review packet を確認する運用メモ: `docs/note/retrieval_triage運用メモ.md`
- review 済み triage report を quarantine_memory へ取り込む運用メモ: `docs/note/retrieval_review_import運用メモ.md`

<!-- Block: Maintenance -->
## 更新ルール（重要）

- リポ構成/入口/主要導線が変わったら、この `docs/00_index.md` を更新する
- 目標構成/責務分割/永続化方針が変わったら `docs/10_目標アーキテクチャ.md` を更新する
- 外部インタフェース/採用技術/接続先が変わったら `docs/20_外部インタフェース.md` を更新する
- 実装単位/処理順序/状態境界が変わったら `docs/30_システム設計.md` を更新する
- 実行単位/入出力仕様/保存順序の細部が変わったら `docs/31_ランタイム処理仕様.md` を更新する
- 記憶の想起/更新/保存設計が変わったら `docs/32_記憶設計.md` を更新する
- `memory_jobs` の payload 仕様や job ごとの責務が変わったら `docs/33_記憶ジョブ仕様.md` を更新する
- SQLite のテーブル名/主キー/主要制約が変わったら `docs/34_SQLite論理スキーマ.md` を更新する
- Web API の path/JSON/SSE 仕様が変わったら `docs/35_WebAPI仕様.md` を更新する
- JSON のキー/型/必須項目が変わったら `docs/36_JSONデータ仕様.md` を更新する
- 起動順、初回 seed、DB 版、排他起動が変わったら `docs/37_起動初期化仕様.md` を更新する
- 入力重複、`cancel`、`SSE` 保持運用が変わったら `docs/38_入力ストリーム運用仕様.md` を更新する
- 設定キー、型制約、`apply_scope` の許可集合が変わったら `docs/39_設定キー運用仕様.md` を更新する
- 設定既定値を変更したら `config/default_settings.json` と `docs/35_WebAPI仕様.md` と `docs/37_起動初期化仕様.md` を更新する
- 人格の可変傾向、経験からの人格変化が変わったら `docs/40_人格変化仕様.md` を更新する
- 人格に基づく選択、hard gate、soft score、行動種別ごとの選び方が変わったら `docs/41_人格選択仕様.md` を更新する
- 設定UIの保存モデル、プリセット、編集フローが変わったら `docs/42_設定UI仕様.md` を更新する
- 開発者用起動設定の schema や適用先が変わったら `config/developer.toml` と `docs/43_開発者設定仕様.md` と `docs/37_起動初期化仕様.md` を更新する
- 初期 SQL 実装を変更したら `sql/core_schema.sql` と `docs/34_SQLite論理スキーマ.md` を両方更新する
- 参考メモや一時資料を追加するときは `docs/note/` に置く
