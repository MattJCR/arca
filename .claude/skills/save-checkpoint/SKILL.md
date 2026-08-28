---
name: save-checkpoint
description: Guarda el estado actual de la tarea en .claude/checkpoint.md antes de una compactación de contexto o al pausar, para poder retomar la tarea sin ambigüedad en otra sesión. Usar cuando el hook PreCompact lo solicite, o cuando el usuario pida pausar/guardar el progreso explícitamente.
allowed-tools: [Read, Write, Bash, Glob, Grep]
---

**Importante**: esta skill se invoca justo antes de que la sesión se detenga
por completo (el hook que la dispara para la sesión inmediatamente después).
No inicies ni continúes ningún otro trabajo tras escribir el checkpoint —
limítate a guardarlo. La tarea se retomará manualmente en otra sesión.

# Guardar checkpoint de la tarea

Vas a perder el contexto de esta conversación (por compactación o fin de sesión).
Antes de que ocurra, escribe en `.claude/checkpoint.md` toda la información que
una persona (o tú mismo, sin memoria de esta sesión) necesitaría para continuar
exactamente donde lo dejaste, sin releer todo el historial ni adivinar decisiones.

## Qué incluir

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

## Reglas

- Sé conciso pero completo — prioriza precisión sobre brevedad si hay conflicto.
- No repitas información que ya esté en CLAUDE.md, docs/ o el plan (INIT.md);
  referencia esos archivos en vez de copiarlos.
- Si ya existe un `.claude/checkpoint.md` de un checkpoint anterior de la MISMA
  tarea, actualízalo en vez de duplicar información antigua.
- Si la tarea ya se completó del todo, no crees checkpoint: indícalo brevemente
  y no escribas el archivo.