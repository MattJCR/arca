#!/usr/bin/env bash
# Hook: event-schema-check (PostToolUse on Edit|Write)
#
# Rule it automates: docs/dashboard/events.md — every event respects its
# versioned schema.
#
# Deliberately inert until phase 5 (SETUP-005 defers it). No event type and no
# schema exist yet, so there is nothing to validate. It stays registered so
# turning it on is a change to this file alone.

exit 0
