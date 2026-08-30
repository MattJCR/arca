# ARCA — Índice del proyecto

## Propósito

Biblioteca de conocimiento consultable sin conexión: un buscador estilo Google sobre todo el corpus (ZIM de Kiwix + documentos propios) y un panel de chat con un modelo local que asiste sobre lo que hay en pantalla. Si el modelo desaparece, la biblioteca sigue siendo enteramente útil: nada del núcleo depende del modelo.

**Fuente de verdad:** `ARCA-especificacion.md`. Las decisiones de la sección 2 están cerradas — no se vuelven a preguntar. El orden de los hitos (1 descargador/vendorizado → 2 buscador/visor → 3 ingesta → 4 chat) no es negociable, y cada hito debe dejar el sistema arrancable y útil.

**Ruta de desarrollo:** `ARCA-plan.md` — fases, desglose de tareas con criterios de aceptación, puertas de hito y asignación de las trampas de §17. Tarea inicial: F0.1.

## Tecnologías principales

- Python (paquete `arca`) con exactamente 5 dependencias directas: `fastapi`, `uvicorn`, `libzim`, `pymupdf`, `numpy` (tope duro: 8). Todo lo demás, biblioteca estándar (`urllib.request`, `xml.etree.ElementTree`, `sqlite3`, `hashlib`).
- SQLite: `library.db` escribible (WAL) + `text.sqlite` por lote sellado (solo lectura, `journal_mode=DELETE`). Vectores: índice plano int8 en `numpy.memmap`, sin motor externo.
- Binarios externos como subprocesos, nunca dependencias: `llama-server` (llama.cpp) y opcionalmente `ocrmypdf`/`tesseract`. Si faltan, el sistema arranca degradado.
- Frontend: HTML servido + JavaScript sin framework ni empaquetador. Todo vendorizado, cero CDN, cero llamadas de red en tiempo de consulta.

## Arquitectura general

Ver `docs/architecture/` para los principios, la jerarquía de carga y la estructura completa. Resumen:

- Jerarquía de carga: CLAUDE.md → Skills → Subagents → docs/data → herramientas.
- La lógica de negocio nunca vive en este archivo.
- Estructura del repositorio y de la biblioteca en disco: `ARCA-especificacion.md` §4 y §5.

## Reglas globales

- Ver `docs/development/` para el conjunto completo de reglas de desarrollo.
- Idioma (`docs/development/language-policy.md` + especificación §0): todo el código, tests y documentación técnica en inglés; la interfaz de usuario es bilingüe español/inglés desde el inicio (español por defecto), siempre vía i18n con ambos catálogos completos.
- **Sin comentarios en el código. Solo código.** Lo que necesite explicación se documenta en `docs/`, no en el fuente (`docs/development/coding-standards.md`).
- Los 7 invariantes de robustez de `ARCA-especificacion.md` §6 no se rompen jamás: consultar no escribe, escribir es reanudable, lotes inmutables, el original nunca se toca, nunca deja de arrancar, detección de entorno en cada arranque, verificación antes de uso. Si una decisión de implementación los contradice, la decisión está mal.
- Ante la duda entre una solución elegante y una aburrida, elige la aburrida (§0).
- Añadir una dependencia obliga a vendorizar su documentación, wheel, sdist y licencia (§3 y §8.3). Ese coste es deliberado.
- Revisar las 12 trampas conocidas (§17) antes de implementar el área afectada.
- Ninguna decisión de arquitectura se toma sin pasar por `docs/architecture/decision-criteria.md`.

## Convenciones obligatorias

- Tipado estricto (`docs/development/typing.md`).
- Toda modificación funcional requiere tests (`docs/development/testing.md`).
- Sin secretos ni credenciales en código (`docs/development/security.md`).
- Ninguna cadena de usuario hardcodeada: usar siempre el sistema i18n (`docs/architecture/i18n.md`), con catálogos `es` y `en` completos en cada cambio.
- Cada hito termina con sus criterios de aceptación verificables (§8.4, §9.5, §10.3, §11.4 y §18). No se empieza el siguiente hasta que pasen.
- Manual de usuario desarrollador (`docs/manual/`): cada funcionalidad se documenta a la par de su desarrollo, y el manual refleja solo la realidad actual del proyecto — nunca lo planificado ni lo obsoleto (`docs/development/developer-manual.md`).

## Flujo de trabajo

1. Comprender la solicitud → 2. Analizar código existente → 3. Identificar restricciones →
4. Planificar → 5. Implementar → 6. Tests → 7. Actualizar manual (`docs/manual/`) → 8. Revisar (subagent `code-reviewer`) → 9. Checkpoint → 10. Entregar.

- **Checkpoint al cerrar cada tarea:** toda tarea terminada crea o actualiza `.claude/checkpoint.md` (skill `save-checkpoint`) indicando brevemente la tarea cerrada, **lo siguiente** (próxima tarea del plan y primera acción concreta) y los bloqueos abiertos. Es acumulativo y refleja el estado actual, no el historial.

Detalle completo en `docs/development/workflow.md`.

## Skills disponibles

- `architecture-review` — decisiones estructurales y de contexto mínimo.
- `dev-guidelines` — reglas generales de desarrollo.
- `testing` — pruebas del proyecto.
- `deployment` — builds y releases.
- `save-checkpoint` — guarda el estado de la tarea en `.claude/checkpoint.md` antes de que la sesión se detenga por límite de contexto.
- `verify-no-write` — comprueba el invariante 1 (consultar no escribe) ejerciendo una sesión completa de consulta.
- `verify-anchors` — comprueba que el ancla `p:PPPP:OO` de los formatos reflowables es determinista con el maquetado fijado (trampa 12 de §17).
- Más skills específicas de ARCA se crearán por capacidad reutilizable a medida que avancen los hitos (candidatas: sellado/validación de lotes, gestión de perfiles de llama.cpp) — ver `docs/architecture/decision-criteria.md` para el criterio.

## Hooks disponibles

`lint-and-format`, `typecheck`, `test-runner`, `secret-scan`, `i18n-key-lint`, `english-only-lint`. Declarados en `.claude/settings.json`, scripts en `.claude/hooks/` (pendientes de implementación real).

`require-checkpoint` — en `PreCompact`; fuerza `save-checkpoint` y detiene la sesión (`continue: false`) al acercarse al límite de contexto, para retomar manualmente con `claude --resume`.

`commit-and-push` — bajo demanda, sin matcher: Claude Code no tiene un evento «el usuario quiere
comitear». Se invoca `.claude/hooks/commit-and-push.sh "<mensaje>"` y encadena stage → guardia de
contenido → puerta de calidad → commit → push. Rechaza ROMs y binarios grandes aunque se hayan
forzado con `git add -f`, no comitea con la puerta en rojo (`SKIP_VERIFY=1` como excepción
justificada) y nunca fuerza el push.

`event-schema-check` — verificación de esquema de eventos (`.claude/hooks/event-schema-check.sh`).

## Subagents disponibles

`code-reviewer`, `architecture-guardian`, `docs-sync`, `automation-auditor`. Definidos en `.claude/agents/`.

- Subagents específicos de ARCA: no hay por ahora; se añadirán solo si la complejidad de un hito lo justifica.

## Principios generales

Contexto mínimo · sin duplicación · determinismo cuando sea posible · IA solo donde aporte valor · modularidad · desacoplamiento · reutilización · escalabilidad · fiabilidad antes que confidencialidad · nada del núcleo depende del modelo.
