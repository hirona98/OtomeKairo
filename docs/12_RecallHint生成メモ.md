# RecallHint生成メモ

<!-- Block: Role -->
## この文書の役割

この文書は、`11_RecallPack候補収集メモ.md` で置いた `RecallHint` を、実際にどう生成するかを具体化するための設計メモである。

ここで固定したいのは次の 3 点である。

- `RecallHint` が何をするためのものか
- どの入力から何を抽出するか
- 曖昧なときにどう単純化するか

ここでも、まだ API や実装方式は確定しない。
ただし、後で DB や内部コンテキスト契約へ落とせる程度の最小形は定める。

<!-- Block: Position -->
## `RecallHint` の位置づけ

`RecallHint` は、長期記憶そのものではない。
また、返答文の下書きでもない。

OtomeKairo では、`RecallHint` を「現在ターンから同期想起の向き先を決めるための軽い正規化結果」として扱う。

役割は次である。

- 構造レーンでどの `scope` を優先して見るかを決める
- 連想レーンにどの方向の問い合わせをかけるかを決める
- どの `RecallPack` section を重くするかを決める
- 回想寄りか、確認寄りか、相談寄りかを粗く決める

逆に、ここでやらないことは次である。

- 長期記憶を読んで深い推論をすること
- 人格判断そのものを確定すること
- 返答内容を組み立てること

`RecallHint` は、深い理解の器ではなく、想起の入口である。

<!-- Block: Principle -->
## 基本方針

`RecallHint` は賢くしすぎないほうがよい。
OtomeKairo では、次を基本方針にする。

- 現在のユーザー入力を最優先する
- 直近会話は補助に使う
- 長期記憶は参照しない
- 迷ったら項目を減らす
- 迷ったら広げるよりも保守的に寄せる

ここで重要なのは、`RecallHint` 自体は長期記憶を読まないが、後続の構造レーンと連想レーンの両方の入口になることである。

特に重要なのは、`RecallHint` が長期記憶を読まないことである。
ここで長期記憶まで混ぜると、候補収集の入口と本体が循環してしまう。

<!-- Block: Inputs -->
## 入力に使うもの

`RecallHint` の生成入力は、次の 3 つで十分である。

1. 現在のユーザー入力
2. 直近会話
3. 現在時刻

それぞれの役割は次である。

- 現在のユーザー入力:
  - 主題
  - 意図
  - 明示対象
  - 時間表現
- 直近会話:
  - 代名詞の補完
  - 話題継続の判定
  - 省略された対象の補完
- 現在時刻:
  - 「昨日」「来週」などの時間表現の正規化

MVP では、直近会話は最後の `4-6 発話` 程度に留めるのがよい。
長く見すぎると、今の入力より前の流れに引っ張られやすい。

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
| `mentioned_entities` | 明示または直近文脈で補完できる対象 |
| `mentioned_topics` | 話題キー |
| `intent` | 想起優先順を少し動かすための粗い意図 |
| `time_reference` | 回想や未来参照を判定するための時間軸 |

`intent` と `time_reference` は必ず 1 つだけ持つ。
その他は空でもよい。

<!-- Block: VectorLink -->
## `RecallHint` は連想レーンの制御にも使う

`RecallHint` 自体にベクトル候補を入れる必要はない。
ただし、後続の連想レーンでは `RecallHint` を次のように使う。

- `focus_scopes`:
  - ベクトル hit をどの section へ寄せるかの補助に使う
- `mentioned_entities` / `mentioned_topics`:
  - 問い合わせテキストの補強に使う
- `intent`:
  - 連想レーンの重みを上下させる
- `time_reference`:
  - `recent` / `past` / `future` のどこを厚く見るかを決める

つまり、`RecallHint` はベクトル問い合わせそのものではないが、連想方向の制御キーではある。

<!-- Block: ScopeRule -->
## `focus_scopes` の生成規則

`focus_scopes` は、今回の返答でまず見るべき棚を順序付きで表す。

MVP では、最大 `3 件` に抑える。
候補は次から選ぶ。

- `self`
- `user`
- `entity:<normalized_ref>`
- `topic:<normalized_topic>`
- `relationship:self|user`
- `world`

生成ルールは次とする。

1. 現在入力に明示対象があるなら、それを優先する
2. ユーザー自身の状態、嗜好、事情が主題なら `user` を入れる
3. 自分とユーザーの距離感、態度、接し方が主題なら `relationship:self|user` を入れる
4. 特定話題が主題なら対応する `topic:*` を入れる
5. 自分について尋ねられたときだけ `self` を入れる
6. 特定対象がなく全体状況の判断が主題なら `world` を入れる

優先順の基本は次である。

1. 明示対象
2. `relationship:self|user`
3. `user` または `self`
4. `topic:*`
5. `world`

ここで重要なのは、`focus_scopes` を広げすぎないことである。
曖昧なときは、`user` と `relationship:self|user` を優先し、無理に `world` まで足さない。

<!-- Block: EntityRule -->
## `mentioned_entities` の生成規則

`mentioned_entities` は、今回の入力で参照対象として明示されたものを持つ。

MVP では、最大 `3 件` に抑える。

拾ってよいものは次である。

- 明示的に出た人物
- 明示的に出た場所
- 明示的に出た道具やサービス
- 直近会話で一意に補完できる代名詞対象

拾わないほうがよいものは次である。

- 長期記憶がないと解釈できない対象
- 直近会話でも一意に補完できない「あの人」「あれ」
- ただの一般名詞

例:

- 「田中さんとは最近どう？」
  - `mentioned_entities = ["person:tanaka"]`
- 「あの件まだ覚えてる？」
  - 直近会話で特定できるなら補完
  - 特定できないなら空のまま

<!-- Block: TopicRule -->
## `mentioned_topics` の生成規則

`mentioned_topics` は、今回の会話の主題キーを持つ。

MVP では、最大 `2 件` に抑える。
大量に出すと、`active_topics` 側が広がりすぎる。

生成ルールは次とする。

1. 現在入力に明示的な主題があるなら採る
2. 明示主題が弱いときだけ、直近会話の継続話題を 1 件まで補完する
3. 同格の候補が複数あるときは、現在入力でより具体的なものを採る

例:

- 「最近ちゃんと眠れてない」
  - `mentioned_topics = ["topic:health"]`
- 「この前のゲーム制作の話だけど」
  - `mentioned_topics = ["topic:game_dev"]`

`mentioned_topics` は分類ラベルであり、説明文ではない。
ここで長い要約を持たない。

<!-- Block: IntentRule -->
## `intent` の最小セット

`intent` は、返答意図を粗く表す。
MVP では増やしすぎないほうがよい。

最小セットは次とする。

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

分類ルールは単純でよい。

- 約束、今度、また後で、覚えてるか:
  - `commitment_check` または `reminisce`
- つらい、悩んでいる、どうしたらいい:
  - `consult`
- 最近どう、元気、眠れてる:
  - `check_state`
- 好き、苦手、どっち派:
  - `preference_query`
- 覚えてる、前に話した:
  - `reminisce`
- 私たち、距離感、どう思う:
  - `meta_relationship`
- それ以外:
  - `smalltalk` または `fact_query`

曖昧なときは、派手な意図に倒さない。
たとえば、軽い雑談を無理に `consult` 扱いしない。

<!-- Block: TimeRule -->
## `time_reference` の最小セット

`time_reference` は、想起でどの時間帯を優先するかを決めるために使う。

MVP の最小セットは次とする。

| `time_reference` | 意味 |
|------|------|
| `current` | 今この場の話 |
| `recent` | 最近の継続状態や近い過去 |
| `past` | 過去の出来事や以前の会話 |
| `future` | 今後、次回、予定 |
| `persistent` | 長期に安定した属性や傾向 |

分類ルールは次とする。

- 今、いま、今日は:
  - `current`
- 最近、このところ、ここ数日:
  - `recent`
- 前に、この前、昔、覚えてる:
  - `past`
- 今度、次回、来週、そのうち:
  - `future`
- いつも、普段、基本的に:
  - `persistent`

明示表現が無い場合は、`current` を基本にする。
ただし、入力が近況確認なら `recent` を優先してよい。

<!-- Block: Flow -->
## 生成フロー

MVP の生成フローは次で十分である。

1. 現在入力から明示対象、主題語、時間語を拾う
2. 直近会話から代名詞補完と話題継続だけを補う
3. `intent` を 1 つ決める
4. `time_reference` を 1 つ決める
5. `focus_scopes` を最大 3 件まで決める
6. `mentioned_entities` と `mentioned_topics` を上限内で確定する
7. 後続の連想レーンで使う重み付け条件を `intent` / `time_reference` から読める形にする

重要なのは、`focus_scopes` を最後に決めることである。
先に `intent` と `time_reference` を決めておくと、必要な棚を選びやすい。

<!-- Block: Precedence -->
## 優先規則

ルールが衝突したときは、次の優先順で処理する。

1. 現在入力の明示表現
2. 現在入力の文全体の意図
3. 直近会話で一意に継続している対象
4. デフォルト規則

これにより、直近会話が強すぎて、現在入力の主題を上書きする事故を減らせる。

<!-- Block: Conservative -->
## 保守的に倒すための禁止事項

`RecallHint` は、過剰に賢くすると破綻しやすい。
そのため、MVP では次を禁止したほうがよい。

- 長期記憶を読んで対象を補完すること
- 長期記憶の本文をベクトル問い合わせに混ぜること
- 1 回の入力から多数の `topic` を立てること
- 明示されていない関係変化を `meta_relationship` と断定すること
- 曖昧な回想を無理に特定エピソードへ寄せること

迷ったら、より狭く、より弱く出す。
`RecallHint` は取りこぼしを完全になくすためのものではなく、同期想起を破綻させないための入口である。

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

- `intent` に応じて section の優先順が妥当に動くか
- `intent` に応じて連想レーンの重み付けが妥当に動くか
- `focus_scopes` の最大 3 件で十分か
- `reminisce` と `commitment_check` が混ざる場面で破綻しないか

そのため、次は `13_想起シナリオ検証メモ.md` を作るのが自然である。
