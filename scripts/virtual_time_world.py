"""A declared local ELYTH fixture. It never connects to the real service."""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required,
                            "additionalProperties": False}}


TEXT = {"type": "string"}
CATALOG = [
    tool("get_information", "ELYTHの現状、今日の話題、公開timelineを読む。", {}, []),
    tool("get_my_posts", "自身の公開投稿を確認する。", {}, []),
    tool("get_notifications", "未読通知を読む。既読状態は変更しない。", {}, []),
    tool("get_thread", "指定した公開投稿のrootと返信を読む。", {"post_id": TEXT}, ["post_id"]),
    tool("create_post", "新しい公開テキスト投稿を一度作成する。", {"content": TEXT}, ["content"]),
    tool("create_reply", "指定した公開投稿へ一度返信する。", {"post_id": TEXT, "content": TEXT}, ["post_id", "content"]),
    tool("mark_notifications_read", "処理した通知を既読にする。", {"notification_ids": {"type": "array", "items": TEXT}}, ["notification_ids"]),
]


class VirtualWorld:
    def __init__(self, snapshot: dict | None = None):
        self.state = deepcopy(snapshot) if snapshot is not None else {
            "posts": [], "notifications": [], "calls": [], "injected_days": [], "fail_next": False}

    def inject(self, day: int, now: str) -> None:
        if day in self.state["injected_days"]:
            return
        self.state["injected_days"].append(day)
        post = {"id": f"post:world-{day}", "content": "公園で木の影を見て、季節による変化が気になりました。あなたは最近どんな発見がありましたか？",
                "author": {"handle": "virtual_observer", "display_name": "観測仲間さん"},
                "created_at": now, "parent_id": None}
        self.state["posts"].append(post)
        self.state["notifications"].append({"id": f"notification:world-{day}", "type": "post.mention",
                                            "post_id": post["id"], "actor": post["author"], "created_at": now, "read": False})

    @staticmethod
    def observed_persons(result: dict) -> list[dict]:
        # Identity comes from the closed fixture account ID, never its text.
        posts = [*result.get("timeline", []), *result.get("posts", []), *result.get("replies", [])]
        for key in ("post", "root"):
            if key in result:
                posts.append(result[key])
        authors = [p["author"] for p in posts]
        authors.extend(n["actor"] for n in result.get("notifications", []))
        refs = {}
        for author in authors:
            if author["handle"] == "virtual_observer":
                refs[author["handle"]] = {"person_ref": "person:mcp:elyth:virtual_observer", "display_name": author["display_name"]}
            elif author["handle"] != "virtual_leika":
                raise ValueError("fixtureのアカウントIDが未定義です。")
        return list(refs.values())

    def call(self, name: str, arguments: dict[str, Any], now: str) -> dict[str, Any]:
        definitions = {item["name"]: item for item in CATALOG}
        if name not in definitions:
            raise ValueError("未定義のfixture toolです。")
        schema = definitions[name]["inputSchema"]
        if not isinstance(arguments, dict) or set(arguments) - set(schema["properties"]) or set(schema["required"]) - set(arguments):
            raise ValueError("fixture toolの引数がschemaに合いません。")
        for key, value in arguments.items():
            expected = schema["properties"][key]["type"]
            if expected == "string" and (not isinstance(value, str) or not value.strip()):
                raise ValueError("fixture toolの文字列が不正です。")
            if expected == "array" and (not isinstance(value, list) or not all(isinstance(v, str) for v in value)):
                raise ValueError("fixture toolの配列が不正です。")
        if self.state["fail_next"]:
            self.state["fail_next"] = False
            result = {"status": "failed", "error": "fixture_service_unavailable", "retryable": False}
        elif name == "get_information":
            result = {"status": "observed", "observed_at": now, "simulation": True,
                      "self_account": {"handle": "virtual_leika", "display_name": "レイカ"},
                      "today_topic": "日々の発見と季節の変化", "timeline": deepcopy(self.state["posts"][-10:]),
                      "unread_count": sum(not n["read"] for n in self.state["notifications"])}
        elif name == "get_my_posts":
            result = {"posts": [deepcopy(p) for p in self.state["posts"] if p["author"]["handle"] == "virtual_leika"]}
        elif name == "get_notifications":
            result = {"notifications": [deepcopy(n) for n in self.state["notifications"] if not n["read"]], "has_more": False}
        elif name == "get_thread":
            target = next((p for p in self.state["posts"] if p["id"] == arguments["post_id"]), None)
            if target is None:
                result = {"status": "failed", "error": "post_not_found"}
            else:
                result = {"root": deepcopy(target), "replies": [deepcopy(p) for p in self.state["posts"] if p["parent_id"] == target["id"]]}
        elif name in {"create_post", "create_reply"}:
            parent = arguments.get("post_id")
            if name == "create_reply" and not any(p["id"] == parent for p in self.state["posts"]):
                result = {"status": "failed", "error": "post_not_found"}
            else:
                post = {"id": f"post:leika-{len(self.state['posts']) + 1}", "content": arguments["content"],
                        "author": {"handle": "virtual_leika", "display_name": "レイカ"}, "created_at": now, "parent_id": parent}
                self.state["posts"].append(post)
                result = {"status": "executed", "post": deepcopy(post)}
        else:
            refs = arguments["notification_ids"]
            if any(ref not in {n["id"] for n in self.state["notifications"]} for ref in refs):
                result = {"status": "failed", "error": "notification_not_found"}
            else:
                for item in self.state["notifications"]:
                    if item["id"] in refs:
                        item["read"] = True
                result = {"status": "executed", "accepted_count": len(set(refs))}
        self.state["calls"].append({"tool": name, "arguments": deepcopy(arguments), "virtual_time": now, "result": deepcopy(result)})
        return result
