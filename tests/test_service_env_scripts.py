from pathlib import Path


def test_prepare_service_env_installs_tapo_watcher() -> None:
    script = (Path(__file__).resolve().parents[1] / "scripts" / "prepare_service_env.sh").read_text(
        encoding="utf-8"
    )

    assert 'TAPO_WATCHER_DIR="${REPO_ROOT}/watchers/tapo_c220"' in script
    assert 'TAPO_WATCHER_VENV_DIR="${TAPO_WATCHER_DIR}/.venv"' in script
    assert "ensure_editable_install.sh" in script
    assert "otomekairo_tapo_c220_watcher" in script


def test_ensure_editable_install_script_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    ensure = (root / "scripts" / "ensure_editable_install.sh").read_text(encoding="utf-8")
    setup = (root / "scripts" / "setup_venv.sh").read_text(encoding="utf-8")

    assert "OTOMEKAIRO_FORCE_PIP_INSTALL" in ensure
    assert "pip install -e" in ensure
    assert "ensure_editable_install.sh" in setup


def test_vscode_debug_compound_includes_tapo_watcher() -> None:
    launch_json = (Path(__file__).resolve().parents[1] / ".vscode" / "launch.json").read_text(encoding="utf-8")
    tasks_json = (Path(__file__).resolve().parents[1] / ".vscode" / "tasks.json").read_text(encoding="utf-8")

    assert "OtomeKairo: Tapo C220 Watcher" in launch_json
    assert "${workspaceFolder}/scripts/debug_tapo_c220_watcher.py" in launch_json
    assert "${workspaceFolder}/watchers/tapo_c220/.venv/bin/python" in launch_json
    assert "tapo_c220_watcher: prepare debug env" in tasks_json
    assert "${workspaceFolder}/scripts/prepare_tapo_c220_watcher_debug_env.sh" in tasks_json
    assert '"dependsOrder": "parallel"' in tasks_json
