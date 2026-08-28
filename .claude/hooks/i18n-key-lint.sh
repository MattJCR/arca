#!/usr/bin/env bash
# Hook: i18n-key-lint (PostToolUse on Edit|Write)
#
# Rule it automates: docs/architecture/i18n.md and RNF-11 — no user-facing
# string written straight into the code.
#
# Deliberately inert until phase 5 (SETUP-005 defers it). There is no CLI, API
# or dashboard yet, so there is no user-facing string to catch: enabling it now
# would only produce noise on every edit. It stays registered so turning it on
# is a change to this file alone.

exit 0
