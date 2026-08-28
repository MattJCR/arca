# ARCA — Manual de usuario desarrollador

Manual vivo del proyecto. Documenta **únicamente la realidad actual**: lo que existe y funciona hoy. Las funcionalidades planificadas viven en `ARCA-especificacion.md` y `ARCA-plan.md`, no aquí. Reglas del manual: `docs/development/developer-manual.md`.

## Estado actual del proyecto

**Fase 0 (fundación) completada.** El paquete `arca` se instala con `uv sync`, la CLI funciona (`init`, `serve`, `diagnostics`), el servidor arranca sobre cualquier raíz — llena, vacía o sin inicializar — y explica su estado en español e inglés. Sin contenido todavía: descargas, búsqueda, ingesta y chat llegan en los hitos 1–4.

## Índice de funcionalidades

- [CLI `arca`](cli.md) — comandos `init`, `serve`, `diagnostics`; opciones `--root`, `--lang`, `--version`.
- [Configuración y estructura en disco](configuration.md) — `arca.json`, resolución de la raíz, directorio de sesión, detección de entorno, logging.
- [Base de datos](database.md) — `library.db`, aperturas RW/RO, eventos, matiz WAL del invariante 1.
- [Diagnóstico](diagnostics.md) — las cuatro comprobaciones, estados y dónde se consumen.
- [Interfaz web y API](web.md) — rutas, bilingüe es/en, plantillas y paleta.
