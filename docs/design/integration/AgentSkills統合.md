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

skill の適用可否は固定文字列や keyword 表では決めない。通常判断と各 autonomous run step の前に、LLM が current input、run 目的、capability decision view と `name / description` catalog を比較して必要な skill を選ぶ。

選択は次の順で行う。

1. 全 skill の `name / description / source_id / digest` だけから必要な skill を選ぶ
2. 選択した `SKILL.md` 本文と resource catalog、本文から直接参照された sibling skill 候補を提示する
3. LLM が必要な追加 skill と text resource だけを選び、未読候補が必要な間は段階的に読む
4. 選択済み instructions と resource を trusted Agent Skill system context として decision、expression、autonomous step に渡す

capability request と autonomous run の起点には instructions 本文ではなく `source_id / skill_id / digest` の activation summary を残す。result follow-up と次 run step の選択 LLM へこの summary を前回文脈として渡し、現在の catalog と目的を基に再選択させる。古い本文を暗黙再利用しない。

選択結果が catalog 外の id/path を含む、binary resource の本文読込を要求する、または追加読込が必要だとしながら候補を増やさない場合は判断サイクルを失敗させる。暗黙に skill なしへ戻さない。

Agent Skill context は host の役割、出力契約、capability availability、安全境界、観測事実を上書きしない。skill に書かれた tool 名は capability との固定対応表ではなく、実行時 catalog と manifest に基づいて通常どおり LLM が capability request を組み立てる。

## script 実行と信頼境界

source ごとの `script_execution.enabled=true` は、その root 全体を code execution まで信頼する明示設定である。個別 script の都度承認は設けない。

script は `agent_skill.run_script` capability からだけ実行する。server process 内で import や eval をせず、専用 runner process が次を再検証して実行する。

- source、skill、package digest、script resource が現在の registry snapshot と一致する
- source 設定で `script_execution.enabled=true` である
- package を request 固有の `agent-skill-runs/<request-id>/workspace/` へコピーし、copy 内の script を実行する
- 実行 interpreter は OtomeKairo ホストの Python（`sys.executable`）に固定する。source ごとの runtime 選択は設けない
- child environment は `PATH / LANG / LC_ALL` だけにする
- wall time、CPU time、address space、process 数、open file 数、file size、合計 output bytes の **固定上限** を runner が適用する（source 設定には持たない）
- stdout/stderr は UTF-8 とし、上限超過、timeout、非ゼロ終了を明示的な failed result にする。出力を途中で切って成功扱いしない

stdout/stderr は後続判断に必要な capability result として cycle trace に残り得る。skill とその入力には、結果へ秘密情報を出さないものだけを使用する。server log は内容を記録せず文字数だけを記録する。

専用 process は障害と resource 使用を server process から分けるが、OS user や filesystem 権限を分離する sandbox ではない。enabled root 内の code は OtomeKairo process と同等の権限で任意の外部作用を行えるものとして信頼する。固定上限は信頼の代替ではなく、暴走 script の fail-fast 用である。source ごとの上限・runtime 調整は設けない。

## ELYTH の登録例

ELYTH の skill repository を OtomeKairo 外へ checkout し、その checkout root を通常の source として登録する。

```bash
git clone https://github.com/Divedesign/elyth-remote-mcp-skills.git /opt/elyth-remote-mcp-skills
```

ブラウザ UI の「Agent Skills」で、例として次を設定する。

- 名前（`source_id`）: `elyth-skills`
- root path: `/opt/elyth-remote-mcp-skills/skills`
- 有効: `true`
- script 実行を許可: repository 内の script が必要な場合だけ、信頼確認後に `true`

ELYTH MCP server の URL、認証、有限 MCP セッションは従来どおり MCP 設定の責務である。Agent Skill source と MCP server を `elyth` という名前で暗黙結合しない。
