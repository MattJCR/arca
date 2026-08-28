# ARCA — Biblioteca de conocimiento offline

Biblioteca de conocimiento consultable sin conexión, con dos mitades: un buscador estilo Google sobre todo el corpus (ZIM de Kiwix + documentos propios) y un panel de chat con un modelo local que asiste sobre lo que hay en pantalla. No es un chatbot con documentos: es un archivo consultable con asistencia encima. Si el modelo desaparece, la biblioteca sigue siendo enteramente útil.

## Documentos clave

- `ARCA-especificacion.md` — **fuente de verdad**: decisiones cerradas, invariantes de robustez, modelo de datos, hitos con criterios de aceptación.
- `ARCA-plan.md` — ruta de desarrollo: fases, tareas con criterios de aceptación, puertas de hito, riesgos.
- `CLAUDE.md` — índice del proyecto para el agente de desarrollo.
- `docs/architecture/` — principios de arquitectura, criterios de decisión, i18n.
- `docs/development/` — reglas de desarrollo (tipado, tests, seguridad, revisión, política de idioma).
- `docs/manual/` — manual de usuario desarrollador: cada funcionalidad documentada a la par del desarrollo, reflejando solo la realidad actual del proyecto.
- `INIT.md` — instrucciones del planificador de proyecto.

## Estado

**Fase 0 (fundación) completada**: paquete `arca` instalable (`uv sync`), CLI (`init`, `serve`, `diagnostics`), servidor que arranca sobre cualquier raíz y explica su estado, diagnóstico de tres estados, interfaz bilingüe español/inglés vía i18n, `library.db` con el esquema completo de §7.1 y cadena de calidad activa (ruff, mypy strict, pytest, hooks). Los hitos se ejecutan en orden estricto:

1. **Hito 1** — Descargador de ZIM (catálogo OPDS de Kiwix) y vendorizado de documentación y dependencias.
2. **Hito 2** — Buscador y visor sin modelo de lenguaje: Xapian de los ZIM + FTS5 de los lotes.
3. **Hito 3** — Ingesta de documentos propios (PDF → texto anclado → lote sellado inmutable).
4. **Hito 4** — Chat con modelo local (`llama-server`), con citación obligatoria y contexto deliberadamente corto.

Cada hito deja el sistema arrancable y útil; al terminar el hito 2 el proyecto ya resuelve casos reales sin modelo.

## Principios innegociables

- Consultar no escribe un byte bajo la raíz de la biblioteca.
- Escribir (descargar, importar) es siempre reanudable.
- Los lotes sellados son inmutables y el original del usuario nunca se toca.
- El sistema nunca deja de arrancar: cualquier subsistema puede faltar y se declara como degradado.
- Cero red en tiempo de consulta; todo vendorizado, incluida la documentación de mantenimiento.

## Idioma

Código, comentarios, tests y documentación técnica en inglés. Interfaz y mensajes de usuario en español.
