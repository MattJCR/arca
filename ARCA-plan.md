# ARCA — Ruta de desarrollo

Plan de ejecución derivado de `ARCA-especificacion.md`. La especificación es la fuente de verdad; este documento no la duplica: ordena el trabajo, lo trocea en tareas verificables y fija las puertas de calidad. Cuando un dato esté aquí y en la especificación, manda la especificación.

---

## 1. Resumen del proyecto

- **Objetivo:** biblioteca de conocimiento consultable sin conexión — buscador sobre todo el corpus + chat con modelo local — que siga funcionando dentro de cinco años, en una máquina desconocida, sin red.
- **Alcance:** lo definido en la especificación §1. Fuera de alcance: login, multiusuario, cifrado, nube, telemetría, red en tiempo de consulta.
- **Usuarios:** un único operador con acceso físico a la máquina. Dos pantallas, dos públicos: interfaz de consulta (4 ajustes) y dashboard de administración (todo lo demás).
- **Funcionalidades principales:** descarga verificada y reanudable de ZIM (catálogo Kiwix), vendorizado de documentación y dependencias, búsqueda por palabras sobre todo el corpus, búsqueda semántica sobre un subconjunto elegido, ingesta de PDF propios con trazabilidad al párrafo, chat local con citación obligatoria, diagnóstico honesto del estado real.

## 2. Requisitos (mapa, no copia)

- **Funcionales:** hito 1 (§8: catálogo, descarga, vendorizado), hito 2 (§9: lectura ZIM, dos niveles de búsqueda, fusión, trazabilidad), hito 3 (§10: ingesta reanudable, lotes sellados), hito 4 (§11: chat con contexto de pantalla, citación), dashboard (§12), administración de llama.cpp (§13), diagnóstico (§14), API (§15), dirección visual (§16).
- **No funcionales:** los 7 invariantes de robustez (§6) son los requisitos no funcionales del proyecto, más el suelo de calidad de interfaz (§16) y los tiempos implícitos (§7.4: búsqueda vectorial < 200 ms).
- **Restricciones:** decisiones cerradas (§2), tope de 8 dependencias (§3), estructura de repositorio y de disco (§4, §5), orden de hitos innegociable (§0), código sin comentarios y en inglés con UI en español (reglas del proyecto).

## 3. Arquitectura (confirmación, no propuesta)

La arquitectura viene cerrada en la especificación: módulos de §4, modelo de datos de §7, API de §15. Este plan no la cambia; solo fija el **orden de construcción** de los módulos:

```
config → db → diagnostics → cli/api   (fundación: arrancable vacío)
catalog → fetch → vendor              (hito 1)
zim → search → frontend               (hito 2)
ingest → embed → vectors → shards     (hito 3, cierra el lote vendor de §8.3)
llama → chat                          (hito 4)
```

**Análisis clave detectado al planificar:** §8.3 dice que `arca vendor` «sella un lote con el texto extraído e indexado», pero la maquinaria de lotes, embeddings y vectores pertenece al hito 3. No es una contradicción: los criterios de aceptación del hito 1 (§8.4) solo exigen descargar, verificar y `--verify` sin red. Por tanto, en la fase 1 el vendorizado **descarga y verifica**; el sellado e indexado del lote vendor se completa en la tarea H3.10. Así se respeta el orden de hitos sin perder funcionalidad.

## 4. Cómo esta ruta protege fiabilidad, sencillez y estabilidad

1. **Los invariantes se construyen primero, no se auditan después.** La apertura solo-lectura de `library.db` para consulta (F0.3), el temporal fuera de la raíz (F0.2) y el arranque que nunca falla (F0.6) existen antes de la primera funcionalidad. Es más barato que perseguirlos a posteriori.
2. **Cada fase termina con una puerta ejecutable** (los criterios de aceptación literales de la especificación). No se abre la fase siguiente con la puerta en rojo.
3. **Las 12 trampas de §17 están asignadas a tareas concretas** (tabla en §8 de este plan). Ninguna queda sin dueño.
4. **Sencillez por construcción:** cada tarea implementa el camino aburrido que la especificación ya eligió (fuerza bruta con numpy, intercalado por rango, relectura completa para el hash). Cualquier tentación de «mejorarlo» pasa por `docs/architecture/decision-criteria.md` y por la especificación §2.
5. **El manual de desarrollador crece a la par** (regla del proyecto): cada tarea funcional incluye su entrada en `docs/manual/`, reflejando solo lo que ya existe.

---

## 5. Roadmap

| Fase | Contenido | Resultado funcional al cerrar |
|---|---|---|
| **F0 — Fundación** | Paquete, config, base de datos, diagnóstico núcleo, esqueleto CLI/API | El sistema arranca vacío y dice honestamente qué falta |
| **F1 — Hito 1** | Catálogo, descarga reanudable, verificación, vendorizado (descarga+verificación) | Se descargan ZIM reales, reanudables y verificados; `vendor --verify` sin red |
| **F2 — Hito 2** | Lectura ZIM, búsqueda por palabras, visor, frontend, fusión | Resuelve casos reales sin ningún modelo de lenguaje |
| **F3 — Hito 3** | Ingesta PDF, embeddings, vectores, lotes sellados, búsqueda semántica, lote vendor indexado | Documentos propios buscables al párrafo, con original a un clic |
| **F4 — Hito 4** | Subproceso llama-server, perfiles con reversión, chat con citación | Chat que cita o admite que no sabe; nunca deja el sistema peor |
| **F5 — Cierre** | Dashboards restantes, verificación integral | Los 10 puntos de §18 son ciertos a la vez |

---

## 6. Desglose de tareas

Formato: **ID — Título** · Descripción · Dependencias · Criterios de aceptación (CA) · Loop sugerido.

### Fase 0 — Fundación

**F0.1 — Esqueleto del paquete y cadena de calidad**
Crear `pyproject.toml` con las 5 dependencias fijadas (§3), el paquete `arca/` con módulos vacíos según §4, `tests/`, el entrypoint `arca` y el entorno (`.venv`) que activa los hooks de calidad del repositorio (hoy fallan por no existir).
· Dependencias: — · CA: `arca --version` responde; `pytest` ejecuta; los hooks `lint-and-format`, `typecheck`, `test-runner` y `english-only-lint` corren en verde en cada edición.
· Loop: **Goal-based** — «hooks de calidad en verde» es un criterio objetivo y permanente; los hooks ya lo automatizan por edición.

**F0.2 — Configuración y raíz de la biblioteca**
`config.py`: cargar/crear `arca.json` en la raíz única (§5); resolver rutas; temporal de sesión y caché **fuera** de la raíz (invariante 1); detección de entorno en cada arranque (invariante 6, versión mínima: qué directorios y ficheros existen realmente).
· Dep: F0.1 · CA: primer arranque sobre carpeta vacía crea la estructura y un `arca.json` editable a mano; ningún estado de sesión bajo la raíz.
· Loop: Ninguno — tarea puntual con tests unitarios.

**F0.3 — Base de datos**
`db.py`: esquema completo de `library.db` (§7.1, WAL solo aquí), migraciones, y dos modos de apertura: escritura (procesos de descarga/importación/administración) y **solo lectura con `query_only`** para el proceso de consulta.
· Dep: F0.2 · CA: el esquema coincide con §7.1; un test demuestra que la conexión de consulta no puede escribir.
· Loop: **Turn-based** — comprobación de deriva de esquema repetible (el hook `event-schema-check` es el sitio natural).

**F0.4 — Eventos y logging**
Tabla `events` + `logs/arca.log` con rotación; solo escriben los procesos administrativos.
· Dep: F0.3 · CA: eventos consultables; el proceso de consulta no escribe ni log ni eventos bajo la raíz.
· Loop: Ninguno.

**F0.5 — Diagnóstico núcleo**
`diagnostics.py` con el patrón de tres estados (correcto/degradado/roto) y las comprobaciones que no dependen de contenido: ruta, espacio libre, soporte de solo lectura, reloj (§14). Cada una dice qué falla, qué se pierde y qué hacer.
· Dep: F0.2 · CA: `GET /api/diagnostics` devuelve la lista con estados reales; desmontar la ruta produce «roto» con mensaje accionable.
· Loop: Ninguno — el diagnóstico ES el loop de runtime; no necesita otro encima.

**F0.6 — Esqueleto CLI y API**
`cli.py` (`arca <comando>`), `api.py` con FastAPI/uvicorn, página raíz mínima y `/admin` vacío. Invariante 5 desde el primer día: sin nada descargado, arranca e invita a descargar.
· Dep: F0.5 · CA: `arca` levanta el servidor sobre una biblioteca vacía sin error; la portada explica el estado.
· Loop: Ninguno.

### Fase 1 — Hito 1: descargador y vendorizado

**H1.1 — Cliente del catálogo OPDS**
`catalog.py`: consulta en el momento a `library.kiwix.org` con filtros (§8.1), parseo con `ElementTree` (espacios de nombres Atom/OPDS/Kiwix), volcado a `zim_files`. Nunca se persiste una URL de descarga (trampa 5).
· Dep: F0.6 · CA: `arca search wikipedia --lang spa` lista resultados reales del catálogo remoto (§8.4); tests con feed fixture, sin red.
· Loop: Ninguno.

**H1.2 — Resolución meta4 y camino sin meta4**
Descarga del `.meta4` con `?country=ES`, parseo de tamaño/sha-256/espejos por prioridad; camino alternativo cuando el meta4 falta: descargar sin hash, avisar, guardar el hash calculado para verificaciones locales futuras (trampa 6).
· Dep: H1.1 · CA: para una entrada real se obtienen espejos ordenados y hash; la ausencia de meta4 degrada con aviso, no rompe.
· Loop: Ninguno.

**H1.3 — Motor de descarga reanudable**
`fetch.py`: `.part` + `Range`, exigir `206` (si `200`, truncar y empezar — trampa 7), failover de espejo sobre el mismo `.part`, reintentos con espera creciente, persistencia periódica de progreso (el tamaño real del `.part` manda al reanudar), verificación por relectura completa, `os.replace` y `.sha256` al lado; en fallo de hash, `failed` sin borrar nada (§8.2).
· Dep: H1.2, F0.3 · CA: los cinco escenarios de §8.4 relativos a descarga (reanudar al 40%, corrupción detectada, cambio de espejo en caliente).
· Loop: **Turn-based** — los escenarios de corte/reanudación son una batería repetible que conviene capturar como skill de verificación y repasar cada vez que se toque `fetch.py`.

**H1.4 — Cola de descargas y CLI**
Tabla `downloads` operativa, `arca add <uuid>`, `arca run`, concurrencia 2 configurable, pausa/reanudación/cancelación/reintento.
· Dep: H1.3 · CA: `arca add` encola y `arca run` descarga con progreso visible (§8.4).
· Loop: Ninguno.

**H1.5 — Verificación local**
`arca verify`: recomprobar hashes de la biblioteca contra los `.sha256` guardados, sin red, marcando `corrupt` en `zim_files`.
· Dep: H1.3 · CA: corromper un byte y ejecutar `arca verify` lo detecta y lo marca (§8.4).
· Loop: Ninguno en desarrollo (en producto ya es un botón del dashboard).

**H1.6 — Vendorizado: lock, descarga y verificación**
`sources.lock` (formato §8.3) y `vendor.py`: `arca vendor` (descarga por el motor de H1.3 y verifica), `arca vendor --verify` (sin tocar la red), `arca vendor --check-drift` (instalado vs documentado). Incluye la captura del `llama-server --help` local cuando exista. El sellado del lote indexado queda para H3.10.
· Dep: H1.3 · CA: `--verify` funciona con el cable desenchufado (§8.4); `--check-drift` detecta una versión desalineada provocada en test.
· Loop: **Goal-based** — «cero deriva entre lock e instalado» es medible; candidato a `/goal` antes de cada release.

**H1.7 — Dashboard: catálogo, descargas y estado general**
Secciones 12.1 (versión inicial) y 12.2: buscador del catálogo con filtros OPDS, cola con progreso/velocidad/espejo y acciones, detección de versiones nuevas por `book_name`, reverificación local.
· Dep: H1.4, H1.5, F0.6 · CA: todo lo listado en §12.2 operable desde el navegador.
· Loop: Ninguno.

**H1.8 — Diagnóstico de contenido**
Añadir comprobaciones: integridad de ZIM, descargas incompletas (`.part` huérfanos), deriva de documentación, dependencia sin documentar (§14).
· Dep: H1.5, H1.6 · CA: cada comprobación distingue sus tres estados con casos provocados en tests.
· Loop: Ninguno.

**H1.9 — Puerta del hito 1**
Ejecutar los 6 criterios de §8.4 de principio a fin, documentar el resultado y actualizar `docs/manual/`.
· Dep: H1.1–H1.8 · CA: los 6 criterios pasan en una ejecución limpia.
· Loop: **Goal-based** — puerta de hito con criterios objetivos; no se abre F2 sin ella.

### Fase 2 — Hito 2: buscador y visor

**H2.1 — Lectura de ZIM**
`zim.py` sobre `libzim`: apertura, detección de `has_fulltext`, búsqueda por título y por texto completo (Xapian interno). Un ZIM sin índice se declara, no se finge (§9.1).
· Dep: H1.9 · CA: búsqueda instantánea sobre un ZIM real; el dashboard refleja cuáles traen índice.
· Loop: Ninguno.

**H2.2 — Servido de artículos**
`GET /zim/{uuid}/{path}` con reescritura de enlaces (`<base>` + resolutor) y recursos internos (CSS, imágenes) desde el propio ZIM. La salida de emergencia `kiwix-serve` queda documentada como plan B, no implementada.
· Dep: H2.1 · CA: un artículo de Wikipedia se abre con estilos e imágenes y se navega por enlaces internos sin salir de la aplicación (§9.5).
· Loop: Ninguno.

**H2.3 — Fusión de resultados**
`search.py`: intercalado por rango dentro de cada fuente, origen visible en cada resultado, sin normalizar scores (§9.3, trampa 3). La franja semántica queda preparada pero vacía hasta F3.
· Dep: H2.1 · CA: una búsqueda devuelve resultados de al menos tres ZIM etiquetados por origen (§9.5).
· Loop: Ninguno.

**H2.4 — Frontend base y dirección visual**
Pantalla partida (archivo/asistente), paleta, tipografías WOFF2 vendorizadas en `static/fonts/` (§16, cero CDN), el **sello de procedencia** como componente central, suelo de calidad (responsive, foco visible, movimiento reducido, contraste).
· Dep: H2.2, H2.3 · CA: cero peticiones de red externas en toda la interfaz; el sello aparece en cada resultado.
· Loop: **Turn-based** — checklist de accesibilidad y de «cero red» repetible en cada cambio de frontend; candidata a skill de verificación.

**H2.5 — Invariante «consultar no escribe», verificable**
Mecanismo de prueba que ejerce una sesión completa de consulta y comprueba que no aparece ningún fichero nuevo bajo la raíz (ni `-wal`, ni `-shm`, ni temporales) y manejo del disco desconectado en caliente con error claro.
· Dep: H2.2, H2.3 · CA: los dos últimos criterios de §9.5.
· Loop: **Turn-based** — esta verificación se repite tras CUALQUIER tarea futura que toque el camino de consulta; es la skill de verificación más importante del proyecto.

**H2.6 — Dashboard: contenido local**
Sección 12.3: lista de ZIM con metadatos y estado, activar/desactivar para búsqueda sin borrar.
· Dep: H2.1, H1.7 · CA: desactivar un ZIM lo excluye de resultados de inmediato.
· Loop: Ninguno.

**H2.7 — Estados vacíos y degradados**
Sin colecciones: invitación a descargar con enlace al catálogo; errores que dicen qué pasó y qué hacer (§16).
· Dep: H2.4 · CA: arranque sin ZIM muestra el estado vacío diseñado, no una pantalla rota.
· Loop: Ninguno.

**H2.8 — Puerta del hito 2**
Ejecutar §9.5 completo; actualizar `docs/manual/`.
· Dep: H2.1–H2.7 · CA: los 4 criterios pasan.
· Loop: **Goal-based** — puerta de hito.

### Fase 3 — Hito 3: ingesta de documentos propios

**H3.1 — Recepción de originales**
sha256 como identidad, deduplicación, `docs/originals/<sha256>.pdf` + sidecar JSON. El original no se toca jamás (invariante 4).
· Dep: H2.8 · CA: reimportar el mismo PDF no duplica; el hash del original es idéntico antes y después de todo el proceso (§10.3).
· Loop: Ninguno.

**H3.2 — Extracción y OCR opcional**
PyMuPDF para texto y bloques; página sin capa de texto → `ocrmypdf` si está, y si no, página marcada como no indexable y visible en el dashboard (trampa 11). La importación nunca aborta por esto.
· Dep: H3.1 · CA: un PDF escaneado sin OCR disponible se importa degradado y aparece señalado.
· Loop: Ninguno.

**H3.3 — Versión consultable con anclas deterministas**
HTML con `<span id="p:0007:03">` por bloque, numeración cero-rellenada, **estable entre reimportaciones del mismo fichero** (trampa 12).
· Dep: H3.2 · CA: reimportar el mismo PDF produce anclas idénticas byte a byte.
· Loop: **Turn-based** — test de determinismo de anclas repetible cada vez que se toque la extracción.

**H3.4 — Troceado**
300–500 tokens por fragmento, límites de párrafo, solapamiento de un párrafo, nunca a media frase (§10.1).
· Dep: H3.3 · CA: distribución de tamaños dentro del rango sobre un corpus de prueba; ningún corte a media frase.
· Loop: Ninguno.

**H3.5 — Cliente de embeddings**
`embed.py` contra `llama-server --embeddings` con `multilingual-e5-base`; prefijos `query: ` y `passage: ` obligatorios y centralizados en un único punto del código (trampa 2).
· Dep: F0.6 · CA: el servidor caído degrada (búsqueda por palabras sigue); dimensiones verificadas contra 768.
· Loop: Ninguno.

**H3.6 — Índice vectorial**
`vectors.py`: memmap int8 `(n_chunks, dims)` + `vectors.ids`, normalización L2 antes de cuantizar, escalas guardadas, búsqueda por producto matricial por lote y fusión entre lotes; nunca índice global (§7.4).
· Dep: H3.5 · CA: 500k fragmentos sintéticos responden < 200 ms; recall verificado contra búsqueda en float sobre una muestra.
· Loop: **Goal-based** — «< 200 ms sobre el corpus de referencia» es un umbral medible; candidato a `/goal` de rendimiento.

**H3.7 — Lotes sellados**
`shards.py`: directorio provisional, `text.sqlite` (§7.2, FTS5), `manifest.json` (§7.3), sellado (solo lectura, `journal_mode=DELETE`, hashes), validación al abrir, y detección de `embed_model`/`embed_dims` incompatibles → `incompatible`, excluido de semántica, reindexable (trampas 1 y 4).
· Dep: H3.4, H3.6 · CA: un lote con manifiesto alterado se marca `corrupt` y se excluye; un lote de otro modelo se marca `incompatible` pero sigue sirviendo FTS5.
· Loop: **Turn-based** — la validación manifiesto/modelo es LA comprobación crítica del sistema (§7.3); skill de verificación que se ejecuta al tocar `shards.py`, `embed.py` o `vectors.py`.

**H3.8 — Reanudabilidad de la ingesta**
`ingest_jobs` por documento; relanzar salta los `done`; el lote solo se sella cuando todos terminan; lotes provisionales visibles en diagnóstico (§10.2, §14).
· Dep: H3.7 · CA: importar 50 PDF, matar a mitad, relanzar: solo repite el documento en curso (§10.3).
· Loop: Ninguno (cubierto por la puerta del hito).

**H3.9 — Búsqueda semántica en la interfaz y trazabilidad completa**
Franja semántica aparte y etiquetada con el párrafo citado (§9.3); todo resultado propio lleva ancla, enlace al consultable posicionado y enlace al original en la página exacta con PDF.js vendorizado (§9.4); la selección de texto conserva su procedencia.
· Dep: H3.7, H2.4 · CA: un resultado semántico lleva al párrafo exacto y el original está a un clic (§10.3).
· Loop: Ninguno.

**H3.10 — Lote vendor indexado (cierra §8.3)**
`arca vendor` sella el lote `vendor/<fecha>` con el texto de la documentación extraído e indexado; entra **siempre** en el índice profundo, sin preguntar.
· Dep: H1.6, H3.7 · CA: una consulta semántica sobre documentación de una dependencia devuelve el párrafo correcto sin red.
· Loop: Ninguno.

**H3.11 — Alcance del índice profundo y dashboards de contenido**
`deep_index_scope` con su pantalla (12.5: fuera/completa/curada, fichero de títulos, estimación de coste antes de lanzar, explicación en pantalla), más dashboards 12.4 (documentos e importación) y 12.6 (modelos GGUF con metadatos leídos de cabecera, aviso de embeddings desalineados).
· Dep: H3.8, H3.9 · CA: todo lo listado en §12.4–12.6 operable; la estimación aparece antes de lanzar un indexado.
· Loop: Ninguno.

**H3.12 — Puerta del hito 3**
Ejecutar §10.3 completo, incluida la copia de la carpeta a otra máquina sin reconstruir nada; actualizar `docs/manual/`.
· Dep: H3.1–H3.11 · CA: los 5 criterios pasan.
· Loop: **Goal-based** — puerta de hito.

### Fase 4 — Hito 4: chat

**H4.1 — Gestión del subproceso llama-server**
`llama.py`: arrancar, parar, reiniciar, `/health`, log en vivo, línea de comandos efectiva visible (§13). Su ausencia degrada, nunca rompe (invariante 5).
· Dep: H3.12 · CA: apagar el binario a mitad de uso lo refleja la interfaz y la búsqueda sigue (§11.4).
· Loop: Ninguno.

**H4.2 — Perfiles: formulario desde `--help` y modo crudo**
Formulario generado parseando la salida real del binario (nunca lista incrustada, §13.1), capa curada por grupos, grupo «otras» para lo no reconocido, modo crudo que sustituye al formulario (§13.2), perfiles en `llama_profiles`.
· Dep: H4.1 · CA: actualizar el binario a otra versión regenera el formulario sin tocar código; una bandera nueva aparece en «otras».
· Loop: Ninguno.

**H4.3 — Arranque de prueba con reversión y validaciones previas**
La secuencia completa de §13.3 (candidato → health 60 s → generación de prueba → `last_good` o reversión automática con log del fallo) y las validaciones de §13.4 (contexto vs `n_ctx_train`, estimación de memoria, `-ngl` vs capas, binario sin GPU — trampas 8 y 9).
· Dep: H4.2 · CA: un perfil inválido revierte solo al último bueno y muestra el log; los cuatro avisos se disparan con casos provocados.
· Loop: **Turn-based** — batería de perfiles buenos/malos repetible al tocar `llama.py`.

**H4.4 — Motor de chat**
`chat.py`: estado de pantalla estructurado (§11.1), tope duro de 5 fragmentos / ~1500 tokens (§11.2), herramienta `search(query, scope)` con máximo 2 llamadas por turno y respuesta honesta al agotarlas.
· Dep: H4.1, H3.9 · CA: preguntar algo ausente del corpus produce «no lo tengo», no invención (§11.4).
· Loop: Ninguno.

**H4.5 — Citación y temas de riesgo**
Fragmentos etiquetados con procedencia, marcadores convertidos en enlaces al párrafo, regla de riesgo (citar o declarar que no hay fuente) reforzada en el prompt Y verificada en la interfaz con aviso visible (§11.3); dashboard 12.8 (topes, prompt editable, temas de riesgo, muestreo por petición).
· Dep: H4.4 · CA: una respuesta sin citas en tema de riesgo muestra el aviso; las citas llevan al párrafo correcto (§11.4).
· Loop: **Turn-based** — conjunto de preguntas de riesgo de referencia, repetible cuando cambie el prompt de sistema o el modelo.

**H4.6 — Puerta del hito 4**
Ejecutar §11.4 completo (incluido borrar el GGUF y comprobar que todo lo demás funciona); actualizar `docs/manual/`.
· Dep: H4.1–H4.5 · CA: los 4 criterios pasan.
· Loop: **Goal-based** — puerta de hito.

### Fase 5 — Cierre

**F5.1 — Dashboards restantes y mantenimiento**
Secciones 12.9 (búsqueda), 12.10 (vendorizado UI), 12.12 (mantenimiento: rutas, temporal, rotación, exportar/importar configuración, verificación integral) y 12.1 completa.
· Dep: H4.6 · CA: el botón de verificación integral recorre ZIM, manifiestos y `library.db` y reporta por elemento.
· Loop: Ninguno.

**F5.2 — Verificación integral final**
Ejecutar los 10 puntos de §18 como una sola sesión de aceptación, sobre biblioteca llena, vacía y a medias.
· Dep: F5.1 · CA: los 10 puntos de §18 son ciertos a la vez.
· Loop: **Goal-based** — la definición de «terminado» del proyecto.

### Fase 6 — Ampliaciones posteriores al cierre

Trabajo pedido después de que §18 se cumpliera entero. Cada tarea deja el sistema arrancable y
útil, como cualquier hito.

**F6.1 — Errores del panel con código estable y accionable** *(cerrada el 2026-08-29)*
Cada respuesta de error que el panel puede provocar lleva un `code`; el panel traduce
`familia.code` y cae al mensaje crudo si no existe la clave.
· Dep: F5.2 · CA: los endpoints devuelven el código, hay clave en `es` y `en` para cada uno, y el
juego de códigos de `api.py` coincide con el juego que tiene mensaje (verificado por test).

**F6.2 — Puesta en marcha automática del chat** *(cerrada el 2026-08-29)*
§13.0: el usuario solo deja el `.gguf` en `models/chat/`; ARCA resuelve la release de llama.cpp en
el momento, descarga el paquete por la cola verificada, lo despliega, crea el perfil de §2, lo
somete al arranque de prueba de §13.3 y fija el commit en `sources.lock`.
· Dep: F6.1 · CA: los candidatos se prueban GPU primero y CPU al final, el que falla se borra, la
puesta en marcha nunca bloquea el arranque y sin red lo dice sin romper nada.
· Loop: Ninguno.

**F6.4 — Importación desde el panel y camino de indexado** *(cerrada el 2026-08-29)*
§12.4 «Subida»: `POST /api/admin/documents/upload` (bytes en crudo, sin dependencia de *multipart*)
y `POST /api/admin/documents/index`, que es el **primer llamador en producción** de
`ingest_documents`. Con él, un documento importado por fin se indexa y se busca.
· Dep: F6.2 · CA: subir → indexar → el documento aparece en `/api/search`, verificado de punta a
punta y contra el servidor vivo.

**F6.3 — Deuda pendiente** *(parcial)*
Revisión de código de H2.2–H2.7: **hecha** el 2026-08-29, veredicto y hallazgo en
`docs/gates/review-h2.md`. Siguen pendientes, y dependen de hardware y del modelo descargado: la
prueba de citación en español con el binario y el modelo reales, y la medición de la recuperación
semántica con `multilingual-e5-base` real.
· Dep: F6.2 · CA: `docs/gates/milestone-4.md` repasado contra el modelo real.

### Fase 7 — Hallazgos de la revisión general

**F7.0 — Puesta en marcha del servidor de embeddings** *(cerrada el 2026-08-29)*
La misma queja que el motor del chat, sin resolver: ARCA solo gestionaba un `llama-server`. Ahora
levanta también el de embeddings con el mismo binario y el `.gguf` de `models/embed/`, probándolo
por sus dimensiones y no por una generación, que un modelo de embeddings no conversa. Desbloquea el
indexado de documentos, la franja semántica y la comprobación `embedding_server`.
· Dep: F6.4 · CA: con el `.gguf` en su carpeta, el panel declara los dos servidores por separado y
un modelo con otras dimensiones se rechaza con el número concreto.

Salen de `docs/gates/review-general.md`, que registra qué se verificó y qué quedó abierto. El orden
es el de esa revisión: primero lo que evita que vuelva a pasar lo de la fase 6.

**F7.1 — Prueba de costura: nada del producto sin llamador** *(cerrada el 2026-08-29)*
Verificación de que toda función pública del paquete la usa alguien fuera de `tests/`. Es la
comprobación que habría cazado el chat roto en el navegador y los documentos que nunca se
indexaban, los dos defectos más graves de la fase 6.
· Dep: F6.4 · CA: la comprobación corre en la puerta de calidad y las cuatro funciones huérfanas de
hoy (`vectors.merge_hits`, `search.unsearchable`, `ingest.readable_html`, `zim.read_info`) quedan
usadas o borradas, con su cobertura movida al camino vivo.

**F7.2 — Indexado y verificación integral en segundo plano** *(media)*
`POST /api/admin/documents/index` y `POST /api/admin/vendor/index` bloquean la petición minutos.
Pasarlos al patrón de `EngineBringup`: hilo demonio con estado consultable y progreso real.
· Dep: F7.1 · CA: la petición vuelve enseguida y el panel muestra documento *n* de *m*.

**F7.3 — Decidir dónde vive el lock que se escribe en ejecución** *(media)*
Fijar el commit de llama.cpp modifica el `sources.lock` versionado. Decidir entre mantenerlo (y
documentar que usar el producto ensucia el árbol) o separar lock de repositorio y lock de
biblioteca.
· Dep: — · CA: la decisión queda en §8.3 y el comportamiento la sigue.

**F7.4 — Agrupar el panel en tres familias** *(cerrada el 2026-08-29)*
Catorce pestañas en una barra que envuelve. Agrupar en Contenido / Modelo y chat / Sistema con la
misma maquinaria ARIA (U1 de la revisión).
· Dep: — · CA: la primera decisión del operador pasa de catorce opciones a tres, sin ocultar
ninguna sección y sin romper el patrón de teclado.

**F7.5 — Pulido de interfaz** *(parcial)*
Hechos: resultado por fichero al subir (U3), altura máxima del historial de chat (U4), estados de
color en la tarjeta del motor (U5) e indicación de carga en el visor (U6). Queda U2 (progreso real
en operaciones largas, que depende de F7.2) y U7 (foco al abrir el visor y texto de orden en las
tablas).
· Dep: F7.2 · CA: cada punto, verificado a mano en las dos pantallas.

**F7.6 — `python -m arca`** *(baja)*
Añadir `__main__.py` para que la invocación obvia funcione.
· Dep: — · CA: `python -m arca --version` responde.

### Fase 8 — Chat libre con el modelo

Pantalla nueva entre Archivo y Administración: conversar con el modelo local sin el archivo de por
medio, con historial persistente y con imágenes adjuntas. Nace de una constatación del operador: el
asistente de §11 responde muy bien **sobre el archivo** y solo sobre él, porque está construido para
negarse sin evidencia. Esta fase no lo toca; añade un segundo cliente del mismo motor.

**Dos decisiones tomadas antes de empezar, y por qué:**

- **El almacén vive fuera de la raíz.** Un historial persistente escribe en cada mensaje, desde el
  camino de consulta, y el invariante 1 prohíbe escribir un byte bajo `<raiz>` — nombrando el chat
  expresamente. Pero el propio §6 ya dice dónde va ese estado: «memoria o el temporal del sistema
  operativo, **configurable**, nunca a la raíz». El almacén va entonces a `session_dir`, que ya
  existe (`arca/config.py:141`) y **ya rechaza todo candidato que resuelva dentro de la raíz**,
  cayendo al temporal del sistema. No hay excepción nueva que sancionar en §6: la fase usa la que ya
  está escrita. `library.db` no se abre desde esta pantalla, ni siquiera para leer ajustes.
- **Solo imágenes, ningún otro fichero.** El modelo no lee ficheros: un PDF habría que extraerlo a
  texto e inyectarlo en el prompt, que es otra funcionalidad, con otro nombre y otros fallos. Las
  imágenes sí viajan al modelo tal cual cuando hay `--mmproj`. Un clip que aceptara un `.docx` y no
  hiciera nada con él sería peor que no tenerlo.

**F8.1 — Almacén de conversaciones en `session_dir`** *(cerrada el 2026-08-30)*
`arca/history.py`: `talk.sqlite` bajo `session_dir(root, config)`, con su propio `user_version`,
independiente del `SCHEMA_VERSION` de `library.db`. Tres tablas —`conversations`, `messages`,
`attachments`— y las imágenes como ficheros bajo `<session_dir>/talk/<conversación>/`, no como BLOB:
así el borrado se verifica mirando el disco. Si el directorio no es escribible, la pantalla degrada
a sesión en memoria y lo declara, como cualquier otro subsistema ausente (invariante 5).
· Dep: F6.2 · CA: crear conversación → mensaje → imagen → borrar no deja ni fila ni fichero, y un
test afirma que la ruta resuelta del almacén nunca cae dentro de la raíz, ni con `session_dir`
apuntando a ella a propósito.
· Loop: Ninguno.

**F8.2 — Contenido multimodal en el transporte** *(cerrada el 2026-08-30)*
`chat_completion` tipa hoy los mensajes como `Mapping[str, str]` (`arca/llama.py:1318`) y el
contenido multimodal de la API es una **lista** de partes. Ensanchar la firma sin cambiar el
comportamiento del asistente del archivo, y añadir la detección de capacidad: hay visión si el
perfil activo lleva `--mmproj` —ya está entre los flags curados, `arca/llama.py:173`— y el fichero
existe; si no, no la hay y se dice.
· Dep: F8.1 · CA: `tests/test_chat.py` pasa sin tocarse, un perfil sin `--mmproj` declara que no hay
visión, y un mensaje con imagen se serializa en partes con la imagen en `data:`.
· Loop: Ninguno.

**F8.3 — Motor de chat libre** *(cerrada el 2026-08-30)*
`arca/talk.py`: llama a `chat_completion` y nada más. Sin evidencia, sin marcadores, sin
`STATE_UNGROUNDED` — el `ChatEngine` de `arca/chat.py` no se reutiliza ni se parametriza, porque su
trabajo es justo el contrario y doblarlo lo estropearía en el suyo. Ajustes propios (prompt de
sistema y muestreo) en `talk.sqlite`, no en `settings` de `library.db`, para no abrirla en escritura
desde la consulta. Recorte de ventana con `estimate_tokens`: se descartan los mensajes más viejos,
nunca el prompt de sistema.
· Dep: F8.2 · CA: una conversación de 60 mensajes se mantiene bajo el presupuesto de contexto
descartando por el principio, y con el servidor caído responde «no disponible» sin perder ningún
mensaje ya guardado.
· Loop: Ninguno.

**F8.4 — Endpoints y CRUD** *(cerrada el 2026-08-30)*
Listar, crear, abrir, renombrar y borrar conversaciones; añadir mensaje; adjuntar imagen (bytes en
crudo, como `POST /api/admin/documents/upload` en `arca/api.py:412`, sin dependencia de
*multipart*); y un borrado total que se lleve conversaciones, mensajes e imágenes. Las imágenes se
validan por número mágico y no por extensión, con lista blanca (PNG, JPEG, WebP) y tope de tamaño.
Códigos de error estables de familia `talk.`, según F6.1.
· Dep: F8.3 · CA: borrar una conversación deja inexistente el directorio de sus imágenes, un fichero
que no es imagen se rechaza con código aunque se llame `.png`, y toda función pública nueva tiene
llamador fuera de `tests/` (`tests/test_seams.py`).
· Loop: Ninguno.

**F8.5 — Pantalla `/talk`** *(cerrada el 2026-08-30)*
Tercer enlace del `seg` de navegación, entre Archivo y Administración, coherente en las tres
plantillas. `templates/talk.html` + `static/talk.js`, con la maquinaria de siempre: sin framework,
iconos clonados de `#icon-set`, sin `innerHTML`, sin una sola llamada de red fuera del origen.
Prefijo i18n `talk.` —no colisiona con el `chat.` del asistente del archivo, que ya tiene diez
claves— con los catálogos `es` y `en` completos. Dos columnas: lista de conversaciones e hilo.
Ningún color nuevo: solo los tokens de §16. El botón de imagen no existe si el modelo no tiene
visión.
· Dep: F8.4 · CA: la navegación es coherente en las tres pantallas, la pantalla sigue siendo
navegable con el motor caído, `tests/test_frontend.py` pasa incluido su veto de red, y
`docs/manual/` documenta la pantalla tal como quedó.
· Loop: Ninguno.

**F8.6 — El invariante 1, con la pantalla puesta** *(cerrada el 2026-08-30)*
Extender `_query_session` de `tests/test_invariants.py` para que barra `/talk` y una conversación
completa —crear, mensaje, imagen, borrar— y no solo la portada y el visor. Ejecutar la skill
`verify-no-write` como puerta de la fase.
· Dep: F8.5 · CA: los ocho tests actuales más el nuevo pasan a la vez, y la raíz queda idéntica
salvo los dos sidecars sancionados.
· Loop: **Turn-based** — la skill `verify-no-write`, que existe exactamente para esto.

**F8.7 — Respuesta token a token** *(cerrada el 2026-08-30)*
La llamada actual es bloqueante (`stream=False`, `arca/llama.py:1324`). En local, esperar en blanco
a que llegue la respuesta entera es la diferencia entre parecer un chat y no parecerlo. SSE en el
endpoint y consumo incremental en `talk.js`, con vuelta al modo bloqueante si el servidor no lo
soporta. Va la última porque F8.1–F8.6 ya entregan la pantalla usable, y es la tarea con más riesgo
de la fase.
· Dep: F8.6 · CA: el texto aparece a medida que se genera, cancelar la petición detiene la
generación en el servidor, y sin *streaming* disponible la pantalla sigue respondiendo.
· Loop: Ninguno.

### Fase 9 — ARCA Music

Capa opcional de música procedural, especificada en el **anexo A** de `ARCA-especificacion.md`. Va en
rama aparte (`music`) y se integra al empezarla, para que un experimento sonoro no pueda tocar un
sistema que ya funciona.

**Tres decisiones tomadas antes de empezar, y por qué:**

- **Muestreador, no banco de osciladores.** El listón es la música dinámica de Left 4 Dead, que está
  compuesta y grabada: lo procedural allí es el *Director*, no el sonido. La calidad viene del
  material. Un sintetizador en tiempo real escrito desde cero suena a politono lleve la teoría
  musical que lleve. El material se prepara antes, y en reproducción solo se disparan muestras.
- **Dos orígenes de material, un solo formato.** El renderizador propio es el suelo: sin red, sin
  licencias, siempre disponible, y hace bien lo percutido, lo metálico, los drones y las texturas.
  Los paquetes de muestras libres —VSCO 2 Community Edition y VCSL son CC0; hay pianos e impulsos de
  salas reales bajo CC-BY— son el techo, y traen lo que ninguna síntesis barata imita: cuerda y metal
  grabados. Se descargan desde el panel con el motor de descarga que ya existe, o se importan de una
  carpeta si no hay red. El motor no distingue el origen porque ambos terminan en el mismo manifiesto.
- **Cero Python en el camino del audio.** Generar PCM en el servidor obliga a un *stream* HTTP, un
  búfer circular y contrapresión, quemando CPU justo cuando llama.cpp ocupa la máquina — contra el
  principio de que Music nunca ralentiza ARCA. Web Audio hace el trabajo en su propio hilo de render.
  Python solo aparece al preparar el material, nunca al reproducirlo.

**La fase puede terminar en F9.0.** Es su rasgo más importante: la primera tarea es una escucha, y si
lo que sale no suena a música, F9 se cierra ahí y no se gasta una línea más. Todo lo demás depende de
esa puerta.

**F9.0 — Rodaja vertical y prueba de escucha** *(pendiente, es una puerta)*
El camino más fino de extremo a extremo, por los dos orígenes de material: un renderizador mínimo
—campanas por síntesis modal, un drone— y un puñado de muestras CC0 traídas a mano, disparadas desde
una página suelta con reverberación por convolución y una progresión sencilla. Sin perfiles, sin
estilos, sin interfaz, sin i18n. Cinco minutos de una sola señal (octubre, ambiental) en tres
intensidades, en las dos versiones. No se juzga con tests: se juzga escuchando.
· Dep: ninguna · CA: el operador escucha las dos y decide. Si suena a biblioteca de producción, la
fase continúa y queda fijado qué origen manda; si suena a politono con los dos, **F9 se cierra como
no viable** y se documenta por qué. Sin clics audibles y sin cortes en esos cinco minutos.
· Loop: Ninguno.

**F9.1 — Manifiesto y renderizador de material** *(pendiente)*
`arca/music/manifest.py` define el formato —instrumento, nota, capa de dinámica, `sha256`, origen,
licencia y `material_version`— y es el único contrato que el navegador conoce. `arca/music/render.py`
y `arca music render` lo llenan sintetizando: modal para lo percutido y metálico, guía de onda para
lo pulsado, pads por filtrado y granulación de ruido, percusión como transitorio más cuerpo
resonante, e impulsos sintéticos. Salida a `<raiz>/music/material/<version>/` en WAV PCM mono.
Determinista y reanudable: al relanzarlo salta lo ya hecho y verifica su hash (invariantes 2 y 7).
`numpy` y `wave` de la estándar; ninguna dependencia nueva.
· Dep: F9.0 · CA: dos renderizados producen los mismos hashes, uno interrumpido a la mitad retoma sin
repetir lo hecho, un fichero corrupto se detecta al validar el manifiesto, y el conjunto completo
cabe en decenas de megas.
· Loop: Ninguno.

**F9.2 — Curador de material muestreado** *(pendiente)*
`arca/music/curate.py` y `arca music import <ruta>`: dado un paquete ya en disco, recorta el
subconjunto que hace falta —unos pocos instrumentos, unas pocas notas, dos dinámicas—, convierte a
mono, normaliza, recorta silencios y escribe el mismo manifiesto que el renderizador, **anotando
licencia y procedencia por fichero** y guardando el texto de la licencia junto al material. Un
paquete de gigas deja decenas de megas; el archivo original se descarta al terminar y su `sha256`
queda registrado para poder rehacerlo. Formatos que no sepamos leer con la estándar se rechazan
diciendo cuál es, en vez de añadir una dependencia a escondidas.
· Dep: F9.1 · CA: un paquete real deja un manifiesto válido con su licencia; un fichero ilegible se
salta y se declara sin abortar; una curación interrumpida retoma; el material curado y el sintetizado
son intercambiables para el motor.
· Loop: Ninguno.

**F9.3 — Descarga del material desde el panel** *(pendiente)*
Pestaña de material en administración: lista de paquetes conocidos con su licencia, su tamaño y su
estado, y un botón que encola la descarga en el motor que ya existe (`arca/fetch.py`), con pausa,
reanudación y verificación por hash como cualquier ZIM. Los paquetes **no se resuelven en el
momento** como el catálogo de Kiwix, porque no hay catálogo: se fijan por versión en `sources.lock`
con su `sha256` y su licencia, igual que llama.cpp y la documentación de §8.3, y `--verify` los
recomprueba sin red. Al terminar la descarga se encadena la curación de F9.2 sola. Sin red, la
pestaña lo dice y remite a `arca music import`.
· Dep: F9.2 · CA: descargar → curar → sonar sin tocar la línea de órdenes; matar el proceso a la
mitad y reanudar no vuelve a bajar lo bajado; un `sha256` que no cuadra marca el paquete inservible y
no lo cura; con la red caída, ni la pestaña ni el resto del panel se rompen; el enlace muerto de un
paquete se declara como tal y los demás siguen disponibles.
· Loop: Ninguno.

**F9.4 — Perfiles, estilos y validación** *(pendiente)*
Doce perfiles de mes y los estilos iniciales —ambiental, cinemático, electrónico— como JSON en
`arca/music/profiles/`, con anulación opcional del operador en `<raiz>/music/profiles/`. Esquema
estricto: identificadores y nunca texto visible, claves desconocidas ignoradas, valores fuera de
rango recortados, semillas como enteros. Un perfil que no valida se descarta y se declara; no rompe
la carga de los demás. Los otros tres estilos del anexo llegan después de escuchar los primeros.
· Dep: F9.0 · CA: un JSON hostil —clave inventada, tempo negativo, `scales` con un objeto dentro,
cadena donde va un número— deja el resto de perfiles cargados y no ejecuta nada; el escáner de i18n
no encuentra ni un nombre visible dentro de un perfil.
· Loop: Ninguno.

**F9.5 — Carga y verificación del material en el navegador** *(pendiente)*
`static/music/material.js`: lee el manifiesto, decodifica con `decodeAudioData` solo los instrumentos
del estilo activo, mantiene un presupuesto de memoria y descarta lo que sale de él al cambiar de
estilo. Sin material o con manifiesto ilegible, se declara el nivel 2 y no se intenta nada más.
· Dep: F9.1, F9.4 · CA: los ficheros de `static/music/` los ve el escáner de red y el del envoltorio
de `fetch` —los dos recorren `static/` en profundidad desde el 2026-08-29; antes se saltaban en
silencio todo lo que no estuviera en la raíz de `static/`—; la memoria decodificada se mantiene bajo
el presupuesto tras veinte cambios de estilo.
· Loop: Ninguno.

**F9.6 — Voces, buses y mezcla** *(pendiente)*
El muestreador: voces con capas de dinámica, alternancia de repeticiones y desafinación leve para que
dos notas iguales nunca suenen idénticas; bus por capa con su ganancia, filtro paso bajo por lejanía,
convolución compartida y limitador en el maestro. Todo cambio de ganancia por rampa; ningún salto.
· Dep: F9.5 · CA: treinta minutos de reproducción continua sin clics audibles, con el número de nodos
del grafo estable al final —sin fugas— y sin efecto ametralladora en notas repetidas.
· Loop: Ninguno.

**F9.7 — Composición y semillas** *(pendiente)*
Generador con PRNG semillado propio: progresión armónica con conducción de voces, motivos, ritmo y
estructura local, todo derivado de `seed + perfil + estilo + generator_version`. Planificador con
anticipación contra el reloj del contexto de audio; no se genera nada con el reproductor parado.
· Dep: F9.6 · CA: la misma terna produce una lista de eventos idéntica —comparada serializada desde
Playwright, que ya es dependencia de desarrollo—, y cambiar el algoritmo sin subir
`generator_version` hace fallar ese test.
· Loop: Ninguno.

**F9.8 — Director** *(pendiente)*
Intensidad de 0 a 100 % traducida a densidad de arreglo, no a volumen. Las transiciones —intensidad,
tema nuevo, cambio de estilo, cambio de mes— caen en el compás y nunca a mitad de frase. Mezcla de
perfiles por día del mes, calculada una vez al generar el tema. Mes actual desde el reloj local.
· Dep: F9.7 · CA: recorrer la intensidad de 0 a 100 y volver no produce ni un corte; toda transición
cae en el primer tiempo del compás; la mezcla entre meses es determinista dada la fecha.
· Loop: Ninguno.

**F9.9 — Reproductor en la interfaz** *(pendiente)*
Un botón de icono en la barra superior, junto a tema e idioma, y un panel con reproducción,
intensidad, volumen y las tres acciones. Iconos SVG en línea como el resto —nada de emojis—, tokens
de §16 y la franja de procedencia (`octubre · ambiental · seed 184729 · gen v1`) en monoespaciada.
Volumen, intensidad, estilo y modo de perfil en `localStorage`, cada acceso en `try`/`catch`. Nunca
arranca solo: la primera reproducción la pide el usuario. Si el material lleva licencia con
atribución, el panel la muestra.
· Dep: F9.8 · CA: `tests/test_i18n.py` pasa con los dos catálogos completos y sin una sola cadena
literal; foco de teclado visible en todos los controles; movimiento reducido respetado; recargar la
página recuerda los ajustes y **no** suena hasta que alguien pulsa.
· Loop: Ninguno.

**F9.10 — Degradación y aislamiento** *(pendiente)*
Los tres niveles del anexo A.6, con cada punto de entrada envuelto: material ausente, Web Audio no
disponible, sin dispositivo de audio, autoarranque bloqueado, perfil inexistente, excepción del
generador. Ninguno enseña una traza; todos degradan y lo declaran.
· Dep: F9.9 · CA: una excepción provocada a propósito en el generador degrada al nivel 2 sin tocar
búsqueda, lectura ni chat; borrar `arca/static/music/` deja la portada funcionando y **la suite
entera en verde**.
· Loop: Ninguno.

**F9.11 — Puerta de la fase: no escribe, no llama a nadie, y está documentada** *(pendiente)*
Verificación de que Music no rompe ningún invariante: ejercitar una sesión de consulta con la música
sonando y comprobar que bajo la raíz no cambia un byte, que no sale una sola petición fuera del
origen **en tiempo de consulta** —la descarga de material es administración, no consulta—, y que sin
red el sistema entero se comporta igual. Página `docs/manual/music.md` con lo que hay de verdad: el
nivel en que está el sistema, de dónde salió el material con su licencia, y cómo borrar la capa.
· Dep: F9.10 · CA: la skill `verify-no-write` pasa con el reproductor activo; el test de red no
encuentra ni un `http` en los fuentes del frontend de Music; el manual describe el estado real, no el
planificado.
· Loop: **Turn-based** — `verify-no-write`, otra vez la más importante.

### Fase 10 — Libros y otros formatos por el camino de los documentos

ARCA ya ingiere PDF. Un libro no es distinto salvo en dos cosas: el contenedor, y que no tiene
páginas. PyMuPDF —ya vendorizado, sin dependencia nueva— abre EPUB, FB2, TXT, SVG, CBZ e imágenes
con la misma API que el PDF; está medido contra el 1.28.2 instalado. Lo que no viene gratis es el
ancla, y esta fase entera gira alrededor de eso.

Lo medido, con muestras reales: **traen texto** el PDF, el EPUB, el FB2, el TXT y el SVG; **abren y
no traen ni un carácter** el CBZ, el PNG, el BMP y el PPM. Son imágenes, y sin OCR no hay nada que
indexar. Sin verificar aquí, documentados por PyMuPDF: XPS/OXPS, MOBI/AZW3, JPEG, TIFF, GIF, JP2 y
PSD.

**Tres decisiones tomadas antes de empezar, y por qué:**

- **No se convierte nada a ZIM.** `libzim` trae API de escritura, así que se podría; sería un error.
  La búsqueda semántica corre solo sobre lotes sellados (`arca/search.py:121`), nunca sobre ZIM:
  convertir un libro lo dejaría navegable pero **no citable al párrafo**, que es justo lo que un
  libro aporta. Y un ZIM es inmutable — uno por libro son miles de filas en `library.db`; uno para
  todos obliga a reescribirlo entero para añadir el siguiente. El lote sellado ya resuelve
  exactamente eso. **El estándar del proyecto es el lote, no el contenedor**, y lo que se
  estandariza es el extractor.
- **El layout se fija y se graba en el lote.** Un EPUB es reflowable: sus páginas se las inventa el
  maquetador con el ancho, alto y cuerpo que le des. Se fijan esos tres valores, se guardan en el
  lote, y el ancla sigue siendo `p:PPPP:OO` — ni el visor, ni el chat, ni los lotes ya sellados se
  enteran. La alternativa (ancla por ítem del *spine*) es más correcta y cambia el formato de ancla;
  ese precio no se paga hasta que F10.0 diga que hace falta.
- **Una extensión entra en la lista blanca solo con un test que la ingiere de verdad.** «PyMuPDF lo
  abre» no es criterio: el CBZ abre y da cero bloques. Aceptar un fichero e indexarlo vacío deja un
  documento fantasma en la biblioteca que no encuentra nada nunca, y eso es peor que rechazarlo.
  Es la trampa 11 de §17 generalizada más allá del PDF.

**Lo que la fase dejó escrito, y no se vuelve a discutir:**

- **El ancla por página vale para los reflowables** con el maquetado fijado en `595x842@11`. Medido
  con dos pases en procesos distintos sobre copias renombradas: `docs/gates/f10-anchors.md`. No se
  pasa al ancla por ítem del *spine*.
- **`.azw3` se queda fuera.** PyMuPDF lo documenta, pero no se ha podido construir un fichero AZW3
  real con el que probarlo, y la regla de la fase es que sin test que lo ingiera de verdad no entra.
  Se rechaza con el código `unverified_format`, que dice exactamente eso. Lo mismo para `.tif`,
  `.tiff`, `.gif`, `.bmp`, `.jp2` y `.psd`.
- **`.mobi` sí entró**, con un fichero MOBI real generado en la propia suite (cabecera PalmDB +
  cabecera MOBI de 232 bytes), no con un PalmDOC de mentira.
- **Deuda que deja la fase, revisada y aceptada:** el detalle que compone `layout_drift`
  (`arca/shards.py`) va en inglés fijo dentro de un mensaje traducido, igual que ya hacía
  `state.reason`. No es regresión de esta fase, pero perpetúa la excepción a «ninguna cadena de
  usuario hardcodeada»; se arregla cuando se traduzcan los `reason` de los lotes, todos a la vez.
- **Las imágenes entran solo con `ocrmypdf` presente.** El binario no está instalado en la máquina
  de desarrollo, así que el camino real de OCR está probado con un ejecutor inyectado —igual que ya
  lo estaba el OCR de PDF desde el hito 3—, y lo que sí está probado de verdad es el renderizado de
  la página, el borrado del temporal y el rechazo con `no_ocr_binary` cuando falta el binario.

**F10.0 — Puerta: ¿es determinista el ancla?** *(cerrada el 2026-08-30, en verde)*
Ingerir el mismo EPUB dos veces con el layout fijado y comparar bloque a bloque y ancla a ancla. Un
script y una comparación de hashes: sin panel, sin interfaz, sin i18n. Se repite con FB2 y TXT, que
son reflowables igual.
· Dep: ninguna · CA: los dos pases producen los mismos bloques con las mismas anclas en los tres
formatos. Si no coinciden, la fase **cambia al ancla por ítem del spine antes de escribir una línea
más**, y se documenta qué cuesta eso sobre los lotes ya sellados. Sin esta puerta no se sigue.
· Loop: **Turn-based** — `verify-anchors`, que existe para esto (trampa 12 de §17).

**F10.1 — El extractor deja de ser el PDF** *(cerrada el 2026-08-30)*
Separar de `arca/ingest.py` lo que asume PDF, dejando un extractor con implementaciones por familia.
La de PDF es la de hoy, sin un solo cambio de comportamiento. Entra con ella el XPS/OXPS, que es de
paginación fija y no depende de F10.0. De las anclas en adelante —troceado, embeddings, sellado— no
cambia nada.
· Dep: F10.0 · CA: `tests/test_documents.py` pasa **sin tocarse**, `arca verify` sigue verde sobre
lotes creados antes del cambio, y un XPS ingerido aparece en `/api/search` con su párrafo.
· Loop: Ninguno.

**F10.2 — Los formatos reflowables** *(cerrada el 2026-08-30)*
EPUB, MOBI/AZW3, FB2 y TXT por el extractor reflowable, con el layout fijado y grabado. El TXT es el
que más se va a usar y el más barato: notas, volcados, transcripciones, subtítulos y código.
· Dep: F10.1 · CA: un EPUB real ingerido produce bloques con ancla, aparece en `/api/search` y el
chat puede citarlo al párrafo; reingerir el mismo fichero da las mismas anclas; el layout usado
queda registrado en el lote y `arca verify` lo comprueba.
· Loop: Ninguno.

**F10.3 — El original deja de llamarse `.pdf`** *(cerrada el 2026-08-30)*
Hoy el original se guarda como `<sha256>.pdf` y `/api/doc/{id}/original` devuelve
`application/pdf` a pelo (`arca/api.py:423`). Pasa a guardarse con su extensión real, con su tipo
registrado en el lote y servido con ese tipo. `render_readable` ya genera el HTML de lectura desde
los bloques, así que «leer» sale gratis; «ver el original» **no**, porque PDF.js no abre un EPUB: se
hace lo aburrido, se descarga el fichero intacto, y no se finge un visor que no existe.
· Dep: F10.2 · CA: buscar → párrafo citado → «leer» muestra el texto con sus anclas → «original»
devuelve el fichero con su tipo real y el mismo `sha256` con el que se ingirió; el invariante 4 (el
original nunca se toca) sigue verificado para todos los formatos.
· Loop: Ninguno.

**F10.4 — Lista blanca, `arca import` y el panel** *(cerrada el 2026-08-30)*
Extender la ingesta por CLI y por panel a las extensiones que el extractor sepa **y que tengan
test**. Un formato que no sepamos leer se rechaza **diciendo cuál es y por qué**, con código estable
de la familia de F6.1: un CBZ o un PNG contestan que son imágenes y que hacen falta OCR, no un
genérico «formato no soportado». Nada entra en la lista blanca sin su fichero de prueba.
· Dep: F10.3 · CA: subir un `.epub` o un `.txt` por el panel lo indexa; subir un `.cbz` lo rechaza
con código y mensaje accionable **en los dos catálogos**; un test recorre la lista blanca entera e
ingiere un fichero real de cada extensión declarada, comprobando que ninguna produce cero bloques.
· Loop: Ninguno.

**F10.5 — Manual** *(cerrada el 2026-08-30)*
`docs/manual/documents.md` cuenta qué formatos entran hoy y cuáles no, por qué el ancla sigue siendo
por página, qué layout se fijó y qué obliga a re-sellar si se cambia.
· Dep: F10.4 · CA: la página describe el estado real, no el planificado, y la tabla de formatos
coincide con la lista blanca que ejercita el test de F10.4.
· Loop: Ninguno.

**F10.6 — OCR de imágenes y cómics** *(cerrada el 2026-08-30)*
El OCR de hoy es PDF-puro: `ocrmypdf --force-ocr --pages N <original>` (`arca/ingest.py:132`), que
recibe el fichero original y un número de página. Para que CBZ, PNG, JPEG y TIFF dejen de ser
documentos vacíos hay que renderizar la página a imagen y alimentar con eso al OCR que ya existe.
Depende de un binario externo, así que degrada si no está, como hoy.
· Dep: F10.5 · CA: un CBZ escaneado produce bloques con ancla; sin `ocrmypdf` el formato se rechaza
diciendo exactamente que falta el binario; la ausencia del binario no rompe ninguna otra ingesta.
· Loop: Ninguno.

**F10.7 — El idioma elegido sobrevive a la navegación** *(cerrada el 2026-08-30)*
Hoy el idioma vive **solo en la URL**: el selector es un `<form method="get">` que añade `?lang=` a
la página actual, y todos los enlaces de navegación (`href="/"`, `href="/talk"`, `href="/admin"`)
son rutas desnudas, así que `_catalog_for(config, lang)` cae al idioma por defecto en cuanto se
pulsa cualquiera. No es que el idioma se pierda: nunca llegó a guardarse. Lo mismo afecta a las
llamadas de `/api/*`, que en su mayoría no envían `lang` y devuelven sus mensajes en el idioma por
defecto aunque la pantalla esté en inglés.

Se resuelve guardando la elección en una cookie que escribe el selector y lee `_catalog_for`: es lo
más aburrido que cumple, sobrevive a escribir una URL a mano, funciona sin JavaScript y cubre de una
vez las páginas y la API. La alternativa —arrastrar `?lang=` en cada enlace interno— deja de
funcionar en cuanto alguien teclea una dirección, y la de `localStorage` obliga a una redirección
con parpadeo. Esta tarea no tiene nada que ver con los formatos: vive aquí por decisión del
operador, no por afinidad temática.
· Dep: ninguna · CA: elegir inglés y navegar por las tres pantallas mantiene el inglés, incluidos
los mensajes de error de `/api/*`; recargar y escribir una URL a mano lo mantienen; sin JavaScript
el selector sigue funcionando; una prueba de navegador real recorre las tres pantallas comprobando
`<html lang>` y un texto de cada catálogo.
· Loop: Ninguno.

### Fase 11 — Catálogos remotos: fuentes, en vez de una fuente

`arca/catalog.py` habla OPDS por dentro —`_parse_entry` es Atom genérico— pero por fuera está casado
con Kiwix: `articleCount`, `flavour`, `_ftindex:yes`, la resolución `.meta4` y `safe_filename()`
quitando el sufijo. La cola de descarga, en cambio, **ya es genérica**: `enqueue()` (`arca/fetch.py`)
toma `kind`, `dest_path`, `sha256` y espejos, y ya conviven cuatro tipos (`zim`, `model`, `vendor`,
`git`). Esta fase no añade un catálogo: convierte «el catálogo» en «las fuentes».

**Tres decisiones tomadas antes de empezar, y por qué:**

- **Ninguna fuente vive en el código.** Standard Ebooks, Gutenberg, OpenStax o la que sea son
  configuración, no constantes. Un producto que lleva dentro una lista de dominios envejece mal, y
  los que le interesan a un operador no son los que le interesan a otro. `CATALOG_BASE_URL` deja de
  ser una constante y pasa a ser un campo de una fuente.
- **El hash se exige, o se declara que no lo hay.** Kiwix da metalink con `sha256`; otras fuentes no
  dan nada. La cola ya sabe verificar: una descarga sin hash se marca **no verificada** y se dice en
  la interfaz, en vez de fingir una garantía que no existe.
- **Sin torrents.** Decidido el 2026-08-30: no se desarrolla. Se pierde con ello la única vía
  estable y verificable hacia las colecciones que solo se publican así; queda escrito para que no se
  vuelva a discutir por olvido, sino por decisión.

**F11.1 — Partir el catálogo en núcleo OPDS y sabor Kiwix** *(pendiente)*
Refactor puro: el Atom genérico por un lado, lo que es de Kiwix —`articleCount`, `flavour`, `tags`,
`_ftindex:yes`, `.meta4`, la URL del lector— por otro. Sin comportamiento nuevo.
· Dep: ninguna · CA: `tests/test_catalog.py` pasa **sin tocarse** y el panel se comporta igual.
· Loop: Ninguno.

**F11.2 — Fuentes declaradas y su estado** *(pendiente)*
Una fuente es nombre, tipo (`opds`, `json`, `manual`), URL base y cómo mapear a `CatalogEntry`.
Viven en `arca.json` y se editan desde el panel. Cada una tiene estado propio: alcanzable, caída, o
sin verificación de hash.
· Dep: F11.1 · CA: añadir una fuente OPDS por configuración la hace buscable **sin tocar una línea
de código**; una fuente caída se declara y **no tumba la búsqueda en las demás**; el estado de cada
fuente aparece en el diagnóstico.
· Loop: Ninguno.

**F11.3 — Sabor de libros y `kind="book"`** *(pendiente)*
El adaptador OPDS de libros y el quinto tipo de la cola, con destino `<raiz>/books/`. Descarga
reanudable, con espejos y verificada por el motor que ya existe. Encadena con la ingesta de la fase
10 cuando el operador lo pide, nunca solo.
· Dep: F11.2, F10.4 · CA: encolar un libro desde el panel lo descarga con pausa y reanudación como
cualquier ZIM; pedir «descargar e indexar» lo deja buscable al párrafo; una descarga sin hash queda
marcada como no verificada.
· Loop: Ninguno.

**F11.4 — Fuente manual** *(pendiente)*
Pegar una URL directa, con su nombre y su hash si se conoce. Cubre cualquier sitio que no publique
catálogo, sin que el repositorio tenga que conocerlo ni versionar sus dominios.
· Dep: F11.3 · CA: una URL pegada a mano entra en la cola y se comporta como cualquier otra
descarga; sin hash se descarga igual y se declara no verificada; una URL inválida se rechaza con
código antes de encolar nada.
· Loop: Ninguno.

**F11.5 — Buscador remoto con selector de fuente** *(pendiente)*
Una pestaña, varias fuentes, y la procedencia visible en cada resultado. Los filtros que una fuente
no soporte se esconden para esa fuente en vez de fallar.
· Dep: F11.4 · CA: la navegación es coherente con el resto del panel, buscar en varias fuentes a la
vez intercala resultados con su origen a la vista, y `tests/test_frontend.py` pasa incluido su veto
de red.
· Loop: Ninguno.

**F11.6 — Manual** *(pendiente)*
`docs/manual/downloads.md` recoge el modelo de fuentes, cómo se declara una, qué significa «no
verificada» y por qué no hay torrents.
· Dep: F11.5 · CA: la página describe el estado real y un lector puede añadir una fuente siguiéndola.
· Loop: Ninguno.

### Fase 12 — Mapas

Un mapa consultable sin conexión, con notas y puntos guardados, y buscable por el modelo. Es la fase
más grande de las tres y la única que abre una pregunta de fondo antes de empezar: **¿un mapa es
conocimiento consultable sin conexión, o es una segunda aplicación dentro de ARCA?** Esa pregunta se
responde por escrito en F12.0, antes de una línea de código.

**Cuatro decisiones tomadas antes de empezar, y por qué:**

- **Los tiles son un fichero, no un servicio.** MBTiles es, por especificación, SQLite con una tabla
  `tiles(zoom_level, tile_column, tile_row, tile_data)`. Servirlo son veinte líneas de biblioteca
  estándar, que ARCA ya usa. Cero red en tiempo de consulta, cero dependencias nuevas.
- **Raster primero, vector después.** El vector pesa mucho menos en disco pero arrastra estilo,
  glifos, sprites y WebGL. Leaflet con raster de una región acotada entrega valor en una tarea;
  MapLibre es una fase por sí solo. Se empieza por lo aburrido y se sube si hace falta.
- **Las notas escriben, así que van al directorio de sesión.** Es el precedente ya sentado por la
  fase 8 (`session_dir/talk/`): `session_dir/maps/maps.sqlite`. No hay decisión nueva que tomar ni
  excepción nueva que sancionar en §6.
- **El modelo no mira el mapa: consulta un gazetteer.** Los tiles son imágenes y un LLM no busca en
  ellas. La búsqueda de lugar es **determinista** sobre SQLite —un volcado de topónimos con
  coordenadas, en CSV, sin dependencias— y el modelo consume el resultado como evidencia, igual que
  ya hace con la búsqueda del archivo. Nominatim queda descartado (arrastra PostgreSQL) y extraer
  POIs del PBF de OSM exigiría una dependencia nueva.

**F12.0 — Puerta: la decisión de arquitectura y un MBTiles real** *(pendiente, es una puerta)*
Dos cosas, en este orden. Primero, pasar la funcionalidad por
`docs/architecture/decision-criteria.md` y escribir la respuesta: si los mapas son parte del archivo
o una aplicación aparte, y qué implica cada respuesta. Segundo, abrir un MBTiles de verdad, servir
un tile, y **medir** cuánto ocupa una región útil a los niveles de zoom que hacen falta.
· Dep: ninguna · CA: la decisión está escrita y razonada; un tile real sale por HTTP desde el
fichero. Si un raster de una región que sirva no cabe en un presupuesto de disco razonable, la fase
**pasa a vector antes de escribir una línea de interfaz**; si tampoco cabe, **F12 se cierra como no
viable** y se documenta por qué.
· Loop: Ninguno.

**F12.1 — Servidor de tiles** *(pendiente)*
`arca/maps.py` y `GET /api/map/{mapa}/{z}/{x}/{y}`, solo lectura, con su comprobación en el panel de
diagnóstico y su degradación si el fichero falta o está corrupto.
· Dep: F12.0 · CA: servir mil tiles no escribe un byte bajo la raíz; un mapa ausente degrada con
mensaje accionable y sin traza; un fichero corrupto se detecta al abrirlo y se declara.
· Loop: Ninguno.

**F12.2 — Visor vendorizado** *(pendiente)*
La pantalla de mapas con su biblioteca vendorizada, sin CDN y sin una sola petición fuera del
origen. Los dos temas, i18n completo, iconos clonados como en el resto. Precedente: PDF.js.
· Dep: F12.1 · CA: `tests/test_frontend.py` pasa incluido su veto de red; la pantalla se ve y
funciona en los dos temas con un navegador real; la biblioteca queda en `sources.lock` con su
licencia.
· Loop: Ninguno.

**F12.3 — Notas y puntos** *(pendiente)*
Varios mapas, puntos con nota, en `session_dir/maps/maps.sqlite`, con la misma degradación a memoria
que el almacén de conversaciones si el directorio no es escribible.
· Dep: F12.2 · CA: crear un punto, editarlo y borrarlo no deja rastro bajo la raíz; con el
directorio de sesión no escribible la pantalla lo declara y sigue navegable; un test afirma que la
ruta del almacén nunca cae dentro de la raíz.
· Loop: **Turn-based** — `verify-no-write`.

**F12.4 — Gazetteer** *(pendiente)*
`arca import-places`: un volcado de topónimos a SQLite con FTS, con nombre, coordenadas, país y
población. Reanudable y verificado por hash, como cualquier otra ingesta.
· Dep: F12.3 · CA: una importación interrumpida a la mitad retoma sin duplicar filas; buscar un
topónimo devuelve coordenadas en milisegundos; el volcado original queda registrado por su `sha256`.
· Loop: Ninguno.

**F12.5 — Búsqueda de lugar, y el modelo** *(pendiente)*
La búsqueda determinista sobre el gazetteer, expuesta en la pantalla y disponible para el modelo
como evidencia: el asistente puede responder «está aquí» con coordenadas citables, o admitir que no
lo tiene, con el mismo suelo de honestidad que el chat del archivo.
· Dep: F12.4 · CA: el modelo cita coordenadas que salen del gazetteer y nunca inventa unas; sin
gazetteer importado lo dice en vez de responder de memoria; con el motor caído la búsqueda de lugar
sigue funcionando, porque es determinista.
· Loop: Ninguno.

**F12.6 — Descarga de mapas** *(pendiente)*
Los mapas por las fuentes de la fase 11, con `kind="map"` en la cola de siempre.
· Dep: F12.5, F11.4 · CA: encolar un mapa lo descarga con pausa, reanudación y verificación; al
terminar aparece en el visor sin reiniciar.
· Loop: Ninguno.

**F12.7 — Puerta de la fase: invariantes y manual** *(pendiente)*
Ejercitar una sesión de consulta con mapas abiertos, puntos creados y búsquedas de lugar, y
comprobar que bajo la raíz no cambia un byte y que no sale una petición fuera del origen.
`docs/manual/maps.md` con lo que hay de verdad.
· Dep: F12.6 · CA: la skill `verify-no-write` pasa con la pantalla de mapas ejercitada; el test de
red no encuentra un solo `http` en los fuentes del frontend de mapas; el manual describe el estado
real, no el planificado.
· Loop: **Turn-based** — `verify-no-write`, la de siempre.


---

## 7. Loops recomendados (resumen)

- **Skills de verificación a crear cuando toque** (turn-based): `verify-no-write` (H2.5, la más importante), `verify-resume` (H1.3), `verify-shard-integrity` (H3.7), `verify-anchors` (H3.3), `verify-frontend-floor` (H2.4), `verify-llama-profiles` (H4.3), `verify-risk-citations` (H4.5).
- **`/goal` en puertas de hito** (H1.9, H2.8, H3.12, H4.6, F5.2) y en umbrales medibles (H3.6 rendimiento, H1.6 deriva).
- **Hooks existentes** cubren el loop continuo de calidad (lint, tipos, tests, idioma, sin comentarios) desde F0.1; no se añaden loops recurrentes de tiempo: no hay procesos externos que vigilar durante el desarrollo.

## 8. Trampas de §17 → tarea responsable

| Trampa | Tarea |
|---|---|
| 1. Embeddings cambiados sin reindexar | H3.7 |
| 2. Prefijos E5 olvidados | H3.5 |
| 3. Mezclar scores Xapian/FTS5 | H2.3 |
| 4. WAL en soporte de consulta | F0.3, H3.7 |
| 5. URLs de descarga guardadas | H1.1 |
| 6. `.meta4` ausente | H1.2 |
| 7. Servidor que ignora `Range` | H1.3 |
| 8. Contexto > `n_ctx_train` | H4.3 |
| 9. Binario sin GPU con `-ngl` alto | H4.3 |
| 10. Reloj mal + nombres de lote | F0.5 (detección) + H3.7 (sufijo aleatorio) |
| 11. PDF sin texto en silencio | H3.2, F10.4 (generalizada a todo formato) |
| 12. Anclas que cambian al reimportar | H3.3, F10.0 (formatos reflowables) |

## 9. Riesgos y decisiones pendientes

**Riesgos:**

1. **`libzim` en Windows.** La máquina de desarrollo es Windows y las wheels de `libzim` han tenido soporte irregular fuera de Linux. Verificar la instalación real en F0.1; si no hay wheel viable, decidir entre desarrollar contra WSL o adelantar el plan B de `kiwix-serve` (§9.1). Es el riesgo técnico número uno porque bloquea el hito 2.
2. **Renderizado de ZIM concretos.** Algunos ZIM pueden renderizar mal con reescritura de enlaces propia; el plan B (`kiwix-serve` como proxy) está previsto en §9.1 y se decide por ZIM, no globalmente.
3. **Calidad del OCR opcional.** `ocrmypdf`/`tesseract` fuera del control del proyecto; mitigado porque su ausencia solo degrada (H3.2).
4. **Estimaciones de memoria de llama.cpp** (H4.3) son aproximadas por diseño; el arranque de prueba con reversión es la red de seguridad real.
5. **Anclas de formatos reflowables.** Un EPUB no tiene páginas propias: se las inventa el maquetador. La mitigación es fijar el layout y grabarlo en el lote, y la puerta F10.0 existe para comprobar que basta. Si no basta, cambia el formato de ancla y eso afecta a lotes ya sellados.
6. **Tamaño de los mapas sin medir.** Que MBTiles sea SQLite es especificación, pero no se ha abierto uno real ni se ha medido cuánto ocupa una región útil. Es el riesgo número uno de la fase 12 y por eso F12.0 es una puerta que puede cerrarla.
7. **Fuentes de catálogo que no cooperan.** Fuera de Kiwix casi nadie publica OPDS con hash. La mitigación es de diseño —fuentes declaradas en configuración, nunca en el código, y descargas marcadas como no verificadas cuando no hay hash— para que el producto no envejezca con los dominios.

**Decisiones pendientes (ninguna bloquea F0–F2):**

1. ~~**Modelo de chat definitivo:** evaluar Qwen3-14B vs Gemma-3-12B (§2).~~ **Decidido el 2026-08-28: Gemma 4 QAT**, densa de 12B o MoE de 26B-A4B según la VRAM. Queda por hacer la prueba de citación en español con el binario y el modelo reales.
2. **Contador de tokens para el troceado** (300–500): aproximación por palabras o tokenizador del modelo de embeddings vía llama.cpp — decidir en H3.4, eligiendo lo más aburrido que cumpla el rango.
3. **Ubicación de las escalas de cuantización:** manifiesto o tercer fichero (§7.4 admite ambas) — decidir en H3.6.
4. **Lista curada de artículos de Wikipedia** para el índice profundo: la aporta el operador; el sistema solo necesita aceptar el fichero de títulos (H3.11).

---

## 10. Tarea inicial

La ruta empieza en **F0.1 — Esqueleto del paquete y cadena de calidad**. No tiene dependencias y desbloquea todo lo demás (incluidos los hooks de calidad del repositorio, que hoy no pueden ejecutarse por falta de `.venv`). Solicita comenzar cuando quieras.
