# 目標アーキテクチャ

<!-- Block: Position -->
## この設計の位置づけ

- OtomeKairo は、ひとつの人格個体として持続する **身体性を持つ AI 人格コア** として設計する
- 中心にあるのはチャット応答ではなく、常時稼働し続ける「観測・判断・行動」の循環である
- 会話は多数ある入出力チャネルの 1 つにすぎず、中心機能ではない
- 設定変更は、人格コア本体とは責務を分離した Web サーバから行う
- 実装単位までの分解は `docs/30_design_breakdown.md` で管理する
- 後方互換やフォールバックは持たせず、常に現在の正しい構成へ寄せる

<!-- Block: Runtime Shape -->
## 成立させるべき稼働像

- 起動すると、まずアイドリング状態に入り、外界からの入力待ちと自己状態の維持を続ける
- 入力がなくても、時間経過、内部欲求、未完了タスクに応じて自発的に次の行動を選べる
- 外部刺激を受けたら、必要な記憶と現在の世界状態を参照して、行動するか静観するかを決められる
- 行動結果は、その場の反応で終わらず、自己状態、記憶、世界理解に反映される
- 外部 API 呼び出し、感覚取得、行動実行、状態更新、永続化、設定変更の責務が混ざらない

<!-- Block: Core Loop -->
## 中心ループ

1. アイドリング中に、センサー、ネットワーク、時刻、内部欲求の変化を監視する
2. 新しい刺激や継続課題があれば、観測イベントとして正規化する
3. 観測イベントをもとに、現在の自己状態、身体状態、世界状態、記憶を読み出す
4. いま反応すべきか、何を優先すべきかを評価する
5. 必要なら、認知処理として LLM を使い、意図と行動計画を組み立てる
6. 発話、移動、視線変更、検索、通知などの行動を実行する
7. 実行結果を再観測し、自己状態、記憶、世界状態に反映して保存する
8. 再びアイドリング状態へ戻る

<!-- Block: Layout -->
## あるべきディレクトリ構成

```text
.
├── README.md
├── pyproject.toml
├── docs/
│   ├── 00_index.md
│   ├── 10_target_architecture.md
│   └── 20_external_interfaces.md
├── config/
│   ├── persona/
│   ├── body/
│   ├── drives/
│   ├── llm/
│   ├── prompts/
│   └── policies/
├── data/
│   ├── core.sqlite3
│   ├── events.jsonl
│   └── media/
├── log/
├── tmp/
└── src/
    └── otomekairo/
        ├── boot/
        │   ├── __main__.py
        │   └── run_web.py
        ├── runtime/
        │   ├── main_loop.py
        │   ├── idle_cycle.py
        │   ├── observe_cycle.py
        │   ├── decide_cycle.py
        │   └── act_cycle.py
        ├── web/
        │   ├── app.py
        │   ├── settings_api.py
        │   ├── text_input_api.py
        │   └── status_api.py
        ├── usecase/
        │   ├── process_observation.py
        │   ├── select_action.py
        │   ├── execute_action.py
        │   ├── persist_state.py
        │   └── consolidate_memory.py
        ├── domain/
        │   ├── self_model.py
        │   ├── body_state.py
        │   ├── world_state.py
        │   ├── perception.py
        │   ├── drive.py
        │   ├── intention.py
        │   ├── action.py
        │   ├── memory.py
        │   └── event.py
        ├── gateway/
        │   ├── cognition_client.py
        │   ├── embedding_store.py
        │   ├── sensory_port.py
        │   ├── actuator_port.py
        │   ├── network_port.py
        │   ├── social_port.py
        │   ├── notification_port.py
        │   ├── memory_store.py
        │   ├── state_store.py
        │   ├── world_store.py
        │   └── event_store.py
        ├── infra/
        │   ├── wifi_camera_sensor.py
        │   ├── wifi_camera_controller.py
        │   ├── microphone_sensor.py
        │   ├── text_input_queue.py
        │   ├── tts_speaker.py
        │   ├── mobility_controller.py
        │   ├── browser_client.py
        │   ├── sns_api_client.py
        │   ├── line_api_client.py
        │   ├── litellm_cognition_client.py
        │   ├── sqlite_vec_memory_store.py
        │   ├── sqlite_state_store.py
        │   ├── sqlite_world_store.py
        │   ├── jsonl_event_store.py
        │   └── fastapi_settings_server.py
        ├── policy/
        │   ├── priority_rules.py
        │   ├── action_rules.py
        │   └── safety_rules.py
        └── schema/
            ├── settings.py
            ├── observation_frame.py
            ├── action_command.py
            └── cognition_result.py
```

<!-- Block: Boundaries -->
## 責務境界

- `boot/`: 起動入口だけを持つ。人格コア起動と Web サーバ起動の開始点だけを担当する
- `runtime/`: アイドリングを含む常時稼働ループを担当する。周期管理と各段階の呼び出し順だけを持つ
- `web/`: 設定変更、テキスト入力、状態確認の HTTP ルーティングだけを担当する
- `usecase/`: 観測処理、行動選択、行動実行、保存といったユースケース単位の調停を担当する
- `domain/`: 自己、身体、世界、欲求、意図、行動、記憶を表す中核概念を置く。外部 API や DB を持ち込まない
- `gateway/`: 感覚、行動、ネットワーク、通知、埋め込み検索、記憶保存など、外部依存との境界を抽象化する
- `infra/`: センサー、アクチュエータ、LiteLLM、sqlite-vec、FastAPI などの具体実装を置く
- `policy/`: 優先度判定、安全制約、行動制約を明示的なルールとして分離する
- `schema/`: 外部入出力や認知結果の構造を明示的に定義する
- `config/`: 人格定義、身体構成、欲求設定、LLM ルーティング、行動ポリシーをコードから分離して管理する
- `data/`: この人格個体の永続状態を置く。自己状態、世界状態、記憶、イベントをここに集約する

<!-- Block: Sensory Model -->
## 観測入力の扱い

- 現時点で決定している観測入力は、Wi-Fi の Web カメラ、マイク入力、テキスト入力である
- 五感は最終的に「視覚」「聴覚」「触覚」「自己受容感覚」「位置・移動感覚」として扱い、すべて `perception` に正規化する
- テキスト入力や外部 API の応答も、同じ観測イベントの一種として扱う
- どの入力チャネルでも、人格側が見るのは統一された観測表現だけにする
- センサー実装ごとの差異は `infra/` に閉じ込め、人格コアへ漏らさない

<!-- Block: Action Model -->
## 行動の扱い

- 行動は「発話」「移動」「視線変更」「ネット検索」「SNS 操作」「LINE 通知」「待機」のような明示的な命令として扱う
- 移動は会話の副作用ではなく、独立した行動カテゴリとして扱う
- TTS による音声出力は、発話の標準的な実行手段として扱う
- インターネットアクセス、SNS API、LINE API は、補助機能ではなく外界へ作用する正式な行動手段として扱う
- 行動実行前に、優先度と安全制約を必ず通す

<!-- Block: Cognition Model -->
## 認知と LLM の扱い

- 複数 LLM プロバイダの利用は `LiteLLM` を通して統一する
- 人格コアは特定ベンダーの SDK を直接知らず、`gateway/cognition_client.py` だけを見る
- モデル選択、プロバイダ切替、ルーティング方針は `config/llm/` に分離して持つ
- LLM は人格の思考補助であり、センサー取得や DB 操作の主体にはしない

<!-- Block: State Model -->
## 中心状態

- `self_model`: 性格傾向、現在の感情、注意対象、長期目標、関係性の認識を持つ
- `body_state`: 姿勢、移動状態、利用可能な感覚器、疲労や負荷のような身体状態を持つ
- `world_state`: 現在地、周辺状況、進行中タスク、外部対象の状態認識を持つ
- `memory`: エピソード記憶、意味記憶、対人記憶、行動結果の学習を持つ
- これらを分離して持ちつつ、判断時には一体として参照する

<!-- Block: Persistence -->
## 永続化方針

- 正式な保存先は `SQLite` を前提にし、自己状態、世界状態、記憶を一元管理する
- 記憶検索用の埋め込み索引は `sqlite-vec` を用いて同じ SQLite 系の保存基盤に統合する
- `JSONL` のイベントログは観測用に使い、正本にはしない
- 設定は `config/`、実行時状態は `data/` と明確に分離する
- メモリ上の一時状態を真実源にせず、各ループ完了時に保存を確定する
- センサー入力の生データを正本にせず、必要な観測結果だけを保存する

<!-- Block: Control Plane -->
## 設定インタフェース

- 設定変更は `FastAPI` を用いた Web サーバで受け付ける
- 実行サーバは `Uvicorn` を前提とする
- Web サーバは、人格コア本体の観測・判断・行動ループとは責務を分離する
- 設定変更、状態確認、テキスト入力の HTTP 窓口は `web/` に集約する

<!-- Block: Non Goals -->
## やらないこと

- 複数人格個体の同時運用
- 分散システム化
- 人格コアの中に個別デバイス制御ロジックを直接書くこと

<!-- Block: First Build -->
## 最初に作る単位

- `pyproject.toml`: Python の実行環境を固定する
- `src/otomekairo/boot/__main__.py`: 起動入口を 1 つに固定する
- `src/otomekairo/runtime/main_loop.py`: アイドリングから再入可能な常時稼働ループを最初に成立させる
- `src/otomekairo/web/app.py`: 設定とテキスト入力の Web 入口を先に固定する
- `src/otomekairo/domain/`: 自己、身体、世界、記憶の中核モデルを先に定義する
- `src/otomekairo/usecase/process_observation.py`: 観測を判断材料へ変換する入口を先に作る
- `src/otomekairo/usecase/select_action.py`: 判断を行動へ落とす核を先に作る
- `src/otomekairo/infra/litellm_cognition_client.py`: 複数プロバイダ対応の入口を最初から 1 箇所に集約する
- `src/otomekairo/infra/sqlite_vec_memory_store.py`: 記憶と埋め込み検索の保存基盤を最初から固定する
- `src/otomekairo/gateway/` と `src/otomekairo/infra/`: 感覚器と行動器の境界を最初から分ける
