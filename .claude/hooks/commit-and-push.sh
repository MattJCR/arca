#!/usr/bin/env bash
# Hook: commit-and-push (on demand, no event matcher)
#
# Rule it automates: G-QUALITY and step 8 of docs/development/workflow.md — a
# delivery is a commit that reached origin, and nothing is delivered without the
# quality gate green.
#
# Claude Code has no "the user wants to commit" event, so this hook is not bound
# to a matcher in .claude/settings.json. It is invoked explicitly:
#
#   .claude/hooks/commit-and-push.sh "<commit message>" ["<body paragraph>" ...]
#
# What it refuses to do, in order: work outside this repository, commit on a
# detached HEAD, stage a ROM or any other oversized binary, commit while the
# quality gate is red, and overwrite history on the remote.
#
# Options:
#   --staged-only   commit what is already staged instead of staging everything.
#
# Justified bypass: SKIP_VERIFY=1 — only when the gate cannot run at all, for
# instance before `uv sync` has ever created the environment. Never to push past
# a failing check.

set -uo pipefail

HOOK_NAME="$(basename "$0" .sh)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"

# Largest blob this hook will let into a commit. ROMs, save states and captures
# all land well above it; source files never do.
MAX_BLOB_BYTES=5242880

# Extensions the repository must never track, mirroring the first block of
# .gitignore. Checked again here because `git add -f` bypasses .gitignore.
FORBIDDEN_EXTENSIONS='\.(gb|gbc|gba|nds|sav|srm|state|st[0-9])$'

TRAILER='Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>'

# Fail with an actionable message, the same contract as the event hooks: a
# non-zero exit means nothing was committed and nothing was pushed.
fail() {
  printf '%s\n' "$@" >&2
  exit 1
}

note() {
  printf '[%s] %s\n' "${HOOK_NAME}" "$1"
}

STAGE_ALL=1
MESSAGE_PARTS=()
for argument in "$@"; do
  case "${argument}" in
    --staged-only) STAGE_ALL=0 ;;
    --*) fail "[${HOOK_NAME}] unknown option: ${argument}" \
      "Usage: ${HOOK_NAME}.sh [--staged-only] \"<commit message>\" [\"<body>\" ...]" ;;
    *) MESSAGE_PARTS+=("${argument}") ;;
  esac
done

if [[ ${#MESSAGE_PARTS[@]} -eq 0 ]]; then
  fail "[${HOOK_NAME}] a commit message is required." \
    "Usage: ${HOOK_NAME}.sh [--staged-only] \"<commit message>\" [\"<body>\" ...]"
fi

cd "${PROJECT_DIR}" || fail "[${HOOK_NAME}] cannot enter ${PROJECT_DIR}."

# ---------------------------------------------------------------------------
# Repository state
# ---------------------------------------------------------------------------

REPOSITORY_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [[ -z "${REPOSITORY_ROOT}" ]]; then
  fail "[${HOOK_NAME}] ${PROJECT_DIR} is not inside a git repository."
fi

# symbolic-ref keeps working on an unborn branch (a fresh repo before its first
# commit), where rev-parse --abbrev-ref reports HEAD and looks detached.
BRANCH="$(git symbolic-ref --short -q HEAD)"
if [[ -z "${BRANCH}" ]]; then
  fail "[${HOOK_NAME}] detached HEAD: check out a branch before committing."
fi

if [[ -z "$(git remote 2>/dev/null)" ]]; then
  fail "[${HOOK_NAME}] no remote configured, so the change cannot be pushed."
fi

# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------

if [[ ${STAGE_ALL} -eq 1 ]]; then
  git add -A || fail "[${HOOK_NAME}] 'git add -A' failed."
fi

STAGED_FILES="$(git diff --cached --name-only --diff-filter=ACMR)"
if [[ -z "${STAGED_FILES}" ]]; then
  note "nothing staged, so there is nothing to commit."
  exit 0
fi

# ---------------------------------------------------------------------------
# Content guard — runs on the staged blobs, not on the working tree, so it sees
# exactly what the commit would carry.
# ---------------------------------------------------------------------------

while IFS= read -r path; do
  [[ -z "${path}" ]] && continue

  if printf '%s' "${path}" | grep -Eqi -- "${FORBIDDEN_EXTENSIONS}"; then
    fail "[${HOOK_NAME}] refusing to commit ${path}: ROMs and save states are never tracked." \
      "Unstage it with 'git restore --staged ${path}' and keep it out of the repository."
  fi

  size="$(git cat-file -s ":${path}" 2>/dev/null)"
  if [[ -n "${size}" && "${size}" -gt ${MAX_BLOB_BYTES} ]]; then
    fail "[${HOOK_NAME}] refusing to commit ${path}: ${size} bytes exceeds the ${MAX_BLOB_BYTES} byte limit." \
      "Large binaries do not belong in this repository; unstage it or add it to .gitignore."
  fi
done <<< "${STAGED_FILES}"

note "staging $(printf '%s\n' "${STAGED_FILES}" | wc -l | tr -d ' ') file(s) on branch ${BRANCH}."

# ---------------------------------------------------------------------------
# Quality gate
# ---------------------------------------------------------------------------

if [[ "${SKIP_VERIFY:-0}" == "1" ]]; then
  note "quality gate skipped through SKIP_VERIFY=1."
else
  note "running the quality gate (uv run python scripts/verify.py)..."
  if ! uv run python scripts/verify.py; then
    fail "[${HOOK_NAME}] the quality gate is red, so nothing was committed (G-QUALITY)." \
      "Fix the failing checks above and run this hook again."
  fi
fi

# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------

COMMIT_ARGUMENTS=()
for part in "${MESSAGE_PARTS[@]}"; do
  COMMIT_ARGUMENTS+=(-m "${part}")
done
# The trailer is appended once: a message that already carries it is left alone.
if ! printf '%s\n' "${MESSAGE_PARTS[@]}" | grep -Fq -- "${TRAILER}"; then
  COMMIT_ARGUMENTS+=(-m "${TRAILER}")
fi

if ! git commit "${COMMIT_ARGUMENTS[@]}"; then
  fail "[${HOOK_NAME}] 'git commit' failed; nothing was pushed."
fi

COMMIT_SHA="$(git rev-parse --short HEAD)"
note "committed ${COMMIT_SHA} on ${BRANCH}."

# ---------------------------------------------------------------------------
# Push — never forced. A rejected push means the remote moved on, and resolving
# that is a decision for a person, not for a hook.
# ---------------------------------------------------------------------------

if git rev-parse --abbrev-ref "@{upstream}" >/dev/null 2>&1; then
  PUSH_ARGUMENTS=()
else
  note "no upstream for ${BRANCH}; setting it on origin."
  PUSH_ARGUMENTS=(--set-upstream origin "${BRANCH}")
fi

if ! git push "${PUSH_ARGUMENTS[@]}"; then
  fail "[${HOOK_NAME}] the push was rejected. The commit ${COMMIT_SHA} is local only." \
    "Integrate the remote branch ('git pull --rebase') and push again. This hook never forces."
fi

note "pushed ${COMMIT_SHA} to origin/${BRANCH}."
