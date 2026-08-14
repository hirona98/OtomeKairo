from __future__ import annotations

import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from otomekairo.agent_skills import AgentSkillError, AgentSkillRegistry, validate_agent_skill_source_definition


class RunnerError(RuntimeError):
    pass


# 信頼済み script でも暴走から本体を守る固定上限。source 設定には出さない。
DEFAULT_SCRIPT_LIMITS: dict[str, int] = {
    "wall_time_seconds": 30,
    "cpu_time_seconds": 20,
    "memory_bytes": 536870912,
    "max_processes": 16,
    "max_open_files": 128,
    "max_file_bytes": 10485760,
    "max_output_bytes": 1048576,
}


def execute_runner_request(request: dict[str, Any]) -> dict[str, Any]:
    required = {
        "source_definition",
        "source_id",
        "skill_id",
        "skill_sha256",
        "script_path",
        "args",
        "stdin_text",
        "run_dir",
    }
    if not isinstance(request, dict) or set(request) != required:
        raise RunnerError("runner request fields are invalid")
    source_id = _required_text(request, "source_id")
    skill_id = _required_text(request, "skill_id")
    expected_digest = _required_text(request, "skill_sha256")
    script_path = _required_relative_path(request, "script_path")
    args = request.get("args")
    stdin_text = request.get("stdin_text")
    if not isinstance(args, list) or not all(isinstance(value, str) for value in args):
        raise RunnerError("args must be an array of strings")
    if stdin_text is not None and not isinstance(stdin_text, str):
        raise RunnerError("stdin_text must be a string or null")
    source_definition = request.get("source_definition")
    if not isinstance(source_definition, dict):
        raise RunnerError("source_definition must be an object")
    normalized_source = validate_agent_skill_source_definition(source_id, source_definition)
    if normalized_source["enabled"] is not True:
        raise RunnerError("source is not enabled")

    registry = AgentSkillRegistry.load({source_id: normalized_source})
    skill = registry.require_skill(skill_id)
    if skill.source_id != source_id or skill.sha256 != expected_digest:
        raise RunnerError("Agent Skill snapshot digest does not match")
    script = skill.resources.get(script_path)
    if script is None or script.kind != "script":
        raise RunnerError("script_path is not an executable Agent Skill resource")

    run_dir = Path(_required_text(request, "run_dir"))
    if not run_dir.is_absolute() or run_dir.exists():
        raise RunnerError("run_dir must be a new absolute path")
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    workspace = run_dir / "workspace" / skill.name
    shutil.copytree(skill.skill_dir, workspace, symlinks=False)
    copied_script = workspace / script_path
    resolved_script = copied_script.resolve(strict=True)
    if workspace.resolve(strict=True) not in resolved_script.parents or not resolved_script.is_file():
        raise RunnerError("copied script path is invalid")

    limits = DEFAULT_SCRIPT_LIMITS
    # 信頼済み source の script は OtomeKairo ホスト Python で実行する。runtime 選択は設けない。
    command = [
        sys.executable,
        str(resolved_script),
        *args,
    ]
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=workspace,
            stdin=subprocess.PIPE,
            stdout=stdout_file,
            stderr=stderr_file,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            },
            start_new_session=True,
            preexec_fn=lambda: _apply_limits(limits),
        )
        try:
            process.communicate(
                input=stdin_text.encode("utf-8") if stdin_text is not None else None,
                timeout=limits["wall_time_seconds"],
            )
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise RunnerError("script exceeded wall_time_seconds") from exc
        stdout_size = stdout_file.tell()
        stderr_size = stderr_file.tell()
        if stdout_size + stderr_size > limits["max_output_bytes"]:
            raise RunnerError("script output exceeded max_output_bytes")
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout_raw = stdout_file.read()
        stderr_raw = stderr_file.read()
    try:
        stdout = stdout_raw.decode("utf-8")
        stderr = stderr_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RunnerError("script output must be UTF-8") from exc
    status = "completed" if process.returncode == 0 else "failed"
    status_text = (
        f"Agent Skill script completed with exit_code={process.returncode}."
        if process.returncode == 0
        else f"Agent Skill script failed with exit_code={process.returncode}."
    )
    if stdout:
        status_text += f"\nstdout:\n{stdout}"
    if stderr:
        status_text += f"\nstderr:\n{stderr}"
    return {
        "status": status,
        "exit_code": process.returncode,
        "status_text": status_text,
        "stdout": stdout,
        "stderr": stderr,
        "client_context": {},
        "error": None if process.returncode == 0 else f"script exited with code {process.returncode}",
    }


def _apply_limits(limits: dict[str, int]) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (limits["cpu_time_seconds"], limits["cpu_time_seconds"]))
    resource.setrlimit(resource.RLIMIT_AS, (limits["memory_bytes"], limits["memory_bytes"]))
    resource.setrlimit(resource.RLIMIT_NPROC, (limits["max_processes"], limits["max_processes"]))
    resource.setrlimit(resource.RLIMIT_NOFILE, (limits["max_open_files"], limits["max_open_files"]))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limits["max_file_bytes"], limits["max_file_bytes"]))


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RunnerError(f"{key} must be a non-empty string")
    return value.strip()


def _required_relative_path(payload: dict[str, Any], key: str) -> str:
    value = _required_text(payload, key)
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise RunnerError(f"{key} must be a normalized relative path")
    return value


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = execute_runner_request(payload)
    except (AgentSkillError, RunnerError, OSError, ValueError, subprocess.SubprocessError) as exc:
        result = {
            "status": "failed",
            "exit_code": None,
            "status_text": "Agent Skill runner failed before successful completion.",
            "stdout": "",
            "stderr": "",
            "client_context": {},
            "error": str(exc),
        }
    json.dump(result, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
