# Documentos propios: ingesta, lotes y búsqueda semántica

Módulos: `arca/ingest.py` (recepción, extracción, anclas, troceado), `arca/embed.py` (cliente de embeddings), `arca/vectors.py` (índice vectorial), `arca/shards.py` (lotes sellados), `arca/pipeline.py` (orquestación y alcance del índice profundo).

## El viaje de un PDF

1. **Identidad** — `receive_document` calcula el sha256. Ese hash **es** el documento: si ya está, no se reimporta.
2. **Custodia** — copia verificada por relectura a `docs/originals/<sha256>.pdf`, con sidecar `.json` (nombre real, fecha, páginas, lote). El original no se vuelve a tocar (invariante 4).
3. **Extracción** — PyMuPDF por bloques ordenados. Página sin capa de texto → `ocrmypdf` si está instalado; si no, se marca no indexable y **la importación sigue** (trampa 11).
4. **Anclas** — `p:0007:03` (página 4 dígitos, bloque 2), deterministas: reimportar el mismo fichero produce anclas idénticas (trampa 12).
5. **Troceado** — 300–500 tokens por fragmento, límites de párrafo, un párrafo de solapamiento, corte solo entre frases.
6. **Embeddings** — `passage: ` delante de cada fragmento contra `multilingual-e5-base`.
7. **Sellado** — `text.sqlite` a solo lectura, `vectors.i8`, `manifest.json` con los hashes y el modelo.

## Las dos formas de importar, y el paso que no se puede olvidar

`arca import <rutas>` acepta ficheros y carpetas (recursivo). Un PDF ilegible o protegido se
rechaza con su motivo y **no detiene** al resto.

Desde el panel (§12.4), la pestaña **Documentos** tiene un selector de ficheros que sube uno o
varios PDF a `POST /api/admin/documents/upload`. La subida va en crudo, con el nombre en la query
y los bytes como cuerpo: así no hace falta ninguna dependencia de *multipart* y el tope de 5
dependencias directas sigue intacto. El fichero aterriza primero en el directorio de sesión —
**fuera de la raíz**— y solo entra en la biblioteca a través de `receive_document`, que es quien
verifica que es un PDF y lo copia por su sha256. El temporal se borra pase lo que pase.

**Recibir no es indexar, y esta es la parte que se olvida.** Los pasos 1 y 2 de arriba dejan el
documento guardado y verificado, pero hasta que no corren del 3 al 7 el documento **no se busca**.
Quien dispara esos pasos es `POST /api/admin/documents/index` (botón «Indexar y sellar»), que toma
los originales cuyo sidecar aún no tiene lote, los pasa por `ingest_documents` y sella. Sin
servidor de embeddings lo dice y no toca nada: los documentos siguen guardados y se indexan cuando
vuelva. El panel muestra en todo momento cuántos hay esperando.

## Reanudabilidad (§10.2)

El estado por documento vive en `ingest_jobs`. Relanzar salta los `done` sin volver a pedir un solo embedding, y el lote se reutiliza mientras no tenga manifiesto.

La distinción que importa: un fallo **transitorio** (servidor de embeddings caído) devuelve el trabajo a la cola y **mantiene el lote provisional** hasta que se recupera; un fichero **rechazado** de verdad no bloquea el lote. Sin esa distinción, un lote se sellaría perdiendo documentos para siempre.

Los vectores de cada documento se guardan en `pending/<doc_id>.npy` dentro del lote provisional y se concatenan al sellar, de modo que un corte cuesta repetir un documento, no la tanda entera.

## El lote sellado

`text.sqlite` sigue §7.2 (`documents`, `chunks`, `chunks_fts` con FTS5) **más una tabla `blocks`**. Es una extensión deliberada: la versión consultable no se puede reconstruir desde `chunks` —se solapan entre sí— ni re-extrayendo del PDF, porque una página recuperada por OCR se perdería al re-extraer sin OCR. También hay `unindexable_pages` para que el dashboard pueda señalar los huecos.

`manifest.json` sigue §7.3, con las escalas de cuantización dentro (`vector_scale`, `query_scale`) en vez de en un tercer fichero.

**Estados de un lote** (`inspect_shard`):

| Estado | Cuándo | Qué se puede hacer con él |
|---|---|---|
| `sealed` | Manifiesto presente, hashes correctos, modelo coincidente | Palabras y semántica |
| `provisional` | Sin manifiesto todavía | Nada: no participa en las búsquedas |
| `corrupt` | Un fichero falta o no coincide con su hash | Nada, y se declara |
| `incompatible` | Sellado con otro modelo o dimensiones | **Solo palabras**, y se ofrece reindexar |

El estado `incompatible` es la defensa contra el peor modo de fallo del sistema (§7.3, trampa 1): un modelo de embeddings cambiado sin reindexar no da error, da resultados plausibles y falsos. Se compara contra el modelo activo en cada apertura.

## Índice vectorial

`vectors.i8` es un memmap int8 de `(n_chunks, dims)` más `vectors.ids` con los `chunk_id` en int64. Vectores normalizados L2 antes de cuantizar; la consulta se cuantiza a int16 (más resolución, mismo coste).

La búsqueda es un producto entero acumulado en int32 (`einsum` contra la matriz int8, sin convertir a float) repartido en hilos por bloques de filas. **500.000 fragmentos de 768 dimensiones: 43 ms.** La conversión int8→float32 era el 94% del coste de la versión anterior y desapareció.

Nunca hay índice global: cada lote se busca por separado y se fusionan los resultados (§7.4).

## Búsqueda semántica en la interfaz

Franja aparte y etiquetada, nunca mezclada con la de palabras (§9.3). Cada resultado propio lleva:

- el **fragmento citado** con su ancla,
- enlace al **consultable** posicionado en esa ancla (`/api/doc/<sha256>#p:0007:03`),
- enlace al **original intacto** en la página exacta (`/api/doc/<sha256>/original#page=7`), abierto con PDF.js vendorizado.

Un documento que no sea PDF —la documentación vendorizada, por ejemplo— no ofrece enlace al original: no lo hay como tal.

`doc_id` viene de la URL y se usa para construir una ruta en disco, así que se exige exactamente 64 caracteres hexadecimales. Cualquier otra cosa es 404 antes de tocar el sistema de ficheros.

## Documentación vendorizada indexada (§8.3)

`arca vendor` termina sellando un lote `docs/vendor/<fecha>` con el texto de la documentación de cada dependencia: README, CHANGELOG y lo que haya bajo `docs/` dentro de cada sdist. Entra **siempre** en el índice profundo, sin preguntar. Si el servidor de embeddings no responde, el resto del vendorizado termina igual y se avisa de que el lote no se indexó.

## Alcance del índice profundo (§12.5)

`deep_index_scope` guarda, por fuente, uno de tres valores: `out`, `full` o `curated` (con su lista de títulos). Tus documentos son `full` fijo. Las colecciones ZIM son `out` por defecto: indexar Wikipedia entera costaría días de cómputo para poca ganancia.

Antes de lanzar nada, la pantalla muestra la **estimación**: fragmentos, segundos de cómputo y espacio en disco. Lo que queda fuera del índice profundo **sigue buscándose por palabras**; solo pierde la precisión de párrafo.

## Comprobado en

`tests/test_ingest.py` (recepción, extracción, OCR inyectado, anclas deterministas, troceado), `tests/test_embed.py` (prefijos, lotes, degradación), `tests/test_vectors.py` (normalización, cuantización, búsqueda, ficheros corruptos), `tests/test_shards.py` (sellado, validación por hash, incompatibilidad de modelo), `tests/test_pipeline.py` (reanudabilidad), `tests/test_documents.py` (extremo a extremo por la API, incluido el rechazo de identificadores forjados), `tests/test_vendor_index.py` y `tests/test_admin_content.py` (GGUF, alcance, dashboards). Puerta del hito: `docs/gates/milestone-3.md`.
