# LLM 呼び出しシーケンス

この文書は、主要な LLM 呼び出しを実装から追うための見取り図である。
内部フローはコードを正とし、意味境界、入出力契約、失敗条件の詳細は各節の設計文書を参照する。
図は実 LLM を利用する経路を示す。

## 図の読み方とモデルの境界

- `LLMClient` への矢印はアプリ内の論理処理呼び出しを示す。1 回の論理処理が、生成、審査、修復のために複数回 API を呼ぶことがある。
- `通常生成API`、`送信前チェックAPI`、`Embedding API` への矢印は、実際のモデル呼び出しを示す。応答矢印は必要な箇所だけ記載する。
- 「図 N」と付いた処理は、対応する図へ展開する。コード側の候補収集、契約検証、保存は、それぞれの担当内の処理として示す。
- `opt` は条件付き処理、`alt` は分岐、`loop` は繰り返し、`par` は独立して進む処理、開いた矢印は非同期の要求・通知を示す。

| 呼び出し先 | 設定の参照先 |
| --- | --- |
| 通常生成API | 選択中の `selected_model_preset_id` が指すモデルプリセット。解釈、判断、表現、審査、記憶、視覚で共有する |
| 送信前チェックAPI | `pre_send_check_model_preset_id` が指す専用プリセット |
| Embedding API | 選択中の記憶集合の `memory_set.embedding` |

モデル設定の正本は [モデルプリセット詳細](../configuration/モデルプリセット詳細.md)、埋め込み設定は [設定モデル](../configuration/設定モデル.md) を参照する。

構造化生成は `LLMClient._generate_structured_payload()` から `transport.complete_text()`、LiteLLM の順に進む。
解析・契約検証に失敗した場合の repair は、検証エラーを添えて同じモデルへ生成を依頼する処理である。
これとは別に、transport には provider の終了理由が `error` の応答に対する同一リクエストの再送がある。
API 呼び出し自体の例外は呼び出し元へ返り、別モデルへの切り替えは行わない。
修復、意味審査に伴う再生成、同一リクエスト再送はそれぞれ別の呼び出しであり、図の矢印の本数を固定のリクエスト数とは扱わない。
構造化出力の契約は [LLM構造化出力transport](LLM構造化出力transport.md)、使用量の記録は [デバッグ可能性](../runtime/デバッグ可能性.md#llm-生成使用量) を参照する。

## 図 1: 通常の判断サイクル

入口は人物発話を受ける `handle_conversation()`。
想起、状態と Skill の文脈構築、判断、出力、記憶統合の順に進む。
図中の `LLMClient` 呼び出しは、下位の生成と審査をまとめて示す。

```mermaid
sequenceDiagram
    participant U as 外部接点
    participant C as 判断サイクル
    participant R as 想起
    participant L as LLMClient
    participant E as Embedding API
    participant M as 記憶統合

    U->>C: 人物発話
    opt 思考前観測または添付画像がある
        C->>C: 観測と画像解釈（図7）
    end
    C->>L: generate_input_interpretation（人格や会話など）
    L-->>C: recall_hint と answer_contract
    C->>R: build_recall_pack
    R->>R: scope別の候補収集
    opt 関連検索のクエリがある
        R->>E: クエリ群の埋め込み
        E-->>R: 検索用ベクトル
        R->>R: 関連候補を検索・統合
    end
    opt 想起候補または競合がある
        R->>L: generate_recall_pack_selection
        L-->>R: 採用・配置する候補
    end
    opt event根拠を必要とし、選択済みeventがある
        R->>R: event読込とsource pack構築
        R->>L: generate_event_evidence（選択済みevent群）
        L-->>R: 各eventの短い根拠表現
        R->>R: 項目検証と出所の付与
    end
    R-->>C: RecallPack
    C->>C: 回答根拠を解決
    opt 空でない人物発話がある
        C->>L: generate_world_state_source_selection
    end
    opt world_stateの生成対象がある
        C->>L: generate_world_state
    end
    opt activity_stateの生成対象がある
        C->>L: generate_activity_state
    end
    C->>C: 状態・人物・関係・workspace文脈を構築
    opt Skill catalogがある
        C->>L: generate_agent_skill_selection
        loop 選択Skillに未確認の関連Skill・資料があり、追加採用する間
            C->>L: generate_agent_skill_material_selection
            C->>C: 選択資料の読込と文脈への追加
        end
    end
    C->>L: generate_decision（生成と審査は図2）
    L-->>C: 判断
    alt 発話を選ぶ
        C->>L: generate_speech（生成と根拠審査は図2）
        opt 他人物由来の開示審査対象がある
            C->>L: generate_disclosure_review（図2）
        end
    else capability_requestを選ぶ
        C->>C: 実行境界へdispatch（図3）
    else autonomous_runを選ぶ
        C->>C: 開始審査と初回step（図5）
    else 保留またはnoop
        C->>C: 判断結果を保持
    end
    C->>C: cycleのeventとtraceを保存
    C->>M: consolidate_turn（図6）
    M-->>C: 統合結果と後処理job
    C-)M: 後処理jobをキューへ登録
    C-->>U: サイクル結果
```

`generate_input_interpretation()` は、記憶検索に先立って「何を思い出すか」「どんな根拠を探すか」を決める入口処理である。正常時は1回の LLM 呼び出しで、次の2つをまとめて返す。

| 出力 | 役割と後続処理 |
| --- | --- |
| `recall_hint` | 想起する記憶の主題、人物・話題、時間軸、参照の曖昧さなどを整理し、後続の `build_recall_pack()` に渡す |
| `answer_contract` | 原文、日時、出所など必要な根拠の種類と、検索対象の人物・会話の範囲を指定し、RecallPack 構築後の回答根拠の解決に使う |

入力には、現在の入力本文と発信者・会話などの構造化情報、人格設定、直近会話、現在時刻を渡す。解釈済みの画像説明と活動状態がある場合は、内部補助文脈として渡す。入力の詳細と出力契約は [RecallHint](../memory/想起と判断.md#recallhint) と [AnswerContract](../memory/想起と判断.md#answercontract) を正とする。

例えば「前に私が言ったことを原文で教えて」という入力では、本人の過去の会話を想起の対象とし、本人の発話原文を必要な根拠として指定する。この段階では検索方針を決め、長期記憶の検索や原文取得は後続処理が行う。行動選択と返答本文の生成は、後段の `generate_decision()` と `generate_speech()` が担当する。

`generate_world_state()` と `generate_activity_state()` は、それぞれ候補と根拠判定を同じ生成で取得し、コード側で採用候補を選ぶ。具体的な根拠判定は [状態候補の根拠判定](状態候補根拠審査.md) を参照する。
`generate_event_evidence()` は対象 event 群をまとめて呼び出し、返った各項目を想起側で検証する。

実装: [入力サイクル](../../../src/otomekairo/service/input/cycle.py)、[共通パイプライン](../../../src/otomekairo/service/input/pipeline.py)、[想起構築](../../../src/otomekairo/recall/builder.py)、[Skill文脈](../../../src/otomekairo/service/agent_skills.py)、[結果の保存](../../../src/otomekairo/service/input/trace_persist.py)。
契約: [想起と判断](../memory/想起と判断.md)、[RecallPackのLLM選別](../memory/RecallPackのLLM選別.md)、[event_evidenceのLLM圧縮](../memory/event_evidenceのLLM圧縮.md)、[状態候補根拠審査](状態候補根拠審査.md)、[AgentSkills統合](../integration/AgentSkills統合.md)。

## 図 2: 判断と発話の審査

入口は `generate_decision()` と、発話を選んだ場合の `generate_speech()`。
審査も通常生成と同じモデル設定を使う。MCP 引数の根拠審査と、外部送信直前の開示可否チェック（図 3）は別の段階である。

```mermaid
sequenceDiagram
    participant C as 判断パイプライン
    participant L as LLMClient
    participant G as 通常生成API

    C->>L: generate_decision
    L->>G: decision_generation
    G-->>L: 判断候補
    opt 候補にMCP tool呼び出しがある
        L->>G: capability_input_grounding_review
        G-->>L: 根拠との照合結果
        opt reconsider
            L->>G: 審査理由を添えて判断を修復
            Note over L,G: 再候補も契約検証とMCP引数の根拠審査を通す
        end
    end
    alt full比較の人物応答で、候補がautonomous_run以外
        L->>G: future_action_alignment_review
        G-->>L: 履行範囲との整合結果
        opt 不整合
            L->>G: decision_future_action_retry
            opt 再候補がautonomous_run以外
                L->>G: future_action_alignment_review
            end
        end
    else 自律判断のoutward_speech比較
        L->>G: decision_grounding_review
        G-->>L: allow または reconsider
        opt reconsider
            L->>G: decision_grounding_reconsider
            L->>G: decision_grounding_review
        end
    end
    L-->>C: 審査を通った判断
    opt 発話生成へ進む
        C->>L: generate_speech
        L->>G: expression_generation（自由文）
        G-->>L: 発話候補
        L->>G: speech_grounding_review
        G-->>L: 採用または根拠に沿った修正文
        L-->>C: 発話と根拠審査結果
        opt 他人物由来の開示審査対象がある
            C->>L: generate_disclosure_review
            L->>G: disclosure_review
            G-->>L: 開示判定
            L-->>C: 採用・書き換え・見送りの判定
        end
    end
```

MCP 引数の根拠審査は判断・step 候補の検証内で実行される。審査自体の失敗と、意味の不整合による修復は実装上も区別される。
上図の各審査の再生成条件、修復の上限、失敗の扱いは対応する契約を参照する。

実装: [LLMClient](../../../src/otomekairo/llm/client.py) の `generate_decision()`、`_review_capability_input_grounding()`、`_ground_outward_decision()`、`generate_speech()` と、[パイプライン](../../../src/otomekairo/service/input/pipeline.py) の `_apply_disclosure_review()`。
契約: [判断と行動](../runtime/判断と行動.md)、[判断根拠審査](判断根拠審査.md)、[外部本文の根拠照合](外部本文の根拠照合.md)、[想起と判断の共有許可](../memory/想起と判断.md#共有許可と受け手の照合)。

## 図 3: 能力実行、送信前チェック、結果からの後続判断

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

    loop 初回候補と、withhold後に再候補が生まれた場合
        C->>B: capability_requestをdispatch
        B->>B: manifest・権限・接続・入力schemaを検証
        opt MCPかつ送信前チェックが有効
            alt 既知の秘密値に一致
                B->>B: withhold（LLM呼出なし）
            else LLMによるチェックへ進む
                B->>L: generate_pre_send_check（最終arguments）
                L->>P: pre_send_check
                P-->>L: allow または withhold
                L-->>B: チェック結果
            end
        end
        alt 実行を許可できる
            B->>B: requestとongoing_actionを保存
            B-)X: 実行要求を配送
            B-->>C: dispatch結果
        else withhold
            B-->>C: 外部送信の見送り
            opt 最初のwithhold
                C->>L: 固定feedbackでgenerate_decision（図2）
                L-->>C: 同じ目的の別案またはnoop
            end
            Note over C,B: 再候補も同じ境界へ。再度withholdなら送信せず終了
        else チェック生成・検証の失敗
            B-->>C: 明示失敗
            C->>C: cycleを失敗として確定
        end
    end
    opt 実行結果が到着
        X-)C: capability_result
        C->>C: 結果の解釈・必要な画像処理
        alt 通常の能力要求
            C->>C: 共通パイプラインで後続判断（図1・図2）
        else autonomous_runに属する能力要求
            C->>C: 状態・run履歴へ反映して次のstep（図5）
        end
    end
```

送信前チェックでは専用モデルへ最終 payload を渡す。
自律作業の `withhold` 後は `generate_decision()` の代わりに step 候補を再生成し、再度見送りまたはチェック失敗になった場合の終了単位も通常 cycle と異なる。
具体的な入力境界、再判断、終了の規則は [送信前チェック](pre_send_check.md) を参照する。

実装: [能力実行境界](../../../src/otomekairo/service/capability.py)、[通常の能力結果サイクル](../../../src/otomekairo/service/spontaneous/capability_cycle.py)、[自律作業](../../../src/otomekairo/service/autonomous_run.py)。
契約: [実行連携](../api/実行連携.md)、[判断と行動の実行結果](../runtime/判断と行動.md#実行結果の扱い)。

## 図 4: 自律判断の入口と分離判断

入口は API の `wake` または定期思考の `background_thinking`。
時刻や候補の適格性はコードが扱い、候補の意味的な選別と判断を LLM が担当する。

```mermaid
sequenceDiagram
    participant W as 起床・定期思考
    participant C as 自律判断パイプライン
    participant O as 思考前観測
    participant L as LLMClient
    participant G as 通常生成API

    W->>C: wake または background_thinking
    C->>C: 適用する起床条件を確認
    opt 評価可能な保留意図候補がある
        C->>L: generate_pending_intent_selection
        L->>G: pending_intent_selection
        G-->>L: 選別結果
        L-->>C: 候補1件またはnone
    end
    opt 定期思考の観測が有効
        C->>O: 設定された思考前観測
        O->>O: 画像解釈と状態反映（図7）
        O-->>C: 観測結果と更新済み文脈
    end
    opt 選択済み候補がなく、直接の入口もなく、入口判定の材料がある
        C->>L: generate_initiative_entry_check
        L->>G: initiative_entry_check
        G-->>L: 入口判定
        L-->>C: 入口判定
    end
    alt 判断へ進む入口がある
        C->>C: 想起・状態・workspaceの構築（図1）
        alt 自身の活動を分離して比較する
            C->>C: 隔離した活動用入力で想起とSkill文脈を構築
            C->>L: generate_decision（self_activity）
            L->>G: 自身の活動の判断
            G-->>L: 判断候補
            L-->>C: 自身の活動の判断
            opt autonomous_runを選んだ
                C->>L: generate_autonomous_activity_alignment_review
                L->>G: autonomous_activity_alignment_review
                G-->>L: 整合審査結果
                L-->>C: 整合審査結果
                opt 目的と主根拠が不整合
                    C->>L: 自身の活動だけを再判断・必要なら再審査
                end
            end
            C->>L: generate_decision（outward_speech）
            L->>G: 対人発話の判断と根拠審査（図2）
            C->>C: 両方の判断を合成して出力へ
        else 共通の判断を使う
            C->>L: generate_decision（図2）
        end
        C->>C: 発話・能力実行・自律作業・保留（図1・図3・図5）
    else 今回は入口がない、または実行境界で見送る
        C->>C: noopとして記録
    end
```

新しい有効な視覚観測などから直接判断へ入れる場合は `initiative_entry_check` の API 呼び出しを省く。
その視覚観測だけを入口にする経路では、入力解釈と記憶検索を通さず観測から直接判断用文脈を作る場合がある。
自身の活動を分離する場合は、活動用の想起・Skill 選択が追加される。処理順は自身の活動判断、その整合審査、対人発話判断、合成の順である。

実装: [起床サイクル](../../../src/otomekairo/service/spontaneous/wake.py)、[保留意図の選別](../../../src/otomekairo/service/spontaneous/pending_intent.py)、[起床パイプライン](../../../src/otomekairo/service/input/wake_pipeline.py)、[分離判断](../../../src/otomekairo/service/input/decision_comparison.py)。
契約: [自律initiative_loop](../runtime/自律initiative_loop.md)、[保留意図候補のLLM選別](保留意図候補のLLM選別.md)、[判断と行動](../runtime/判断と行動.md)、[プロンプト文脈分離方針](プロンプト文脈分離方針.md)。

## 図 5: 自律作業の開始、step、完了

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

    C->>A: autonomous_run開始候補
    A->>L: generate_autonomous_start_review
    L->>G: autonomous_start_review
    G-->>L: 開始判定
    L-->>A: 開始判定
    alt 開始を許可
        A->>A: 必要な既存runの置換と新規runの保存
        loop 初回、時刻到来、結果到着などのstep機会
            A->>A: run・現在状態・Skill文脈を構築
            Note over A,L: catalogと追加資料があればSkill選択APIも呼ぶ
            A->>L: generate_autonomous_step
            L->>G: autonomous_step_generation
            G-->>L: step候補
            opt MCP tool呼び出しの候補
                L->>G: capability_input_grounding_review（図2）
            end
            L-->>A: action と transition
            opt actionがspeech
                A->>L: generate_speech
                L->>G: expression_generation と speech_grounding_review（図2）
            end
            opt capability_request以外でcompleteを候補にする
                A->>L: generate_autonomous_completion_review
                L->>G: autonomous_completion_review
                G-->>L: 完了可否
                L-->>A: 完了可否
                opt 継続が必要
                    A->>A: feedbackを添えてstep候補から再生成・再審査
                end
            end
            alt actionがcapability_request
                A-)X: 同じ実行境界を通して要求（図3）
                X-)A: 結果到着、状態と履歴へ反映
            else actionがspeech
                A-)X: 審査済み発話を配送
            else actionがnone
                A->>A: 外向き出力なし
            end
            A->>A: transitionを保存して継続・待機・終了
        end
        opt runが終端へ遷移
            A->>M: 紐付くcommitmentの解決
            opt 更新対象のcommitmentがある
                M->>L: generate_commitment_lifecycle_summaries
                L->>G: commitment更新用の要約生成
            end
            A->>M: 終端eventの記憶統合と後処理登録（図6）
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

## 図 6: 記憶統合と非同期後処理

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

    C->>M: consolidate_turn
    M->>M: 訂正対象候補と記憶文脈を準備
    M->>G: memory_interpretation
    G-->>M: episode・記憶候補・感情候補
    M->>G: memory_retention_review
    G-->>M: 候補の保持判断
    M->>G: memory_candidate_review
    G-->>M: episodeと訂正の審査結果
    M->>G: affect_review
    G-->>M: 感情候補の審査結果
    M->>M: episode・memory action・感情を正規化
    opt 記憶候補と既存記憶の意味比較が必要
        M->>E: 候補と未キャッシュの比較対象の埋め込み
    end
    M->>S: episode・記憶更新・感情を保存
    M-->>C: 同期統合結果とjob
    C->>S: 後処理jobを永続化
    C-)S: 後処理jobをキューへ登録
    par サイクル結果の返却
        C-->>U: サイクル結果
    and 独立したworkerの後処理
        S-)W: 後処理job
        W->>S: 訂正の整合処理と追加更新の保存
        opt 索引同期に埋め込み生成が必要
            W->>E: episode・記憶更新の埋め込み
            E-->>W: ベクトル
        end
        opt ベクトル索引同期が成功し、内省の実行条件を満たす
            W->>W: 内省・集約用の根拠を構築
            W->>S: 感情状態の更新を保存
            loop 要約を生成するscope
                W->>G: memory_reflection_summary
                G-->>W: 集約要約
            end
            W->>S: 要約・記憶更新・記憶linkを反映
            opt 要約などの更新を索引へ反映する
                W->>E: 更新された記憶の埋め込み
            end
            W->>S: 志向状態などの派生状態を更新
        end
        W->>S: 関係索引・trace・jobの完了結果を保存
    end
```

`generate_memory_candidate_review()` は内部で保持審査、episode と訂正の審査を順に呼ぶ。
後処理 worker は同期統合とは別の実行主体であり、外部への応答配送と後処理の完了順は固定されない。
内省では scope ごとの意味要約を LLM に依頼し、根拠の収集、状態の計算、正本の保存はコード側で行う。
記憶候補の意味比較に使う埋め込み生成と、保存後のベクトル索引同期は別の呼び出し箇所である。内省や視覚記憶の昇格でも、memory action の構築時に意味比較が必要なら埋め込み生成が発生する。

実装: [記憶統合](../../../src/otomekairo/memory/consolidator.py)、[記憶候補の解決](../../../src/otomekairo/memory/actions.py)、[記憶worker](../../../src/otomekairo/service/memory.py)、[内省統合](../../../src/otomekairo/memory/reflection/consolidator.py)、[内省要約](../../../src/otomekairo/memory/reflection/summary.py)。
契約: [記憶更新と再整理](../memory/記憶更新と再整理.md)、[内省要約のLLM生成](../memory/内省要約のLLM生成.md)、[感情モデル](../runtime/感情モデル.md)。

## 図 7: 視覚観測と日次整理

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

    C->>L: generate_visual_observation_summary（画像と文脈）
    L->>G: visual_observation
    G-->>L: 詳細説明・確信度・視覚変化判定
    L->>L: 出力契約を検証
    L-->>C: 契約検証済みの視覚観測
    C->>S: visual_observation_recordを保存
    alt 思考前観測の結果
        opt world_stateの生成対象がある
            C->>L: generate_world_state
            L->>G: world_state
            G-->>L: 状態候補と候補ごとの根拠判定
            L->>L: 全候補の契約検証と根拠種別による採用候補の選別
            L-->>C: 採用された状態候補
        end
        opt activity_stateの生成対象がある
            C->>L: generate_activity_state
            L->>G: activity_state
            G-->>L: 活動候補と候補ごとの根拠判定
            L->>L: 全候補の契約検証と根拠種別・人物同定による採用候補の選別
            L-->>C: 採用された活動候補
        end
        C->>C: 観測結果を後続の判断文脈へ
    else 会話添付または通常の能力結果
        C->>C: 観測結果を共通パイプラインへ（図1）
    else 自律作業の能力結果
        C->>C: 状態更新と後続stepへ（図5）
    end
    Note over S,W: 日次整理は独立したbackground処理
    W->>S: 対象日の保存済み視覚観測を読む
    W->>L: generate_visual_daily_grouping
    L->>G: visual_daily_grouping
    G-->>L: 観測groupと記憶候補
    L-->>W: 観測groupと記憶候補
    W->>S: daily_visual_digestと観測の整理結果を保存
    opt 昇格候補と過去日の比較材料がある
        W->>L: generate_visual_daily_support
        L->>G: visual_daily_support
        G-->>L: 別日の反復根拠との照合結果
        L-->>W: 候補ごとの支持根拠
        opt 支持された昇格対象がある
            W->>W: memory actionを構築（意味比較は図6）
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
