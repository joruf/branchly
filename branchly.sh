#!/usr/bin/env bash
# Starts Branchly, preferring the project's own virtual environment.
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="${HERE}/.venv/bin/python"

if [[ -x "${VENV_PYTHON}" ]]; then
    exec "${VENV_PYTHON}" "${HERE}/run.py" "$@"
fi

echo "No virtual environment found. Run ./install_dependencies.py first." >&2
echo "Trying the system interpreter anyway…" >&2
exec python3 "${HERE}/run.py" "$@"
