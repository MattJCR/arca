#!/usr/bin/env bash
# Shared helpers for the project hooks.
#
# Hooks receive the tool payload as JSON on stdin and communicate through their
# exit code: 0 lets the action through, 2 blocks it and shows stderr to Claude.
#
# The rule that shapes this file: a check that cannot see what it is checking
# must never report success. No parser, no payload or unreadable JSON all block.
# An earlier version fell back to a sed pseudo-parser, which truncated the value
# at the first embedded quote and silently scanned a fragment.

set -uo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
HOOK_NAME="$(basename "$0" .sh)"

if [[ -x "${PROJECT_DIR}/.venv/Scripts/python.exe" ]]; then
  PYTHON="${PROJECT_DIR}/.venv/Scripts/python.exe"
elif [[ -x "${PROJECT_DIR}/.venv/bin/python" ]]; then
  PYTHON="${PROJECT_DIR}/.venv/bin/python"
else
  PYTHON=""
fi

# Parsing the payload only needs some Python, not the project one. Without this
# fallback every hook blocks before the venv exists, including the very command
# that would create it. Tool hooks still call require_python and keep demanding
# the venv.
PARSE_PYTHON="${PYTHON}"
if [[ -z "${PARSE_PYTHON}" ]]; then
  for _candidate in python python3 py; do
    if "${_candidate}" -c "import json" >/dev/null 2>&1; then
      PARSE_PYTHON="${_candidate}"
      break
    fi
  done
fi

# Consume the payload here, once, so every helper can read it as many times as
# it needs. A terminal on stdin means the hook is being run by hand.
if [[ -t 0 ]]; then
  HOOK_PAYLOAD=""
else
  HOOK_PAYLOAD="$(cat)"
fi

# Fail the hook with an actionable message instead of passing silently.
hook_block() {
  printf '%s\n' "$@" >&2
  exit 2
}

require_python() {
  if [[ -z "${PYTHON}" ]]; then
    hook_block "[${HOOK_NAME}] no virtual environment at ${PROJECT_DIR}/.venv," \
      "so this check cannot run its tools." \
      "Run 'uv sync' before editing."
  fi
}

require_parse_python() {
  if [[ -z "${PARSE_PYTHON}" ]]; then
    hook_block "[${HOOK_NAME}] no Python available to parse the hook payload;" \
      "refusing to report success. Install Python or run 'uv sync'."
  fi
}

_HOOK_READER='
import json
import sys

value = json.load(sys.stdin)
for part in sys.argv[1].split("."):
    if not isinstance(value, dict):
        value = ""
        break
    value = value.get(part, "")
sys.stdout.write(value if isinstance(value, str) else "")
'

# Read a dotted field, e.g. tool_input.file_path, into HOOK_FIELD_VALUE.
#
# Deliberately not a command substitution at the call site: hook_block has to run
# in the hook's own shell. Inside `$( )` its exit would only end the subshell and
# the hook would carry on with an empty value — passing when it meant to block.
HOOK_FIELD_VALUE=""
hook_field() {
  require_parse_python
  if [[ -z "${HOOK_PAYLOAD}" ]]; then
    hook_block "[${HOOK_NAME}] empty hook payload on stdin; refusing to report success."
  fi
  if ! HOOK_FIELD_VALUE="$(
    printf '%s' "${HOOK_PAYLOAD}" | "${PARSE_PYTHON}" -c "${_HOOK_READER}" "$1" 2>&1
  )"; then
    hook_block "[${HOOK_NAME}] cannot parse the hook payload: ${HOOK_FIELD_VALUE}" \
      "Blocking: a check that cannot see the change must not report success."
  fi
}

# Project-relative path with forward slashes, for matching.
relative_path() {
  local absolute="${1//\\//}"
  local root="${PROJECT_DIR//\\//}"
  printf '%s' "${absolute#"${root}/"}"
}

is_python_file() {
  [[ "$1" == *.py ]]
}
