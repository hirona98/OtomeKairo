# RecallPack候補収集メモ

<!-- Block: Role -->
## この文書の役割

この文書は、`08_記憶ユニット設計メモ.md`、`09_記憶更新規則メモ.md`、`10_エピソードと想起注入メモ.md` を受けて、`RecallPack` をどう埋めるかを具体化するための設計メモである。

ここで固定したいのは次の 3 点である。

- `RecallPack` の各枠に何を入れるか
- 同期想起でどこまで取りに行くか
- 非同期整理へ回すものをどこで切るか

ここでも、まだ DB スキーマや API は確定しない。
ただし、返答時の想起経路がぶれない程度の中核形は定める。

<!-- Block: Principle -->
## 基本方針

OtomeKairo では、候補収集を「検索経路」からではなく、「今の返答にどの役割の記憶が必要か」から組み立てるほうがよい。

その理由は次である。

- 人格は、検索結果の寄せ集めではなく、想起された文脈で立ち上がる
- `relationship_model` や `active_commitments` は、単純な類似検索より優先度が高い
- 役割の違う記憶を 1 つの巨大候補プールで混ぜると、返答の軸がぶれやすい
- `events` まで毎回広く読みに行くと、重く、しかも直近会話と二重化しやすい

そのため、OtomeKairo では次を基本にする。

- `RecallPack` は section ごとに別ルールで集める
- 同期想起の主役は `memory_units` と `episode_digests` に限定する
- `events` は監査正本であり、通常の返答注入では使わない
- 多段展開や広域探索は、同期想起の標準経路に入れない

<!-- Block: SectionFirst -->
## 先に section を決めてから候補を集める

OtomeKairo では、最初に大きな候補集合を作ってから分類するのではなく、各 section が必要とする候補を先に定義してから集める。

順序としては次である。

1. 現在ターンから `RecallHint` を作る
2. `RecallPack` の各 section を、それぞれ専用ルールで埋める
3. section ごとの上限と全体上限をかける
4. 最後に `conflicts` を付与する

この流れにすると、人格形成で重要な `relationship_model` と `active_commitments` が、単なる話題類似に埋もれにくい。

<!-- Block: Hint -->
## `RecallHint` の最小形

同期想起の入口には、現在ターンを軽く正規化した `RecallHint` を置くほうがよい。

MVP の最小形は次で十分である。

```json
{
  "focus_scopes": ["user", "relationship:self|user", "topic:health"],
  "mentioned_entities": ["user"],
  "mentioned_topics": ["topic:health"],
  "intent": "check_state",
  "time_reference": "recent"
}
```

ここで持ちたいのは次の程度である。

- `focus_scopes`:
  - 今回の返答で主に参照すべき棚
- `mentioned_entities`:
  - 明示的に出た人物、場所、道具
- `mentioned_topics`:
  - 今回の話題キー
- `intent`:
  - 近況確認、相談、回想、約束確認など
- `time_reference`:
  - 最近、過去、次回などの時間手がかり

この `RecallHint` は、現在のユーザー入力と直近会話から作る。
長期記憶そのものは、ここにはまだ混ぜない。

<!-- Block: Roles -->
## `RecallPack` の役割分類

OtomeKairo では、`RecallPack` の各 section を次の 3 系統として扱う。

- 人格文脈を決めるもの:
  - `self_model`
  - `relationship_model`
  - `active_commitments`
- 話題理解を支えるもの:
  - `user_model`
  - `active_topics`
- 根拠と抑制を与えるもの:
  - `episodic_evidence`
  - `conflicts`

この分類は重要である。
返答内容そのものだけでなく、どういう温度感で、どこまで断定してよいかを決めるのは、前半の人格文脈側だからである。

<!-- Block: SelfModel -->
## `self_model` の候補収集

`self_model` は、固定人格設定の再掲ではなく、「記憶から育った自己像」を入れる枠である。

収集元は次に限定する。

- `scope_type = self` の `memory_units`
- `memory_type` は `fact` / `preference` / `interpretation` / `summary`

優先したいものは次である。

- 今の返答姿勢に効く自己理解
- 最近の自分の傾向
- 繰り返し確認されている自己嗜好

逆に、通常は入れないものは次である。

- system prompt に既に含まれる固定人格設定
- 今回の話題に関係しない古い自己情報
- 単発の出来事だけで終わる未整理の感情

MVP では `1-2 件` でよい。
`self_model` は多く入れるものではなく、返答姿勢を少し補正する程度に留める。

<!-- Block: UserModel -->
## `user_model` の候補収集

`user_model` は、現在のユーザー理解に直接効く枠である。

収集元は次を基本にする。

- `scope_type = user` の `memory_units`
- 必要なら、明示的に話題化された `entity` scope の `memory_units`

優先順は次がよい。

1. `RecallHint.mentioned_topics` と一致するもの
2. `RecallHint.intent` に関係するもの
3. 直近で reinforce / refine されたもの
4. 長期に安定した高 `confidence` のもの

ここで重要なのは、単なるプロフィール一覧にしないことである。
今回の返答に効く理解だけを出す。

MVP では `2-4 件` で十分である。

<!-- Block: RelationshipModel -->
## `relationship_model` の候補収集

`relationship_model` は、OtomeKairo で特に重要な枠である。
ユーザーへの距離感、信頼、気遣い、緊張、踏み込み方はここで決まりやすい。

収集元は次を基本にする。

- `scope_type = relationship`
- `scope_key = self|user`
- `memory_type` は `relation` / `interpretation` / `summary`

優先したいものは次である。

- 直近で変化した関係理解
- 継続して効いている距離感や信頼感
- 今回の話題で特に慎重さが必要になる関係情報

MVP では `1-3 件` に絞る。
この枠は量より一貫性が重要である。

<!-- Block: ActiveTopics -->
## `active_topics` の候補収集

`active_topics` は、「今まだ生きている話題」を返答へ持ち込むための枠である。

収集元は次を基本にする。

- `scope_type = topic` の `memory_units`
- `episode_digests.open_loops`

優先順は次がよい。

1. 今回明示的に触れられた話題
2. 直近で未完了のまま残っている話題
3. `user_model` や `relationship_model` で選ばれた項目に接続する話題

ここで入れたいのは「話題の現状態」であり、出来事の羅列ではない。
そのため、できるだけ `summary` や `commitment` に近い短文へ寄せる。

MVP では `1-3 件` でよい。

<!-- Block: ActiveCommitments -->
## `active_commitments` の候補収集

`active_commitments` は、通常の類似検索より常に優先されるべき枠である。

収集元は単純でよい。

- `memory_type = commitment`
- `status` は有効なもののみ

優先順は次を推奨する。

1. 今回の話題に直接関係する約束
2. 期限や次回確認を含むもの
3. 関係維持に影響するもの

この枠は、現在ターンとの類似が弱くても確認対象になりうる。
約束は人格の連続性に直結するためである。

MVP では `0-3 件` とする。
ただし、強い未完了がある場合は 0 件にしない。

<!-- Block: EpisodicEvidence -->
## `episodic_evidence` の候補収集

`episodic_evidence` は、単独で広く取りに行くのではなく、先に選ばれた `memory_units` の根拠を補う形で付与するほうがよい。

通常経路では、次の順序にする。

1. 選ばれた `memory_units.evidence_event_ids` に対応する `episode_digests` を引く
2. その中から今回の話題に最も近いものを絞る

例外的に、次のときだけ `episode_digests` 先行で拾ってよい。

- ユーザーが過去の会話や出来事を直接尋ねている
- 「前に話したことを覚えているか」が主題になっている
- 時期や経緯の説明が返答の中心になる

それ以外では、`episode_digests` を主役にしない。
OtomeKairo の返答の中心は継続理解であり、回想の断片ではないからである。

MVP では通常 `0-2 件`、回想系の意図では `1-4 件` 程度でよい。

<!-- Block: Conflicts -->
## `conflicts` の候補収集

`conflicts` は、別の検索経路で集めるのではなく、選ばれた候補集合の中から検出する。

見るべきものは次である。

- 同じ比較キーで内容が反転しているもの
- 新旧が競合しているもの
- `confirmed` と `inferred` が衝突しているもの
- 同じ対象について時期違いで揺れているもの

`conflicts` の役割は、情報量を増やすことではない。
断定を止め、確認寄りに寄せるためのガードである。

MVP では `0-2 件` で十分である。

<!-- Block: CollectionFlow -->
## 同期想起の標準フロー

MVP の同期想起は、次の流れに抑えるのがよい。

1. `RecallHint` を生成する
2. `active_commitments` を先に確認する
3. `relationship_model` を確認する
4. `user_model` / `self_model` / `active_topics` を section ごとに集める
5. 選ばれた `memory_units` に対応する `episodic_evidence` を付与する
6. 候補集合の中で `conflicts` を検出する
7. section ごとの上限と全体上限をかけて `RecallPack` を確定する

この順番にすることで、約束と関係理解を、単なる話題検索より前に置ける。

<!-- Block: Expansion -->
## 同期想起で許す拡張は 1 段までにする

同期想起で無制限に辿ると、重くなるだけでなく、人格文脈がぼやけやすい。
そのため、MVP では拡張を 1 段までに制限する。

許してよいのは次である。

- `memory_unit` から根拠 `episode_digest` への展開
- 選ばれた `topic` から直結する `commitment` への展開
- 明示的に出た `entity` から同一 `scope` の `memory_units` を拾うこと

標準経路に入れないものは次である。

- 複数 hop のリンク展開
- `events` への再帰的遡り
- 広域ベクトル検索による大量補充
- 反省生成途中の中間産物をそのまま注入すること

特に、MVP ではベクトル検索を同期想起の必須条件にしないほうがよい。
まずは `scope` と `memory_type` に基づく収集で安定させる。

<!-- Block: Budget -->
## 返答注入の全体上限

長い内部コンテキストは、それだけで有利とは限らない。
OtomeKairo では、`RecallPack` 全体を小さく保つほうがよい。

MVP の目安は次とする。

- 合計 `8-14 件`
- 長文本文ではなく、短い `summary_text` を中心にする
- `episodic_evidence` は補助に留める

これにより、返答モデルが「何が今重要か」を見失いにくくなる。

<!-- Block: PriorityShift -->
## 意図によって優先順を少し変える

基本優先順は次である。

1. `active_commitments`
2. `relationship_model`
3. `user_model`
4. `active_topics`
5. `self_model`
6. `episodic_evidence`
7. `conflicts`

ただし、これは固定ではなく、意図によって少し動かす。

- 過去回想が主題:
  - `episodic_evidence` を引き上げる
- 約束確認が主題:
  - `active_commitments` を最優先に固定する
- 感情的な相談:
  - `relationship_model` と `user_model` の重みを上げる

ここでも、section の存在は変えない。
変えるのは優先順だけでよい。

<!-- Block: AsyncBoundary -->
## 非同期整理へ回すもの

同期想起でやらないほうがよい処理は、先に明示しておく。

非同期整理へ回すべきものは次である。

- `events` からの `episode_digests` 生成
- `episode_digests` から `memory_units` への育成
- `summary` 系 `memory_units` の生成
- `salience` の経時調整
- 関係リンクや話題リンクの再編
- 将来的な意味検索用インデックス更新

この分離により、返答の同期経路は短く保てる。
また、人格形成は非同期に育ち、返答時はその結果を読むだけにしやすい。

<!-- Block: Observability -->
## 観測したい項目

後で挙動を検証できるように、少なくとも次は残したほうがよい。

- 生成した `RecallHint`
- section ごとの候補件数
- 実際に採用した項目と `why_now`
- 上限で落とした項目
- `conflicts` に入った組み合わせ

OtomeKairo は、想起の成否が人格の連続性に直結する。
そのため、候補収集は結果だけでなく、途中判断も追える形にするべきである。

<!-- Block: ExternalIdeas -->
## 外部手法から採る点

この設計では、外部手法から次の観点だけを採る。

- Generative Agents:
  - 出来事の蓄積、反省、動的想起の三層構造
- MemoryBank:
  - 長期対話での継続更新と重要度を見た保持
- MemGPT:
  - 速い想起層と遅い保持層を分ける発想
- Lost in the Middle:
  - 注入量が増えるほど使われるとは限らないという前提

ただし、OtomeKairo ではそれらをそのまま実装形へ写すのではなく、`RecallPack` の section ごとに必要記憶を集める構造へ寄せる。

<!-- Block: References -->
## 参考

- [Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442)
- [MemoryBank: Enhancing Large Language Models with Long-Term Memory](https://arxiv.org/abs/2305.10250)
- [MemGPT: Towards LLMs as Operating Systems](https://arxiv.org/abs/2310.08560)
- [Lost in the Middle: How Language Models Use Long Contexts](https://arxiv.org/abs/2307.03172)
