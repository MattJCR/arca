# Catálogo y descargas

Módulos: `arca/catalog.py` (cliente OPDS + resolución meta4) y `arca/fetch.py` (motor de descarga, cola y verificación local).

## Cliente del catálogo (`catalog.py`)

- `entries_url()` construye la URL de `https://library.kiwix.org/catalog/v2/entries` con `count=-1` y los filtros `q`, `lang`, `category`, `name`, `start`.
- `parse_entries()` parsea el feed Atom/OPDS con `ElementTree` comparando por nombre local, de modo que tolera que Kiwix sirva `name`, `flavour`, `tags` o `articleCount` con o sin el espacio de nombres `http://www.kiwix.org/opds/`. Entradas sin enlace de adquisición se descartan.
- `has_fulltext` se deriva de la etiqueta `_ftindex:yes` en `tags`.
- Validación de entrada externa: solo se aceptan URLs `http`/`https` (en el feed, el `.meta4` y cada espejo; un esquema distinto, como `file://`, se rechaza), y el nombre de fichero derivado del `href` remoto se sanea — separadores de ruta o `..` invalidan la entrada. `queue_zim_download` lo re-comprueba antes de construir la ruta de destino, como defensa en profundidad.
- Ninguna URL de descarga se persiste (trampa 5): el catálogo se consulta en el momento y `arca add` resuelve entonces.
- `resolve_downloads(meta4_url)` descarga el `.meta4` con `?country=ES` y devuelve tamaño, sha-256 y espejos ordenados por `priority`. Si el servidor responde un error HTTP (meta4 ausente), degrada al camino sin meta4: un único espejo (la URL sin `.meta4`), sin hash, con `hash_missing=True` (trampa 6). Si no hay red, lanza `CatalogError`.
- `record_zim_entry()` vuelca la entrada en `zim_files` (upsert por UUID) sin tocar el `status` de una fila existente.

## Motor de descarga (`fetch.py`)

Estados de una fila de `downloads`: `queued → running → verifying → done`, con `paused` y `failed` como desvíos. Transiciones del operador: `request_pause` (queued/running→paused), `request_resume` (paused→queued), `request_cancel` (activa→failed con `last_error='cancelled'`), `request_retry` (failed→queued con contadores a cero), y `request_delete` (cualquier estado): elimina la fila de la cola y borra su `.part` parcial si existe — es contenido recuperable con red porque el catálogo re-resuelve —, pero **nunca** toca el fichero final ya completado y verificado ni su sidecar. Un motor en marcha sobre esa fila se detiene solo en su siguiente persistencia de progreso; si el `.part` estaba bloqueado en ese instante, queda como huérfano y lo señala el diagnóstico.

Reglas del motor (`run_download`), en correspondencia con §8.2:

1. Descarga a `<destino>.part` con cabecera `Range: bytes=<tamaño real del .part>-`.
2. Si el servidor responde `206`, continúa en modo append; si responde `200` habiendo pedido rango, trunca y empieza de cero (trampa 7). Un `416` con el `.part` completo pasa directamente a verificación.
3. Si la conexión se corta antes del `Content-Length` declarado, se considera fallo reintenable (la lectura HTTP de la biblioteca estándar devuelve EOF silencioso; el motor lo detecta comparando bytes recibidos con los declarados).
4. Cada fallo avanza al siguiente espejo **sobre el mismo `.part`**, incrementa `attempts` y espera con retroceso exponencial (2 s, 4 s… tope 60 s). `download_max_attempts` (por defecto 5) marca `failed`.
5. El progreso se persiste periódicamente (cada 8 MiB o 2 s). En cada persistencia se relee el estado: si el operador pausó o canceló, el motor se detiene sin pisar ese estado.
6. Al completar: estado `verifying`, sha256 por **relectura completa** del fichero, comparación con el hash esperado. Si coincide (o no había hash de origen): se avisa al enganche `before_replace` (que en el servidor suelta ese fichero del caché de `ZimLibrary`, ver `zim.md`), `os.replace()` al nombre final —con tres reintentos cortos, porque el destino puede estar bloqueado un instante—, sidecar `<fichero>.sha256` al lado, estado `done`; sin hash de origen se guarda el calculado y se registra un evento de aviso. Si no coincide: `failed`, el `.part` **no se borra** y se registra el evento.
7. `run_queue()` re-encola al arrancar las filas `running`/`verifying` huérfanas de un proceso muerto; al reanudar manda el tamaño real del `.part`. Solo debe haber un proceso descargando a la vez (CLI `arca run` o el worker del servidor, no ambos).

`QueueWorker` es el worker en segundo plano del servidor: `kick()` lanza un hilo que drena la cola y muere al vaciarla; encolar, reanudar o reintentar desde el dashboard lo despiertan.

## Verificación local (`verify_library`)

Para cada fila de `zim_files`: si el fichero falta → `missing`; si no hay `.sha256` ni hash en la base → `no_hash` (se informa, no se inventa); si el hash coincide → `present`; si no → `corrupt` + evento. Es la base de `arca verify` y del botón de reverificación del dashboard.

## Ficheros en disco

- `<raíz>/zim/<fichero>.zim` — el ZIM verificado.
- `<raíz>/zim/<fichero>.zim.sha256` — sidecar en formato `sha256sum` para reverificar sin red.
- `<raíz>/zim/<fichero>.zim.part` — descarga en curso o interrumpida; nunca se borra automáticamente.
