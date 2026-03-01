# 自律行動システムの研究メモ

<!-- Block: Purpose -->
## このドキュメントの役割

- このドキュメントは、`人間に近い存在` や `アンドロイド / ロボット寄りの人格個体` を作るときに効く先行研究の比較メモである
- 記憶設計に限定せず、観測、判断、行動、身体性、自己改善、安全制約まで含めて整理する
- 最終設計に直接書き込む前の比較検討や参考資料は、この `docs/note/` に置く
- 構成の正本は `docs/10_target_architecture.md`、詳細設計の正本は `docs/30_design_breakdown.md` とする

<!-- Block: High Level -->
## 先に結論

- 現在の先行研究には、`人間に近い存在` を単独で完成させる決定版はない
- 実際に使えるのは、`認知ループ`、`スキル再利用`、`身体への接地`、`外界フィードバック`、`安全制御` を別々の研究から統合するやり方である
- 特に有効なのは、`高位の意図形成` と `低位の実行制御` を分離する設計である
- 逆に、LLM に直接すべてを任せる end-to-end 志向は、現時点では brittle で、実世界システムには不向きである
- OtomeKairo では、`人格ランタイムが状態の正本を持ち、LLM は認知補助にとどめる` 方針が、先行研究と比較しても妥当である

<!-- Block: Main Buckets -->
## 研究の主要カテゴリ

- `認知アーキテクチャ`: 知覚、注意、行動選択、記憶、学習、推論の全体構造を扱う
- `LLMエージェント`: 推論、ツール利用、反省、自己改善を扱う
- `Embodied LLM / ロボット`: 視覚、状態、言語を結び、実行可能な行動へ落とす
- `実世界統合`: 複数モジュールを接続して、現実のセンサーと行動器で動かす
- `安全制御`: 自律行動が暴走しないための優先度や権限分離を扱う

<!-- Block: Cognitive Architecture -->
## 認知アーキテクチャ系

- `A Review of 40 Years of Cognitive Architecture Research`
  - 参照: https://arxiv.org/abs/1610.08602
  - 84 の認知アーキテクチャを広く整理したレビューで、共通コアとして `perception`, `attention`, `action selection`, `memory`, `learning`, `reasoning` を扱う
  - OtomeKairo に直接効くのは、`記憶だけでは不十分で、注意と行動選択が同格に必要` という整理である
  - 設計示唆:
    - `memory` と同じ強さで `attention` と `action selection` を独立した責務にする
    - `観測 -> 注意配分 -> 行動選択 -> 実行 -> 学習` の骨格を明示する
    - 単なる会話ループではなく、認知アーキテクチャとしての全体構造を保つ

<!-- Block: Reasoning Agents -->
## 推論と自己改善のエージェント系

- `ReAct`
  - 参照: https://arxiv.org/abs/2210.03629
  - 推論と行動を交互に回す設計で、`reasoning traces` と `actions` をインタリーブする
  - OtomeKairo に効くのは、`考えるだけ` でも `動くだけ` でもなく、`考えながら動き、動いた結果で考えを更新する` という骨格である
  - 設計示唆:
    - `decide -> act -> observe` を 1 サイクルに固定する
    - 行動前の根拠と、行動後の更新理由を内部表現として残す

- `Reflexion`
  - 参照: https://arxiv.org/abs/2303.11366
  - 試行錯誤の結果を、重み更新ではなく言語的な反省として次回へ持ち越す
  - OtomeKairo に効くのは、`反省` を独立した状態更新として持つこと
  - 設計示唆:
    - 長周期ループに `reflection_notes` を追加する
    - 失敗時に `retry_hint` や `avoid_pattern` を残す

- `Generative Agents`
  - 参照: https://arxiv.org/abs/2304.03442
  - `observation`, `planning`, `reflection` を持つ人間らしい行動シミュレーション
  - OtomeKairo に効くのは、記憶の `蓄積 -> 反省 -> 未来計画` の流れである
  - 設計示唆:
    - 出来事ログから高次の反省を生成する
    - 計画は都度生成だけでなく、次の時刻へ持ち越す

- `Voyager`
  - 参照: https://arxiv.org/abs/2305.16291
  - 自動カリキュラム、スキルライブラリ、実行フィードバックを使う lifelong embodied agent
  - OtomeKairo に効くのは、`一度成功した行動列をスキルとして再利用する` 発想である
  - 設計示唆:
    - `skill_registry` を `task_state` と別で持つ
    - 自発行動は、未探索や未完了を埋めるカリキュラムとして扱う

<!-- Block: Grounded Embodied Control -->
## 身体へ接地する制御系

- `Do As I Can, Not As I Say (SayCan)`
  - 参照: https://arxiv.org/abs/2204.01691
  - LLM が高位の手順知識を出し、実行可能性はスキルの価値関数で制約する
  - OtomeKairo に効くのは、`言語的にもっともらしい` と `その個体に実行可能` を分ける点である
  - 設計示唆:
    - LLM は高位の `action proposal` だけを出す
    - 実行可否は `policy` や `actuator` 側で検証する

- `Inner Monologue`
  - 参照: https://arxiv.org/abs/2207.05608
  - 実行中のフィードバックを自然言語的な内部独白として取り込み、計画を更新する
  - OtomeKairo に効くのは、外界フィードバックを 1 回の命令で終わらせず、継続的に取り込む点である
  - 設計示唆:
    - 行動中の観測変化を、次の判断材料にそのまま戻す
    - `実行中の内部状態更新` を前提にする

- `Code as Policies`
  - 参照: https://arxiv.org/abs/2209.07753
  - LLM に高位の方針コードを書かせ、知覚結果と制御プリミティブを接続する
  - OtomeKairo に効くのは、`自然言語 -> そのまま制御` ではなく、`自然言語 -> 手続き表現 -> 制御` へ落とす点である
  - 設計示唆:
    - `intention` と `action_command` の間に、手続き的な中間表現を置けるようにする
    - 低位のデバイス制御は決定論的に保つ

- `VoxPoser`
  - 参照: https://arxiv.org/abs/2307.05973
  - 言語指示を空間的な affordance と constraint に分解し、3D の価値地図で軌道化する
  - OtomeKairo に効くのは、空間的な判断を `言語のまま` 持たないこと
  - 設計示唆:
    - `world_state` に `affordances`, `constraints`, `attention_targets` を持たせる
    - 視点変更や移動は、空間状態を更新する正式な行動として扱う

<!-- Block: Multimodal Embodied Models -->
## マルチモーダル統合系

- `PaLM-E`
  - 参照: https://arxiv.org/abs/2303.03378
  - 連続センサー入力を言語モデルへ直接取り込む embodied language model
  - OtomeKairo に効くのは、`視覚`, `状態`, `言語` を同じ認知入力として扱う考え方である
  - ただし、巨大な end-to-end 学習そのものは、現時点の実装方針には重すぎる
  - 設計示唆:
    - `observation_frame` を最初からマルチモーダル前提で定義する
    - センサー入力種別と観測由来を必ず保持する

- `RT-2`
  - 参照: https://arxiv.org/abs/2307.15818
  - 行動をテキストトークンとして扱う vision-language-action model
  - OtomeKairo に効くのは、`行動を構造化した離散表現として扱う` という点である
  - 設計示唆:
    - `action_command` は自然文だけでなく、型付きコマンドとして持つ
    - 発話も移動も検索も、同じ行動フレームで表現する

<!-- Block: Practical Robotics -->
## 実世界統合とシステム設計

- `OK-Robot`
  - 参照: https://arxiv.org/abs/2401.12202
  - 認識、ナビゲーション、把持など既存モジュールを、systems-first で統合する実践研究
  - OtomeKairo に効くのは、`各モジュールが強くても、組み方が悪いと全体が壊れる` という知見である
  - 設計示唆:
    - `gateway` を統合点として明示する
    - 各外部インタフェースを人格コアから直接呼ばない
    - 失敗モードをイベントとして記録し、統合の癖を学習対象にする

<!-- Block: Evaluation and Limits -->
## 能力の限界と評価系

- `PlanBench`
  - 参照: https://arxiv.org/abs/2206.10498
  - LLM の計画能力を、常識問題ではなく自動計画ドメインで評価するベンチマーク
  - 論文は、SOTA でも重要な計画能力では性能が不十分だと報告している
  - OtomeKairo に効くのは、`LLM は計画の万能器ではない` という設計上の警告である
  - 設計示唆:
    - 長い計画の正しさは、LLM 出力だけで信用しない
    - タスクは短い実行単位に分解し、都度観測で補正する

<!-- Block: Safety -->
## 安全制御と権限分離

- `The Instruction Hierarchy`
  - 参照: https://arxiv.org/abs/2404.13208
  - システム指示、ユーザー入力、外部ツール出力の優先順位を分け、低優先度入力に上書きされにくくする
  - OtomeKairo に効くのは、`外界から入ったテキスト` と `人格の中核方針` を同格にしないこと
  - 設計示唆:
    - `system policy > runtime policy > user input > tool output` のような優先順位を明示する
    - Web 入力、SNS 入力、Web 検索結果は、上位ポリシーを上書きできない

<!-- Block: Synthesis -->
## OtomeKairo に取り込むべき設計要素

- `認知ループ`
  - `observe -> attend -> decide -> act -> reflect -> consolidate` を明示する
- `高位と低位の分離`
  - LLM は意図、候補、要約、反省まで
  - 実行可否、デバイス制御、保存は決定論的モジュールで行う
- `スキル化`
  - 成功した反復行動は、単発イベントではなく再利用可能スキルへ昇格させる
- `空間化`
  - `world_state` に位置、見える対象、近づける対象、避ける対象を持つ
- `マルチモーダル化`
  - 観測をテキスト前提で持たず、モダリティ付きの共通フレームに統一する
- `安全制御`
  - 外部入力はすべて低優先度化し、中核ポリシーを直接上書きさせない
- `逐次補正`
  - 長い計画を一括で信じず、短い行動単位で観測と補正を挟む

<!-- Block: Things To Avoid -->
## 避けるべき設計

- LLM に直接デバイス制御をさせる
- 1 回の長い計画をそのまま最後まで実行する
- テキスト入力だけを「本物の入力」とみなす
- 記憶だけを強化して、注意や行動選択を弱くする
- 外部 API の応答で中核ポリシーを書き換える

<!-- Block: Final Judgment -->
## 最終判断

- `人間に近い存在` を作るには、`記憶` だけでなく `注意`, `行動選択`, `スキル`, `身体への接地`, `反省`, `安全制御` が必要である
- 現時点の研究では、`SayCan`, `Inner Monologue`, `ReAct`, `Reflexion`, `Voyager`, `Code as Policies`, `PaLM-E`, `RT-2`, `VoxPoser`, `OK-Robot` を組み合わせて読むのが実践的である
- OtomeKairo の現在方針である `人格ランタイムが状態の正本を持ち、LLM は認知補助にとどめる` は、先行研究との比較でも妥当である
- 次に詳細設計へ反映すべきなのは、`attention`, `reflection`, `skill_registry`, `affordance-aware world_state`, `instruction priority` の 5 点である
