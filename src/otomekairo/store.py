from __future__ import annotations

import json
import tempfile
from pathlib import Path

from otomekairo.defaults import build_default_state


# Block: Constants
STATE_FILE_NAME = "server_state.json"


# Block: Store
class FileStore:
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
        return json.loads(self.state_path.read_text(encoding="utf-8"))

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

    def append_jsonl(self, file_name: str, record: dict) -> None:
        # Block: AppendRecord
        path = self.root_dir / file_name
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.write("\n")

    def read_jsonl(self, file_name: str) -> list[dict]:
        # Block: ReadRecords
        path = self.root_dir / file_name
        if not path.exists():
            return []

        records: list[dict] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped:
                    records.append(json.loads(stripped))
        return records
