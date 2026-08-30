# Documentos propios: ingesta, lotes y búsqueda semántica

Módulos: `arca/formats.py` (qué formatos existen, cómo se abren, qué puede fallar al abrirlos), `arca/extract.py` (extractores por familia y OCR), `arca/ingest.py` (recepción, custodia, anclas, troceado), `arca/embed.py` (cliente de embeddings), `arca/vectors.py` (índice vectorial), `arca/shards.py` (lotes sellados), `arca/pipeline.py` (orquestación y alcance del índice profundo).

## El viaje de un documento

1. **Identidad** — `receive_document` calcula el sha256. Ese hash **es** el documento: si ya está, no se reimporta.
2. **Custodia** — copia verificada por relectura a `docs/originals/<sha256><extensión real>`, con sidecar `.json` (nombre real, tipo, fecha, páginas, lote). El original no se vuelve a tocar (invariante 4).
3. **Extracción** — PyMuPDF por bloques ordenados, con el extractor de la familia del formato, y OCR donde toque (ver la sección de OCR).
4. **Anclas** — `p:0007:03` (página 4 dígitos, bloque 2), deterministas: reimportar el mismo fichero produce anclas idénticas (trampa 12).
5. **Troceado** — 300–500 tokens por fragmento, límites de párrafo, un párrafo de solapamiento, corte solo entre frases.
6. **Embeddings** — `passage: ` delante de cada fragmento contra `multilingual-e5-base`.
7. **Sellado** — `text.sqlite` a solo lectura, `vectors.i8`, `manifest.json` con los hashes y el modelo.

## Qué formatos entran hoy, y por qué los demás no

La lista blanca vive en `arca/formats.py` (`FORMATS`) y es **exactamente** lo que ARCA acepta. La
regla que la gobierna: una extensión entra solo con un test que ingiere un fichero real de ese
formato y comprueba que produce bloques. «PyMuPDF lo abre» no basta — un CBZ abre y da cero
bloques, y un documento fantasma que nunca encuentra nada es peor que un rechazo.

| Extensión | Tipo servido | Familia | OCR | Original en la aplicación |
|---|---|---|---|---|
| `.pdf` | `application/pdf` | paginado | de reserva | PDF.js, en la página exacta |
| `.xps` | `application/vnd.ms-xpsdocument` | paginado | no | descarga |
| `.oxps` | `application/oxps` | paginado | no | descarga |
| `.epub` | `application/epub+zip` | reflowable | no | descarga |
| `.mobi` | `application/x-mobipocket-ebook` | reflowable | no | descarga |
| `.fb2` | `application/x-fictionbook+xml` | reflowable | no | descarga |
| `.txt` | `text/plain` | reflowable | no | descarga |
| `.cbz` | `application/vnd.comicbook+zip` | imagen | **obligatorio** | descarga |
| `.png` | `image/png` | imagen | **obligatorio** | descarga |
| `.jpg` · `.jpeg` | `image/jpeg` | imagen | **obligatorio** | descarga |
| `.ppm` | `image/x-portable-pixmap` | imagen | **obligatorio** | descarga |

**Paginado** significa que el fichero trae sus propias páginas y ARCA no las toca. **Reflowable**
significa que no las trae y hay que fijarlas: ver la sección siguiente. **Imagen** significa que no
hay ni una letra dentro: todo el texto sale del OCR, y sin `ocrmypdf` instalado esas extensiones
**no se aceptan** — ni se listan en el selector del panel.

Lo que **no** entra, y el motivo exacto que se devuelve:

| Extensiones | Código | Qué se dice |
|---|---|---|
| `.cbz`, `.png`, `.jpg`, `.jpeg`, `.ppm` **sin `ocrmypdf` instalado** | `no_ocr_binary` | Es imagen pura y solo se indexa con OCR; instala el binario y vuelve a importarla |
| `.azw3`, `.tif`, `.tiff`, `.gif`, `.bmp`, `.jp2`, `.psd` | `unverified_format` | PyMuPDF los documenta, pero no hay prueba que los ingiera de verdad |
| cualquier otra | `unsupported_format` | No está en la lista |
| una de la lista con contenido que no le corresponde | `bad_file` | El fichero está renombrado o a medio copiar |

Los cuatro códigos son estables y tienen mensaje accionable en los dos catálogos
(`documents.refusal.*`); el panel y `arca import` usan **los mismos**.

## OCR: el PDF y las imágenes, por caminos distintos

El OCR lo hace siempre el binario externo `ocrmypdf`, que ARCA lanza como subproceso y **nunca**
como dependencia: si no está, el sistema arranca igual y lo dice. Lo que cambia es cómo llega la
página hasta él.

- **PDF** — OCR *de reserva*: solo entra en las páginas que no traen capa de texto. `ocrmypdf`
  recibe el original y el número de página. Una página que el OCR no recupera se marca **no
  indexable** y la importación sigue (trampa 11).
- **Imágenes y cómics** — OCR *obligatorio*: no hay capa de texto en ninguna página. ARCA renderiza
  la página a 300 ppp, la envuelve en un PDF de una sola página en el directorio de trabajo del
  lote, y **ese** PDF es el que va al OCR. El fichero renderizado se borra en cuanto termina, pase
  lo que pase.

El ancla no cambia: la página del CBZ es la página del ancla, y el bloque recuperado es
`p:0002:01`. Por eso un cómic escaneado se cita igual que un PDF.

**Sin `ocrmypdf` en el PATH, las extensiones de imagen se rechazan en la puerta** con el código
`no_ocr_binary`, en vez de entrar y quedarse vacías. Es la trampa 11 llevada al extremo: aceptar un
cómic que nunca va a encontrar nada es peor que decir por qué no se acepta. Ningún otro formato se
entera de que falta el binario.

## El maquetado fijado, y qué obliga a re-sellar

Un EPUB, un MOBI, un FB2 o un TXT no tienen páginas: se las inventa el maquetador con el ancho, el
alto y el cuerpo que se le den. ARCA los fija en `FIXED_LAYOUT`: **595 × 842 puntos, cuerpo 11**
(A4 a 72 ppp), firma `595x842@11`. Con eso el ancla sigue siendo `p:PPPP:OO`, y ni el visor, ni el
chat, ni los lotes ya sellados se enteran de que el formato no tenía páginas propias.

La firma del maquetado **se graba en el manifiesto de cada lote** (`layout`) y `arca verify` la
contrasta con la activa. Dos cosas invalidan un lote que lleve formatos reflowables dentro:

- **cambiar `FIXED_LAYOUT`**, y
- **cambiar la versión de PyMuPDF/MuPDF**, porque el reparto de páginas es suyo.

En ambos casos las anclas ya selladas siguen siendo internamente coherentes —el consultable y las
citas del chat funcionan— pero **reimportar el mismo libro daría otras anclas**, así que hay que
re-sellar los lotes afectados si se quiere que las citas nuevas y las viejas hablen del mismo
sitio. `arca verify` lo dice con el código `MAQUETADO` y el panel lo marca como «layout distinto».
Que el maquetado fijado basta para que el ancla sea determinista está medido en
`docs/gates/f10-anchors.md`, y la skill `verify-anchors` lo vuelve a medir cuando haga falta.

## Las dos formas de importar, y el paso que no se puede olvidar

`arca import <rutas>` acepta ficheros y carpetas (recursivo). El escaneo de una carpeta recoge
**toda extensión que ARCA conozca**, no solo las que puede aceptar ahora mismo: así un `.cbz` en una
máquina sin `ocrmypdf` se rechaza **diciendo que falta el binario**, en vez de desaparecer del
recuento sin decir nada. Un fichero ilegible, protegido o de un formato que no entra se rechaza
**con su motivo** y **no detiene** al resto; el código de salida es 1 si hubo algún rechazo.

Desde el panel (§12.4), la pestaña **Documentos** tiene un selector de ficheros que sube uno o
varios documentos a `POST /api/admin/documents/upload`. La subida va en crudo, con el nombre en la
query y los bytes como cuerpo: así no hace falta ninguna dependencia de *multipart* y el tope de 5
dependencias directas sigue intacto. El fichero aterriza primero en el directorio de sesión —
**fuera de la raíz**— y solo entra en la biblioteca a través de `receive_document`, que es quien
comprueba la extensión **y el contenido** antes de copiarlo por su sha256. El temporal se borra
pase lo que pase.

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
- enlace al **original intacto**, servido con su tipo real por `/api/doc/<sha256>/original`.

El original se sirve de dos maneras, y la diferencia es deliberada. Un PDF va `inline` y se abre en
la página exacta (`/api/doc/<sha256>/original#page=7`) con PDF.js vendorizado. **Cualquier otro
formato va `attachment` y se descarga tal cual**: PDF.js no abre un EPUB, y fingir un visor que no
existe sería peor que dar el fichero. El botón lo dice: «Abrir el original» frente a «Descargar el
original».

Un documento sin original en disco —la documentación vendorizada, por ejemplo— no ofrece ninguno de
los dos: no lo hay como tal.

`doc_id` viene de la URL y se usa para construir una ruta en disco, así que se exige exactamente 64 caracteres hexadecimales. Cualquier otra cosa es 404 antes de tocar el sistema de ficheros.

## Documentación vendorizada indexada (§8.3)

`arca vendor` termina sellando un lote `docs/vendor/<fecha>` con el texto de la documentación de cada dependencia: README, CHANGELOG y lo que haya bajo `docs/` dentro de cada sdist. Entra **siempre** en el índice profundo, sin preguntar. Si el servidor de embeddings no responde, el resto del vendorizado termina igual y se avisa de que el lote no se indexó.

## Alcance del índice profundo (§12.5)

`deep_index_scope` guarda, por fuente, uno de tres valores: `out`, `full` o `curated` (con su lista de títulos). **Hoy la elección se registra y nada la consume todavía**: la indexación profunda de artículos de ZIM no está construida, así que `curated` describe una intención, no un efecto. `pipeline.scope_titles` es su lector, a la espera. Tus documentos son `full` fijo. Las colecciones ZIM son `out` por defecto: indexar Wikipedia entera costaría días de cómputo para poca ganancia.

Antes de lanzar nada, la pantalla muestra la **estimación**: fragmentos, segundos de cómputo y espacio en disco. Lo que queda fuera del índice profundo **sigue buscándose por palabras**; solo pierde la precisión de párrafo.

## Comprobado en

`tests/test_formats.py` (tabla de formatos, detección por extensión y contenido, determinismo del ancla con el maquetado fijado), `tests/test_books.py` (la lista blanca entera ingerida fichero a fichero, XPS y reflowables de extremo a extremo, maquetado grabado en el lote, `arca verify`, original servido con su tipo), `tests/test_ingest.py` (recepción, extracción, OCR inyectado, anclas deterministas, troceado), `tests/test_embed.py` (prefijos, lotes, degradación), `tests/test_vectors.py` (normalización, cuantización, búsqueda, ficheros corruptos), `tests/test_shards.py` (sellado, validación por hash, incompatibilidad de modelo), `tests/test_pipeline.py` (reanudabilidad), `tests/test_documents.py` (extremo a extremo por la API, incluido el rechazo de identificadores forjados), `tests/test_vendor_index.py` y `tests/test_admin_content.py` (GGUF, alcance, dashboards). Puertas: `docs/gates/milestone-3.md` (hito 3) y `docs/gates/f10-anchors.md` (determinismo del ancla en los formatos reflowables).
