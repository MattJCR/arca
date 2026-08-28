#!/bin/bash
# .claude/hooks/require-checkpoint.sh
#
# Se dispara en PreCompact (auto, cuando Claude Code se acerca a su límite
# interno de contexto). Comportamiento en dos pasos:
#
#   1) Si no hay checkpoint reciente: bloquea (exit 2) y obliga a Claude a
#      ejecutar la skill save-checkpoint antes de nada más.
#   2) Si el checkpoint YA está fresco (se acaba de guardar en el paso 1):
#      detiene la sesión por completo con continue:false. Esto no depende
#      de que Claude "decida" parar — Claude Code lo fuerza igualmente.
#
# Requiere jq instalado (jq -n para construir el JSON de salida).

CHECKPOINT_FILE="${CLAUDE_PROJECT_DIR}/.claude/checkpoint.md"
MAX_AGE_MIN=5

is_stale() {
  [ ! -f "$CHECKPOINT_FILE" ] || [ -n "$(find "$CHECKPOINT_FILE" -mmin +$MAX_AGE_MIN)" ]
}

if is_stale; then
  echo "El contexto está cerca del límite y va a pausarse. Antes de nada, ejecuta AHORA la skill save-checkpoint (.claude/skills/save-checkpoint) para guardar el estado en .claude/checkpoint.md. No continúes con la tarea después de guardarlo: la sesión se detendrá." >&2
  exit 2
fi

# Checkpoint ya fresco: se acaba de guardar en el paso anterior.
# Parar la sesión de forma determinista, sin depender de Claude.
jq -n '{
  continue: false,
  stopReason: "Contexto cerca del límite. Estado guardado en .claude/checkpoint.md. Retoma la tarea manualmente cuando quieras, con: claude --resume"
}'
exit 0