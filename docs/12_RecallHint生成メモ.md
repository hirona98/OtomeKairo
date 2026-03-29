# RecallHint生成メモ

<!-- Block: Role -->
## この文書の役割

この文書は、`11_RecallPack候補収集メモ.md` で置いた `RecallHint` を、実際にどう生成するかを具体化するための設計メモである。

ここで固定したいのは次の 3 点である。

- `RecallHint` を何で生成するか
- 生成入力の範囲をどう切るか
- 返す JSON 契約をどう固定するか

ここでも、まだ API や実装方式は確定しない。
ただし、後で内部コンテキスト契約へ落とせる程度の最小形は定める。

<!-- Block: Position -->
## `RecallHint` の位置づけ

`RecallHint` は、長期記憶そのものではない。
また、返答文の下書きでもない。

OtomeKairo では、`RecallHint` を「現在ターンに対して、どの記憶をどう想起するかを決めるための軽い前処理結果」として扱う。

役割は次である。

- 構造レーンでどの `scope` を優先して見るかを決める
- 連想レーンにどの方向の問い合わせをかけるかを決める
- どの `RecallPack` section を重くするかを決める
- 回想寄りか、確認寄りか、相談寄りかを粗く決める

逆に、ここでやらないことは次である。

- 長期記憶を読んで深い推論をすること
- 返答本文を組み立てること
- 記憶候補そのものを選び切ること

`RecallHint` は、想起の入口である。

<!-- Block: Policy -->
## 基本方針

MVP では、`RecallHint` は軽量高速な LLM 1 回で生成するのがよい。

この方針にする理由は次である。

- `intent` は日本語の含みや遠回しな表現を含みやすい
- 回想、相談、約束確認、関係確認は、単純なキーワード規則だけだと取りこぼしやすい
- `RecallHint` だけルールベースと LLM を混ぜると、判断責務が二重化しやすい

そのため、MVP では次を基本にする。

- 判断主体は軽量高速 LLM のみ
- 出力は JSON の固定契約に制限する
- 長期記憶は入力に渡さない
- 低温度で安定した分類に寄せる
- コード側は分類しない

ここでいうコード側の役割は、JSON を受け取ることだけである。
分類ロジックをコードへ分散しない。

<!-- Block: Window -->
## 入力ウィンドウ

`RecallHint` の生成入力は、次の 3 つで十分である。

1. 最新のユーザー入力
2. 直近会話
3. 現在時刻

直近会話の範囲は、次で切るのがよい。

- 時間条件:
  - 現在から `3 分以内`
- 件数条件:
  - 最大 `4-6 発話`
- 必須条件:
  - 最新のユーザー入力は常に含める

この形にすると、今の会話の空気は残しつつ、古い流れに引っ張られすぎにくい。

入力として渡すものは次に限定する。

- ユーザー発話
- 直近のアシスタント発話
- 各発話の時刻
- 現在時刻

渡さないものは次である。

- `memory_units`
- `episode_digests`
- `affect_state`
- 長期記憶に由来する補助説明

<!-- Block: Contract -->
## `RecallHint` の最小契約

MVP の `RecallHint` は、次の形でよい。

```json
{
  "focus_scopes": [],
  "mentioned_entities": [],
  "mentioned_topics": [],
  "intent": "smalltalk",
  "time_reference": "current"
}
```

各項目の役割は次のとおり。

| 項目 | 役割 |
|------|------|
| `focus_scopes` | 今回優先して見る `scope` |
| `mentioned_entities` | 会話中で明示または補完された対象 |
| `mentioned_topics` | 今回の話題キー |
| `intent` | 想起優先順を動かすための粗い意図 |
| `time_reference` | 回想や未来参照を判定するための時間軸 |

`intent` と `time_reference` は必ず 1 つだけ持つ。
その他は空でもよい。

<!-- Block: IntentEnum -->
## `intent` の固定集合

LLM に自由記述させないため、`intent` は次の固定集合から選ばせる。

| `intent` | 用途 |
|------|------|
| `smalltalk` | 雑談、軽い応答 |
| `check_state` | 近況、体調、感情、状況確認 |
| `consult` | 悩み相談、助言要請 |
| `fact_query` | 事実確認、プロフィール確認 |
| `preference_query` | 好み、苦手、傾向の確認 |
| `commitment_check` | 約束、未完了、今後の確認 |
| `reminisce` | 過去の会話や出来事の想起 |
| `meta_relationship` | 距離感、信頼、関係そのものの確認 |

MVP では、複数 `intent` を返させない。
主 `intent` を 1 つに決める。

<!-- Block: TimeEnum -->
## `time_reference` の固定集合

`time_reference` も固定集合から選ばせる。

| `time_reference` | 意味 |
|------|------|
| `current` | 今この場の話 |
| `recent` | 最近の継続状態や近い過去 |
| `past` | 過去の出来事や以前の会話 |
| `future` | 今後、次回、予定 |
| `persistent` | 長期に安定した属性や傾向 |

これも 1 つだけ返す。

<!-- Block: ScopeMeaning -->
## `focus_scopes` の意味

`focus_scopes` は、今回の返答でまず見るべき棚を順序付きで表す。

MVP では、最大 `3 件` に抑える。
候補は次から選ぶ。

- `self`
- `user`
- `entity:<normalized_ref>`
- `topic:<normalized_topic>`
- `relationship:self|user`
- `world`

ここで重要なのは、`focus_scopes` を広げすぎないことである。
LLM には「迷ったら狭く返す」よう指示する。

<!-- Block: EntityMeaning -->
## `mentioned_entities` の意味

`mentioned_entities` は、今回の入力や直近会話で参照対象として明示されたものを持つ。

MVP では、最大 `3 件` に抑える。

返してよいものは次である。

- 人物
- 場所
- 道具やサービス
- 直近会話から自然に補完できる代名詞対象

返さないほうがよいものは次である。

- 長期記憶がないと確定できない対象
- 一意に補完できない代名詞
- ただの一般名詞

<!-- Block: TopicMeaning -->
## `mentioned_topics` の意味

`mentioned_topics` は、今回の会話の主題キーを持つ。

MVP では、最大 `2 件` に抑える。
大量に返すと、`active_topics` 側が広がりすぎる。

ここでも、LLM には「今の入力で主題になっているものを優先し、曖昧なら少なく返す」よう指示する。

<!-- Block: LLMTask -->
## LLM にやらせること

`RecallHint` 生成 LLM には、次だけをやらせるのがよい。

1. 最新入力と直近会話から、主 `intent` を 1 つ選ぶ
2. 時間軸を `time_reference` に落とす
3. `focus_scopes` を最大 3 件で返す
4. `mentioned_entities` / `mentioned_topics` を短く返す

重要なのは、深い人格判断ではなく、想起方向の決定だけをやらせることである。

<!-- Block: PromptContract -->
## LLM への指示契約

軽量 LLM への指示は、次の内容に絞るのがよい。

- 返すのは JSON のみ
- `intent` と `time_reference` は固定集合から 1 つ選ぶ
- `focus_scopes` は最大 3 件
- `mentioned_entities` は最大 3 件
- `mentioned_topics` は最大 2 件
- 迷ったら広げずに狭く返す
- 長期記憶を仮定しない
- 明示されていない関係変化や過去出来事を断定しない

この契約なら、判断は LLM に寄せつつ、出力形は安定させやすい。

<!-- Block: GenerationFlow -->
## 生成フロー

MVP の生成フローは次で十分である。

1. 最新入力を確定する
2. 直近 `3 分以内` かつ `最大 4-6 発話` の会話を切り出す
3. 現在時刻を添えて軽量高速 LLM へ渡す
4. JSON の `RecallHint` を受け取る
5. そのまま後続の構造レーンと連想レーンへ渡す

ここでは、分類ルールをコード側へ戻さない。
判断は 1 回の LLM 呼び出しに寄せる。

<!-- Block: Failure -->
## 失敗時の扱い

MVP では、失敗時の扱いも単純でよい。

- JSON が壊れている:
  - 同じ軽量 LLM に 1 回だけ再試行する
- enum 外の値を返した:
  - その呼び出しは失敗として再試行する
- 再試行後も壊れている:
  - `RecallHint` 生成失敗としてターンを異常終了にする
  - 失敗入力、入力ウィンドウ、モデル出力を観測ログへ残す
  - 後続の想起と返答生成には進まない

ここで重要なのは、分類失敗を別経路で吸収しないことである。
最小値をコード側で差し込むと、`LLM だけで判定する` という設計が崩れる。

<!-- Block: Conservative -->
## LLM にさせないこと

軽量 LLM には、次をさせないほうがよい。

- 長期記憶を前提に対象を補完すること
- 曖昧な回想を特定エピソードへ決め打ちすること
- 明示されていない関係変化を断定すること
- 多数の `topic` を立てること
- 自分で返答方針まで決めること

迷ったら、より狭く、より弱く出す。

<!-- Block: Examples -->
## 例

### 例 1: 体調の近況確認

入力:

```text
最近ちゃんと眠れてないんだよね
```

出力例:

```json
{
  "focus_scopes": ["user", "topic:health"],
  "mentioned_entities": ["user"],
  "mentioned_topics": ["topic:health"],
  "intent": "check_state",
  "time_reference": "recent"
}
```

### 例 2: 約束の確認

入力:

```text
この前の続き、また今度話すって言ってたよね
```

出力例:

```json
{
  "focus_scopes": ["relationship:self|user", "user"],
  "mentioned_entities": ["user"],
  "mentioned_topics": [],
  "intent": "commitment_check",
  "time_reference": "future"
}
```

### 例 3: 過去の会話の回想

入力:

```text
前に私が辛いもの好きって話したの覚えてる？
```

出力例:

```json
{
  "focus_scopes": ["user", "topic:food"],
  "mentioned_entities": ["user"],
  "mentioned_topics": ["topic:food"],
  "intent": "reminisce",
  "time_reference": "past"
}
```

### 例 4: 関係そのものの確認

入力:

```text
最近ちょっと距離ある感じする？
```

出力例:

```json
{
  "focus_scopes": ["relationship:self|user", "user"],
  "mentioned_entities": ["user"],
  "mentioned_topics": [],
  "intent": "meta_relationship",
  "time_reference": "recent"
}
```

<!-- Block: Connection -->
## 次に接続するもの

このメモの次は、`RecallHint` を使って `RecallPack` がどう組まれるべきかを、具体的な会話シナリオで検証する段階に進むのがよい。

特に確認したいのは次である。

- 軽量 LLM が `intent` を自然に取り分けられるか
- `3 分以内` と `4-6 発話` の窓が適切か
- `intent` に応じて連想レーンの重み付けが妥当に動くか
- `reminisce` と `commitment_check` が混ざる場面で破綻しないか

そのため、次は `14_想起シナリオ検証メモ.md` のような文書を作るのが自然である。
