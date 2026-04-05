from __future__ import annotations

import json
import tempfile
from pathlib import Path

from otomekairo.defaults import build_default_state, normalize_state


# Block: Constants
STATE_FILE_NAME = "server_state.json"


# Block: Store
class StateStore:
    def __init__(self, root_dir: Path) -> None:
        # Block: Paths
        self.root_dir = root_dir
        self.state_path = root_dir / STATE_FILE_NAME

        # Block: Initialization
        self.root_dir.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            self.write_state(build_default_state())

    def read_state(self) -> dict:
        # Block: ReadState
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state, changed = normalize_state(state)
        if changed:
            self.write_state(state)
        return state

    def write_state(self, state: dict) -> None:
        # Block: AtomicWrite
        self.root_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.root_dir,
            delete=False,
        ) as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temp_path = Path(handle.name)

        # Block: CommitWrite
        temp_path.replace(self.state_path)
