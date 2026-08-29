# Interfaz web y API

Módulos: `arca/api.py`, `arca/templates/`, `arca/static/`, `arca/i18n.py`.

## Rutas

| Ruta | Qué sirve |
|---|---|
| `GET /` | Interfaz de consulta: buscador, resultados con sello de procedencia, visor y panel de asistente (ver `interface.md`). Con cero ZIM explica que no hay nada donde buscar y enlaza al catálogo (invariante 5) |
| `GET /admin` | Dashboard de administración (estado general, catálogo, descargas, contenido local, vendorizado, diagnóstico) |
| `GET /api/diagnostics` | Diagnóstico en JSON localizado |
| `GET /api/admin/status` | 12.1 (versión inicial): espacio libre y ocupado por categoría, contadores, peor estado del diagnóstico, últimos 20 eventos |
| `GET /api/admin/catalog?q=&lang=&category=&name=` | Búsqueda en vivo del catálogo OPDS; cada entrada marca `in_library` y `is_update` (versión nueva por `book_name`); sin red devuelve 502 con el error |
| `GET /api/admin/catalog/filters` | Categorías (`/catalog/v2/categories`) e idiomas con recuento de libros (`/catalog/v2/languages`), consultados en vivo — nunca listas incrustadas; sin red devuelve 502 |
| `POST /api/admin/downloads` (`{"uuid": …}`) | Resuelve la entrada + meta4, registra el ZIM, encola y despierta el worker |
| `GET /api/admin/downloads` | Cola completa con progreso, espejo en uso y último error |
| `POST /api/admin/downloads/{id}/{action}` | `pause` / `resume` / `cancel` / `retry` / `delete` (elimina la fila y su `.part`, nunca el fichero completado); acción desconocida → 400 |
| `GET /api/search?q=&scope=&mode=&limit=` | Búsqueda por palabras sobre las colecciones locales, con intercalado por rango y parte por fuente; la franja semántica llega vacía y declarada (ver `search.md`) |
| `GET /zim/{uuid}` · `GET /zim/{uuid}/{path}` | Artículo o recurso interno de una colección, con `<base>` inyectado para que los enlaces relativos no salgan de la aplicación (ver `zim.md`); sin `path`, la portada de la colección |
| `GET /api/doc/{doc_id}` | Versión consultable de un documento propio, con un ancla por bloque (ver `documents.md`); identificador que no sea sha256 → 404 |
| `GET /api/doc/{doc_id}/original` | El PDF original intacto, servido `inline` para PDF.js |
| `GET /api/admin/documents` | 12.4: documentos importados, lotes con su estado, cola de importación y estado del servidor de embeddings |
| `POST /api/admin/documents/upload?filename=` | 12.4: sube un PDF en crudo (bytes en el cuerpo, sin *multipart*), lo recibe por su sha256 y lo deja pendiente de indexar; no es un PDF o el nombre no es seguro → 400 con su código |
| `POST /api/admin/documents/index` | Indexa y sella los documentos recibidos que aún no tienen lote; sin servidor de embeddings lo declara y no toca nada (ver `documents.md`) |
| `GET /api/admin/scope` · `PUT /api/admin/scope` | 12.5: alcance del índice profundo por fuente (`out` / `full` / `curated`) con estimación de coste antes de lanzar |
| `GET /api/admin/models` | 12.6: GGUF presentes con arquitectura, cuantización y `n_ctx_train` leídos de la cabecera, más el aviso de lotes con embeddings desalineados |
| `GET /api/admin/zim` | Colecciones ZIM presentes en `zim/`, leídas del fichero: idioma, artículos, tamaño, modo de índice real y estado según `library.db`; los ficheros ilegibles salen aparte en `failures` (ver `zim.md`) |
| `POST /api/admin/zim/{uuid}/{action}` | `enable` / `disable`: mete o saca una colección de las búsquedas sin borrarla; acción desconocida → 400, colección desconocida → 404 (ver `zim.md`) |
| `POST /api/admin/verify` | Reverificación local de hashes (ver `downloads.md`) |
| `GET /api/admin/vendor` · `POST /api/admin/vendor/verify` | Estado del lock pieza a pieza + deriva de versiones, sin red |
| `POST /api/admin/vendor/download` | Encola las piezas del lock ausentes o no verificables (sin duplicar las ya activas) y despierta el worker; la descarga se sigue desde la pestaña Descargas |
| `POST /api/chat` | Mensaje + estado de pantalla → respuesta con citas, evidencia y aviso de riesgo (ver `chat.md`); sin modelo responde 200 declarando la degradación |
| `GET /api/admin/llama` | Estado del binario y del proceso, línea efectiva, log, perfiles y avisos previos (ver `llama.md`) |
| `GET /api/admin/llama/help` | Opciones del binario parseadas de su `--help` y agrupadas por la capa curada |
| `GET /api/admin/llama/profiles` · `PUT`/`DELETE /api/admin/llama/profiles/{name}` | Perfiles de arranque; nombre inválido → 400 |
| `POST /api/admin/llama/profiles/{name}/test` | Arranque de prueba con reversión automática (§13.3) |
| `POST /api/admin/llama/{start\|stop\|restart}` | Control del subproceso; sin binario o sin perfil → 409 |
| `GET /api/admin/settings` · `PUT /api/admin/settings` | Ajustes de chat (12.8) y de búsqueda (12.9), más las rutas en uso |
| `GET /api/admin/hub?q=&limit=` | 12.6: busca modelos GGUF en el hub configurado; sin `q` no hace ninguna llamada de red; sin red, 502 con el error (ver `models.md`) |
| `GET /api/admin/hub/{model_id}` | Variantes de un modelo con su cuantización, tamaño y hash; modelo con licencia por aceptar → 403 declarándolo |
| `POST /api/admin/hub/download` | Encola una cuantización concreta en la cola verificada, con destino `models/chat` o `models/embed` |
| `POST /api/admin/vendor/llama` | Fija el commit de llama.cpp en `sources.lock` y encola su tarball (código y documentación) |
| `GET /api/admin/engine` | §13.0: estado de la puesta en marcha automática del motor de chat (paso actual, paquete en curso, paquetes ya descartados, binario y perfil resultantes) |
| `POST /api/admin/engine/start` | Lanza esa puesta en marcha en segundo plano y devuelve el estado; nunca bloquea la petición (ver `engine.md`) |
| `GET /api/manual` · `GET /api/manual/{page}` | Índice del manual y página renderizada, servidos desde la propia aplicación (ver `manual-browser.md`) |
| `GET /api/admin/maintenance` | 12.12: rutas en uso, ficheros de log y política de rotación (ver `maintenance.md`) |
| `POST /api/admin/maintenance/verify` | Verificación integral: ZIM, manifiestos de lote, originales y consistencia de `library.db`, **elemento a elemento** |
| `GET /api/admin/maintenance/config` · `PUT` | Exportar e importar configuración; la importación aplica solo los ajustes, nunca las rutas |
| `POST /api/admin/vendor/index` | Regenera el lote de documentación vendorizada; sin servidor de embeddings lo dice y no hace nada |
| `GET /static/*` | CSS, JavaScript y tipografías WOFF2 vendorizados (cero CDN) |

`/docs`, `/redoc` y `/openapi.json` están desactivados: la API no expone esquemas.

## Errores con código

Cada respuesta de error que el panel puede provocar lleva, además del campo `error`
en inglés para el log, un campo `code` estable que nombra **la causa**, no el texto:

| `code` | Dónde sale | Estado |
|---|---|---|
| `unreachable` | Catálogo Kiwix y hub de modelos sin respuesta | 502 |
| `gated` | Modelo del hub con licencia por aceptar | 403 |
| `bad_model_id` · `bad_kind` · `bad_file` | Entrada forjada en la descarga del hub | 400 |
| `unknown_file` | La variante ya no está publicada | 404 |
| `no_binary` | No hay `llama-server` en ninguno de los tres sitios donde se busca | 409 |
| `no_profile` | No hay perfil de arranque configurado | 409 |
| `unknown_profile` | El perfil se borró desde otra sesión | 404 |
| `invalid_name` · `unknown_mode` | Nombre o modo de perfil no válidos | 400 |
| `bad_commit` | El commit de llama.cpp no es un SHA | 400 |

`fetchJson` conserva el código en el error que lanza y `errorText(familia, error)`
traduce la clave `familia.code` si existe en el catálogo activo; si no existe, cae al
mensaje crudo dentro del texto de la familia. Las claves de código son mensajes
completos: dicen qué pasó **y qué hacer** (§16), y ningún `{error}` en crudo se cuela
en ellas — lo verifica `tests/test_i18n.py`. El juego de códigos que emite `api.py` y
el juego que tiene mensaje se comparan en ese mismo test: añadir un código sin su
mensaje en `es` y en `en` rompe la suite.

## Dashboard (`/admin`)

`templates/admin.html` + `static/admin.js` (JavaScript sin framework, cero peticiones externas). El servidor inyecta las cadenas `admin.*` del catálogo activo como JSON embebido (`<script id="i18n-data">`); el JS las aplica a los elementos `data-i18n` y compone los textos dinámicos. Las catorce secciones — estado general (12.1 inicial, refresco cada 15 s), buscador del catálogo, cola de descargas con progreso, velocidad calculada en cliente, espejo en uso y acciones (sondeo cada 3 s) más reverificación local, contenido local (una fila por colección ZIM con su modo de índice real y su estado, más los ficheros que no se han podido abrir, con botón para volver a explorar la carpeta), vendorizado con verificación sin red y tabla de deriva, documentos (12.4), alcance del índice profundo (12.5), modelos GGUF (12.6), llama.cpp (§13 completa: estado del binario, control del proceso, línea efectiva, log en vivo y perfiles con su formulario generado), chat (12.8: topes, prompt de sistema editable, temas de riesgo y muestreo por petición), búsqueda (12.9: resultados por fuente, pesos de intercalado, longitud del extracto y troceado para futuras importaciones), mantenimiento (12.12: rutas, registro, verificación integral y configuración), manual (12.13), y diagnóstico (§12.11) con las dieciséis comprobaciones y su botón de recomprobar — se navegan en **dos niveles**: una barra de tres familias —**Contenido** (estado, catálogo, descargas, colecciones, documentos, alcance), **Modelo y chat** (modelos, llama.cpp, chat, búsqueda) y **Sistema** (dependencias, mantenimiento, diagnóstico, manual)— y debajo las pestañas de la familia elegida, con el patrón ARIA `tablist`/`tab`/`tabpanel` y sin depender de anclas de URL. Catorce pestañas en una sola fila habían dejado de ser un menú; con las familias la primera decisión son tres opciones. Las familias son botones con `aria-pressed`, y **sin JavaScript las secciones se muestran apiladas**, como antes. La cabecera lleva enlace al archivo, igual que la portada lo lleva a la administración. La lógica de pestañas vive en `static/tabs.js`. En la interfaz la sección de vendorizado se etiqueta «Dependencias» — el término técnico se reserva para código y documentación. El idioma se cambia con un combo en la cabecera (formulario GET con `?lang=`), presente también en la portada.

Capa visual (`static/style.css`): barra superior fija con desenfoque, tarjetas con radio y sombra suave, chips de estado en rejilla, tablas con filas alternas, hover, cabecera pegajosa y desplazamiento propio (`.table-scroll`, que evita romper texto en columnas estrechas), tipografías WOFF2 vendorizadas en `static/fonts/` (ver `interface.md`), y responsive hasta móvil con el resto del suelo de calidad de §16 (foco visible, movimiento reducido, contraste).

Buscador del catálogo: campo de texto con etiqueta, combos de idioma (con nombre, código y número de libros) y categoría poblados desde `/api/admin/catalog/filters` al cargar la página — si los feeds no responden, los combos quedan vacíos, se avisa y la búsqueda por texto sigue funcionando —, filtro de sabor construido en cliente a partir de los sabores presentes en los resultados, recuento «mostrados de totales», y tabla ordenable por cualquier columna (clic o teclado en la cabecera, con `aria-sort`; por defecto tamaño descendente; columnas numéricas alineadas a la derecha). La tabla vive en un contenedor con scroll y cabecera fija; cada fila conserva las insignias «en la biblioteca» / «versión nueva» y el botón de descarga. Las descargas encoladas desde el dashboard las atiende `QueueWorker` (ver `downloads.md`) en el propio proceso del servidor, que para eso es un proceso administrativo.

## Bilingüe

Toda ruta acepta `?lang=es|en`; sin él manda `language` de `arca.json`. Los textos salen de `arca/locale/{es,en}.json` (mismo juego de claves en ambos, verificado por test). El HTML declara `lang` y ofrece el cambio de idioma en la cabecera. Añadir una cadena de usuario = añadirla a los dos catálogos.

## Renderizado

Plantillas HTML con `string.Template` (biblioteca estándar, sin Jinja): sustitución de `$placeholders`, todo valor dinámico escapado con `html.escape` (el bloque JSON de i18n del dashboard se neutraliza vía `json.dumps` más el escape de `</`, y el JS solo usa `textContent`, nunca `innerHTML`). El CSS usa la paleta de §16 (base `#E7E9E4`, papel `#F1F2EF`, tinta `#17242B`, acento `#2F6F5E`, ámbar `#9A6B1F`, rojo `#8E2B22`), foco visible y `prefers-reduced-motion`. Las tres tipografías de §16 se sirven vendorizadas desde `static/fonts/`.

## Invariante 1

El proceso de servicio no escribe bajo la raíz. El contrato exacto y su
verificación viven en `invariants.md`; la suite es `tests/test_invariants.py` y
la skill `verify-no-write` la ejecuta tras cualquier cambio en el camino de
consulta.
