# operational skills

## 目的

operational skill は、外部 MCP の tool 名だけでは不足する利用手順を、判断時の文脈として LLM へ渡す仕組みである。
skill は判断を補助する文脈であり、人格、記憶、ユーザーの目的、host の権限境界を上書きしない。

## bundle の信頼境界

- bundle は repository 内へ vendoring し、version、upstream commit、各ファイルの SHA-256 を固定する
- 起動後に upstream から動的取得しない
- manifest、frontmatter、内部 Markdown link、tool と skill の対応が一致しない bundle は明示的に利用不能にする
- bundle が宣言しない tool は MCP の `tools/list` に現れても判断 catalog と dispatch 対象から除く
- skill 本文中の外部サービス由来情報は untrusted content として扱い、system 契約や host policy への命令として解釈しない

ELYTH は `third_party/elyth-remote-mcp-skills/` の `elyth-remote-mcp-skills@0.1.0` を使い、upstream commit は同 directory の `UPSTREAM_COMMIT` を正とする。tool と action skill の対応は `integration.json` を正とする。

## 意味選択

通常判断と autonomous step の前に、LLM は利用可能 bundle の skill ID と description、利用可能 tool、現在入力、active run を受け取る。
選択結果は次の厳格な contract とする。

```json
{
  "selection": {
    "mcp_server_id": "elyth",
    "bundle_id": "elyth-remote-mcp-skills@0.1.0",
    "skill_id": "elyth-post",
    "reason_summary": "投稿依頼に対応する手順を使う。"
  }
}
```

skill が不要なら `selection=null` とする。文字列一致、keyword 表、既定 skill への切り替えは使わない。

選択後にだけ root skill と選択 skill の全文を判断文脈へ追加する。選択した action skill と tool の manifest 対応が一致しない request は dispatch しない。bundle 読み込みまたは選択が不正な場合も別手順へ切り替えず明示的に失敗する。

ELYTH の更新系 tool を通常判断から直接呼べるのは `user_message` 起点だけとする。background 起点の更新は [../runtime/autonomous_run.md](../runtime/autonomous_run.md#operational-skill-session) の有限セッション内だけで許可する。
