# `RecallPack` の LLM 選別

## 位置づけ

この文書は `RecallPack` 候補選別の完成形を定める。
現在地は [../../plan/01_現行計画.md](../../plan/01_現行計画.md) を正とする。
LLM 補助処理の共通境界、source pack、出力、failure、inspection の共通ルールは [../19_LLM補助契約共通.md](../19_LLM補助契約共通.md) を正とする。
背景で述べる fixed priority / fixed boost は、導入前の比較対象として扱う。

## 背景

導入前の `RecallPack` は、候補収集までは `RecallHint` と deterministic な query / filter で絞り、
最終採用は固定ロジックで決めている。

導入前の実装では、少なくとも次がロジック主体だった。

- `primary_recall_focus` ごとの fixed な section priority
- `secondary_recall_focuses` による fixed な section boost
- `association_query_weight()` による query 種別重み
- `association_score()` による候補 score 補正
- `conflicts.summary_text` の固定文

この方式は、初期段階では次の点で有利だった。

- 壊れにくい
- trace と inspection で追いやすい
- recall の責務境界を早く固めやすい

一方で、このまま本命実装にすると次の問題が出る。

- focus ごとの差が fixed priority に閉じ、文脈の細かな違いに追従しにくい
- `secondary_recall_focuses` や `time_reference` の効き方が、固定 boost の足し算に潰れやすい
- `association` 候補の意味的な良し悪しを、distance と手書き補正だけで扱い続けることになる
- `conflicts.summary_text` が常に同じ文面になり、何が競合しているかが見えにくい
- repo 全体の LLM 判断優先方針に対して、想起の中心選別だけがロジック寄りのまま残る

そのため、`RecallPack` のうち **意味的な候補選別、section 配置、`conflicts` 文面** を LLM へ分離する。

## 採用方針

OtomeKairo では、`RecallPack` 全体を LLM 任せにはしない。

- 候補 retrieval はコードで行う
- `scope` / `memory_type` / `status` / `commitment_state` による候補境界はコードで守る
- vector 検索、検索上限、pre-limit、dedupe、global limit はコードで守る
- section 名と section への所属規則はコードで固定する
- `conflicts` の検出自体はコードで行う
- **どの候補を採るか、どの section を前に置くか、`conflicts` をどう短く説明するか** だけを LLM へ任せる

要するに、`何が候補になりうるか` はコードが決め、`今どれがより効くか` だけを LLM に任せる。

## 目的

- `RecallPack` の採用順を fixed weight ではなく意味的な優先度で決める
- `interaction_mode` / `primary_recall_focus` / `secondary_recall_focuses` / `time_reference` を、硬い if 分岐ではなく文脈判断として効かせる
- `association` 候補を補助レーンのまま保ちつつ、採否そのものは意味的に決められるようにする
- `conflicts.summary_text` を固定文ではなく、競合の中身が分かる短い説明にする
- deterministic な境界、inspection、監査構造は壊さない

## 対象範囲

この設計の対象は次だけである。

- `RecallPack` 候補群の最終採用
- section ごとの優先配置
- `conflicts.summary_text`
- source pack 構築
- LLM 入出力契約
- 失敗時の扱いと観測

## 対象外

この設計では次は変えない。

- 構造レーンと連想レーンの存在
- embedding query の生成そのもの
- vector 検索の実行
- `active_commitments` や `episodic_evidence` の候補条件
- section 名の集合
- `event_evidence` の生成
- `decision_generation` と `expression_generation`
- `MemoryActionResolver` や記憶更新の状態遷移

## 基本構成

`RecallPack` の LLM 選別は、次の 4 段に分ける。

1. コードが構造レーンと連想レーンで候補群を集める
2. コードが request-local な candidate ref を持つ source pack を作る
3. `recall_pack_selection` role が section 配置と採用候補を返す
4. コードが ref を実レコードへ戻し、dedupe / section limit / global limit を強制して `RecallPack` を確定する

ここで重要なのは、LLM が **候補の外側を増やさない** ことである。
新しい候補、未知 section、未検出 conflict を作ってはいけない。

## 追加する論理 role

この機能では、モデルプリセットに `recall_pack_selection` という論理 role を追加する。

この role の責務は次だけである。

- 候補群の中から、どの候補をどの順で `RecallPack` に採るべきかを返す
- `conflicts` 候補に対して短い `summary_text` を返す

この role を `input_interpretation` や `decision_generation` から分ける理由は次である。

- 想起の中心選別と、会話判断の責務を分離できる
- recall 品質の改善が decision prompt に波及しにくい
- 回帰時に「候補収集」と「候補選別」を分けて追える

## source pack の設計

LLM に渡すのは raw DB row 群ではなく、候補群を request-local ref へ変換した source pack とする。

最低限、次を含める。

```json
{
  "input_text": "この前の続きだけど、どう進める？",
  "recall_hint": {
    "interaction_mode": "conversation",
    "primary_recall_focus": "commitment",
    "secondary_recall_focuses": ["episodic"],
    "time_reference": "past",
    "focus_scopes": ["relationship:self|user"],
    "mentioned_entities": [],
    "mentioned_topics": [],
    "risk_flags": ["mixed_intent"]
  },
  "constraints": {
    "global_recall_limit": 14,
    "section_limits": {
      "self_model": 2,
      "user_model": 4,
      "relationship_model": 3,
      "active_topics": 2,
      "active_commitments": 3,
      "episodic_evidence": 2,
      "conflicts": 2
    }
  },
  "candidate_sections": [
    {
      "section_name": "active_commitments",
      "candidates": [
        {
          "candidate_ref": "candidate:active_commitments:1",
          "source_kind": "memory_unit",
          "retrieval_lane": "structured",
          "summary_text": "また体調の話の続きをする流れが残っている。",
          "memory_type": "commitment",
          "scope_type": "relationship",
          "scope_key": "self|user",
          "commitment_state": "open",
          "salience": 0.88
        }
      ]
    },
    {
      "section_name": "episodic_evidence",
      "candidates": [
        {
          "candidate_ref": "candidate:episodic_evidence:1",
          "source_kind": "episode",
          "retrieval_lane": "association",
          "summary_text": "前回の相談の続きとして様子を確認した。",
          "primary_scope_type": "relationship",
          "primary_scope_key": "self|user",
          "open_loops": ["体調の変化をまた確認する"],
          "salience": 0.82
        }
      ]
    }
  ],
  "conflicts": [
    {
      "conflict_ref": "conflict:1",
      "compare_key": {
        "memory_type": "commitment",
        "scope_type": "relationship",
        "scope_key": "self|user",
        "subject_ref": "self",
        "predicate": "talk_again"
      },
      "variant_summaries": [
        "また体調の話の続きをする流れが残っている。",
        "いったん休んでから改めて話すつもりになっている。"
      ]
    }
  ]
}
```

入力の原則は次である。

- `candidate_ref` と `conflict_ref` は request-local な参照であり、永続 ID をそのまま渡さない
- 候補は section ごとに分けて渡す
- section 名は canonical なものだけを使う
- 各 candidate は `summary_text` と意味判断に効く最小の構造化項目に絞る
- `retrieval_lane` は残し、`association` 候補が補助レーンであることは downstream にも保つ
- 現行の `association_score` や query 種別は、source pack に残すが、本命判断値としては育てない
- `conflicts` には compare key と variant の短い summary だけを入れ、memory unit の内部 ID は渡さない

## LLM 出力契約

LLM の出力は JSON object 1 個に固定する。

```json
{
  "section_selection": [
    {
      "section_name": "active_commitments",
      "candidate_refs": ["candidate:active_commitments:1"]
    },
    {
      "section_name": "episodic_evidence",
      "candidate_refs": ["candidate:episodic_evidence:1"]
    }
  ],
  "conflict_summaries": [
    {
      "conflict_ref": "conflict:1",
      "summary_text": "続けて話す流れと、いったん区切る流れの理解が並んでいる。"
    }
  ]
}
```

契約は次とする。

- 必須キーは `section_selection` と `conflict_summaries` の 2 つ
- `section_selection` は配列
- 各要素は `section_name` と `candidate_refs` を持つ
- `section_name` は `self_model / user_model / relationship_model / active_topics / active_commitments / episodic_evidence` のいずれかで、重複しない
- `candidate_refs` は source pack 内に存在する ref だけを使う
- `candidate_refs` は section をまたいで重複しない
- candidate は元の所属 section から移動させない
- `conflict_summaries` の `conflict_ref` も source pack 内に存在する ref だけを使う
- `summary_text` は 1 文、改行なし、内部識別子なし、固定文の繰り返しではない

最終的な `RecallPack` では、コード側が ref を実 candidate へ戻し、同じ shape に射影する。

## プロンプト方針

system prompt では、少なくとも次を明示する。

- あなたは `RecallPack` の候補選別だけを行う
- 候補外のものを足さない
- section 名を発明しない
- `primary_recall_focus` を主軸にし、`secondary_recall_focuses` は軽い補助に留める
- `risk_flags` があるときは、広く拾うより断定を抑える
- `association` 候補は使えても、構造候補より無条件に優先しない
- `primary_recall_focus=commitment` では open loop や active commitment を重く見やすくする
- `primary_recall_focus=episodic` や `time_reference=past` では `episodic_evidence` を前へ置きやすくする
- `conflicts.summary_text` では、何が競合しているかを短く説明する
- 比較不能なら候補を広く並べるより、少なく選ぶ

user prompt では、入力文、`RecallHint`、constraint、候補 sections、conflicts をそのまま構造化で渡す。

## 処理フロー

実装時の flow は次とする。

1. 既存ロジックで構造候補と連想候補を集める
2. 既存ロジックで `conflicts` 候補を検出する
3. source pack 用に candidate ref / conflict ref を振る
4. `recall_pack_selection` role を呼ぶ
5. parse / contract が崩れたときだけ repair prompt で 1 回だけ再試行する
6. `section_selection` を実 candidate へ戻す
7. コード側で dedupe / section limit / global limit を強制する
8. `conflict_summaries` を対応する conflict 候補へ反映する
9. `selected_memory_ids` / `selected_episode_ids` / `selected_event_ids` を既存どおり計算する
10. trace と retrieval run へ結果を記録する

## 失敗時の扱い

この設計では fallback 選別を入れない。
LLM が失敗したときに、古い fixed section priority や fixed score へ戻すことはしない。

この設計の失敗は、`event_evidence` と違って optional ではない。
`RecallPack` の中心選別そのものを置き換えるため、失敗時は recall step を失敗として扱う。

- `recall_pack_selection` が repair 1 回後も parse / contract を満たせなければ、その cycle は `internal_failure` にする
- `retrieval_runs.result_status=failed` とし、failure reason を残す
- `events` には `recall_pack_selection_failure` を残す
- 失敗後に old selection ロジックや空の `RecallPack` で続行しない

一方で、**contract は通るが deterministic 制約で一部 ref が落ちる** ケースは failure ではない。
この場合は trace に dropped ref を残して継続する。

## 観測と監査

inspection で追いやすくするため、`cycle_trace.recall_trace` に `recall_pack_selection` を追加する。

最低限、次を持つ。

- `candidate_section_counts`
- `selected_section_order`
- `selected_candidate_refs`
- `dropped_candidate_refs`
- `conflict_summary_count`
- `result_status`
- `failure_reason`

`retrieval_runs.payload_json` には、少なくとも次を追加する。

- `recall_pack_selection.result_status`
- `recall_pack_selection.selected_section_order`
- `recall_pack_selection.selected_candidate_count`
- `recall_pack_selection.dropped_candidate_count`

監査 event として `recall_pack_selection_failure` を追加する。
この event は `RecallHint` failure と同様に、cycle 単位の失敗理由を追えるようにする。

## `03_想起と判断.md` との関係

`docs/design/memory/03_想起と判断.md` は、`RecallPack` の意味境界と section 構成を定める上位設計である。

この文書は、その候補選別と section 配置を LLM で行うための実装設計として次を具体化する。

- role
- source pack
- structured output 契約
- failure の扱い
- inspection と監査への出し方

この機能で実装上やることは、要するに次である。

- fixed priority / fixed boost / fixed rerank を本命として育てない
- candidate retrieval と deterministic 制約はコード側へ残す
- 最終採用と `conflicts` 文面だけを専用 role へ分離する
- 失敗を fallback で隠さず、cycle failure として監査へ出す

## この設計で守ること

- `RecallPack` の section 名と意味境界は変えない
- LLM は候補の外側を増やさない
- `association` 候補は補助レーンのまま扱う
- deterministic な件数制約は最後に必ずコード側で強制する
- 失敗を旧ロジックでごまかさない
