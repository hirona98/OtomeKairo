from pathlib import Path


def test_prepare_service_env_installs_tapo_watcher() -> None:
    script = (Path(__file__).resolve().parents[1] / "scripts" / "prepare_service_env.sh").read_text(
        encoding="utf-8"
    )

    assert 'TAPO_WATCHER_DIR="${REPO_ROOT}/watchers/tapo_c220"' in script
    assert 'TAPO_WATCHER_VENV_DIR="${TAPO_WATCHER_DIR}/.venv"' in script
    assert 'python3 -m venv "${TAPO_WATCHER_VENV_DIR}"' in script
    assert '"${TAPO_WATCHER_VENV_DIR}/bin/python" -m pip install -e "${TAPO_WATCHER_DIR}"' in script


def test_vscode_debug_compound_includes_tapo_watcher() -> None:
    launch_json = (Path(__file__).resolve().parents[1] / ".vscode" / "launch.json").read_text(encoding="utf-8")
    tasks_json = (Path(__file__).resolve().parents[1] / ".vscode" / "tasks.json").read_text(encoding="utf-8")

    assert "OtomeKairo: Tapo C220 Watcher" in launch_json
    assert "${workspaceFolder}/scripts/debug_tapo_c220_watcher.py" in launch_json
    assert "${workspaceFolder}/watchers/tapo_c220/.venv/bin/python" in launch_json
    assert "tapo_c220_watcher: prepare debug env" in tasks_json
    assert "${workspaceFolder}/scripts/prepare_tapo_c220_watcher_debug_env.sh" in tasks_json
