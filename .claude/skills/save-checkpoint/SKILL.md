---
name: save-checkpoint
description: Guarda el estado del trabajo en .claude/checkpoint.md — al cerrar una tarea (indicando brevemente lo siguiente), antes de una compactación de contexto o al pausar. Usar al terminar cualquier tarea del plan, cuando el hook PreCompact lo solicite, o cuando el usuario pida pausar/guardar el progreso.
allowed-tools: [Read, Write, Bash, Glob, Grep]
---

# Guardar checkpoint

`.claude/checkpoint.md` es acumulativo: siempre refleja el estado actual del
proyecto, nunca el historial. Se sobrescribe entero en cada escritura.

Hay dos momentos de uso, con distinto alcance:

## A) Al cerrar una tarea (modo breve)

Es la regla de `docs/development/workflow.md`: toda tarea terminada actualiza el
checkpoint antes de entregar. Sobrescribe `.claude/checkpoint.md` con:

```markdown
# Checkpoint — [fecha ISO 8601]

## Estado
- Fase y última tarea cerrada (ID del plan) con su resultado en una línea
- Puerta de calidad: tests / mypy / ruff en el momento de cerrar

## Lo siguiente
- Próxima tarea del plan (ID y título)
- Primera acción concreta al retomar, en una sola frase

## Bloqueos / decisiones abiertas
- Solo si las hay
```

Sé breve: referencia `ARCA-plan.md`, `docs/manual/` y `docs/gates/` en vez de
copiarlos. Tras escribirlo, continúa con el trabajo normalmente.

## B) Antes de compactar o al pausar (modo completo)

**Importante**: en este modo la skill se invoca justo antes de que la sesión se
detenga por completo (el hook que la dispara para la sesión inmediatamente
después). No inicies ni continúes ningún otro trabajo tras escribir el
checkpoint — limítate a guardarlo. La tarea se retomará manualmente en otra
sesión.

Vas a perder el contexto de esta conversación. Antes de que ocurra, escribe toda
la información que una persona (o tú mismo, sin memoria de esta sesión)
necesitaría para continuar exactamente donde lo dejaste, sin releer todo el
historial ni adivinar decisiones.

Sobrescribe `.claude/checkpoint.md` con esta estructura:

```markdown
# Checkpoint — [fecha/hora ISO 8601]

## Tarea en curso
- ID/título de la tarea (referencia al plan si existe uno, ej. INIT.md o roadmap)
- Fase del roadmap en la que está

## Hecho hasta ahora
- Lista concreta de archivos creados/modificados, con qué cambió en cada uno
- Decisiones técnicas ya tomadas y su justificación breve
- Comandos ejecutados relevantes (tests, builds) y su resultado

## Pendiente
- Próximos pasos en orden, el más inmediato primero
- Qué NO se ha empezado todavía dentro de esta tarea

## Bloqueos / dudas abiertas
- Cualquier cosa que impidió avanzar o que requiere decisión humana

## Siguiente acción concreta
- Una sola frase: qué hacer literalmente al retomar (ej. "ejecutar
  `npm test` en src/pokemon-battle y corregir el fallo en
  damage-calculator.test.ts")
```

## Reglas (ambos modos)

- Sé conciso pero completo — en el modo completo prioriza precisión sobre
  brevedad si hay conflicto; en el modo breve, brevedad.
- No repitas información que ya esté en CLAUDE.md, `docs/` o `ARCA-plan.md`;
  referencia esos archivos en vez de copiarlos.
- Sobrescribe siempre el fichero entero: el checkpoint anterior no se conserva.
- El checkpoint nunca queda vacío mientras quede plan por delante; si el
  proyecto se diera por terminado, dilo explícitamente en «Lo siguiente».