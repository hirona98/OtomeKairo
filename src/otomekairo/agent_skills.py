from __future__ import annotations

import hashlib
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml


AGENT_SKILL_FRONTMATTER_FIELDS = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}
_LOCAL_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_FRONTMATTER = re.compile(
    r"\A---[ \t]*\r?\n(?P<frontmatter>.*?)^---[ \t]*\r?(?:\n|\Z)",
    re.MULTILINE | re.DOTALL,
)
_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class AgentSkillError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class AgentSkillResource:
    relative_path: str
    absolute_path: Path
    kind: str
    size_bytes: int
    sha256: str
    text_content: str | None

    def catalog_entry(self) -> dict[str, Any]:
        return {
            "path": self.relative_path,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "text_available": self.text_content is not None,
        }


@dataclass(frozen=True, slots=True)
class AgentSkill:
    source_id: str
    source_root: Path
    name: str
    description: str
    body: str
    skill_dir: Path
    skill_md_path: Path
    sha256: str
    license: str | None
    compatibility: str | None
    metadata: dict[str, str]
    allowed_tools: str | None
    resources: dict[str, AgentSkillResource]
    linked_skill_names: tuple[str, ...]

    def catalog_entry(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source_id": self.source_id,
            "skill_id": self.name,
            "description": self.description,
            "sha256": self.sha256,
        }
        if self.compatibility is not None:
            payload["compatibility"] = self.compatibility
        return payload

    def instruction_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source_id": self.source_id,
            "skill_id": self.name,
            "sha256": self.sha256,
            "instructions": self.body,
            "resource_catalog": [
                resource.catalog_entry()
                for resource in sorted(self.resources.values(), key=lambda item: item.relative_path)
            ],
            "linked_skill_ids": list(self.linked_skill_names),
        }
        for key, value in (
            ("license", self.license),
            ("compatibility", self.compatibility),
            ("allowed_tools", self.allowed_tools),
        ):
            if value is not None:
                payload[key] = value
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True, slots=True)
class AgentSkillSourceSnapshot:
    source_id: str
    root_path: Path
    definition: dict[str, Any]
    skills: dict[str, AgentSkill]
    sha256: str

    def inspection_payload(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "root_path": str(self.root_path),
            "enabled": True,
            "status": "available",
            "sha256": self.sha256,
            "skill_count": len(self.skills),
            "skills": [
                skill.catalog_entry()
                for skill in sorted(self.skills.values(), key=lambda item: item.name)
            ],
        }


class AgentSkillRegistry:
    def __init__(self, sources: dict[str, AgentSkillSourceSnapshot]) -> None:
        self.sources = dict(sources)
        skills: dict[str, AgentSkill] = {}
        for source in self.sources.values():
            for skill_name, skill in source.skills.items():
                if skill_name in skills:
                    raise AgentSkillError(
                        "duplicate_agent_skill_name",
                        f"Agent Skill name is duplicated across enabled sources: {skill_name}",
                    )
                skills[skill_name] = skill
        self.skills = skills

    @classmethod
    def empty(cls) -> "AgentSkillRegistry":
        return cls({})

    @classmethod
    def load(cls, definitions: dict[str, dict[str, Any]]) -> "AgentSkillRegistry":
        snapshots: dict[str, AgentSkillSourceSnapshot] = {}
        for source_id, definition in definitions.items():
            normalized = validate_agent_skill_source_definition(source_id, definition)
            if normalized["enabled"] is not True:
                continue
            snapshots[source_id] = _load_source(source_id, normalized)
        return cls(snapshots)

    def catalog(self) -> list[dict[str, Any]]:
        return [
            skill.catalog_entry()
            for skill in sorted(self.skills.values(), key=lambda item: item.name)
        ]

    def require_skill(self, skill_id: str) -> AgentSkill:
        skill = self.skills.get(skill_id)
        if skill is None:
            raise AgentSkillError(
                "agent_skill_not_available",
                f"Agent Skill is not available: {skill_id}",
            )
        return skill

    def inspection_payload(self) -> dict[str, Any]:
        return {
            "skill_count": len(self.skills),
            "sources": [
                source.inspection_payload()
                for source in sorted(self.sources.values(), key=lambda item: item.source_id)
            ],
        }


def validate_agent_skill_source_definition(source_id: str, definition: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(source_id, str) or not source_id.strip():
        raise AgentSkillError("invalid_agent_skill_source_id", "source_id must be a non-empty string.")
    if not isinstance(definition, dict):
        raise AgentSkillError("invalid_agent_skill_source", "agent_skill_source must be an object.")
    required = {"source_id", "enabled", "root_path", "script_execution"}
    if set(definition) != required:
        raise AgentSkillError(
            "invalid_agent_skill_source_fields",
            "agent_skill_source requires exactly source_id, enabled, root_path, script_execution.",
        )
    normalized = {
        **definition,
        "source_id": str(definition.get("source_id") or "").strip(),
        "root_path": str(definition.get("root_path") or "").strip(),
    }
    if normalized["source_id"] != source_id:
        raise AgentSkillError("agent_skill_source_id_mismatch", "source_id must match its collection key.")
    if not isinstance(definition.get("enabled"), bool):
        raise AgentSkillError("invalid_agent_skill_source_field", "enabled must be a boolean.")
    if not normalized["root_path"]:
        raise AgentSkillError("invalid_agent_skill_source_field", "root_path must be non-empty.")
    root_path = Path(normalized["root_path"])
    if not root_path.is_absolute():
        raise AgentSkillError("invalid_agent_skill_source_field", "root_path must be absolute.")
    normalized["script_execution"] = _validate_script_execution(definition.get("script_execution"))
    return normalized


def _validate_script_execution(value: Any) -> dict[str, Any]:
    # runtimes / limits は設定に持たない。実行はホスト Python、暴走防護は runner 固定上限。
    if not isinstance(value, dict) or set(value) != {"enabled"}:
        raise AgentSkillError(
            "invalid_agent_skill_script_execution",
            "script_execution requires exactly enabled.",
        )
    enabled = value.get("enabled")
    if not isinstance(enabled, bool):
        raise AgentSkillError("invalid_agent_skill_script_execution", "enabled must be a boolean.")
    return {"enabled": enabled}


def _load_source(source_id: str, definition: dict[str, Any]) -> AgentSkillSourceSnapshot:
    root_text = str(definition.get("root_path") or "").strip()
    configured_root = Path(root_text)
    if configured_root.is_symlink():
        raise AgentSkillError(
            "agent_skill_symlink_forbidden",
            f"Agent Skill source root is a symlink: {source_id}",
        )
    try:
        root = configured_root.resolve(strict=True)
    except OSError as exc:
        raise AgentSkillError(
            "agent_skill_source_unavailable",
            f"Agent Skill source cannot be opened: {source_id}",
        ) from exc
    if not root.is_dir():
        raise AgentSkillError("agent_skill_source_unavailable", f"Agent Skill source is not a directory: {source_id}")
    skills: dict[str, AgentSkill] = {}
    for skill_dir in sorted(root.iterdir(), key=lambda path: path.name):
        if skill_dir.name.startswith(".") or not skill_dir.is_dir():
            continue
        if skill_dir.is_symlink():
            raise AgentSkillError("agent_skill_symlink_forbidden", f"Skill directory is a symlink: {skill_dir.name}")
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        skill = _load_skill(source_id=source_id, source_root=root, skill_dir=skill_dir)
        if skill.name in skills:
            raise AgentSkillError("duplicate_agent_skill_name", f"Agent Skill name is duplicated: {skill.name}")
        skills[skill.name] = skill
    if not skills:
        raise AgentSkillError("agent_skill_source_empty", f"Agent Skill source has no skills: {source_id}")
    source_digest = hashlib.sha256(
        "\n".join(f"{name}:{skill.sha256}" for name, skill in sorted(skills.items())).encode("utf-8")
    ).hexdigest()
    return AgentSkillSourceSnapshot(
        source_id=source_id,
        root_path=root,
        definition=dict(definition),
        skills=skills,
        sha256=source_digest,
    )


def _load_skill(*, source_id: str, source_root: Path, skill_dir: Path) -> AgentSkill:
    skill_md = skill_dir / "SKILL.md"
    raw = _read_regular_file(skill_md, source_root)
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentSkillError("invalid_agent_skill_encoding", f"SKILL.md must be UTF-8: {skill_dir.name}") from exc
    metadata, body = _parse_frontmatter(content, skill_md)
    _validate_skill_metadata(metadata, skill_dir.name)
    resources = _load_skill_resources(skill_dir=skill_dir, source_root=source_root)
    linked_skill_names: list[str] = []
    for target in _LOCAL_MARKDOWN_LINK.findall(body):
        raw_target = target.strip().strip("<>")
        parsed_target = urlsplit(raw_target)
        if parsed_target.scheme or parsed_target.netloc or not parsed_target.path:
            continue
        candidate = _resolve_within(source_root, skill_dir / unquote(parsed_target.path))
        if candidate.name == "SKILL.md" and candidate.parent.parent == source_root:
            linked_name = candidate.parent.name
            if linked_name not in linked_skill_names:
                linked_skill_names.append(linked_name)
    package_digest = hashlib.sha256()
    package_digest.update(b"SKILL.md\0")
    package_digest.update(raw)
    for relative_path, resource in sorted(resources.items()):
        package_digest.update(relative_path.encode("utf-8"))
        package_digest.update(b"\0")
        package_digest.update(resource.sha256.encode("ascii"))
    return AgentSkill(
        source_id=source_id,
        source_root=source_root,
        name=str(metadata["name"]),
        description=str(metadata["description"]).strip(),
        body=body.strip(),
        skill_dir=skill_dir,
        skill_md_path=skill_md,
        sha256=package_digest.hexdigest(),
        license=_optional_text(metadata.get("license")),
        compatibility=_optional_text(metadata.get("compatibility")),
        metadata=dict(metadata.get("metadata") or {}),
        allowed_tools=_optional_text(metadata.get("allowed-tools")),
        resources=resources,
        linked_skill_names=tuple(linked_skill_names),
    )


def _load_skill_resources(*, skill_dir: Path, source_root: Path) -> dict[str, AgentSkillResource]:
    resources: dict[str, AgentSkillResource] = {}
    for directory, directory_names, file_names in os.walk(skill_dir, followlinks=False):
        directory_path = Path(directory)
        for name in list(directory_names):
            path = directory_path / name
            if path.is_symlink():
                raise AgentSkillError("agent_skill_symlink_forbidden", f"Skill resource directory is a symlink: {path}")
        for file_name in file_names:
            path = directory_path / file_name
            if path == skill_dir / "SKILL.md":
                continue
            raw = _read_regular_file(path, source_root)
            relative_path = path.relative_to(skill_dir).as_posix()
            text_content: str | None
            try:
                text_content = raw.decode("utf-8")
            except UnicodeDecodeError:
                text_content = None
            file_mode = path.stat().st_mode
            kind = "script" if relative_path.startswith("scripts/") or bool(file_mode & stat.S_IXUSR) else "resource"
            resources[relative_path] = AgentSkillResource(
                relative_path=relative_path,
                absolute_path=path,
                kind=kind,
                size_bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                text_content=text_content,
            )
    return resources


def _parse_frontmatter(content: str, path: Path) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER.match(content)
    if match is None:
        raise AgentSkillError("invalid_agent_skill_frontmatter", f"SKILL.md frontmatter is missing or invalid: {path}")
    frontmatter_text = match.group("frontmatter")
    try:
        metadata = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        raise AgentSkillError("invalid_agent_skill_frontmatter", f"SKILL.md frontmatter is invalid: {path}") from exc
    if not isinstance(metadata, dict):
        raise AgentSkillError("invalid_agent_skill_frontmatter", f"SKILL.md frontmatter must be an object: {path}")
    return metadata, content[match.end():]


def _validate_skill_metadata(metadata: dict[str, Any], directory_name: str) -> None:
    unsupported = sorted(set(metadata) - AGENT_SKILL_FRONTMATTER_FIELDS)
    if unsupported:
        raise AgentSkillError(
            "unsupported_agent_skill_frontmatter_field",
            f"Unsupported Agent Skill frontmatter fields: {', '.join(unsupported)}",
        )
    name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(name, str) or not name.strip():
        raise AgentSkillError("invalid_agent_skill_name", "Agent Skill name must be a non-empty string.")
    normalized_name = unicodedata.normalize("NFKC", name.strip())
    if (
        len(normalized_name) > 64
        or normalized_name != normalized_name.lower()
        or _SKILL_NAME.fullmatch(normalized_name) is None
    ):
        raise AgentSkillError("invalid_agent_skill_name", f"Agent Skill name is invalid: {name}")
    if normalized_name != unicodedata.normalize("NFKC", directory_name):
        raise AgentSkillError("agent_skill_directory_mismatch", f"Skill directory must match name: {name}")
    metadata["name"] = normalized_name
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise AgentSkillError("invalid_agent_skill_description", f"Agent Skill description is invalid: {name}")
    for field_name in ("license", "allowed-tools"):
        value = metadata.get(field_name)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise AgentSkillError("invalid_agent_skill_frontmatter", f"{field_name} must be a non-empty string.")
    compatibility = metadata.get("compatibility")
    if compatibility is not None and (
        not isinstance(compatibility, str) or not compatibility.strip() or len(compatibility) > 500
    ):
        raise AgentSkillError("invalid_agent_skill_frontmatter", "compatibility is invalid.")
    extra_metadata = metadata.get("metadata")
    if extra_metadata is not None and (
        not isinstance(extra_metadata, dict)
        or not all(isinstance(key, str) and isinstance(value, str) for key, value in extra_metadata.items())
    ):
        raise AgentSkillError("invalid_agent_skill_frontmatter", "metadata must map strings to strings.")


def _read_regular_file(path: Path, source_root: Path) -> bytes:
    resolved = _resolve_within(source_root, path)
    if path.is_symlink() or not resolved.is_file():
        raise AgentSkillError("agent_skill_symlink_forbidden", f"Skill file must be a regular non-symlink file: {path}")
    try:
        return resolved.read_bytes()
    except OSError as exc:
        raise AgentSkillError("agent_skill_file_unreadable", f"Skill file cannot be read: {path}") from exc


def _resolve_within(root: Path, path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AgentSkillError("agent_skill_reference_missing", f"Skill reference is missing: {path}") from exc
    if resolved != root and root not in resolved.parents:
        raise AgentSkillError("agent_skill_path_escape", f"Skill path escapes its source root: {path}")
    return resolved


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
