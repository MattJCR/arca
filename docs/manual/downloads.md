# Fuentes, catálogo y descargas

Módulos: `arca/opds.py` (núcleo Atom/OPDS genérico), `arca/catalog.py` (sabor Kiwix: `articleCount`, `flavour`, `_ftindex:yes`, resolución `.meta4`, URL del lector), `arca/sources.py` (el modelo de fuentes y sus adaptadores) y `arca/fetch.py` (motor de descarga, cola y verificación local).

## El modelo: fuentes, no «el catálogo»

**Ninguna fuente vive en el código.** Standard Ebooks, Gutenberg, OpenStax o la que le interese a un operador concreto son configuración, no constantes. Una fuente se declara en la lista `sources` de `arca.json` o desde la pestaña **Fuentes** del panel, y con eso ya es buscable: no hay una línea de código que conozca su dominio.

### Las que vienen declaradas

`arca init` escribe cuatro fuentes en `arca.json`. **Solo Kiwix viene activada**; las otras tres están declaradas y apagadas, para que se vean y se enciendan con un clic sin que el producto salga a sitios que nadie ha pedido. Una lista vacía o entera inválida vuelve a estas cuatro, para que un `arca.json` mal editado no deje el sistema sin poder descargar nada.

| Fuente | Tipo | Contenido | Activa | Qué hay que saber |
| --- | --- | --- | --- | --- |
| **Kiwix Library** (`kiwix`) | `opds`, sabor `kiwix` | `zim` | sí | la única que **verifica hash**, por su metalink |
| **Standard Ebooks** (`standard-ebooks`) | `opds` | `book` (EPUB) | no | el listado completo pide credenciales; **solo responde a búsquedas**, así que la fuente lleva `requires_query` |
| **Project Gutenberg** (`gutenberg`) | `opds` de **dos niveles** | `book` (EPUB) | no | ver abajo: la búsqueda solo devuelve punteros, y sus ficheros se llaman `1497.epub.images` |
| **OpenStax** (`openstax`) | `json` | `book` (PDF) | no | libros de texto completos; los PDF van de 20 MB a más de 150 MB |

Ninguna de las tres nuevas publica hash, así que lo que venga de ellas queda marcado **no verificado**. Que las cuatro contestan, y que de cada una se puede descargar un fichero real, lo comprueba `tests/test_live_sources.py`, que **solo se ejecuta con `ARCA_LIVE=1`** porque sale a la red de verdad:

```
ARCA_LIVE=1 pytest tests/test_live_sources.py
```

### Cómo se declara una desde el panel

`/admin` → familia **Contenido** → pestaña **Fuentes** → «Añadir una fuente». Solo el identificador es obligatorio, y la dirección base para todo lo que no sea `manual`. Al guardar se reescribe la clave `sources` de `arca.json` y la fuente aparece ya en las casillas del buscador. El botón **Comprobar fuentes** pregunta a cada una si responde.

El formulario no expone tres cosas, que se editan a mano en `arca.json` porque son de dialecto y no de uso diario: `flavour` (el sabor `kiwix`), `suffixes` (elegir el enlace por extensión en vez de por tipo) y `fields` (la correspondencia de campos de una fuente JSON).

Campos de una fuente:

| Campo | Para qué |
| --- | --- |
| `source_id` | identificador corto en minúsculas (`a-z0-9-_`, máximo 40). Es la clave: si se repite, gana la primera |
| `name` | lo que se ve en el panel y en la columna «Fuente» de los resultados |
| `kind` | `opds` (feed Atom), `json` (documento JSON) o `manual` (no se busca: solo recibe direcciones pegadas a mano) |
| `base_url` | dirección base. Obligatoria salvo para `manual` |
| `flavour` | `kiwix` para el sabor de Kiwix, `plain` para OPDS genérico. Una fuente `kiwix` es siempre de contenido `zim` |
| `item_kind` | `zim` (va a `<raíz>/zim/`) o `book` (va a `<raíz>/books/`) |
| `entries_path` | ruta del listado que se cuelga de `base_url` |
| `query_param` | nombre del parámetro de búsqueda por texto (por defecto `q`) |
| `language_param` | nombre del parámetro de idioma. Vacío significa que la fuente **no** filtra por idioma, y el filtro se esconde para ella |
| `media_type` | tipo de fichero del enlace de adquisición que hay que elegir (por ejemplo `application/epub+zip`) |
| `suffixes` | alternativa al anterior: elegir el enlace por extensión |
| `items_key` | fuentes `json`: clave del documento que contiene la lista |
| `fields` | fuentes `json`: correspondencia entre los nombres de la fuente y `id`, `title`, `summary`, `language`, `url`, `sha256`, `size`, `filename` |
| `requires_query` | la fuente **exige** su parámetro de búsqueda: se manda siempre, aunque vaya vacío. Sin esto, Standard Ebooks responde 401 al listado desnudo |
| `acquisition_via` | `entry` (por defecto): el enlace de descarga está en la propia entrada. `subsection`: la entrada solo trae un puntero a un segundo feed, que es donde vive el enlace |
| `item_pattern` | expresión regular que el `href` del enlace debe cumplir para que la entrada cuente como ítem. Sirve para separar los libros de las entradas de navegación cuando el feed no las distingue de ninguna otra forma. Una expresión que no compila invalida la fuente |
| `enabled` | una fuente desactivada no se busca ni se comprueba, pero se conserva declarada |

Una declaración incompleta o con un `kind` desconocido **se descarta al cargar** en vez de romper el arranque (invariante 5). Desde el panel, en cambio, se rechaza con código `bad_source`: allí hay alguien mirando a quien se le puede decir qué falta.

## Estado de una fuente

Tres estados, y ninguno tumba a los demás:

- **`ok`** — respondió la última vez que se le preguntó **y** publica hash, así que lo que venga de ella se verifica.
- **`unverified`** — respondió, pero no publica hash. La descarga se hace igual y queda **marcada como no verificada**: se prefiere decirlo a fingir una garantía que no existe. Se guarda el sha-256 calculado al terminar, que sirve para reverificar en local aunque no diga nada del origen.
- **`down`** — no respondió; el detalle del error se conserva y se enseña.
- **`unknown`** — todavía no se le ha preguntado en esta sesión.

El estado observado **vive en memoria** (`SourceRegistry`), no en disco: se actualiza cuando se busca en la fuente o cuando se pulsa «Comprobar fuentes» (`POST /api/admin/sources/probe`, que pide un listado mínimo). Consultar no escribe, tampoco aquí. La comprobación `remote_sources` del diagnóstico enseña ese estado y baja a **degradado** si alguna fuente activa no responde o no verifica hash — y también si no queda ninguna activa, porque entonces no se puede añadir nada.

## Buscar en varias fuentes a la vez

`GET /api/admin/catalog?q=&lang=&category=&sources=a,b` pregunta a cada fuente elegida, **intercala** los resultados por posición (el primero de cada una, luego el segundo…) y devuelve en cada uno su `source_id` y su `source_name`. Una fuente caída se anota como `down` y se sigue con las demás; solo se responde 502 (`unreachable`) cuando **ninguna** contestó. Los filtros que una fuente no soporta se esconden para ella en vez de fallar: el panel toma la unión de los filtros de las fuentes marcadas.

## Sin torrents

Decidido el 2026-08-30: **no se desarrolla**. Se pierde con ello la única vía estable y verificable hacia las colecciones que solo se publican así. Queda escrito para que no se vuelva a discutir por olvido, sino por decisión.

## Núcleo OPDS (`opds.py`)

- `parse_feed()` lee cualquier feed Atom comparando por **nombre local**, de modo que tolera espacios de nombres arbitrarios (`kiwix:name`, `dc:language`). Cada entrada conserva su `id`, sus campos directos y **todos** sus enlaces con `rel`, `type` y `length`.
- `OpdsEntry.acquisition(media_types, suffixes)` elige el enlace de descarga por tipo, por extensión o, sin pistas, por la relación `http://opds-spec.org/acquisition`.
- Validación de entrada externa: solo se aceptan URLs `http`/`https` (en el feed, el `.meta4` y cada espejo; un esquema distinto, como `file://`, se rechaza).

## Sabor Kiwix (`catalog.py`)

- `entries_url()` construye la URL de `/catalog/v2/entries` con `count=-1` y los filtros `q`, `lang`, `category`, `name`, `start`.
- `has_fulltext` se deriva de la etiqueta `_ftindex:yes` en `tags`; `articleCount` y `flavour` salen del espacio de nombres de Kiwix.
- El nombre de fichero derivado del `href` remoto se sanea — separadores de ruta o `..` invalidan la entrada. `queue_zim_download` lo re-comprueba antes de construir la ruta de destino, como defensa en profundidad.
- Ninguna URL de descarga se persiste (trampa 5): la fuente se consulta en el momento, y encolar vuelve a preguntar.
- `resolve_downloads(meta4_url)` descarga el `.meta4` con `?country=ES` y devuelve tamaño, sha-256 y espejos ordenados por `priority`. Si el servidor responde un error HTTP (meta4 ausente), degrada al camino sin meta4: un único espejo (la URL sin `.meta4`), sin hash, con `hash_missing=True` (trampa 6). Si no hay red, lanza `CatalogError`.
- `record_zim_entry()` vuelca la entrada en `zim_files` (upsert por UUID) sin tocar el `status` de una fila existente.
- Los filtros de idioma y categoría (`GET /api/admin/catalog/filters?source=…`) **solo existen para una fuente Kiwix**; para cualquier otra se devuelven listas vacías, y el panel esconde esos campos.

## Feeds de dos niveles y nombres de fichero

Gutenberg no publica el enlace de descarga en su feed de búsqueda: cada entrada es un puntero (`rel="subsection"`) a `/ebooks/<id>.opds`, y solo ese segundo feed trae el `application/epub+zip`. Sus entradas de navegación («Authors», «Subjects») son **idénticas en forma** a las de un libro; lo único que las separa es la ruta, de ahí `item_pattern`.

Eso se resuelve en dos pasos, y el segundo **no ocurre al buscar**:

1. `search()` devuelve el puntero, con el nombre de fichero vacío. Buscar cuesta una petición, no veintisiete.
2. `acquire()` se llama **al encolar**, sigue el puntero, elige el enlace por tipo y devuelve el ítem ya resuelto. Es el mismo sitio donde el sabor Kiwix baja su `.meta4`: nada de esto se persiste (trampa 5).

El nombre del fichero sale de la URL, pero Gutenberg sirve `1497.epub.noimages` y `1497.epub3.images` — sufijos que la ingesta rechazaría. `download_filename()` lo arregla de forma general: si el sufijo del nombre **no es un formato conocido** y el tipo de medio del enlace **sí lo es**, se recorta el nombre a su raíz y se le pone la extensión de ese formato. `1497.epub.noimages` con `application/epub+zip` queda en `1497.epub`, y un nombre que ya era bueno no se toca.

## Libros (`kind = "book"`)

El quinto tipo de la cola, junto a `zim`, `model`, `vendor` y `git`. Destino `<raíz>/books/<fichero>`. Descarga reanudable, con espejos y verificada por el mismo motor: un libro no es un caso especial, es otra fila de `downloads`.

**Descargar e indexar** es una petición explícita, nunca automática: encolar con `index` marcado guarda `after_done = "index"` en la fila. Al completarse la descarga, el enganche `on_done` de la cola llama a `ingest_downloaded_book()` (`pipeline.py`), que mete el fichero por la ingesta de la fase 10 —custodia por sha-256, extracción, anclas, troceado, embeddings y sellado— y lo deja buscable al párrafo. Si el servidor de embeddings no responde, **el fichero no se pierde**: queda descargado en `books/` y se registra un evento diciendo por qué no se indexó.

## Direcciones pegadas a mano

Para lo que no publica catálogo. `POST /api/admin/downloads/manual` y `arca add --url` aceptan una dirección, opcionalmente un nombre de fichero y un sha-256, y la meten en la cola como cualquier otra descarga. Se rechaza **antes de encolar nada**:

| Código | Cuándo |
| --- | --- |
| `bad_url` | esquema distinto de `http`/`https`, o ninguna forma de sacar un nombre de fichero seguro de la dirección |
| `bad_hash` | el sha-256 no son 64 caracteres hexadecimales |
| `bad_item_kind` | el destino no es `zim` ni `book` |

Sin hash, la descarga se hace igual y queda declarada no verificada.

Un ZIM pegado a mano **no se registra en `zim_files`**: no hay UUID de catálogo del que sacar la ficha. Se descarga, se verifica y `ZimLibrary` lo encuentra al explorar la carpeta —se lee y se busca como cualquier otro—, pero el catálogo del panel no podrá decir de él «en la biblioteca» ni «versión nueva», porque esas dos insignias se calculan contra esa tabla.

## Motor de descarga (`fetch.py`)

Estados de una fila de `downloads`: `queued → running → verifying → done`, con `paused` y `failed` como desvíos. Transiciones del operador: `request_pause` (queued/running→paused), `request_resume` (paused→queued), `request_cancel` (activa→failed con `last_error='cancelled'`), `request_retry` (failed→queued con contadores a cero), y `request_delete` (cualquier estado): elimina la fila de la cola y borra su `.part` parcial si existe — es contenido recuperable con red porque la fuente re-resuelve —, pero **nunca** toca el fichero final ya completado y verificado ni su sidecar. Un motor en marcha sobre esa fila se detiene solo en su siguiente persistencia de progreso; si el `.part` estaba bloqueado en ese instante, queda como huérfano y lo señala el diagnóstico.

Reglas del motor (`run_download`), en correspondencia con §8.2:

1. Descarga a `<destino>.part` con cabecera `Range: bytes=<tamaño real del .part>-`.
2. Si el servidor responde `206`, continúa en modo append; si responde `200` habiendo pedido rango, trunca y empieza de cero (trampa 7). Un `416` con el `.part` completo pasa directamente a verificación.
3. Si la conexión se corta antes del `Content-Length` declarado, se considera fallo reintenable (la lectura HTTP de la biblioteca estándar devuelve EOF silencioso; el motor lo detecta comparando bytes recibidos con los declarados).
4. Cada fallo avanza al siguiente espejo **sobre el mismo `.part`**, incrementa `attempts` y espera con retroceso exponencial (2 s, 4 s… tope 60 s). `download_max_attempts` (por defecto 5) marca `failed`.
5. El progreso se persiste periódicamente (cada 8 MiB o 2 s). En cada persistencia se relee el estado: si el operador pausó o canceló, el motor se detiene sin pisar ese estado.
6. Al completar: estado `verifying`, sha256 por **relectura completa** del fichero, comparación con el hash esperado. Si coincide (o no había hash de origen): se avisa al enganche `before_replace` (que en el servidor suelta ese fichero del caché de `ZimLibrary`, ver `zim.md`), `os.replace()` al nombre final —con tres reintentos cortos, porque el destino puede estar bloqueado un instante—, sidecar `<fichero>.sha256` al lado, estado `done`; sin hash de origen se guarda el calculado y se registra un evento de aviso. Si no coincide: `failed`, el `.part` **no se borra** y se registra el evento.
7. `run_queue()` re-encola al arrancar las filas `running`/`verifying` huérfanas de un proceso muerto; al reanudar manda el tamaño real del `.part`. Solo debe haber un proceso descargando a la vez (CLI `arca run` o el worker del servidor, no ambos).
8. Terminada cada fila, el enganche `on_done` recibe el resultado con su `dest_path` y su `after_done`. Es lo único que sabe de libros: el motor no importa nada de la ingesta.

`QueueWorker` es el worker en segundo plano del servidor: `kick()` lanza un hilo que drena la cola y muere al vaciarla; encolar, reanudar o reintentar desde el dashboard lo despiertan.

## Verificación local (`verify_library`)

Para cada fila de `zim_files`: si el fichero falta → `missing`; si no hay `.sha256` ni hash en la base → `no_hash` (se informa, no se inventa); si el hash coincide → `present`; si no → `corrupt` + evento. Es la base de `arca verify` y del botón de reverificación del dashboard.

## Ficheros en disco

- `<raíz>/zim/<fichero>.zim` — el ZIM verificado.
- `<raíz>/books/<fichero>` — el libro tal como lo sirvió su fuente. Si se pidió indexar, la ingesta guarda **además** su propia copia inmutable en `docs/originals/<sha256>.<ext>`; el fichero de `books/` no se toca.
- `<fichero>.sha256` — sidecar en formato `sha256sum` para reverificar sin red.
- `<fichero>.part` — descarga en curso o interrumpida; nunca se borra automáticamente.
