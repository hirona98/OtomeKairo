# LLM 呼び出しシーケンス

この文書は、主要な LLM 呼び出しを実装から追うための見取り図である。
内部フローはコードを正とし、意味境界、入出力契約、失敗条件の詳細は各節の設計文書を参照する。
図は実 LLM を利用する経路を示す。

## 図の読み方とモデルの境界

- `LLMClient` への矢印はアプリ内の論理処理呼び出しを示す。1 回の論理処理が、生成、審査、修復のために複数回 API を呼ぶことがある。
- `通常生成API`、`送信前チェックAPI`、`Embedding API` への矢印は、実際のモデル呼び出しを示す。応答矢印は必要な箇所だけ記載する。
- 入力や処理条件の説明は本文に記し、他の図への参照は `［図 N］` で示す。
- `opt` は条件付き処理、`alt` は分岐、`loop` は繰り返し、`par` は独立して進む処理、開いた矢印は非同期の要求・通知を示す。

| 呼び出し先 | 設定の参照先 |
| --- | --- |
| 通常生成API | 選択中の `selected_model_preset_id` が指すモデルプリセット。解釈、判断、表現、審査、記憶、視覚で共有する |
| 送信前チェックAPI | `pre_send_check_model_preset_id` が指す専用プリセット |
| Embedding API | 選択中の記憶集合の `memory_set.embedding` |

## 図 1: 通常の判断サイクル

入口は人物発話を受ける `handle_conversation()`。
入力解釈や想起などは `LLMClient` への論理呼び出しで示し、行動・共有範囲・最終本文の生成は API 呼び出しまで示す。

```mermaid
sequenceDiagram
    participant U as 外部接点
    participant C as 判断サイクル
    participant R as 想起
    participant D as DB
    participant L as LLMClient
    participant G as 通常生成API
    participant E as Embedding API
    participant M as 記憶統合

    U->>C: 人物発話
    C->>D: 直近会話を取得（events）
    D-->>C: 直近会話
    opt 思考前観測または添付画像がある
        C->>C: 観測と画像解釈［図 6］
    end
    C->>L: 人格・会話などを参照して入力を解釈（generate_input_interpretation）
    L-->>C: 検索すべき内容（recall_hint）と回答に必要な根拠の条件（answer_contract）
    C->>R: 想起パックを構築（build_recall_pack）
    R->>D: 適用範囲別の記憶・エピソード候補を取得（memory_units・episodes）
    D-->>R: 記憶・エピソード候補
    opt 関連検索のクエリがある
        R->>E: クエリ群の埋め込み
        E-->>R: 検索用ベクトル
        R->>D: 記憶・エピソードをベクトル検索
        D-->>R: 関連候補
    end
    R->>D: 人物・対象のつながりを手がかりに記憶候補を取得（relation_index）
    D-->>R: 関係索引の記憶候補
    R->>R: 候補を統合
    R->>D: 同じ対象・項目についての別の記憶を取得（memory_units）
    D-->>R: 同じ対象についての記憶
    R->>R: 競合を検出
    R->>D: 候補の記憶について、支持・矛盾・由来などの関係情報を取得（memory_links）
    D-->>R: 候補の関係情報
    R->>R: 選別用の入力を構築（source_pack）
    opt 想起候補または競合がある
        R->>L: 想起候補を選別し、採用順を決定（generate_recall_pack_selection）
        L-->>R: 採用・配置する候補
    end
    R->>D: 採用した記憶について、支持・矛盾・由来などの関係情報を取得（memory_links）
    D-->>R: 選定済み記憶の関係情報
    opt 記憶の更新元報告を確認する必要がある
        R->>D: 更新元のイベントをIDで取得（load_events_for_evidence）
        D-->>R: 更新元の報告本文と出所（events）
    end
    opt イベントの根拠を必要とし、選択済みのイベントがある
        R->>R: 根拠のイベントIDを選定：標準分と追加確認分
        R->>D: 選定したイベントをIDで取得（load_events_for_evidence）
        D-->>R: イベント本文・判断理由・出所（events）
        R->>R: 項目の型を確認し、本文・判断理由・出所を想起パックへ追加
    end
    R->>D: 視覚観測と日次要約を取得
    D-->>R: 視覚観測と日次要約（visual_observation_records・daily_visual_digests）
    R-->>C: 想起パック（RecallPack）
    C->>C: 回答根拠を解決
    opt 空でない人物発話がある
        C->>L: 世界状態の更新に使う根拠を選別（generate_world_state_source_selection）
    end
    opt 世界状態の生成対象がある（world_state）
        C->>L: 世界状態の候補と根拠判定を生成（generate_world_state）
    end
    opt 活動状態の生成対象がある（activity_state）
        C->>L: 活動状態の候補と根拠判定を生成（generate_activity_state）
    end
    C->>C: 状態・人物・関係・作業領域の文脈を構築
    opt Skill一覧がある
        C->>L: 使用するSkillを選別（generate_agent_skill_selection）
        loop 選択Skillに未確認の関連Skill・資料があり、追加採用する間
            C->>L: 追加で読むSkill・資料を選別（generate_agent_skill_material_selection）
            C->>C: 選択資料の読込と文脈への追加
        end
    end
    C->>C: 判断材料の参加者IDを、現在の応答相手のIDと比較
    opt 判断材料に、現在の相手以外の人物に由来する情報が含まれる
        opt その情報に出所eventの参照がある
            C->>D: 出所eventをIDで取得（load_events_for_evidence）
            D-->>C: 元発話・話者・参加者・発生日時
            Note over C,D: 指定したeventが揃わなければ明示的に失敗
        end
        C->>C: 共有条件の確認資料を構築（disclosure_context）
    end
    C->>L: 行動と、伝えてよい最終本文を考える（generate_decision）
    L->>G: 行動・共有範囲・最終本文を生成（decision_generation）
    G-->>L: 行動と、発話する場合の本文（speech_text）
    L->>L: 出力契約・参照・能力の実行条件を検証
    L-->>C: 採用する判断と最終本文
    alt 発話を選ぶ
        C->>C: 判断に含まれる最終本文を配送
    else 能力実行を選ぶ（capability_request）
        C->>C: 能力の実行境界へ要求を渡す［図 2］
    else 自律作業を選ぶ（autonomous_run）
        C->>C: 自律作業の開始審査と最初の一手［図 4］
    else 保留または何もしない（pending_intent・noop）
        C->>C: 判断結果を保持
    end
    C->>D: サイクルのイベントと追跡情報を保存
    C->>M: 会話の記憶を統合（consolidate_turn）［図 5］
    M-->>C: 統合結果と後処理ジョブ
    C-)M: 後処理ジョブをキューへ登録
    C-->>U: サイクル結果
```

`generate_input_interpretation()` は、記憶検索に先立って「何を思い出すか」「どんな根拠を探すか」を決める入口処理である。正常時は1回の LLM 呼び出しで、次の2つをまとめて返す。

| 出力 | 役割と後続処理 |
| --- | --- |
| `recall_hint` | 想起する記憶の主題、人物・話題、時間軸、参照の曖昧さなどを整理し、後続の `build_recall_pack()` に渡す |
| `answer_contract` | 原文、日時、出所など必要な根拠の種類と、検索対象の人物・会話の範囲を指定し、RecallPack 構築後の回答根拠の解決に使う |

入力には、現在の入力本文と発信者・会話などの構造化情報、人格設定、直近会話、現在時刻を渡す。解釈済みの画像説明と活動状態がある場合は、内部補助文脈として渡す。入力の詳細と出力契約は [RecallHint](../memory/想起と判断.md#recallhint) と [AnswerContract](../memory/想起と判断.md#answercontract) を正とする。

例えば「前に私が言ったことを原文で教えて」という入力では、本人の過去の会話を想起の対象とし、本人の発話原文を必要な根拠として指定する。この段階では検索方針を決め、長期記憶の検索や原文取得は後続処理が行う。行動選択と返答本文の生成は、後段の `generate_decision()` がまとめて担当する。

`generate_decision()` は、行動選択、今の相手へ伝えてよい範囲、最終本文を1回の生成で決める。人物発話・別の場の会話・観測・記憶・共有条件は出所を分けて渡す。

共有条件の確認資料を用意する対象は、選定された記憶や参照会話の参加者IDと、現在の応答相手のIDを比較して決める。現在の相手以外の人物に由来する情報が含まれ、その情報に出所eventの参照があれば、生成前にDBから取得する。指定したeventが揃わない場合は明示的に失敗する。取得済みの会話と追加取得した本人発話を、対象の情報・出所とともに `disclosure_context` へまとめる。共有を許可する記述の有無や、今の相手へ伝えてよい範囲の意味判断は、最終本文を作る同じ生成が行う。

通常の成功経路で、発話生成や開示確認のための後続LLM呼び出しは行わない。出力契約違反時の1回のrepairは共通の構造化生成の規則に従う。
外部MCPへ送る要求の送信前チェック［図 2］は別の実行境界で行う。

実装: [入力サイクル](../../../src/otomekairo/service/input/cycle.py)、[共通パイプライン](../../../src/otomekairo/service/input/pipeline.py) の `_build_disclosure_context()` と `_run_pipeline_output()`、[想起構築](../../../src/otomekairo/recall/builder.py)、[Skill文脈](../../../src/otomekairo/service/agent_skills.py)、[LLMClient](../../../src/otomekairo/llm/client.py) の `generate_decision()`、[結果の保存](../../../src/otomekairo/service/input/trace_persist.py)。
契約: [想起と判断](../memory/想起と判断.md)、[RecallPackのLLM選別](../memory/RecallPackのLLM選別.md)、[event_evidenceの取得](../memory/event_evidenceの取得.md)、[状態候補根拠審査](状態候補根拠審査.md)、[AgentSkills統合](../integration/AgentSkills統合.md)、[判断と行動](../runtime/判断と行動.md)、[プロンプトの意味レイヤー](プロンプト文脈分離方針.md#意味レイヤー)、[外部本文の生成と根拠](外部本文の根拠照合.md)、[想起と判断の共有許可](../memory/想起と判断.md#共有許可と受け手の照合)。

## 図 2: 能力実行、送信前チェック、結果からの後続判断

入口は判断または自律 step が返した `capability_request`。
通常判断、能力結果からの後続判断、自律作業のいずれも同じ dispatch 境界を通る。
図の再判断は通常 cycle の経路を示す。

```mermaid
sequenceDiagram
    participant C as 判断サイクル
    participant B as 能力実行境界
    participant L as LLMClient
    participant P as 送信前チェックAPI
    participant X as Connector・能力実行先

    loop 初回候補と、送信見送り後に再候補が生まれた場合
        C->>B: 能力の実行要求を渡す（capability_request）
        B->>B: 能力定義・権限・接続・入力形式を検証
        opt MCPかつ送信前チェックが有効
            alt 既知の秘密値に一致
                B->>B: LLMを呼ばずに外部送信を見送る（withhold）
            else LLMによるチェックへ進む
                B->>L: 最終的な引数で送信前チェックを実行（generate_pre_send_check）
                L->>P: 送信前の可否を判定（pre_send_check）
                P-->>L: 送信許可（allow）または見送り（withhold）
                L-->>B: チェック結果
            end
        end
        alt 実行を許可できる
            B->>B: 実行要求と進行中の行動を保存（request・ongoing_action）
            B-)X: 実行要求を配送
            B-->>C: 実行要求の受付結果
        else 外部送信を見送る（withhold）
            B-->>C: 外部送信の見送り
            opt 最初の送信見送り
                C->>L: 固定の再判断理由を添えて行動を判断（generate_decision）［図 1］
                L-->>C: 同じ目的の別案、または何もしない判断（noop）
            end
            Note over C,B: 再候補も同じ境界へ。再度見送りなら送信せず終了
        else チェック生成・検証の失敗
            B-->>C: 明示失敗
            C->>C: サイクルを失敗として確定
        end
    end
    opt 実行結果が到着
        X-)C: 能力の実行結果（capability_result）
        C->>C: 結果の解釈・必要な画像処理
        alt 通常の能力要求
            C->>C: 共通パイプラインで後続判断［図 1］
        else 自律作業に属する能力要求（autonomous_run）
            C->>C: 状態と自律作業の履歴へ反映して次の一手へ［図 4］
        end
    end
```

送信前チェックでは専用モデルへ最終 payload を渡す。
自律作業の `withhold` 後は `generate_decision()` の代わりに step 候補を再生成し、再度見送りまたはチェック失敗になった場合の終了単位も通常 cycle と異なる。
具体的な入力境界、再判断、終了の規則は [送信前チェック](pre_send_check.md) を参照する。

実装: [能力実行境界](../../../src/otomekairo/service/capability.py)、[通常の能力結果サイクル](../../../src/otomekairo/service/spontaneous/capability_cycle.py)、[自律作業](../../../src/otomekairo/service/autonomous_run.py)。
契約: [実行連携](../api/実行連携.md)、[判断と行動の実行結果](../runtime/判断と行動.md#実行結果の扱い)。

## 図 3: 自律判断の入口と分離判断

入口は API の `wake` または定期思考の `background_thinking`。
時刻や候補の適格性はコードが扱い、候補の意味的な選別と判断を LLM が担当する。

```mermaid
sequenceDiagram
    participant W as 起床・定期思考
    participant C as 自律判断パイプライン
    participant O as 思考前観測
    participant L as LLMClient
    participant G as 通常生成API

    W->>C: 起床（wake）または定期思考（background_thinking）
    C->>C: 適用する起床条件を確認
    opt 評価可能な保留意図候補がある
        C->>L: 保留意図の候補を選別（generate_pending_intent_selection）
        L->>G: 保留意図の選別結果を生成（pending_intent_selection）
        G-->>L: 選別結果
        L-->>C: 保留意図候補1件、または該当なし（none）
    end
    opt 定期思考の観測が有効
        C->>O: 設定された思考前観測
        O->>O: 画像解釈と状態反映［図 6］
        O-->>C: 観測結果と更新済み文脈
    end
    opt 選択済み候補がなく、直接の入口もなく、入口判定の材料がある
        C->>L: 自律判断へ進む入口を審査（generate_initiative_entry_check）
        L->>G: 自律判断の入口判定を生成（initiative_entry_check）
        G-->>L: 入口判定
        L-->>C: 入口判定
    end
    alt 判断へ進む入口がある
        C->>C: 想起・状態・作業領域の文脈を構築［図 1］
        alt 自身の活動を分離して比較する
            C->>C: 隔離した活動用入力で想起とSkill文脈を構築
            C->>L: 自身の活動を判断（generate_decision / comparison_scope=self_activity）
            L->>G: 自身の活動の判断
            G-->>L: 判断候補
            L-->>C: 自身の活動の判断
            opt 自律作業を選んだ（autonomous_run）
                C->>L: 自身の活動の目的と主根拠を照合（generate_autonomous_activity_alignment_review）
                L->>G: 自身の活動の整合判定を生成（autonomous_activity_alignment_review）
                G-->>L: 整合審査結果
                L-->>C: 整合審査結果
                opt 目的と主根拠が不整合
                    C->>L: 自身の活動だけを再判断・必要なら再審査
                end
            end
            C->>L: 対人発話を判断（generate_decision / comparison_scope=outward_speech）
            L->>G: 対人発話を判断［図 1］
            C->>C: 両方の判断を合成して出力へ
        else 共通の判断を使う
            C->>L: 行動を判断（generate_decision）［図 1］
        end
        C->>C: 発話・能力実行・自律作業・保留［図 1・図 2・図 4］
    else 今回は入口がない、または実行境界で見送る
        C->>C: 何もしない判断を記録（noop）
    end
```

新しい有効な視覚観測などから直接判断へ入れる場合は `initiative_entry_check` の API 呼び出しを省く。
その視覚観測だけを入口にする経路では、入力解釈と記憶検索を通さず観測から直接判断用文脈を作る場合がある。
自身の活動を分離する場合は、活動用の想起・Skill 選択が追加される。処理順は自身の活動判断、その整合審査、対人発話判断、合成の順である。

実装: [起床サイクル](../../../src/otomekairo/service/spontaneous/wake.py)、[保留意図の選別](../../../src/otomekairo/service/spontaneous/pending_intent.py)、[起床パイプライン](../../../src/otomekairo/service/input/wake_pipeline.py)、[分離判断](../../../src/otomekairo/service/input/decision_comparison.py)。
契約: [自律initiative_loop](../runtime/自律initiative_loop.md)、[保留意図候補のLLM選別](保留意図候補のLLM選別.md)、[判断と行動](../runtime/判断と行動.md)、[プロンプト文脈分離方針](プロンプト文脈分離方針.md)。

## 図 4: 自律作業の開始、step、完了

入口は `kind=autonomous_run` の判断。
通常判断は目的を作り、以後の一手は `generate_autonomous_step()` が決める。

```mermaid
sequenceDiagram
    participant C as 判断サイクル
    participant A as 自律作業
    participant L as LLMClient
    participant G as 通常生成API
    participant X as 実行先・外部接点
    participant M as 記憶統合

    C->>A: 自律作業の開始候補（autonomous_run）
    A->>L: 自律作業の開始を審査（generate_autonomous_start_review）
    L->>G: 自律作業の開始判定を生成（autonomous_start_review）
    G-->>L: 開始判定
    L-->>A: 開始判定
    alt 開始を許可
        A->>A: 必要な既存作業の置換と新規作業の保存
        loop 初回、時刻到来、結果到着など、次の一手を判断する機会
            A->>A: 自律作業・現在状態・Skillの文脈を構築
            Note over A,L: Skill一覧と追加資料があればSkill選択APIも呼ぶ
            A->>L: 自律作業の次の一手を判断（generate_autonomous_step）
            L->>G: 次の一手・必要な入力・最終発話本文を生成（autonomous_step_generation）
            G-->>L: 次の一手の候補
            L-->>A: 今回の行動（action）と作業の状態遷移（transition）
            opt 能力実行以外で作業完了を候補にする（complete）
                A->>L: 自律作業の完了を審査（generate_autonomous_completion_review）
                L->>G: 自律作業の完了判定を生成（autonomous_completion_review）
                G-->>L: 完了可否
                L-->>A: 完了可否
                opt 継続が必要
                    A->>A: 審査理由を添えて次の一手から再生成・再審査
                end
            end
            alt 能力実行を選んだ（capability_request）
                A-)X: 同じ実行境界を通して要求［図 2］
                X-)A: 結果到着、状態と履歴へ反映
            else 発話を選んだ（speech）
                A-)X: 生成した発話を配送
            else 外向きの行動を選ばなかった（none）
                A->>A: 外向き出力なし
            end
            A->>A: 状態遷移を保存して継続・待機・終了（transition）
        end
        opt 自律作業が終了状態へ遷移
            A->>M: 紐付く約束の状態を更新（commitment）
            opt 更新対象の約束がある（commitment）
                M->>L: 約束の状態更新に使う要約を生成（generate_commitment_lifecycle_summaries）
                L->>G: 約束の状態更新に使う要約を生成
            end
            A->>M: 終了時のイベントを記憶に統合し、後処理を登録［図 5］
        end
    else 開始拒否または審査失敗
        A-->>C: 明示失敗
    end
```

step の文脈では保存済みの状態と run 履歴を読み、Skill の選択を行う。通常 cycle の入力解釈・RecallPack 構築を毎 step 実行する経路とは分かれている。
能力結果は run に対応づけて受け取り、必要な視覚解釈・`world_state` 更新を経て後続 step へ進む。
step の再生成は開始時と同じ文脈構築から行うため、Skill 選択や発話生成も再度発生しうる。
発話候補の完了審査は配送前に行う。

実装: [自律作業](../../../src/otomekairo/service/autonomous_run.py) の `_start_autonomous_run_from_decision()`、`_build_autonomous_step_context()`、`_generate_reviewed_autonomous_step_candidate()`、`_execute_autonomous_capability_result_cycle_inner()`、`_finalize_autonomous_run_commitments()`。
契約: [autonomous_run](../runtime/autonomous_run.md)、[送信前チェック](pre_send_check.md)、[音声出力](../audio/音声出力.md)。

## 図 5: 記憶統合と非同期後処理

入口は cycle 成功後の `consolidate_turn()`、または自律作業の終端からの記憶統合。
通常会話では記憶解釈と審査・保存までを同期実行し、後処理をキューへ登録してからサイクル結果を返す。

```mermaid
sequenceDiagram
    participant U as 外部接点
    participant C as 判断サイクル
    participant M as 記憶統合
    participant G as 通常生成API
    participant S as 記憶store・jobキュー
    participant W as 記憶後処理worker
    participant E as Embedding API

    C->>M: 会話の記憶を統合（consolidate_turn）
    M->>M: 訂正対象候補と記憶文脈を準備
    M->>G: 経験と記憶を解釈（memory_interpretation）
    G-->>M: エピソード・記憶候補・感情候補
    M->>G: 記憶候補の保持を審査（memory_retention_review）
    G-->>M: 候補の保持判断
    M->>G: エピソードと訂正候補を審査（memory_candidate_review）
    G-->>M: エピソードと訂正の審査結果
    M->>G: 感情候補を審査（affect_review）
    G-->>M: 感情候補の審査結果
    M->>M: エピソード・記憶更新操作・感情を正規化
    opt 記憶候補と既存記憶の意味比較が必要
        M->>E: 候補と未キャッシュの比較対象の埋め込み
    end
    M->>S: エピソード・記憶更新・感情を保存
    M-->>C: 同期統合結果と後処理ジョブ
    C->>S: 後処理ジョブを永続化
    C-)S: 後処理ジョブをキューへ登録
    par サイクル結果の返却
        C-->>U: サイクル結果
    and 独立したworkerの後処理
        S-)W: 後処理ジョブ
        W->>S: 訂正の整合処理と追加更新の保存
        opt 索引同期に埋め込み生成が必要
            W->>E: エピソード・記憶更新の埋め込み
            E-->>W: ベクトル
        end
        opt ベクトル索引同期が成功し、内省の実行条件を満たす
            W->>W: 内省・集約用の根拠を構築
            W->>S: 感情状態の更新を保存
            loop 要約を生成する対象範囲ごと（scope）
                W->>G: 内省による集約要約を生成（memory_reflection_summary）
                G-->>W: 集約要約
            end
            W->>S: 要約・記憶更新・記憶間リンクを反映
            opt 要約などの更新を索引へ反映する
                W->>E: 更新された記憶の埋め込み
            end
            W->>S: 志向状態などの派生状態を更新
        end
        W->>S: 関係索引・追跡情報・ジョブの完了結果を保存
    end
```

`generate_memory_candidate_review()` は内部で保持審査、episode と訂正の審査を順に呼ぶ。
後処理 worker は同期統合とは別の実行主体であり、外部への応答配送と後処理の完了順は固定されない。
内省では scope ごとの意味要約を LLM に依頼し、根拠の収集、状態の計算、正本の保存はコード側で行う。
記憶候補の意味比較に使う埋め込み生成と、保存後のベクトル索引同期は別の呼び出し箇所である。内省や視覚記憶の昇格でも、memory action の構築時に意味比較が必要なら埋め込み生成が発生する。

実装: [記憶統合](../../../src/otomekairo/memory/consolidator.py)、[記憶候補の解決](../../../src/otomekairo/memory/actions.py)、[記憶worker](../../../src/otomekairo/service/memory.py)、[内省統合](../../../src/otomekairo/memory/reflection/consolidator.py)、[内省要約](../../../src/otomekairo/memory/reflection/summary.py)。
契約: [記憶更新と再整理](../memory/記憶更新と再整理.md)、[内省要約のLLM生成](../memory/内省要約のLLM生成.md)、[感情モデル](../runtime/感情モデル.md)。

## 図 6: 視覚観測と日次整理

入口は会話添付画像、思考前の `vision.capture`、または能力実行で取得した画像。
画像を解釈して保存する処理と、保存済み観測を後から整理する日次 worker は別の実行経路である。

```mermaid
sequenceDiagram
    participant C as 観測・入力処理
    participant L as LLMClient
    participant G as 通常生成API
    participant S as 視覚記録・記憶store
    participant W as 日次視覚worker
    participant E as Embedding API

    C->>L: 画像と文脈から視覚観測を解釈（generate_visual_observation_summary）
    L->>G: 視覚観測の説明と変化判定を生成（visual_observation）
    G-->>L: 詳細説明・確信度・視覚変化判定
    L->>L: 出力契約を検証
    L-->>C: 契約検証済みの視覚観測
    C->>S: 視覚観測の記録を保存（visual_observation_record）
    alt 思考前観測の結果
        opt 世界状態の生成対象がある（world_state）
            C->>L: 世界状態の候補と根拠判定を生成（generate_world_state）
            L->>G: 世界状態の候補と根拠判定を生成（world_state）
            G-->>L: 状態候補と候補ごとの根拠判定
            L->>L: 全候補の契約検証と根拠種別による採用候補の選別
            L-->>C: 採用された状態候補
        end
        opt 活動状態の生成対象がある（activity_state）
            C->>L: 活動状態の候補と根拠判定を生成（generate_activity_state）
            L->>G: 活動状態の候補と根拠判定を生成（activity_state）
            G-->>L: 活動候補と候補ごとの根拠判定
            L->>L: 全候補の契約検証と根拠種別・人物同定による採用候補の選別
            L-->>C: 採用された活動候補
        end
        C->>C: 観測結果を後続の判断文脈へ
    else 会話添付または通常の能力結果
        C->>C: 観測結果を共通パイプラインへ［図 1］
    else 自律作業の能力結果
        C->>C: 状態更新と次の一手へ［図 4］
    end
    Note over S,W: 日次整理は独立したバックグラウンド処理
    W->>S: 対象日の保存済み視覚観測を読む
    W->>L: 視覚観測を日単位で整理（generate_visual_daily_grouping）
    L->>G: 観測のグループと記憶候補を生成（visual_daily_grouping）
    G-->>L: 観測のグループと記憶候補
    L-->>W: 観測のグループと記憶候補
    W->>S: 日次の視覚要約と観測の整理結果を保存（daily_visual_digest）
    opt 昇格候補と過去日の比較材料がある
        W->>L: 別日の観測から昇格候補の支持根拠を確認（generate_visual_daily_support）
        L->>G: 別日の反復根拠との照合結果を生成（visual_daily_support）
        G-->>L: 別日の反復根拠との照合結果
        L-->>W: 候補ごとの支持根拠
        opt 支持された昇格対象がある
            W->>W: 記憶の更新操作を構築［図 5］
            W->>S: 推定記憶と根拠を保存
            opt 索引への反映が必要
                W->>E: 昇格記憶の埋め込み
            end
            W->>S: 関係索引と昇格結果を更新
        end
    end
```

思考前観測の状態反映と、その後の判断サイクルによる状態処理は別の呼び出し箇所である。
日次視覚整理は、会話ターンの `memory_interpretation` を通す処理とは別に、反復根拠を審査して記憶候補を昇格する。

実装: [視覚入力](../../../src/otomekairo/service/input/visual.py)、[思考前観測](../../../src/otomekairo/service/input/wake_observation.py)、[日次視覚整理](../../../src/otomekairo/service/visual_daily.py)、[LLMClient](../../../src/otomekairo/llm/client.py) の `generate_visual_observation_summary()`。
契約: [対話画像入力](../capability/対話画像入力.md)、[vision_capture画像意味理解](../capability/vision_capture画像意味理解.md)、[記憶更新と再整理](../memory/記憶更新と再整理.md)。
