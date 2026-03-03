#!/usr/bin/env bash

# Block: Strict mode
set -euo pipefail

# Block: Repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# Block: Python path
if [[ -n "${PYTHONPATH:-}" ]]; then
  export PYTHONPATH="${SCRIPT_DIR}/src:${PYTHONPATH}"
else
  export PYTHONPATH="${SCRIPT_DIR}/src"
fi

# Block: Combined startup
exec python3 -m otomekairo.boot.run_all
