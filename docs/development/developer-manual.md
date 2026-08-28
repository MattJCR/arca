# Manual de usuario desarrollador

## Qué es

Un manual vivo en `docs/manual/` que explica cada funcionalidad del proyecto: qué hace, cómo se usa, cómo se comporta ante fallos y dónde vive en el código.

## Regla

**El manual se escribe a la par del desarrollo, no después.** Ninguna funcionalidad se da por terminada sin su entrada correspondiente en el manual.

## Solo la realidad actual

El manual refleja **únicamente lo que existe y funciona hoy** en el proyecto:

- No documenta funcionalidades planificadas, futuras ni a medias. Para eso ya existe `ARCA-especificacion.md`.
- Si una funcionalidad cambia, su entrada se actualiza en la misma tarea.
- Si una funcionalidad se elimina, su entrada se elimina.
- Una entrada que describe algo que ya no es cierto es un bug de documentación y se corrige con la misma prioridad que un bug de código.

## Estructura

- `docs/manual/README.md` — índice del manual y estado actual del proyecto.
- Un archivo por área funcional (por ejemplo `docs/manual/downloads.md`, `docs/manual/search.md`), creado cuando esa área tiene su primera funcionalidad real.

## Contenido de cada entrada

Para cada funcionalidad:

1. Qué hace y para qué sirve.
2. Cómo se usa (comando CLI, endpoint o pantalla, con ejemplos reales).
3. Comportamiento ante fallos y estados degradados.
4. Dónde vive en el código (módulos implicados).
