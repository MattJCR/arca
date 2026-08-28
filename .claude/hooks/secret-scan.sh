#!/usr/bin/env bash
# Hook: secret-scan (PreToolUse on Bash)
#
# Rule it automates: docs/development/security.md and RES-08 — no secrets or
# credentials in the project.
#
# Blocks a shell command that carries what looks like a credential. It matches
# assignment shapes and known key formats, never a bare word, so the usual
# commands of this project (including editing this very file) go through.
#
# Justified bypass: SKIP_SECRET_SCAN=1 — for a false positive on a value that
# is provably not a credential. Never to push a real one through.

set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

[[ "${SKIP_SECRET_SCAN:-0}" == "1" ]] && exit 0

# A payload that cannot be parsed blocks inside hook_field: scanning a truncated
# command would be worse than not scanning at all.
hook_field tool_input.command
COMMAND="${HOOK_FIELD_VALUE}"
[[ -z "${COMMAND}" ]] && exit 0

# Each entry is "description|extended regular expression".
PATTERNS=(
  "credential assigned inline|(password|passwd|secret|token|api[-_]?key|access[-_]?key|auth[-_]?token)[[:space:]]*[=:][[:space:]]*[\"']?[A-Za-z0-9/+_.-]{8,}"
  "AWS access key id|AKIA[0-9A-Z]{16}"
  "private key block|-----BEGIN[A-Z ]*PRIVATE KEY-----"
  "GitHub token|gh[pousr]_[A-Za-z0-9]{20,}"
  "Slack token|xox[abprs]-[A-Za-z0-9-]{10,}"
  "OpenAI-style key|sk-[A-Za-z0-9]{20,}"
  "bearer credential|[Aa]uthorization:[[:space:]]*[Bb]earer[[:space:]]+[A-Za-z0-9._-]{16,}"
)

for entry in "${PATTERNS[@]}"; do
  description="${entry%%|*}"
  pattern="${entry#*|}"
  if printf '%s' "${COMMAND}" | grep -Eqi -- "${pattern}"; then
    hook_block "[secret-scan] blocked: the command looks like it contains a ${description}." \
      "Move the value to the environment or a file outside version control (RES-08)." \
      "If this is a false positive, re-run it with SKIP_SECRET_SCAN=1 and say why."
  fi
done

exit 0
