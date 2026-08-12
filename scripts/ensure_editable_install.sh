#!/usr/bin/env bash

set -euo pipefail

# editable パッケージを、必要なときだけ venv に入れる。
# F5 の preLaunch などで毎回 pip しないための共通処理。
#
# 使い方:
#   ensure_editable_install.sh <venv_dir> <project_dir> <import_name>
#
# 再 install する条件:
#   - OTOMEKAIRO_FORCE_PIP_INSTALL=1
#   - venv が無い / python が動かない
#   - import_name を import できない
#   - project の pyproject.toml が、インストール metadata より新しい
#
# ソース変更だけでは reinstall しない（editable のまま動く）。

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || $# -ne 3 ]]; then
  echo "usage: $0 <venv_dir> <project_dir> <import_name>" >&2
  exit 2
fi

VENV_DIR="$1"
PROJECT_DIR="$2"
IMPORT_NAME="$3"
PYPROJECT="${PROJECT_DIR}/pyproject.toml"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 が見つかりません。" >&2
  exit 1
fi

if [[ ! -f "${PYPROJECT}" ]]; then
  echo "pyproject.toml が見つかりません: ${PROJECT_DIR}" >&2
  exit 1
fi

# venv が無ければ作る
if [[ ! -d "${VENV_DIR}" ]]; then
  python3 -m venv "${VENV_DIR}"
fi

PYTHON="${VENV_DIR}/bin/python"
if [[ ! -x "${PYTHON}" ]]; then
  echo "venv の python がありません: ${PYTHON}" >&2
  exit 1
fi

# project 直下または src 配下の setuptools metadata を探す
_metadata_stamp() {
  local stamp=""
  local path
  shopt -s nullglob
  for path in \
    "${PROJECT_DIR}"/*.egg-info/PKG-INFO \
    "${PROJECT_DIR}"/*.dist-info/METADATA \
    "${PROJECT_DIR}"/src/*.egg-info/PKG-INFO \
    "${PROJECT_DIR}"/src/*.dist-info/METADATA
  do
    if [[ -z "${stamp}" || "${path}" -nt "${stamp}" ]]; then
      stamp="${path}"
    fi
  done
  shopt -u nullglob
  printf '%s' "${stamp}"
}

needs_install=0
reason=""

if [[ "${OTOMEKAIRO_FORCE_PIP_INSTALL:-}" == "1" ]]; then
  needs_install=1
  reason="OTOMEKAIRO_FORCE_PIP_INSTALL=1"
elif ! "${PYTHON}" -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('${IMPORT_NAME}') else 1)"; then
  needs_install=1
  reason="package not importable: ${IMPORT_NAME}"
else
  meta_stamp="$(_metadata_stamp)"
  if [[ -z "${meta_stamp}" ]]; then
    needs_install=1
    reason="install metadata missing"
  elif [[ "${PYPROJECT}" -nt "${meta_stamp}" ]]; then
    needs_install=1
    reason="pyproject.toml is newer than ${meta_stamp}"
  fi
fi

if [[ "${needs_install}" -eq 1 ]]; then
  echo "editable install を実行します: ${IMPORT_NAME} (${reason})"
  "${PYTHON}" -m pip install -e "${PROJECT_DIR}"
else
  echo "editable install は最新です: ${IMPORT_NAME}"
fi
