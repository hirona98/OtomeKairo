from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


ELYTH_REMOTE_BUNDLE_ID = "elyth-remote-mcp-skills@0.1.0"
ELYTH_REMOTE_UPSTREAM_COMMIT = "7428cea9a37ca7d4ea2dd0764707be43d10f3f29"
_BUNDLE_DIRS = {
    ELYTH_REMOTE_BUNDLE_ID: Path(__file__).resolve().parents[2] / "third_party" / "elyth-remote-mcp-skills",
}
_FRONTMATTER_FIELD = re.compile(r"^([a-zA-Z0-9_-]+):\s*(.*?)\s*$")
_MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+\.md)(?:#[^)]+)?\)")


class SkillBundleError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OperationalSkill:
    name: str
    description: str
    content: str
    path: Path

    def catalog_entry(self) -> dict[str, str]:
        return {"skill_id": self.name, "description": self.description}


@dataclass(frozen=True, slots=True)
class SkillBundle:
    bundle_id: str
    version: str
    upstream_commit: str
    root_skill: str
    session_skill: str
    skills: dict[str, OperationalSkill]
    declared_tools: frozenset[str]
    tool_skills: dict[str, str]
    mutating_tools: frozenset[str]

    def catalog(self) -> list[dict[str, str]]:
        return [self.skills[name].catalog_entry() for name in sorted(self.skills)]

    def guidance_for_tool(self, tool_name: str) -> dict[str, Any]:
        skill_id = self.tool_skills.get(tool_name)
        if skill_id is None:
            raise SkillBundleError(f"Tool is not covered by the skill bundle: {tool_name}")
        return {
            "bundle_id": self.bundle_id,
            "root_skill": self.skills[self.root_skill].content,
            "action_skill_id": skill_id,
            "action_skill": self.skills[skill_id].content,
            "mutating": tool_name in self.mutating_tools,
        }


@lru_cache(maxsize=None)
def load_skill_bundle(bundle_id: str) -> SkillBundle:
    bundle_dir = _BUNDLE_DIRS.get(bundle_id)
    if bundle_dir is None:
        raise SkillBundleError(f"Unsupported skill bundle: {bundle_id}")
    manifest = _read_json(bundle_dir / "manifest.json")
    integration = _read_json(bundle_dir / "integration.json")
    upstream_commit = _read_text(bundle_dir / "UPSTREAM_COMMIT").strip()
    if manifest.get("name") != "elyth-remote-mcp-skills" or manifest.get("version") != "0.1.0":
        raise SkillBundleError("ELYTH skill bundle manifest identity is invalid.")
    if upstream_commit != ELYTH_REMOTE_UPSTREAM_COMMIT:
        raise SkillBundleError("ELYTH skill bundle commit is invalid.")
    if integration.get("bundle_id") != bundle_id:
        raise SkillBundleError("Skill bundle integration identity is invalid.")
    declared_skill_names = manifest.get("skills")
    declared_tool_names = manifest.get("tools")
    if not isinstance(declared_skill_names, list) or not all(isinstance(item, str) for item in declared_skill_names):
        raise SkillBundleError("Skill bundle manifest skills are invalid.")
    if not isinstance(declared_tool_names, list) or not all(isinstance(item, str) for item in declared_tool_names):
        raise SkillBundleError("Skill bundle manifest tools are invalid.")
    checksums = _read_checksums(bundle_dir / "SHA256SUMS")
    _verify_checksum(bundle_dir, "manifest.json", checksums)
    _verify_checksum(bundle_dir, "LICENSE", checksums)
    skills: dict[str, OperationalSkill] = {}
    for skill_name in declared_skill_names:
        relative_path = f"skills/{skill_name}/SKILL.md"
        _verify_checksum(bundle_dir, relative_path, checksums)
        path = bundle_dir / relative_path
        content = _read_text(path)
        metadata = _parse_frontmatter(content, path)
        if metadata.get("name") != skill_name:
            raise SkillBundleError(f"Skill name does not match its manifest entry: {skill_name}")
        description = metadata.get("description")
        if not isinstance(description, str) or not description.strip():
            raise SkillBundleError(f"Skill description is missing: {skill_name}")
        _validate_markdown_links(bundle_dir, path, content)
        skills[skill_name] = OperationalSkill(
            name=skill_name,
            description=description.strip(),
            content=content,
            path=path,
        )
    if set(skills) != set(declared_skill_names):
        raise SkillBundleError("Skill bundle contents do not match the manifest.")
    tool_skills = integration.get("tool_skills")
    mutating_tools = integration.get("mutating_tools")
    root_skill = integration.get("root_skill")
    session_skill = integration.get("session_skill")
    if not isinstance(tool_skills, dict) or set(tool_skills) != set(declared_tool_names):
        raise SkillBundleError("Skill bundle tool mapping does not match the manifest.")
    if not all(isinstance(tool, str) and isinstance(skill, str) and skill in skills for tool, skill in tool_skills.items()):
        raise SkillBundleError("Skill bundle tool mapping is invalid.")
    if not isinstance(mutating_tools, list) or not set(mutating_tools).issubset(set(declared_tool_names)):
        raise SkillBundleError("Skill bundle mutating tool list is invalid.")
    if root_skill not in skills or session_skill not in skills:
        raise SkillBundleError("Skill bundle entry skill is invalid.")
    return SkillBundle(
        bundle_id=bundle_id,
        version=str(manifest["version"]),
        upstream_commit=upstream_commit,
        root_skill=str(root_skill),
        session_skill=str(session_skill),
        skills=skills,
        declared_tools=frozenset(declared_tool_names),
        tool_skills={str(key): str(value) for key, value in tool_skills.items()},
        mutating_tools=frozenset(str(item) for item in mutating_tools),
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(_read_text(path))
    except json.JSONDecodeError as exc:
        raise SkillBundleError(f"Skill bundle JSON is invalid: {path.name}") from exc
    if not isinstance(payload, dict):
        raise SkillBundleError(f"Skill bundle JSON root must be an object: {path.name}")
    return payload


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillBundleError(f"Skill bundle file cannot be read: {path}") from exc


def _read_checksums(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in _read_text(path).splitlines():
        if not line:
            continue
        checksum, separator, relative_path = line.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise SkillBundleError("Skill bundle checksum file is invalid.")
        checksums[relative_path] = checksum
    return checksums


def _verify_checksum(bundle_dir: Path, relative_path: str, checksums: dict[str, str]) -> None:
    expected = checksums.get(relative_path)
    if expected is None:
        raise SkillBundleError(f"Skill bundle checksum is missing: {relative_path}")
    path = bundle_dir / relative_path
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise SkillBundleError(f"Skill bundle file cannot be read: {relative_path}") from exc
    if actual != expected:
        raise SkillBundleError(f"Skill bundle checksum does not match: {relative_path}")


def _parse_frontmatter(content: str, path: Path) -> dict[str, str]:
    lines = content.splitlines()
    if not lines or lines[0] != "---":
        raise SkillBundleError(f"Skill frontmatter is missing: {path}")
    metadata: dict[str, str] = {}
    for line in lines[1:]:
        if line == "---":
            return metadata
        match = _FRONTMATTER_FIELD.fullmatch(line)
        if match is None:
            raise SkillBundleError(f"Skill frontmatter is invalid: {path}")
        value = match.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        metadata[match.group(1)] = value
    raise SkillBundleError(f"Skill frontmatter is not closed: {path}")


def _validate_markdown_links(bundle_dir: Path, path: Path, content: str) -> None:
    root = bundle_dir.resolve()
    for target in _MARKDOWN_LINK.findall(content):
        resolved = (path.parent / target).resolve()
        if root not in resolved.parents or not resolved.is_file():
            raise SkillBundleError(f"Skill link escapes or is missing: {path} -> {target}")
