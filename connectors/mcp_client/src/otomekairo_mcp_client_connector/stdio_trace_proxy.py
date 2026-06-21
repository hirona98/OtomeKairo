from __future__ import annotations

import argparse
import subprocess
import sys
import threading
from typing import BinaryIO

from .trace import trace_writer_from_env


def main() -> int:
    parser = argparse.ArgumentParser(description="Trace stdio MCP JSON-RPC traffic while proxying a server command.")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to run after --.")
    args = parser.parse_args()
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("stdio trace proxy requires a command after --.", file=sys.stderr)
        return 2

    trace = trace_writer_from_env()
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None

    threads = [
        threading.Thread(
            target=_forward,
            args=(sys.stdin.buffer, process.stdin, trace, "client_to_mcp"),
            daemon=True,
        ),
        threading.Thread(
            target=_forward,
            args=(process.stdout, sys.stdout.buffer, trace, "mcp_to_client"),
            daemon=True,
        ),
        threading.Thread(
            target=_forward_stderr,
            args=(process.stderr, trace),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()

    try:
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        return 130


def _forward(source: BinaryIO, target: BinaryIO, trace, direction: str) -> None:
    while True:
        chunk = source.readline()
        if not chunk:
            try:
                target.close()
            except OSError:
                pass
            return
        if trace is not None:
            trace.write(
                boundary="mcp_stdio",
                direction=direction,
                kind="jsonrpc",
                payload=_decode_line(chunk),
            )
        target.write(chunk)
        target.flush()


def _forward_stderr(source: BinaryIO, trace) -> None:
    while True:
        chunk = source.readline()
        if not chunk:
            return
        text = _decode_line(chunk)
        if trace is not None:
            trace.write(boundary="mcp_stdio", direction="mcp_stderr", kind="stderr", payload=text)
        sys.stderr.write(text)
        sys.stderr.flush()


def _decode_line(chunk: bytes) -> object:
    text = chunk.decode("utf-8", errors="replace").rstrip("\r\n")
    if not text:
        return ""
    try:
        import json

        value = json.loads(text)
    except json.JSONDecodeError:
        return text
    return value


if __name__ == "__main__":
    raise SystemExit(main())
