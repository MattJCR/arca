# ARCA — Biblioteca de conocimiento offline

Especificación de construcción. Versión 1, agosto de 2026.

---

## 0. Cómo usar este documento

Va dirigido a un agente de IA que va a construir el proyecto completo.

- **Las decisiones están cerradas.** La sección 2 contiene todo lo que en algún momento estuvo abierto. No vuelvas a preguntarlas.
- **El orden de los hitos no es negociable.** Cada hito termina con criterios de aceptación verificables. No empieces el siguiente hasta que los del anterior pasen.
- **Cada hito debe dejar el sistema arrancable y útil.** Al terminar el hito 2 el proyecto ya resuelve casos reales sin modelo de lenguaje. Eso es intencionado.
- **Ante la duda entre una solución elegante y una aburrida, elige la aburrida.** El criterio de éxito de este proyecto es que siga funcionando dentro de cinco años en una máquina desconocida, sin red y sin nadie que recuerde cómo se hizo.
- Escribe todo el código y los comentarios en inglés. La interfaz y los mensajes de usuario, en español.

---

## 1. Qué es y qué no es

Una biblioteca de conocimiento consultable sin conexión, con dos mitades: un buscador estilo Google sobre todo el corpus, y un panel de chat con un modelo local que asiste sobre lo que hay en pantalla.

**No es** un chatbot con documentos. Es un archivo consultable con asistencia encima. Si el modelo desaparece, se corrompe o se sustituye, la biblioteca sigue siendo enteramente útil. Esta frase gobierna todas las decisiones de arquitectura: nada del núcleo puede depender del modelo.

**Fuera de alcance, explícitamente:**

- Login, usuarios, permisos, multiusuario. No hay. El sistema asume un único operador con acceso físico a la máquina.
- Endurecimiento de seguridad, cifrado, auditoría. La prioridad es la fiabilidad, no la confidencialidad.
- Sincronización, nube, telemetría, actualizaciones automáticas.
- Cualquier llamada de red en tiempo de consulta. La red solo se toca al descargar o al vendorizar.

---

## 2. Decisiones cerradas

| Asunto | Decisión |
|---|---|
| Hardware objetivo | GPU con 8-12 GB de VRAM |
| Disco | Sin límite práctico |
| Idiomas del corpus | Español e inglés |
| Modelo de chat por defecto | **Gemma 4 QAT**, decidido en F4 (2026-08-28): la variante densa de 12B o la MoE de 26B con 4B activos, según la VRAM de la máquina. La cuantización se elige al descargar desde el buscador de modelos del panel. Candidatos anteriores, ya descartados: Qwen3-14B y Gemma-3-12B |
| Contexto inicial | 8192, con KV cache a Q8 |
| Modelo de embeddings | `multilingual-e5-base`, 768 dimensiones, GGUF, servido por llama.cpp |
| Prefijos de embedding | Obligatorios: `query: ` en consultas, `passage: ` en fragmentos |
| Almacén vectorial | Índice plano int8 en memmap, uno por lote. Sin motor externo |
| Wikipedia | ES y EN, variante `nopic` |
| Colecciones con imágenes | WikiMed, Where There Is No Doctor, Where There Is No Dentist, Appropedia, iFixit, Wikivoyage |
| Otras colecciones | Wikilibros ES, Wikcionario ES, Stack Exchange por materias, ArchWiki, Stack Overflow, DevDocs |
| Índice semántico profundo | Todos los documentos importados por el usuario, todos los ZIM pequeños completos, el lote de documentación técnica. De Wikipedia solo una lista curada de artículos |
| Frontend | HTML servido más JavaScript sin framework ni empaquetador. Todo vendorizado, cero CDN |
| Nombre del paquete Python | `arca` |
| Puesta en marcha del chat | **Automática**, decidido el 2026-08-29: el usuario solo deja el `.gguf` en `models/chat/`. ARCA consigue el binario de `llama-server`, lo despliega, fija su commit, crea el perfil y lo prueba. Si un paquete no arranca, prueba el siguiente. Ver §13.0 |
| Orígenes de red | Tres, y solo tres: el catálogo de Kiwix, el hub de modelos y las releases de llama.cpp en GitHub. Los tres se resuelven en el momento; ninguno se guarda como URL fija |

---

## 3. Dependencias

**Tope duro: ocho dependencias directas.** El árbol actual tiene cinco. Añadir una obliga a añadir su documentación, su wheel, su sdist y su licencia al lote vendorizado (sección 8.3). Ese coste es el mecanismo que mantiene el proyecto pequeño, no un trámite.

| Paquete | Para qué |
|---|---|
| `fastapi` | API HTTP |
| `uvicorn` | Servidor ASGI |
| `libzim` | Lectura de archivos ZIM y su índice Xapian interno |
| `pymupdf` | Extracción de texto y bloques de PDF |
| `numpy` | Búsqueda vectorial por fuerza bruta |

Todo lo demás sale de la biblioteca estándar. En particular:

- **Descargas HTTP: `urllib.request`.** Soporta cabecera `Range` sin problema. No añadas `requests` ni `httpx`.
- **XML del catálogo: `xml.etree.ElementTree`.**
- **Base de datos: `sqlite3`.**
- **Hashes: `hashlib`.**

Binarios externos, gestionados como subprocesos y no como dependencias de Python: `llama-server` de llama.cpp, y opcionalmente `ocrmypdf` con `tesseract` para PDF sin capa de texto. Si no están, el sistema arranca igual y lo marca como degradado.

---

## 4. Estructura del repositorio

```
arca/
  pyproject.toml
  README.md
  sources.lock              # inventario de todo lo vendorizado (sección 8.3)
  arca/
    __init__.py
    config.py               # carga de config, detección de entorno
    db.py                   # conexiones SQLite, esquemas, migraciones
    catalog.py              # cliente OPDS de Kiwix
    fetch.py                # motor de descarga reanudable y verificada
    vendor.py               # vendorizado de documentación y wheels
    zim.py                  # lectura, búsqueda y servido de ZIM
    shards.py               # lotes sellados: crear, abrir, validar manifiesto
    ingest.py               # PDF -> texto anclado -> lote
    embed.py                # cliente del servidor de embeddings
    vectors.py              # índice plano int8, memmap
    search.py               # fusión de resultados de todas las fuentes
    llama.py                # gestión del subproceso llama-server
    chat.py                 # construcción de contexto, citación, herramientas
    diagnostics.py          # comprobaciones de estado
    api.py                  # rutas FastAPI
    cli.py                  # arca <comando>
    static/                 # css, js y tipografías vendorizadas
    templates/              # HTML
  tests/
```

---

## 5. Estructura de la biblioteca en disco

Una sola ruta raíz. Es lo único que se pide en la instalación.

```
<raiz>/
  arca.json                 # configuración. Texto plano, editable a mano
  library.db                # catálogo, estado de descargas, historial. Escribible
  zim/
    wikipedia_es_all_nopic_2026-07.zim
    wikipedia_es_all_nopic_2026-07.zim.sha256
    ...
  docs/
    originals/
      <sha256>.pdf          # el original, intacto, nunca se modifica
      <sha256>.json         # nombre real, fecha de importación, lote al que pertenece
    shards/
      2026-08-27T1043-a1b2c3/
        manifest.json
        text.sqlite
        vectors.i8
        vectors.ids
    vendor/                 # lote de documentación técnica, misma forma que un shard
      2026-08-27T1102-d4e5f6/
        ...
  models/
    chat/*.gguf
    embed/*.gguf
  vendor/
    wheelhouse/             # wheels y sdists de todo el árbol de dependencias
    llama.cpp/              # repositorio al commit fijado + binarios compilados
    docs/                   # tarballs originales de documentación upstream
  logs/
    arca.log
```

**Regla:** todo lo que hay bajo `<raiz>` es autosuficiente. Copiar el directorio a otro ordenador o a otro disco produce una biblioteca funcional sin reconstruir ningún índice.

---

## 6. Invariantes de robustez

Estas siete reglas no se rompen. Si una decisión de implementación las contradice, la decisión está mal.

1. **Consultar no escribe.** Ninguna operación de lectura, búsqueda o chat escribe un byte bajo `<raiz>`. Ni ficheros temporales, ni logs, ni `-wal`, ni `-shm`, ni fechas de acceso. Consecuencia práctica: se puede desconectar el disco en caliente durante una consulta sin consecuencias.
   - Los lotes sellados se abren en `mode=ro` con `PRAGMA query_only=ON`, y se sellan con `journal_mode=DELETE`, nunca WAL.
   - `library.db` sí es escribible, pero solo lo abren los procesos de descarga, importación y administración. El proceso de consulta lo abre en solo lectura.
   - Estado de sesión, caché y logs de consulta van a memoria o al temporal del sistema operativo, configurable, nunca a la raíz.

2. **Escribir es reanudable.** Descargar e importar son las únicas operaciones que escriben, y ambas retoman donde se cortaron. Un corte a mitad de indexar 400 documentos cuesta repetir uno, no 400.

3. **Los lotes son inmutables.** Un lote sellado no se modifica jamás. Añadir contenido crea un lote nuevo. Corregir contenido crea un lote nuevo y marca el anterior como reemplazado en `library.db`. Nunca se reescribe un `text.sqlite` ya sellado.

4. **El original nunca se toca.** El PDF que sube el usuario se guarda tal cual, se le calcula el hash, y ese hash es su identidad. Cualquier resultado de búsqueda que venga de ese documento lleva un enlace directo al original, a la página exacta.

5. **Nunca deja de arrancar.** Sin modelo de chat, la búsqueda funciona. Sin servidor de embeddings, la búsqueda por palabras funciona. Sin ZIM descargados, el sistema arranca y te dice que descargues. Cualquier subsistema puede faltar; el arranque nunca falla por ello. Lo que esté degradado se declara en el panel de diagnóstico.

6. **La detección de entorno ocurre en cada arranque.** El sistema mira qué binarios, modelos, ZIM y lotes hay realmente presentes, y se ajusta. No confía en lo que dice la configuración sobre lo que debería existir.

7. **Verificación antes de uso.** Todo fichero descargado se verifica por hash antes de darse por bueno. Todo lote se valida contra su manifiesto antes de abrirse. Un lote que no valida se marca como inservible y se excluye de las búsquedas, no se usa a medias.

---

## 7. Modelo de datos

### 7.1 `library.db` (escribible)

```sql
PRAGMA journal_mode = WAL;   -- solo aquí; los lotes no

CREATE TABLE zim_files (
    uuid          TEXT PRIMARY KEY,   -- UUID del catálogo OPDS
    book_name     TEXT NOT NULL,      -- nombre sin fecha, para detectar versiones nuevas
    filename      TEXT NOT NULL,      -- nombre real con fecha
    title         TEXT,
    description   TEXT,
    language      TEXT,
    flavour       TEXT,               -- nopic, maxi, mini...
    tags          TEXT,
    size_bytes    INTEGER,
    sha256        TEXT,
    article_count INTEGER,
    has_fulltext  INTEGER,            -- el ZIM trae índice Xapian o no
    added_at      TEXT,
    status        TEXT                -- present | missing | corrupt
);

CREATE TABLE downloads (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL,      -- zim | vendor
    ref           TEXT NOT NULL,      -- uuid del ZIM, o clave de sources.lock
    filename      TEXT NOT NULL,
    dest_path     TEXT NOT NULL,
    size_bytes    INTEGER,
    sha256        TEXT,
    mirrors       TEXT,               -- JSON, por orden de prioridad
    mirror_index  INTEGER DEFAULT 0,
    bytes_done    INTEGER DEFAULT 0,
    status        TEXT NOT NULL,      -- queued|running|paused|verifying|done|failed
    attempts      INTEGER DEFAULT 0,
    last_error    TEXT,
    started_at    TEXT,
    updated_at    TEXT
);

CREATE TABLE shards (
    id            TEXT PRIMARY KEY,   -- 2026-08-27T1043-a1b2c3
    kind          TEXT NOT NULL,      -- docs | vendor
    path          TEXT NOT NULL,
    doc_count     INTEGER,
    chunk_count   INTEGER,
    embed_model   TEXT,
    embed_dims    INTEGER,
    sealed_at     TEXT,
    status        TEXT,               -- ok | incompatible | corrupt | superseded
    note          TEXT
);

CREATE TABLE ingest_jobs (
    id            INTEGER PRIMARY KEY,
    shard_id      TEXT,
    source_path   TEXT NOT NULL,
    sha256        TEXT,
    pages         INTEGER,
    status        TEXT,               -- pending|extracting|ocr|chunking|embedding|done|failed
    last_error    TEXT,
    updated_at    TEXT
);

CREATE TABLE deep_index_scope (
    source_kind   TEXT NOT NULL,      -- zim | shard
    source_ref    TEXT NOT NULL,
    scope         TEXT NOT NULL,      -- all | curated
    article_list  TEXT,               -- ruta a fichero de títulos, si scope=curated
    PRIMARY KEY (source_kind, source_ref)
);

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE llama_profiles (
    name       TEXT PRIMARY KEY,
    args       TEXT NOT NULL,         -- JSON del formulario
    raw_cmd    TEXT,                  -- línea cruda, si el perfil es de tipo crudo
    mode       TEXT NOT NULL,         -- form | raw
    is_default INTEGER DEFAULT 0,
    last_good  INTEGER DEFAULT 0,     -- arrancó y respondió /health
    created_at TEXT
);
CREATE TABLE events (
    id INTEGER PRIMARY KEY, ts TEXT, level TEXT, area TEXT, message TEXT, detail TEXT
);
```

### 7.2 `text.sqlite` de un lote (solo lectura tras sellar)

```sql
CREATE TABLE documents (
    doc_id      TEXT PRIMARY KEY,     -- sha256 del original
    title       TEXT,
    orig_name   TEXT,
    orig_path   TEXT,                 -- relativo a <raiz>
    mime        TEXT,
    pages       INTEGER,
    imported_at TEXT,
    meta        TEXT                  -- JSON: autor, año, lo que se pueda extraer
);

CREATE TABLE chunks (
    chunk_id    INTEGER PRIMARY KEY,
    doc_id      TEXT NOT NULL,
    anchor      TEXT NOT NULL,        -- p:0007:03  (página, bloque)
    page        INTEGER,
    ord         INTEGER,              -- orden dentro del documento
    text        TEXT NOT NULL,
    n_tokens    INTEGER
);
CREATE INDEX idx_chunks_doc ON chunks(doc_id, ord);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
    text, content='chunks', content_rowid='chunk_id', tokenize='unicode61 remove_diacritics 2'
);
```

### 7.3 `manifest.json` de un lote

```json
{
  "shard_id": "2026-08-27T1043-a1b2c3",
  "schema_version": 1,
  "kind": "docs",
  "sealed_at": "2026-08-27T10:43:12Z",
  "embed_model": "multilingual-e5-base",
  "embed_model_sha256": "…",
  "embed_dims": 768,
  "embed_prefix_query": "query: ",
  "embed_prefix_passage": "passage: ",
  "chunk_count": 18432,
  "doc_count": 214,
  "files": { "text.sqlite": "sha256:…", "vectors.i8": "sha256:…", "vectors.ids": "sha256:…" }
}
```

**El campo `embed_model` es crítico.** Si cambia el modelo de embeddings, los lotes antiguos no dan error: dan resultados plausibles y equivocados. Al abrir un lote cuyo `embed_model` o `embed_dims` no coincide con el activo, márcalo `incompatible`, exclúyelo de la búsqueda semántica (sigue sirviendo para la búsqueda por palabras) y ofrece reindexarlo en el dashboard. Este es el peor modo de fallo del sistema entero, porque es silencioso.

### 7.4 `vectors.i8`

`numpy.memmap` de forma `(n_chunks, dims)` en `int8`, más `vectors.ids` con los `chunk_id` en `int64` en el mismo orden, más las escalas de cuantización guardadas en el manifiesto o en un tercer fichero pequeño. Vectores normalizados L2 antes de cuantizar, para que el producto escalar sea la similitud coseno.

Búsqueda: producto matricial de la consulta contra el bloque entero con numpy. Con 500.000 fragmentos a 768 dimensiones son 384 MB, cabe en memoria y responde por debajo de 200 ms. Si la biblioteca crece más, se buscan los lotes por separado y se fusiona; nunca se construye un índice global.

---

## 8. Hito 1 — Descargador y vendorizado

Sin contenido no hay nada que probar. Este hito es primero.

### 8.1 Catálogo Kiwix

El catálogo se consulta **en el momento** y la URL se resuelve entonces. Los nombres de fichero llevan fecha y caducan; nunca guardes una URL de descarga en el código ni en la configuración.

- Endpoint: `https://library.kiwix.org/catalog/v2/entries?count=-1`
- Filtros admitidos, combinables: `lang` (código de tres letras, admite lista separada por comas), `category`, `q`, `name`, `start`, `count`.
- La respuesta es un feed Atom OPDS. Parsear con `ElementTree`. Espacios de nombres relevantes: Atom, OPDS (`http://opds-spec.org/2010/catalog`) y Kiwix (`http://www.kiwix.org/opds/`), del que salen `name`, `flavour`, `tags` y `articleCount`.
- De cada entrada interesa: `id` (UUID), `title`, `summary`, `language`, `name`, `flavour`, `tags`, `articleCount`, y el enlace de adquisición, cuyo `href` apunta a un fichero `.meta4`.

### 8.2 Resolución y descarga

1. Descargar el `.meta4` añadiendo `?country=ES` para que priorice espejos cercanos.
2. Parsear: `<size>`, `<hash type="sha-256">` y la lista de `<url priority="N">`. Guardar las URLs ordenadas por prioridad.
3. Descargar a `<destino>.part` con cabecera `Range: bytes=<bytes_done>-`, comprobando que el servidor responde `206`. Si responde `200`, empieza de cero y trunca el `.part`.
4. Fallo de un espejo: pasar al siguiente de la lista sobre el **mismo** `.part`, sin reiniciar. Reintentos con espera creciente, tope configurable.
5. Al completar, calcular el sha256 leyendo el fichero entero y comparar con el del meta4. No intentes mantener el estado del hasher entre reanudaciones; `hashlib` no lo permite y una relectura de 100 GB cuesta un par de minutos, que es aceptable frente a la complejidad de la alternativa.
6. Si coincide: `os.replace()` del `.part` al nombre final, y escribir un fichero `.sha256` al lado para poder reverificar sin red.
7. Si no coincide: marcar `failed`, conservar el `.part` y registrar el evento. No borrar nada automáticamente.
8. Todo el progreso se persiste en `downloads` con actualizaciones periódicas, no solo al final. Un corte de corriente debe dejar `bytes_done` razonablemente cerca de la realidad; al reanudar, el tamaño real del `.part` manda sobre lo que diga la base de datos.

Descargas simultáneas: por defecto dos, configurable. Más espejos en paralelo no ayuda y complica el diagnóstico.

### 8.3 Vendorizado de documentación y del árbol de mantenimiento

El requisito es poder mantener y reconstruir el sistema sin conexión. Documentación y capacidad de reconstruir son dos cosas distintas y ambas hacen falta: tener la documentación de una librería sin su código fuente ni su instalador te deja mirando la descripción de una pieza que ya no puedes fabricar.

**Nivel A, lo que ya viaja en ZIM.** Kiwix publica ZIMs de DevDocs bajo `/zim/devdocs/`, uno por tecnología. Entran por el mismo descargador, sin maquinaria nueva. Mínimo: Python, SQLite, HTML, CSS, JavaScript, HTTP. Más ArchWiki y el ZIM de Stack Overflow, que es donde están los errores concretos con su solución.

**Nivel B, fuentes upstream fijadas por versión.** Lo que DevDocs no cubre:

- El archivo HTML de la documentación oficial de Python, de la versión exacta instalada.
- El zip de documentación de SQLite.
- **llama.cpp: el repositorio completo al commit del binario en uso.** llama.cpp renombra y retira banderas con frecuencia, así que la única documentación fiable es la de tu commit. Incluye `docs/`, el README del servidor y los ejemplos. Ese commit no se teclea: lo fija la puesta en marcha automática de §13.0 a partir de la release de la que salió el binario. El paquete descargado se queda en `vendor/llama.cpp/` con su `.sha256`, así que el motor se puede volver a desplegar sin red.
- **El volcado de `llama-server --help` a fichero, hecho en el momento de la instalación.** Describe tu binario, no el de la web. Es la pieza de documentación más valiosa de todo el conjunto y la única que nadie más puede darte.
- Documentación de FastAPI, uvicorn, libzim, PyMuPDF y numpy en HTML, desde su fuente oficial.

**Nivel C, el árbol vendorizado.** Un `wheelhouse/` generado con `pip download` de todo el árbol de dependencias, **incluyendo los sdists**, porque el código fuente es la documentación de último recurso. Más las fuentes de llama.cpp, el paquete del binario tal y como se descargó y los GGUF de los modelos. Con eso se reinstala el sistema entero en una máquina limpia sin red.

**`sources.lock`**, en la raíz del repositorio, una entrada por pieza:

```json
{
  "key": "llama.cpp",
  "version": "b4021",
  "kind": "git",
  "url": "https://github.com/ggml-org/llama.cpp",
  "commit": "…40 hex…",
  "sha256": "…del tarball resultante…",
  "license": "MIT",
  "captured_at": "2026-08-27",
  "notes": "compilado con CUDA; ver build.log"
}
```

Comandos:

- `arca vendor` descarga todo lo del lock, verifica hashes, y sella un lote `vendor/<fecha>` con el texto extraído e indexado, igual que cualquier otro lote.
- `arca vendor --verify` recomprueba hashes contra el lock **sin tocar la red**. Debe funcionar con el cable desenchufado.
- `arca vendor --check-drift` compara la versión instalada de cada dependencia con la versión documentada en el lock.

El lote vendorizado **siempre** entra en el índice semántico profundo, sin preguntar. Es poco voluminoso y es justo lo que vas a consultar con el sistema roto y sin red.

### 8.4 Criterios de aceptación del hito 1

- `arca search wikipedia --lang spa` lista resultados reales del catálogo remoto.
- `arca add <uuid>` encola, `arca run` descarga con progreso visible.
- Matar el proceso al 40% y relanzar reanuda desde donde iba, sin volver a bajar lo ya bajado.
- Corromper un byte del fichero final y ejecutar `arca verify` lo detecta y lo marca.
- Desconectar la red a mitad de descarga y reconectar: pasa al siguiente espejo y termina.
- `arca vendor --verify` funciona sin red.

---

## 9. Hito 2 — Buscador y visor

Sin modelo. Al terminar este hito el sistema ya resuelve casos reales por sí solo, y eso hay que poder demostrarlo.

### 9.1 Lectura de ZIM

Usar `libzim` directamente: un solo proceso, sin binarios por plataforma. La búsqueda por palabras aprovecha el índice Xapian que el ZIM ya trae dentro, que es la razón de que buscar sobre todo el corpus sea instantáneo sin haber indexado nada.

- Un ZIM sin índice de texto completo (`has_fulltext = 0`) solo admite búsqueda por título. Decláralo en el dashboard en vez de fingir que busca.
- Servir artículos por `/zim/{uuid}/{path}`. Reescribir enlaces internos con una etiqueta `<base>` más un resolutor de rutas. Los recursos (CSS, imágenes) salen del propio ZIM por la misma ruta.
- Si el renderizado de algún ZIM concreto resulta demasiado problemático, la salida de emergencia es lanzar `kiwix-serve` como subproceso y hacer de proxy. No es el camino por defecto porque ata el proyecto a un binario por plataforma.

### 9.2 Los dos niveles de búsqueda

| Nivel | Alcance | Motor | Granularidad |
|---|---|---|---|
| Por palabras | Todo el corpus | Xapian interno de cada ZIM + FTS5 de los lotes | Artículo o documento |
| Semántica | Subconjunto elegido | Vectores del lote | Párrafo |

La diferencia es deliberada. Indexar en profundidad todo el corpus costaría días de cómputo para poca ganancia: los artículos de Wikipedia ya se encuentran bien por palabras.

### 9.3 Fusión de resultados

**Los scores de Xapian y los de FTS5 no son comparables.** No los normalices ni inventes una fórmula que los mezcle. En lugar de eso, intercala por fuente usando el rango dentro de cada fuente, y muestra el origen en cada resultado. Es honesto, no requiere calibrar nada y el usuario entiende lo que está viendo.

Cuando hay resultados semánticos, van en una franja aparte, etiquetada, con el párrafo exacto citado. No se mezclan con los de palabras.

### 9.4 Trazabilidad

Todo resultado que venga de un documento propio lleva:

- el fragmento con su ancla `p:0007:03`,
- un enlace al documento consultable, posicionado en ese ancla,
- un enlace al **original intacto**, abierto en la página exacta (`#page=7` con PDF.js).

Ese segundo enlace es lo que te salva cuando la conversión perdió un plano o descuadró una tabla. Tiene que estar a un clic desde cualquier resultado, no escondido en un menú.

Al seleccionar texto en el visor, el sistema conserva no solo qué se ha seleccionado sino de dónde sale: documento, ancla, página. Esa procedencia viaja con la selección hasta el chat en el hito 4.

### 9.5 Criterios de aceptación del hito 2

- Buscar un término y obtener resultados de al menos tres ZIM distintos, cada uno etiquetado con su origen.
- Abrir un artículo de Wikipedia con sus estilos e imágenes internas, y navegar por sus enlaces internos sin salir de la aplicación.
- Desconectar el disco externo durante una búsqueda: el sistema da un error claro y no corrompe nada.
- Comprobar con `lsof` o equivalente que no se ha creado ningún fichero bajo la raíz durante una sesión completa de consulta.

---

## 10. Hito 3 — Ingesta de documentos propios

### 10.1 Flujo

1. Recibir el PDF. Calcular sha256. Si ya existe, no reimportar.
2. Guardar el original en `docs/originals/<sha256>.pdf`. **Nunca se modifica después.**
3. Extraer texto y bloques con PyMuPDF. Si una página no tiene capa de texto, pasarla por OCR con `ocrmypdf` si está disponible; si no lo está, registrar la página como no indexable y seguir. No abortes la importación por eso.
4. Generar la versión consultable en HTML, con un ancla permanente por bloque: `<span id="p:0007:03">`. Página y bloque, cero-rellenados. Estable entre reimportaciones del mismo fichero.
5. Trocear respetando límites de párrafo, nunca a media frase. Objetivo de 300-500 tokens por fragmento, solapamiento de un párrafo.
6. Embeber con `passage: ` delante de cada fragmento.
7. Sellar el lote: escribir `manifest.json` con los hashes, poner `text.sqlite` en solo lectura, registrar en `shards`.

### 10.2 Reanudabilidad

El estado por documento vive en `ingest_jobs`. Un corte a mitad de importar 400 documentos cuesta repetir **uno**: el que estaba en curso. Al relanzar, los `done` se saltan.

Un lote se sella solo cuando todos sus trabajos han terminado. Si el proceso muere antes de sellar, al relanzar se reanuda sobre el mismo directorio de lote, que hasta el sellado se considera provisional y no participa en las búsquedas.

### 10.3 Criterios de aceptación del hito 3

- Importar 50 PDF, matar el proceso a mitad, relanzar: termina y solo repite el documento en curso.
- Un resultado de búsqueda semántica lleva al párrafo exacto, no al documento.
- El enlace al original abre el PDF en la página correcta.
- El sha256 del original es idéntico antes y después de todo el proceso.
- Copiar la carpeta entera a otra máquina: busca sin reconstruir nada.

---

## 11. Hito 4 — Chat

El último, cuando todo lo que necesita citar ya existe.

### 11.1 Qué ve el modelo

El estado de la pantalla, en forma estructurada y compacta, nunca como HTML crudo:

```json
{
  "query": "…",
  "results": [{"title": "…", "source": "…", "snippet": "…"}],
  "open_document": {"doc_id": "…", "source_kind": "zim|doc", "source_id": "…", "path": "…", "title": "…", "page": 7},
  "selection": {"text": "…", "source_kind": "zim|doc", "source_id": "…", "path": "…", "doc_id": "…", "anchor": "p:0007:03"}
}
```

La procedencia va en ambos porque **lo señalado y lo abierto pueden ser un artículo de ZIM, no solo un documento importado**: sin `source_kind` y `source_id` el asistente no puede hablar de lo que tienes delante. Cuando hay una página abierta, su texto entra como evidencia con su procedencia, acotado; es lo que permite preguntar «¿de qué va esto?» sin señalar nada.

### 11.2 Política de contexto

**Deliberadamente corto.** Tope duro: 5 fragmentos, unos 1500 tokens de evidencia. En un equipo modesto, meter mucho texto no mejora la respuesta y multiplica la espera. Pocos fragmentos bien elegidos, no muchos mediocres.

El modelo puede buscar por su cuenta si le falta información, mediante una herramienta `search(query, scope)`, con un máximo de **dos** llamadas por turno. Pasado ese tope, responde con lo que tenga o dice que no lo tiene.

### 11.3 Citación

Cada fragmento entra al contexto etiquetado con su procedencia (fuente, documento, ancla). La respuesta cita con marcadores que la interfaz convierte en enlaces al párrafo exacto.

**En temas de riesgo, salud y química señaladamente, la regla es citar la fuente o decir que no la tiene. Nunca improvisar.** Esto se refuerza en el prompt de sistema y además se comprueba en la interfaz: una respuesta sin ninguna cita en un tema marcado como de riesgo se muestra con un aviso visible.

**Fuera de esos temas, el asistente sigue siendo un asistente.** Decidido el 2026-08-29, corrigiendo una implementación que se había pasado de estricta: cuando la biblioteca no devuelve nada, el modelo **responde igual con su propio conocimiento** en vez de callarse. Perder eso era perder la mitad de la herramienta: ayuda aunque el archivo no cubra el tema. Lo que no puede hacer, nunca, es presentar lo que sabe él como si viniera del archivo. Por eso hay dos modos visibles:

| Modo | Cuándo | Cómo se ve |
|---|---|---|
| Con respaldo | La evidencia sostiene la respuesta | Citas `[n]` que enlazan al párrafo exacto |
| Sin respaldo | La biblioteca no tenía nada | La respuesta se marca **«sin respaldo del archivo»** |

La distinción la hace el propio sistema, no el modelo: una respuesta sin ninguna cita se marca como sin respaldo, la diga el modelo o no.

### 11.4 Criterios de aceptación del hito 4

- Preguntar algo que está en el corpus: responde citando, y las citas llevan al párrafo correcto.
- Preguntar algo que no está: responde con su propio conocimiento **marcado como sin respaldo del archivo**, sin inventar una cita ni atribuirle nada a la biblioteca.
- Apagar `llama-server` a mitad de conversación: la interfaz lo indica, la búsqueda sigue funcionando.
- Borrar el GGUF y reiniciar: el sistema arranca, el panel de diagnóstico lo marca en rojo, todo lo demás funciona.

---

## 12. Dashboard de administración

Sin login. Una sección por área, con estado real y no supuesto.

**12.1 Estado general.** Espacio libre y ocupado por categoría, número de ZIM, lotes, documentos y fragmentos, subsistemas activos y degradados, últimos veinte eventos.

**12.2 Catálogo y descargas.** Buscador del catálogo remoto con los filtros de OPDS (idioma, categoría, texto, variante). Cada resultado ofrece **«Leer en línea»**, que abre esa colección en el lector de la propia biblioteca de Kiwix, en una pestaña nueva y fuera de ARCA, para poder mirar qué trae antes de gastar disco. No descarga nada, necesita conexión y desaparece de utilidad en cuanto la colección está en la biblioteca, donde ya se lee sin conexión. La URL se construye en el servidor a partir del `book_name` del catálogo y viaja en el JSON: ningún estático de la interfaz contiene una dirección externa. Cola de descargas con progreso, velocidad, espejo en uso, pausar, reanudar, cancelar, reintentar. Detección de versiones nuevas comparando `book_name` contra el catálogo. Reverificación de hashes de la biblioteca local, con y sin red.

**12.3 Contenido local.** Lista de ZIM con tamaño, idioma, sabor, número de artículos, si trae índice de texto completo, y estado. Activar y desactivar un ZIM para las búsquedas sin borrarlo.

**12.4 Documentos e importación.** Subida, cola de importación con estado por documento, lotes sellados con su fecha, número de documentos, modelo de embeddings y estado de validación. Acción de reindexar un lote. Acceso directo a los originales.

**12.5 Alcance del índice profundo.** La interfaz de `deep_index_scope`. Por cada fuente: fuera, completa, o lista curada. Para la lista curada, cargar un fichero de títulos. Muestra una estimación de fragmentos, tiempo de cómputo y espacio antes de lanzar. Esta pantalla determina qué preguntas se responden con precisión de párrafo y cuáles solo a nivel de artículo, así que explícalo con esas palabras en la propia pantalla.

**12.6 Modelos.** GGUF presentes con arquitectura, cuantización, tamaño y `n_ctx_train` leídos de la cabecera del propio fichero. Selección del modelo de chat y del de embeddings. Aviso si el modelo de embeddings activo no coincide con el de algún lote sellado. **Buscador de modelos:** con red, busca modelos GGUF en el hub configurado, muestra sus variantes con cuantización, tamaño y hash, y descarga la elegida por la misma cola reanudable y verificada que los ZIM. Un modelo con licencia que hay que aceptar se marca como tal y necesita un token en `hub_token`.

**12.7 llama.cpp.** Sección 13, completa.

**12.8 Chat.** Tope de fragmentos, tope de tokens de evidencia, máximo de llamadas a herramienta, prompt de sistema editable, temas marcados como de riesgo, parámetros de muestreo por defecto.

**12.9 Búsqueda.** Resultados por fuente, pesos de intercalado, longitud del extracto, tamaño y solapamiento del troceado para futuras importaciones.

**12.10 Vendorizado y documentación.** Contenido del `sources.lock`, versión capturada frente a versión instalada, botones para verificar sin red y para regenerar el lote.

**12.11 Diagnóstico.** Sección 14.

**12.13 Manual.** El manual del proyecto, servido desde la propia aplicación con un índice de páginas y su contenido renderizado. Sin red, como todo lo demás.

**12.12 Mantenimiento.** Rutas, ubicación del temporal, rotación de logs, exportar e importar configuración, y un botón de verificación integral que recorre hashes de ZIM, manifiestos de lotes y consistencia de `library.db`.

### Regla de la interfaz de usuario final

El usuario configura cuatro cosas, no cuarenta: **la ruta de la biblioteca, el modelo de chat, qué colecciones se buscan, y qué entra en el índice profundo.** Todo lo demás vive en el dashboard de administración, que es otra pantalla y otro público. No mezcles los dos.

---

## 13. Sección de administración de llama.cpp

`llama-server` se gestiona como subproceso: arrancar, parar, reiniciar, ver el log en vivo, ver la línea de comandos efectiva.

### 13.0 Puesta en marcha automática

Conseguir el motor no es trabajo del usuario. El `.gguf` son pesos; `llama-server` es el programa que los carga. Lo único que se le pide a la persona es dejar el modelo en `models/chat/`; a partir de ahí ARCA hace sola todo lo demás:

1. **Resolver la release en el momento.** Se consulta la lista de releases publicadas de llama.cpp cuando hace falta, nunca una URL guardada (trampa 5), y se toma **la primera que publique un paquete para esta plataforma**. No vale preguntar por «la última»: llama.cpp publica sus builds como *prereleases*, así que el endpoint de «latest» devuelve una release de versión sin binarios. El nombre del paquete decide plataforma y backend, y descarta lo que no lleva servidor dentro (el runtime de CUDA, los `xcframework`, los backends que no se conducen); no hay una tabla de URLs incrustada.
2. **Descargar por la cola que ya existe.** El paquete entra en la misma cola reanudable, con espejo y verificación de hash, que los ZIM, y se ve en la pestaña Descargas como una descarga más. Sin hash publicado se aplica el camino de la trampa 6: se avisa y se guarda el calculado.
3. **Desplegar en `vendor/llama.cpp/`**, que es donde el descubrimiento del binario ya mira. El desempaquetado rechaza cualquier miembro que se salga del destino.
4. **Crear el perfil por defecto** a partir del modelo: `--model`, `--ctx-size 8192`, `--cache-type-k/-v q8_0` (§2), host y puerto de `chat_url`, y la plantilla del propio GGUF, que `llama-server` toma de la cabecera sin que haya que pasarla.
5. **Probarlo con la secuencia de §13.3.** Si arranca y genera, queda `last_good` y activo. Si no, ese paquete se descarta y se prueba el siguiente.
5b. **Levantar también el servidor de embeddings**, con el mismo binario y el `.gguf` de `models/embed/`, en el puerto de `embed_url`. Su prueba no puede ser la del chat —un modelo de embeddings no conversa—: arranca, responde a `/health` y devuelve un vector con las dimensiones esperadas. Sin él no se pueden sellar lotes ni hay búsqueda semántica, así que tampoco esto es trabajo del usuario.
6. **Elegir el backend sin adivinar.** Los candidatos se prueban en orden, GPU primero y CPU como último recurso, y se queda el primero que supera el arranque de prueba. Es la maquinaria de reversión de §13.3 generalizada, no una heurística nueva. El paquete que falla se borra del disco: solo queda el que funciona. El orden entre backends de GPU lo decide una evidencia, no una corazonada: con driver NVIDIA presente manda CUDA —y su runtime viaja con él, porque el paquete no lo trae—; sin él manda Vulkan, que corre en cualquier GPU sin runtime externo y pesa una décima parte.
7. **Fijar el commit en `sources.lock`** y capturar `--help` y `--version` del binario recién desplegado (§8.3).

Restricciones que no se negocian: **nunca bloquea el arranque** (invariante 5), así que corre en segundo plano con su estado visible en el panel; sin red se queda esperando y lo dice, con el resto de la biblioteca intacta; y todo lo que descarga queda vendorizado y verificable sin red. Si la release no trae paquete para la plataforma, se dice claramente y queda el camino manual de siempre: compilar y apuntar `llama_bin` en `arca.json`.

### 13.1 El formulario se genera del binario, no del código

Ejecuta `llama-server --help` al arrancar y **construye el formulario a partir de su salida**. No incruste una lista de banderas en el código fuente: llama.cpp añade, renombra y retira opciones entre versiones, y una lista incrustada empieza a mentir el día que actualizas. Sobre esa lista generada, aplica una capa curada que agrupa y explica las opciones habituales:

- **Modelo:** `--model`, `--alias`, `--mmproj`, `--lora`, `--lora-scaled`, `--control-vector`
- **Contexto y memoria:** `--ctx-size`, `--n-predict`, `--keep`, `--cache-type-k`, `--cache-type-v`, `--defrag-thold`, `--no-context-shift`, `--cache-reuse`
- **Cómputo:** `--threads`, `--threads-batch`, `--batch-size`, `--ubatch-size`, `--flash-attn`
- **GPU:** `--n-gpu-layers`, `--split-mode`, `--tensor-split`, `--main-gpu`, `--device`
- **Memoria del sistema:** `--mlock`, `--no-mmap`, `--numa`
- **RoPE y contexto extendido:** `--rope-scaling`, `--rope-freq-base`, `--rope-freq-scale`, familia `--yarn-*`
- **Concurrencia:** `--parallel`, `--cont-batching`, `--threads-http`
- **Plantilla:** `--chat-template`, `--chat-template-file`, `--jinja`
- **Servidor:** `--host`, `--port`, `--path`, `--timeout`, `--metrics`, `--slots`, `--props`, `--no-webui`
- **Embeddings:** `--embeddings`, `--pooling`
- **Registro:** `--verbose`, `--log-file`

Muestra también las opciones que el `--help` reporta y la capa curada no conoce, en un grupo "otras", en vez de ocultarlas.

Muestreo (temperatura, top-k, top-p, min-p, penalizaciones de repetición, DRY, XTC, mirostat, semilla, gramática, esquema JSON, secuencias de parada) va **por petición**, no en la línea de arranque. Configúralo en la sección de chat, no aquí.

### 13.2 Modo crudo

Un campo de texto libre con el `argv` completo. **Sustituye al formulario, no se mezcla con él.** Mezclar los dos produce combinaciones que nadie sabe explicar cuando fallan. El perfil guarda cuál de los dos modos usa.

En ambos modos, muestra siempre la línea de comandos efectiva antes de arrancar.

### 13.3 Arranque de prueba con reversión

Esto es lo que hace la sección fiable, y no es opcional:

1. Guardar el perfil candidato.
2. Arrancar con él y esperar a `/health` con un tiempo límite de 60 segundos.
3. Si responde, lanzar una generación de prueba corta.
4. Si todo va bien, marcar el perfil como `last_good` y dejarlo activo.
5. Si falla o expira el plazo, matar el proceso, **volver automáticamente al último perfil que funcionó** y mostrar el log del intento fallido.

Sin esto, una configuración mala deja el chat muerto sin explicación, que es exactamente el escenario que este proyecto existe para evitar.

### 13.4 Validaciones previas

Leídas de la cabecera del GGUF y del entorno, mostradas antes de arrancar:

- Contexto pedido mayor que `n_ctx_train` del modelo: aviso, no bloqueo.
- Estimación de memoria (pesos + KV cache según contexto, capas en GPU y tipo de caché) por encima de la VRAM o la RAM disponibles: aviso con el número concreto.
- `--n-gpu-layers` mayor que el número de capas del modelo: se acepta, es la forma habitual de decir "todas", pero indícalo.
- Binario compilado sin soporte de GPU y `--n-gpu-layers` mayor que cero: aviso explícito, porque el síntoma (lentitud extrema) no apunta a la causa.

---

## 14. Diagnóstico

Una lista de comprobaciones con tres estados: correcto, degradado, roto. Cada una dice qué falla, qué deja de funcionar por ello y qué hacer.

| Comprobación | Qué mira |
|---|---|
| Ruta de la biblioteca | Existe, es legible, y si toca escribir, es escribible |
| Espacio libre | Umbral configurable |
| Soporte de solo lectura | Detecta si el volumen está montado en solo lectura y ajusta la interfaz en vez de fallar al escribir |
| Integridad de ZIM | Presencia y hash contra el `.sha256` guardado |
| Índice de texto completo | Cuáles de los ZIM lo traen y cuáles no |
| Manifiestos de lotes | Hashes de ficheros contra el manifiesto |
| **Coherencia de embeddings** | `embed_model` y `embed_dims` de cada lote contra el modelo activo |
| Servidor de embeddings | Responde, y devuelve el número de dimensiones esperado |
| `llama-server` | Binario presente, versión, proceso vivo, `/health` |
| Modelo de chat | GGUF presente, cabecera legible, cabe en memoria |
| Deriva de documentación | Versión instalada de cada dependencia contra `sources.lock` |
| Dependencia sin documentar | Paquete instalado sin entrada en el lock |
| Descargas incompletas | Ficheros `.part` huérfanos |
| Lotes provisionales | Directorios de lote sin sellar de una importación interrumpida |
| Reloj | Desviación grande de la fecha del sistema, que ensucia los identificadores de lote |

---

## 15. API HTTP

```
GET  /                          interfaz principal (buscador + visor + chat)
GET  /api/search?q=&scope=&mode=      mode: keyword | semantic | both
GET  /api/doc/{doc_id}                versión consultable
GET  /api/doc/{doc_id}/original       original intacto
GET  /zim/{uuid}/{path...}            artículo o recurso del ZIM
POST /api/chat                        mensaje + estado de pantalla
GET  /api/diagnostics
GET  /admin                           dashboard
GET  /api/admin/catalog?…             búsqueda en el catálogo remoto
POST /api/admin/downloads             encolar
POST /api/admin/downloads/{id}/{action}   pause | resume | cancel | retry
POST /api/admin/ingest                subir documentos
GET  /api/admin/shards
POST /api/admin/shards/{id}/reindex
GET  /api/admin/llama/help            opciones del binario, parseadas
GET  /api/admin/llama/profiles
POST /api/admin/llama/profiles/{name}/test   arranque de prueba con reversión
POST /api/admin/llama/{action}        start | stop | restart
GET  /api/admin/vendor
POST /api/admin/vendor/verify
GET  /api/admin/settings
PUT  /api/admin/settings
GET  /api/admin/hub?q=               búsqueda de modelos en el hub configurado
GET  /api/admin/hub/{model_id}       variantes y cuantizaciones de un modelo
POST /api/admin/hub/download         encolar una cuantización concreta
GET  /api/admin/maintenance          rutas, registro y rotación
POST /api/admin/maintenance/verify   verificación integral elemento a elemento
GET  /api/manual                     índice del manual
GET  /api/manual/{page}              página del manual renderizada
```

---

## 16. Dirección visual

La pantalla principal se parte en tres columnas fijas: resultados a la izquierda, lectura en el centro, asistente a la derecha. El archivo es el que manda: la búsqueda y sus resultados nunca desaparecen al abrir un documento, y el centro es una superficie de lectura, no un marco encajonado. El chat es un panel, no la protagonista. Por debajo de 64 rem las tres columnas se apilan en ese mismo orden.

La cabecera es una sola fila de 50 px con la identidad, el conmutador de zona, el estado de los subsistemas, el tema y el idioma. En administración se le añade una tira de pestañas de 34 px que solo lista las de la familia activa y se desplaza en horizontal cuando no caben, sin partirse en varias líneas ni crecer en alto.

**Sujeto.** Un archivo consultable, no un producto de IA. La referencia visual es el catálogo de fichas de una biblioteca técnica y el panel de instrumentos de un equipo que debe seguir funcionando cuando falla todo lo demás. Nada de estética de asistente conversacional.

**Paleta.** Interfaz de emergencias: manual de defensa civil, no producto de consumo. Dos temas completos, claro por defecto, elegibles por el usuario y recordados en el navegador; sin elección explícita se sigue `prefers-color-scheme`. Todos los pares de texto y fondo listados cumplen AA (4,5:1) en ambos temas.

| Papel | Claro | Oscuro |
| --- | --- | --- |
| Fondo (`--base`) | `#d7d3c8` | `#0f0e0d` |
| Superficie (`--paper`) | `#e6e2d8` | `#1a1917` |
| Tarjeta (`--card`) | `#f4f2ec` | `#211f1d` |
| Papel de lectura (`--reader`) | `#f7f5ef` | `#1c1b19` |
| Tinta (`--ink`) | `#171512` | `#ece8df` |
| Tinta suave (`--ink-soft`) | `#5b564d` | `#a49d90` |
| Borde (`--line`) | `#c6c0b2` | `#35322d` |

| Señal | Claro | Oscuro | Uso |
| --- | --- | --- | --- |
| `--accent` | `#b04408` | `#e8763a` | acción principal, foco, resultado abierto |
| `--red` | `#a1160f` | `#f0685c` | destructivo y roto |
| `--amber` | `#8a5a06` | `#d9a63f` | degradado, avisos |
| `--green` | `#23663a` | `#5fb377` | correcto, arrancar |
| `--info` | `#1f4f6b` | `#6aaed0` | informativo, sin urgencia |

El naranja de señalización es la única fuente de color de la acción: no se reparte por adorno. El verde no significa «marca», significa «esto ya está bien».

**Temperatura.** El conjunto es cálido a propósito, con la saturación baja de un impreso: hormigón y hueso en claro, carbón en oscuro. Lo que no vale es el degradado decorativo, la tarjeta con esquina muy redondeada y filete de color a la izquierda por defecto, ni el acento pastel: eso es el aspecto por defecto de la interfaz generada por IA y aquí no significa nada.

**Tipografía.** Display: una grotesca condensada, Archivo Narrow. Cuerpo de lectura: una serif de pantalla, Source Serif 4, porque aquí se leen artículos largos. Datos, hashes, tamaños, banderas y rutas: IBM Plex Mono. **Las tres se descargan como WOFF2 y se sirven desde `static/fonts/`.** Enlazar Google Fonts en un sistema offline es un fallo de diseño, no un detalle.

**Elemento distintivo: el sello de procedencia.** Cada resultado y cada cita lleva una franja monoespaciada con su origen: colección, documento, ancla, fecha del lote. En un archivo, saber de dónde sale algo es tan importante como el contenido, y es lo que separa este proyecto de un chatbot. Ese sello es también lo que viaja al chat cuando seleccionas texto. Gasta ahí la personalidad del diseño y mantén todo lo demás callado.

**Estados vacíos y errores.** Sin colecciones descargadas, la pantalla principal invita a descargar y enlaza al catálogo. Sin modelo, el panel de chat explica que la búsqueda funciona igual. Los errores dicen qué pasó y qué hacer, sin disculpas ni vaguedades.

**Suelo de calidad:** responsive hasta móvil, foco de teclado visible, movimiento reducido respetado, contraste suficiente para leer una hora seguida.

---

## 17. Trampas conocidas

Doce cosas que van a salir mal si nadie las escribe.

1. **Modelo de embeddings cambiado sin reindexar.** No da error, da resultados plausibles y falsos. Comprobación obligatoria contra el manifiesto (7.3).
2. **Olvidar los prefijos de E5.** `query: ` y `passage: `. Sin ellos la calidad cae mucho y de forma poco evidente.
3. **Mezclar scores de Xapian y FTS5.** No son comparables. Intercalar por rango (9.3).
4. **WAL en el soporte de consulta.** Crea `-wal` y `-shm` y rompe la invariante 1. Lotes con `journal_mode=DELETE` y `query_only`.
5. **URLs de descarga guardadas.** Los nombres llevan fecha y caducan. Resolver siempre en el momento (8.1).
6. **`.meta4` ausente para algún fichero.** Ha pasado históricamente. Ten un camino alternativo: descargar sin hash del meta4, avisar de que no se pudo verificar, y guardar el hash calculado para futuras verificaciones locales.
7. **Servidor que ignora `Range` y responde `200`.** Comprueba el código; si es `200`, trunca y empieza de cero.
8. **Contexto mayor que `n_ctx_train`.** Genera texto degradado sin avisar. Valídalo (13.4).
9. **Binario de llama.cpp sin GPU con `-ngl` alto.** El síntoma es lentitud, y nadie sospecha de la causa.
10. **Nombres de fichero de lote basados en la fecha con el reloj mal.** Los identificadores se desordenan. Comprueba el reloj y usa además un sufijo aleatorio.
11. **PDF sin capa de texto importados en silencio.** Se indexan como vacíos y desaparecen de las búsquedas sin que nadie lo note. Márcalos y muéstralos en el dashboard.
12. **Anclas que cambian al reimportar.** Rompen todas las citas guardadas. La numeración de página y bloque debe ser determinista para el mismo fichero de entrada.

---

## 18. Resumen de criterios de aceptación

El proyecto está terminado cuando todo esto es cierto a la vez:

- Se instala pidiendo una sola ruta.
- Arranca con el disco lleno, vacío, o a medias, y en todos los casos dice en qué estado está.
- Se puede desenchufar el soporte durante una consulta sin corromper nada.
- Una descarga o una importación interrumpidas se reanudan y cuestan repetir, como mucho, un elemento.
- La biblioteca copiada a otra máquina funciona sin reconstruir ningún índice.
- Sin modelo de chat, sin servidor de embeddings, sin ZIM: sigue arrancando, y el diagnóstico explica qué falta y qué se pierde.
- Un resultado semántico lleva al párrafo exacto, y el original está a un clic.
- El chat cita o admite que no sabe, y en temas de riesgo nunca improvisa.
- La documentación de todo lo que usa el proyecto, incluida la del commit exacto de llama.cpp compilado, se consulta sin red desde la propia biblioteca.
- Un cambio de configuración de llama.cpp que no arranca revierte solo al último perfil que funcionaba.
