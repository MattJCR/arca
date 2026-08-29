# ARCA — Manual de usuario desarrollador

Manual vivo del proyecto. Documenta **únicamente la realidad actual**: lo que existe y funciona hoy. Las funcionalidades planificadas viven en `ARCA-especificacion.md` y `ARCA-plan.md`, no aquí. Reglas del manual: `docs/development/developer-manual.md`.

## Estado actual del proyecto

**Hito 1 (descargador y vendorizado) completado** sobre la fundación de la fase 0. Funciona hoy: búsqueda en el catálogo Kiwix, descarga reanudable y verificada de ZIM con cola y espejos, verificación local sin red, vendorizado con `sources.lock` (verificación offline y detección de deriva), dashboard de administración con catálogo/cola/vendorizado, y diagnóstico con comprobaciones de contenido. Resultado de la puerta del hito: `docs/gates/milestone-1.md`.

**Hito 2 (buscador y visor) completado.** Funciona hoy: lectura de colecciones ZIM con su índice real, servido de artículos con sus estilos e imágenes y navegación por sus enlaces internos, búsqueda por palabras sobre todo el corpus local con fusión por rango y procedencia visible, interfaz de consulta partida archivo/asistente con tipografías vendorizadas, activar y desactivar colecciones sin borrarlas, y el invariante «consultar no escribe» verificado por suite propia. Resultado de la puerta del hito: `docs/gates/milestone-2.md`. Sin ningún modelo de lenguaje de por medio: el archivo ya resuelve casos reales por sí solo.

**Hito 3 (ingesta de documentos propios) completado.** Funciona hoy: `arca import` con el original guardado por su sha256 y nunca modificado, extracción con OCR opcional, anclas deterministas al párrafo, troceado, embeddings, lotes sellados con validación de modelo, índice vectorial int8 (500k fragmentos en 43 ms), franja semántica con el párrafo citado y el original a un clic con PDF.js vendorizado, documentación vendorizada indexada, y los dashboards de documentos, alcance del índice profundo y modelos GGUF. Resultado de la puerta del hito: `docs/gates/milestone-3.md`.

**Hito 4 (chat) completado.** Funciona hoy: `llama-server` gestionado como subproceso (arrancar, parar, reiniciar, `/health`, log en vivo y línea de comandos efectiva), formulario de arranque generado del `--help` del propio binario con capa curada y grupo «otras», modo crudo, perfiles con arranque de prueba y reversión automática al último bueno, validaciones previas de contexto, memoria y GPU, y panel de chat que cita el párrafo exacto o admite que no lo tiene, con aviso visible en temas de riesgo sin cita. Resultado de la puerta del hito: `docs/gates/milestone-4.md`.

**Fase 5 (cierre) completada.** Funciona hoy: ajustes de búsqueda (resultados por fuente, pesos de intercalado, longitud del extracto y troceado para futuras importaciones), mantenimiento con rutas en uso, registro y rotación, verificación integral elemento a elemento, exportar e importar configuración, regeneración del lote de documentación desde el panel, y los contadores reales de lotes, documentos y fragmentos en el estado general. **Los 10 puntos de §18 son ciertos a la vez**: registro en `docs/gates/final.md`.

**Añadido después del cierre**, a petición explícita: la puesta en marcha automática del chat (§13.0: el usuario solo deja el `.gguf` y ARCA consigue, despliega, configura y prueba el motor `llama-server`), los errores del panel localizados con código estable, las dieciséis comprobaciones de §14 completas en el panel de diagnóstico, buscador y descargador de modelos GGUF con sus cuantizaciones (12.6), fijado del commit de llama.cpp para vendorizar su código y su documentación, manual consultable desde el propio panel (12.13), barra de pestañas que envuelve sin ocultar opciones y navegación en los dos sentidos entre el archivo y la administración.

## Índice de funcionalidades

- [CLI `arca`](cli.md) — comandos `init`, `serve`, `diagnostics`, `search`, `add`, `run`, `verify`, `vendor`.
- [Configuración y estructura en disco](configuration.md) — `arca.json`, resolución de la raíz, directorio de sesión, detección de entorno, logging.
- [Base de datos](database.md) — `library.db`, aperturas RW/RO, eventos, matiz WAL del invariante 1.
- [Catálogo y descargas](downloads.md) — cliente OPDS, resolución meta4, motor reanudable, cola, verificación local.
- [Vendorizado](vendor.md) — `sources.lock`, verificación sin red, deriva de versiones, captura de `llama-server --help`.
- [Lectura de colecciones ZIM](zim.md) — metadatos reales, los tres modos de índice, búsqueda, lectura de entradas, servido de artículos, `ZimLibrary`.
- [Interfaz de consulta](interface.md) — pantalla partida, sello de procedencia, visor, estados honestos, dirección visual y tipografías vendorizadas.
- [Documentos propios](documents.md) — ingesta reanudable, anclas, lotes sellados, índice vectorial, franja semántica y alcance del índice profundo.
- [Búsqueda y fusión](search.md) — los dos niveles, intercalado por rango, procedencia de cada resultado, `GET /api/search`.
- [Invariantes verificables](invariants.md) — el contrato de «consultar no escribe» y la degradación en caliente, con su suite y su skill.
- [Modelos](models.md) — buscador de modelos GGUF en el hub, variantes y cuantizaciones, descarga por la cola verificada, y elección del modelo activo.
- [Puesta en marcha automática del chat](engine.md) — el usuario solo deja el modelo: ARCA consigue el motor, lo despliega, crea el perfil y lo prueba.
- [Servidor llama.cpp](llama.md) — descubrimiento del binario, subproceso, formulario generado del `--help`, perfiles, arranque de prueba con reversión y validaciones previas.
- [Chat con citación](chat.md) — estado de pantalla, política de contexto, suelo de relevancia, citación al párrafo y temas de riesgo.
- [Manual en la aplicación](manual-browser.md) — el manual servido desde la propia ARCA, con su renderizador de Markdown acotado.
- [Mantenimiento](maintenance.md) — rutas en uso, registro y rotación, verificación integral elemento a elemento, exportar e importar configuración.
- [Diagnóstico](diagnostics.md) — las dieciséis comprobaciones, estados y dónde se consumen.
- [Interfaz web y API](web.md) — rutas, API de administración, dashboard, bilingüe es/en.
