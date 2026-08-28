#!/usr/bin/env bash
# Hook: test-runner (PostToolUse on Edit|Write)
#
# Rule it automates: docs/development/testing.md and RNF-10 — no functional
# change without tests.
#
# Runs the tests related to the edited file: the file itself when it is a test,
# otherwise the test modules named after it or after its package. When nothing
# matches, it runs the whole suite rather than reporting a green it did not
# earn. Coverage is not measured here; that belongs to scripts/verify.py.
#
# Justified bypass: SKIP_TEST_HOOK=1 — only while a refactor is deliberately
# mid-flight and the task ends with scripts/verify.py in green.

set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

[[ "${SKIP_TEST_HOOK:-0}" == "1" ]] && exit 0

hook_field tool_input.file_path
FILE="${HOOK_FIELD_VALUE}"
[[ -z "${FILE}" ]] && exit 0
is_python_file "${FILE}" || exit 0

require_python
[[ -f "${FILE}" ]] || exit 0

# Everything below derives from RELATIVE, which has already had Windows
# separators normalised. Deriving from the raw path would leave the result at the
# mercy of whether this basename treats a backslash as a separator.
RELATIVE="$(relative_path "${FILE}")"
STEM="$(basename "${RELATIVE}" .py)"
PACKAGE="$(basename "$(dirname "${RELATIVE}")")"

TARGETS=()
if [[ "${STEM}" == "conftest" ]]; then
  # A conftest holds fixtures, not tests: pointing pytest at it collects nothing
  # and exits 5. Its directory is the honest scope, since it shapes every test
  # underneath it.
  TARGETS=("$(dirname "${RELATIVE}")")
elif [[ "${RELATIVE}" == tests/* ]]; then
  TARGETS=("${RELATIVE}")
else
  while IFS= read -r candidate; do
    [[ -n "${candidate}" ]] && TARGETS+=("${candidate}")
  done < <(
    cd "${PROJECT_DIR}" &&
      find tests -type f \( -name "test_${STEM}.py" -o -name "test_${PACKAGE}.py" \) 2>/dev/null
  )
fi

# Nothing related found: running the whole suite is the honest answer.
[[ ${#TARGETS[@]} -eq 0 ]] && TARGETS=("tests")

if ! OUTPUT="$(
  cd "${PROJECT_DIR}" &&
    "${PYTHON}" -m pytest --no-header -q -p no:cacheprovider "${TARGETS[@]}" 2>&1
)"; then
  hook_block "[test-runner] tests related to ${RELATIVE} are failing:" "${OUTPUT}"
fi
