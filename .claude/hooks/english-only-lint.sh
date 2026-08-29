#!/usr/bin/env bash
# Hook: english-only-lint (PostToolUse on Edit|Write)
#
# Rule it automates: docs/development/language-policy.md and RES-05 — code,
# identifiers and comments in English.
#
# Only looks inside src/, scripts/ and tests/. docs/, README.md and CLAUDE.md
# are written in Spanish by policy and are never inspected. The message
# catalogues under i18n/catalogs/ are Spanish by definition and are skipped too:
# their whole purpose is to hold the user-facing text.
#
# It flags two unambiguous signals: Spanish-only accented characters, and a
# short list of Spanish words that are not English words. Ambiguous words are
# deliberately absent — a false positive on every edit would train people to
# ignore the hook.
#
# Known limitation: the word list leaves out `de`, `en`, `la`, `un` and other
# very frequent Spanish words that collide with English or with identifiers. A
# short unaccented Spanish sentence built only from those can slip through. The
# accent check catches most real prose; widening the list would produce a false
# positive on every edit, which is worse than a rare miss.
#
# Justified bypasses:
#   SKIP_LANGUAGE_HOOK=1 — one-off, for a proper noun or quotation.
#   A file carrying the marker `english-only-lint: allow-spanish` on any line is
#   skipped entirely. It is meant for the few files whose job is to contain
#   Spanish — the regression tests of this very hook, for instance — and the
#   marker should be followed by the reason.

set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

[[ "${SKIP_LANGUAGE_HOOK:-0}" == "1" ]] && exit 0

hook_field tool_input.file_path
FILE="${HOOK_FIELD_VALUE}"
[[ -z "${FILE}" ]] && exit 0
[[ -f "${FILE}" ]] || exit 0

RELATIVE="$(relative_path "${FILE}")"
case "${RELATIVE}" in
  arca/*|src/*|scripts/*|tests/*) ;;
  *) exit 0 ;;
esac
case "${RELATIVE}" in
  arca/locale/*|arca/templates/*|arca/static/*) exit 0 ;;
esac

# Per-file opt-out for the few files whose job is to contain Spanish.
grep -q 'english-only-lint: allow-spanish' "${FILE}" && exit 0

# "Pokémon" is the spelling of the proper noun and is allowed everywhere, in any
# casing, so it never trips the accent check.
#
# "Archivo" and "Archivo Narrow" are the names of two typefaces we vendor, and
# they collide with the Spanish word on the list below. The rule that keeps the
# check honest: a word glued to a path separator or an extension is part of a
# file name, not prose, so it is stripped; the bare word with spaces around it
# still trips, which is what catches real Spanish.
STRIPPED="$(sed -e 's/[Pp][Oo][Kk][éÉ][Mm][Oo][Nn]//g' \
  -e 's/[Aa]rchivo[ -][Nn]arrow//g' \
  -e 's/[Aa]rchivo[-.\/]//g' \
  -e 's/[-\/][Aa]rchivo//g' "${FILE}")"

ACCENTED="$(printf '%s' "${STRIPPED}" | grep -n '[áéíóúÁÉÍÓÚñÑ¿¡]' || true)"
SPANISH_WORDS='\b(el|los|las|una|unos|unas|del|para|por|que|pero|cuando|donde|esto|esta|este|estos|estas|tiene|debe|puede|cada|todo|todos|fichero|archivo|cadena|prueba|pruebas|entorno|siguiente|mediante|porque|aunque|hasta|desde|entre|sobre)\b'
WORDS="$(printf '%s' "${STRIPPED}" | grep -nEi "${SPANISH_WORDS}" || true)"

if [[ -n "${ACCENTED}" || -n "${WORDS}" ]]; then
  hook_block "[english-only-lint] ${RELATIVE} looks like it contains Spanish." \
    "Code, identifiers, comments and tests are written in English (RES-05);" \
    "user-facing text goes through the i18n catalogue instead." \
    "${ACCENTED}" "${WORDS}"
fi
