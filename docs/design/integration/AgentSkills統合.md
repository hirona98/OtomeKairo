# Agent Skills 統合

Agent Skills の発見、選択、文脈適用、resource 読込、script 実行に関する意味境界の正本。
設定の HTTP wire は [状態と設定](../api/状態と設定.md)、capability wire は [実行連携](../api/実行連携.md) を正本とする。

## 目的

OtomeKairo は、外部ディレクトリに配置された [Agent Skills](https://agentskills.io/specification) を汎用形式で利用する。
特定の配布元、skill 名、MCP server、tool 名を loader に組み込まない。
[ELYTH Remote MCP Skills](https://github.com/Divedesign/elyth-remote-mcp-skills) は利用可能な source の一例であり、repository 内へ vendoring しない。

Agent Skill は人格設定や記憶の代替ではない。現在の個が特定作業を行う際の専門ワークフローとして、選択された判断サイクルと autonomous run step にだけ加える。

## source と package

`agent_skill_source` は絶対 `root_path` を持つ。enabled source の直下にある各ディレクトリを skill package とし、その直下の `SKILL.md` を入口にする。

- `SKILL.md` は UTF-8 とし、YAML frontmatter の `name / description` を必須にする
- `name` は Agent Skills specification の形式に従い、package directory 名と一致させる
- loader が受理する任意 frontmatter field は `license / compatibility / metadata / allowed-tools` とする
- package 内の通常 file を resource とし、`scripts/` 配下または executable bit を持つ file を script と分類する
- source root、package directory、file、参照先の symlink と source root 外への path escape を拒否する
- enabled source が読めない、skill がない、frontmatter や参照が不正、enabled source 間で `name` が重複する場合は、別 source へ切り替えず明示的に失敗する

server 起動時と設定全体置換時に immutable registry snapshot を作る。明示的な reload は全 source の次 snapshot を検証し、全体が成功した場合だけ runtime registry を一括交換する。部分 reload は行わない。

## LLM による選択と progressive disclosure

skill の適用可否は固定文字列や keyword 表では決めない。通常判断と各 autonomous run step の前に、LLM が向きである current input、直近会話、作業記録、run 目的、capability selection summary と `name / description` catalog を比較して必要な skill を選ぶ。`wake / background_thinking` の通常判断で due な気にかけていることがあるときは、`concern_summary` そのものを `orientation_context.standing_concerns[].summary_text` として追加の向きの材料にする。`factor_ref` と `summary_text` 以外の workspace 判断盤面は Skill 選択へ渡さない。
capability selection summary は skill の必要性を比較するための短い view である。capability 共通の識別、種類、利用可否、短い説明、risk と unavailable 理由を持ち、MCP は server の利用可否と tool の `name / description`、vision source は識別、利用可否、対応操作だけを持つ。最終実行用の input schema、manifest 条件、権限列、readiness は載せない。最終的な capability request は完全な capability decision view を受ける decision または autonomous step が組み立て、server が従来どおり検証する。
人物発話の向きでは、直近会話と作業記録を見ずに skill を選ばない。向きと到着の分離は [../llm/プロンプト文脈分離方針.md](../llm/プロンプト文脈分離方針.md) を正とする。

`orientation_context.standing_concerns` は実行指示ではなく、しばらく関わっていない気にかけていることである。current input はこの cycle の向きの本体のままとし、関心があることだけで skill を必須にしない。関心の向き全体に合う workflow があるときは、複合目的を始める判断材料として workflow も比較する。ただし workflow の将来の各手順を現在必要な material として先読みしない。意味境界は [気にかけていること](../runtime/気にかけていること.md) を正とする。

選択文脈は `selection_horizon=current_decision / current_autonomous_step` を持つ。前者は今回の通常判断、後者は run の次の一手を選択範囲にする。選択済み workflow 本文から linked skill や resource を読む場合も、この範囲で現在必要なものだけを選ぶ。将来の step が現在になったときは、その時点の run、作業記録、catalog から改めて選択する。機械的な件数上限は設けず、現在の一手に複数 skill が必要なら同時に選んでよい。

比較を分けた `wake / background_thinking` では、自身の活動の Skill 選択は隔離済み current input と `orientation_context` で行う。人物側の視覚観測、直近会話、観測 work_log は渡さない。外向き比較には Agent Skill context を渡さない。比較の材料境界は [../runtime/判断と行動.md](../runtime/判断と行動.md) を正とする。

選択は次の順で行う。

1. 全 skill の `name / description / source_id / digest` と、その catalog から作った `allowed_skill_ids` から、`selection_horizon` の現在の一手に必要な skill を選ぶ。capability selection summary は必要性の判断材料とし、capability id を skill id として返さない
2. 選択した `SKILL.md` 本文と resource catalog、本文から直接参照された sibling skill 候補を提示する
3. LLM が `allowed_additional_skill_ids` と `allowed_resource_reads` から現在の一手に必要な追加 skill と UTF-8 text resource だけを選ぶ。すでに `active_skills` にある skill_id を `additional_skill_ids` に含めた場合は追加済みとして扱い、候補外とはしない。選択した resource 本文と追加 skill 本文を次の選択へ渡し、両方の選択結果が空になるまで段階的に読む。sibling `SKILL.md` へのリンクは追加 skill 候補であり resource path として扱わず、script と binary resource は本文読込候補に含めない
4. 選択済み instructions と resource を trusted Agent Skill system context として decision、expression、autonomous step に渡す

capability request と autonomous run の起点には instructions 本文ではなく `source_id / skill_id / digest` の activation summary を残す。result follow-up と次 run step の選択 LLM へこの summary を前回文脈として渡し、現在の catalog と目的を基に再選択させる。古い本文を暗黙再利用しない。

material 選択の出力は `additional_skill_ids / resource_reads / reason_summary` とし、終了状態を別 field で返させない。追加 skill と resource の選択が両方空なら読込完了、どちらかが選ばれた場合は候補を消費して次の選択へ進む。候補は有限であり、未読候補がなくなれば選択を終了する。

選択結果が catalog 外の skill id または提示した候補外の skill/resource path を含む場合は、同じ source pack と validator error を使って 1 回だけ repair する。repair 後も契約違反や候補違反が残る場合は判断サイクルを失敗させる。暗黙に skill なしへ戻さない。

Agent Skill context は host の役割、出力契約、capability availability、安全境界、観測事実を上書きしない。`skill_id` は `capability_id` でも MCP `tool_name` でもない。skill に書かれた tool 名は capability との固定対応表ではなく、実行時 catalog と manifest に基づいて通常どおり LLM が capability request を組み立てる。

## ホスト許可

skill が Human の明示依頼、trusted host policy、trusted workflow を求めるとき、ホストはその許可を `host_authorization` として選択と適用の両方へ渡す。

`host_authorization.kind` は次のいずれかである。

| kind | 意味 |
| --- | --- |
| `current_individual_decision` | いまの個がこの判断で働きかける許可。`wake` / `background_thinking` 起点、またはそこから始まった run / capability result |
| `person_request` | 人物の明示依頼、またはそこから続く作業 |
| `none` | 上記の許可がこの入力から立っていない |

判定は `sender_kind`、`response_target_refs`、`trigger_kind`、`source_kind`、`run.origin_kind` の閉じた値だけで行う。自然文や skill 名では判定しない。

`current_individual_decision` は、skill が求める trusted host policy / trusted workflow である。Human の明示依頼が無いことだけを理由に公開や送信を見送らない。送信前チェック、catalog、host の出力契約、秘密情報の境界は上書きしない。公開は今この判断の範囲で一度だけ行う。

## script 実行と信頼境界

source の `enabled=true` は、その root 全体を instructions、resource、code execution まで信頼する明示設定である。script 実行を source 有効化から分けない。個別 script の都度承認は設けない。

script は `agent_skill.run_script` capability からだけ実行する。これは skill の管理軸ではなく、MCP の `mcp.call_tool` と同じ実行の入口である。server process 内で import や eval をせず、専用 runner process が次を再検証して実行する。

- source、skill、package digest、script resource が現在の registry snapshot と一致する
- source が enabled であり、registry に載っている
- package を request 固有の `agent-skill-runs/<request-id>/workspace/` へコピーし、copy 内の script を実行する
- 実行 interpreter は OtomeKairo ホストの Python（`sys.executable`）に固定する。source ごとの runtime 選択は設けない
- child environment は `PATH / LANG / LC_ALL` だけにする
- wall time、CPU time、address space、process 数、open file 数、file size、合計 output bytes の **固定上限** を runner が適用する（source 設定には持たない）
- stdout/stderr は UTF-8 とし、上限超過、timeout、非ゼロ終了を明示的な failed result にする。出力を途中で切って成功扱いしない

stdout/stderr は後続判断に必要な capability result として cycle trace に残り得る。skill とその入力には、結果へ秘密情報を出さないものだけを使用する。server log は内容を記録せず文字数だけを記録する。

専用 process は障害と resource 使用を server process から分けるが、OS user や filesystem 権限を分離する sandbox ではない。enabled root 内の code は OtomeKairo process と同等の権限で任意の外部作用を行えるものとして信頼する。固定上限は信頼の代替ではなく、暴走 script の fail-fast 用である。source ごとの上限・runtime 調整は設けない。

## ELYTH の登録例

新規既定状態は、disabled の `elyth-skills` source を設定例として持つ。
skill package 本体は OtomeKairo repository 内へ vendoring せず、有効化前に次を checkout する。

```bash
git clone https://github.com/Divedesign/elyth-remote-mcp-skills.git /opt/elyth-remote-mcp-skills
```

既定の雛形は次である。

- 名前（`source_id`）: `elyth-skills`
- root path: `/opt/elyth-remote-mcp-skills/skills`
- 有効: `false`（既定）。有効化は root 内の script 実行も含む信頼確認である

checkout 後にブラウザ UI の「Agent Skills」で source を有効にする。
path を変えた場合は `root_path` を合わせて更新する。

ELYTH MCP server の URL と認証は MCP 設定の責務である。Agent Skill source と MCP server を `elyth` という名前で暗黙結合しない。
