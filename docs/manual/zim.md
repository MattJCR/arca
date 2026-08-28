# Lectura de colecciones ZIM

Módulo: `arca/zim.py`. Sobre `libzim` directamente, sin binarios por plataforma ni subprocesos (§9.1).

## Qué existe hoy

Abrir un `.zim` de la carpeta `zim/` de la biblioteca, leer sus metadatos reales, buscar dentro con el índice que el propio fichero trae, extraer el contenido de una entrada y **servirlo por HTTP** en `/zim/{uuid}/{path}` con sus estilos e imágenes.

## Identidad de una colección

La clave de una colección es **el UUID que el propio ZIM lleva dentro** (`archive.uuid`), no el UUID del catálogo Kiwix ni el nombre del fichero. Así una colección copiada a mano en `zim/`, sin fila en `library.db`, se lee y se busca igual que una descargada.

## Metadatos leídos del fichero (`ZimInfo`)

`uuid`, `filename`, `path`, `title`, `description`, `language`, `article_count`, `media_count`, `has_fulltext`, `has_title_index`, `main_path`, `size_bytes`.

`title`, `description` y `language` salen de los metadatos internos del ZIM; si faltan, `title` cae al nombre del fichero sin extensión. `main_path` es la portada ya resuelta: los ZIM apuntan su entrada principal con una redirección interna, y aquí se guarda el destino final, no la redirección.

Los metadatos del catálogo OPDS que ya viven en `zim_files` (incluido su `has_fulltext`) son una *afirmación del catálogo*; los de `ZimInfo` son la realidad del fichero. Donde importa qué se puede buscar de verdad, manda `ZimInfo`.

## Los tres modos de índice

`index_mode(info)` devuelve cuál de los tres aplica, y `search_archive()` usa exactamente ese:

| Modo | Cuándo | Qué busca |
|---|---|---|
| `fulltext` | El ZIM trae índice Xapian de texto completo | Dentro del texto de los artículos |
| `title` | Solo trae índice de títulos | Solo títulos (`SuggestionSearcher`) |
| `none` | No trae ningún índice | Nada: devuelve cero resultados, y se declara |

Un ZIM sin índice de texto completo **no se finge**: se declara en el dashboard y solo se busca por título (§9.1).

## Búsqueda

`search_archive(archive, info, query, limit, offset)` devuelve un `ZimResults` con el modo empleado, el número estimado de coincidencias y la lista de `ZimHit`. Cada `ZimHit` lleva el UUID y el título de la colección de origen — la procedencia viaja con el resultado desde el primer momento, antes incluso de que exista la interfaz que la pinta.

`rank` es la posición del resultado **dentro de su fuente** (`offset` incluido). No hay ninguna puntuación normalizada, y no la habrá: los scores de Xapian y los de FTS5 no son comparables, así que la fusión de H2.3 intercalará por rango (§9.3, trampa 3).

La consulta vacía o en blanco devuelve cero resultados sin tocar el índice. `limit` se recorta a `MAX_LIMIT` (100).

## Lectura de entradas

`read_entry(archive, path)` devuelve `ZimContent` (ruta final, título, mimetype y bytes). Sigue las redirecciones internas hasta un máximo de 8 saltos; más allá, error. Una ruta inexistente o vacía produce `ZimEntryNotFoundError`. Las rutas son internas del ZIM, nunca del sistema de ficheros: no hay superficie de path traversal.

## `ZimLibrary`

Vista de la carpeta `zim/` para el proceso servidor. `refresh()` la explora, abre lo nuevo y olvida lo que ya no está; los archivos ya abiertos se reutilizan mientras su `(mtime, tamaño)` no cambie, de modo que refrescar es una llamada `stat` por fichero, no una reapertura. El acceso al caché está protegido por un cerrojo.

Un fichero que no se puede abrir (corrupto, truncado, o simplemente no es un ZIM) **no rompe la exploración**: se aparta a `failures()` con su motivo y el resto de la biblioteca sigue disponible (invariante 5).

Abrir y leer un ZIM no escribe nada bajo la raíz: `libzim` abre en solo lectura (invariante 1).

### Soltar un fichero antes de reemplazarlo

Un ZIM abierto queda bloqueado por el sistema operativo mientras siga en el caché — en Windows, de forma estricta. Si mientras tanto se vuelve a descargar esa misma colección (una reverificación tras corrupción, un reintento), el `os.replace` final de `fetch.py` fallaría con un error del sistema y la descarga quedaría marcada como fallida pese a haberse bajado y verificado entera, rompiendo el invariante 2.

Por eso `ZimLibrary.release(filename)` saca del caché la entrada de ese fichero, y el motor de descargas la invoca **justo antes** de reemplazar el destino: `QueueWorker` acepta un `before_replace`, que la aplicación conecta a `release`. El reemplazo además reintenta tres veces con una espera corta, porque una petición en vuelo puede sostener la referencia un instante más. `fetch.py` no sabe nada de ZIM: recibe una función, no un módulo.

## Servido de artículos

`GET /zim/{uuid}/{path}` devuelve la entrada tal cual sale del ZIM, con su propio mimetype (al que se le añade `charset=utf-8` solo si es texto y no lo traía). `GET /zim/{uuid}` abre la portada declarada por la colección. Los recursos internos —CSS, imágenes, SVG— salen por la misma ruta, del mismo fichero: **cero peticiones externas**.

Los enlaces internos se resuelven con una etiqueta `<base>` (§9.1). Al servir HTML se elimina el `<base>` que el ZIM traiga y se inserta el nuestro justo detrás de `<head>`, apuntando a la **carpeta de la entrada ya resuelta**: `/zim/{uuid}/` para `Teorema_de_Pitágoras`, `/zim/{uuid}/sub/dir/` para `sub/dir/page.html`. Así los enlaces relativos del ZIM (`./_mw_/estilo.css`, `Geometría`) caen dentro de la aplicación sin reescribir el HTML enlace a enlace, y siguen funcionando aunque se haya llegado por una redirección interna.

Todo lo servido lleva `Cache-Control: private, max-age=86400`: el contenido de un ZIM es inmutable y una versión nueva de una colección trae un UUID nuevo, así que la URL cambia con ella.

### El contenido del ZIM es contenido de terceros

Un ZIM lo escribió otra gente, así que se sirve **con su propia correa**, no confiando en el marco que lo muestre. Cada respuesta de `/zim/...` lleva `X-Content-Type-Options: nosniff` y esta política:

```
default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline';
font-src 'self' data:; media-src 'self' data:; base-uri 'self';
form-action 'none'; frame-ancestors 'self'
```

`default-src 'none'` deja fuera el JavaScript del ZIM y **cualquier petición de red suya**, lo que convierte en verificable la promesa de «cero llamadas de red en tiempo de consulta» (§3). Los estilos e imágenes siguen saliendo del propio fichero, que es lo que hace falta para leer.

Va en la cabecera, y no solo en el `sandbox` del visor, porque un artículo se puede abrir en una pestaña suelta —basta un clic con el botón central sobre un resultado— y ahí no habría marco que lo contuviera: el script del ZIM correría en nuestro propio origen, con acceso a `/api/admin/*`. La política viaja con el contenido, así que protege en los dos caminos. Las páginas de error de `/zim/...` la llevan también.

Errores, como página HTML en el idioma activo y con su código correcto: colección desconocida o entrada inexistente → 404; colección ilegible (por ejemplo, disco desconectado en caliente) → 503 pidiendo comprobar el disco. Ninguno tumba el servidor ni deja la interfaz en blanco.

## Dónde se ve: contenido local (§12.3)

Dashboard `/admin`, pestaña «Contenido local», servida por `GET /api/admin/zim`
(ver `web.md`): una fila por colección con título, idioma, sabor, número de
artículos, tamaño, **modo de índice real**, estado y si entra o no en las
búsquedas. El estado viene de `zim_files` cuando la colección se descargó por
ARCA (`present` / `corrupt` / `missing`) y se marca «añadida a mano» cuando no
hay fila; el sabor también sale del catálogo, así que una colección añadida a
mano no lo tiene. Los ficheros ilegibles se listan aparte con su error.

### Activar y desactivar sin borrar

`POST /api/admin/zim/{uuid}/{enable|disable}`. Desactivar una colección **no
toca el fichero**: solo la saca de los resultados, y el efecto es inmediato
porque cada búsqueda relee la lista. Se puede volver a activar cuando se quiera.

La lista de desactivadas vive en la tabla `settings` de `library.db`, en la clave
`search.disabled_zim`, como un array JSON de UUID. Se guarda ahí, y no como una
columna de `zim_files`, porque una colección copiada a mano en `zim/` no tiene
fila en esa tabla y también tiene que poder desactivarse. Cada cambio deja su
evento.

Una colección desactivada queda fuera **aunque se la nombre explícitamente** en
el `scope` de una búsqueda: desactivada es desactivada.

## Comprobado en

`tests/test_zim.py` (metadatos, los tres modos de índice, límite y desplazamiento, redirecciones, ficheros ilegibles, alta y baja de ficheros en caliente) y `tests/test_api.py` (el endpoint del dashboard). Los ZIM de prueba se construyen en el momento con `libzim.writer` desde `tests/support.py`: no hay ningún binario en el repositorio.
