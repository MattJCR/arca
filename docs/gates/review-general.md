# Revisión general y análisis de interfaz

Fecha: 2026-08-29. Alcance: todo lo desarrollado en la fase 6 (F6.1 errores con código, F6.2 puesta
en marcha automática del motor) más una pasada sobre el producto completo. Checklist aplicado:
`docs/development/review.md`. Registro previo de esta serie: `docs/gates/review-h2.md`.

Este documento no sustituye al manual (`docs/manual/`, que solo describe la realidad actual) ni al
plan (`ARCA-plan.md`, que ordena el trabajo). Recoge **qué se verificó, qué estaba roto, qué se
arregló y qué queda abierto**, con su prioridad.

---

## 1. Qué se verificó, y cómo

No basta con la suite: casi todos los defectos de esta tanda vivían **entre** piezas que sus tests
daban por buenas por separado. La verificación se hizo a tres niveles.

| Nivel | Qué se ejerció | Resultado |
|---|---|---|
| Suite | `scripts/verify.py`: `ruff format`, `ruff check`, `mypy` estricto (56 ficheros), `pytest` | **515 tests en verde** |
| Invariante 1 | `tests/test_invariants.py` tras cada cambio en el camino de consulta | **8/8** |
| Servidor vivo | `arca serve` sobre una biblioteca **vacía**, 16 rutas ejercidas con peticiones reales | **16/16 responden 200** |
| Red real | Resolución de la release de llama.cpp contra `api.github.com` | Elige `b10679`, CUDA 12.4 + su `cudart`, detecta el driver NVIDIA |
| Máquina del operador | `arca serve` con `auto_engine` | El motor se desplegó y quedó fijado en `sources.lock` al commit `50f068f` |

Las tres pruebas contra el servidor vivo que importan, porque cubren lo que la suite no veía:

- `POST /api/chat` → **200** y degradación honesta («No lo tengo: no hay nada en la biblioteca que
  sostenga una respuesta a eso»). Antes de esta revisión devolvía **405**.
- `POST /api/admin/documents/upload` → **200** con el sha256 del documento; un fichero que no es
  PDF se rechaza con **400** y `code = bad_file`.
- `POST /api/admin/documents/index` → declara `embeddings_down` sin tocar nada, que es lo correcto
  cuando no hay servidor de embeddings.

---

## 2. Defectos encontrados y corregidos

Ordenados por gravedad. Los cinco primeros eran fallos que impedían usar una funcionalidad
completa; ninguno lo detectaba la suite.

### 2.1 El chat nunca funcionó desde el navegador

`archive.js` definía `fetchJson(url)` **con un solo parámetro**, y `chat.js` lo llamaba con
`{ method: "POST", body: … }`. Las opciones se descartaban en silencio: la petición salía como GET
contra una ruta que solo acepta POST, y el usuario veía `Method Not Allowed`.

El hito 4 se validó llamando a `/api/chat` directamente y contra un doble del protocolo de
`llama-server`; la página nunca se ejerció. **Corregido**, con un test que exige que toda
definición de `fetchJson` acepte y reenvíe las opciones.

### 2.2 Los documentos importados nunca se indexaban

`ingest_documents` —extracción, anclas, troceado, embeddings y sellado: el corazón del hito 3— **no
lo llamaba nada en el producto**, solo los tests. `arca import` recibe el PDF y escribe su sidecar,
y ahí se acababa el viaje: el documento quedaba guardado y jamás entraba en la búsqueda.

**Corregido** con `POST /api/admin/documents/index`, que toma los originales sin lote y los pasa
por la tubería real. Verificado de punta a punta: subir → indexar → aparece en `/api/search`.

### 2.3 §12.4 pedía «Subida» y no existía

El panel mandaba al usuario a la línea de comandos («Usa «arca import» para añadirlos»), y la
especificación pide subida en el propio panel. **Corregido**: selector de ficheros, subida en crudo
(bytes en el cuerpo, nombre en la query) para **no añadir una dependencia de *multipart*** y
respetar el tope de 5 dependencias directas. El fichero aterriza en el directorio de sesión, fuera
de la raíz, y solo entra por `receive_document`.

### 2.4 La resolución de la release no podía funcionar nunca

`GET /releases/latest` de llama.cpp devuelve `v0.3.0`, cuyo único adjunto es un `nightly-tag.txt`:
las builds con binarios se publican como **prereleases** y ese endpoint las salta. La puesta en
marcha automática no habría descargado nada jamás.

**Corregido**: se recorre la lista de releases y se toma la primera que publique un paquete para
esta plataforma. De paso salieron dos defectos más del mismo sitio:

- Solo se aceptaban `.zip`, y Linux y macOS publican `.tar.gz`: **ambas plataformas se quedaban sin
  ningún candidato**. Ahora se manejan los dos formatos con la misma validación de miembros.
- Los paquetes `cudart-*` pasaban el filtro y se habrían descargado 391 MB para descubrir que no
  llevan servidor; y a la vez **hacían falta**, porque el paquete de CUDA no trae su runtime. Ahora
  se excluyen como candidatos y se despliegan como acompañantes del paquete CUDA que les toca.

### 2.5 El sello atribuía una posición que la página nunca tuvo

Al seguir un enlace interno, la franja de procedencia se reescribía con la ruta nueva pero
conservaba el `#3` del resultado original. Detalle en `docs/gates/review-h2.md`. **Corregido**:
ahora dice «enlace seguido desde #3».

### 2.6 Correcciones menores

| Defecto | Corrección |
|---|---|
| El estado vacío de Documentos seguía mandando a `arca import` | Ahora explica el flujo del panel y menciona la CLI como alternativa |
| Cero regiones `aria-live` en dos pantallas que se reescriben enteras por JS | `aria-live="polite"` en los 25 elementos que el JS reescribe, con test que lo mantiene |
| Cuatro tests dependían del estado de la máquina (lock ya fijado, binario instalado, **un puerto real escuchando**) | Reescritos contra el lock real y contra puertos muertos |
| Un test escribía en el `sources.lock` del repositorio | `tests/test_engine.py` aísla el lock en todas las pruebas que fijan el commit |
| La puesta en marcha reescribía el perfil por defecto en cada arranque | Con binario y perfil `last_good`, no toca nada |

---

## 3. Hallazgos abiertos

Nada de esto rompe el producto hoy. Está ordenado por lo que costaría si se deja.

### Alta

**H1 — Código del producto que solo ejercitan los tests.** Cuatro funciones públicas no las llama
nadie fuera de `tests/`:

| Función | Qué pasa |
|---|---|
| `vectors.merge_hits` | **Duplica una regla viva**: ordenar por `(-score, chunk_id)` y truncar está escrito otra vez, a mano, dentro de `search.search_shards_by_vector`. Si alguien cambia el desempate en un sitio, el otro diverge en silencio — y el que tiene tests es el que no se usa |
| `search.unsearchable` | La misma regla («qué fuente no puede contestar») vive en `renderSources` de `archive.js`. La versión Python está muerta |
| `ingest.readable_html` | Envoltorio sobre `render_readable` para renderizar antes de sellar; el producto renderiza desde el lote |
| `zim.read_info` | Envoltorio de conveniencia sobre `open_archive` + `archive_info` |

El riesgo real no es el peso: es que **la suite mide cobertura sobre código que el producto no
ejecuta**, que es exactamente cómo 2.1 y 2.2 pasaron desapercibidos. Recomendación: usar
`merge_hits` desde `search_shards_by_vector` o borrarlo y mover su cobertura al camino vivo; borrar
los otros tres y reapuntar sus tests.

**H2 — Nada verifica que el camino vivo esté conectado.** Los dos defectos más graves de esta tanda
(2.1 y 2.2) son del mismo tipo: pieza correcta, pieza correcta, y nadie las une. La comprobación
que los habría cazado a los dos es barata y ya está escrita a mano en este documento: *toda función
pública del paquete tiene al menos un llamador fuera de `tests/`*. Candidata clara a test o a hook.

### Media

**H3 — El indexado bloquea la petición.** `POST /api/admin/documents/index` corre `ingest_documents`
en línea: con muchos PDF, la petición dura minutos y el navegador solo ve un mensaje. Es coherente
con `POST /api/admin/vendor/index`, que ya se comporta así, pero ambos deberían pasar al patrón que
ya existe y funciona: hilo en segundo plano con estado consultable, como `EngineBringup`.

**H4 — `sources.lock` del repositorio se escribe en tiempo de ejecución.** Fijar el commit de
llama.cpp modifica un fichero versionado, no algo bajo la raíz de la biblioteca. Funciona y está
documentado, pero significa que **usar el producto ensucia el árbol de trabajo** y que en una
instalación por wheel se escribiría dentro del paquete instalado. Merece una decisión explícita.

**H5 — `python -m arca` no arranca** («'arca' is a package and cannot be directly executed»). El
punto de entrada es el script de consola `arca`. Un `__main__.py` de tres líneas quita una piedra
del camino a cualquiera que pruebe lo obvio.

**H6 — El detalle técnico del motor se muestra en inglés.** El estado de la puesta en marcha está
traducido y es accionable, pero la línea de detalle (`the release feed is unreachable: …`) es el
mensaje crudo. Es evidencia, no interfaz, y así está pensado — pero conviene marcarlo como tal
visualmente para que no se lea como un texto sin traducir.

### Baja

**H7 — Deuda declarada que sigue viva:** citación en español con el modelo real, calidad de la
recuperación con `multilingual-e5-base` real, y validación contra un ZIM real de varios GB. La
primera ya es alcanzable: el motor arranca solo en cuanto hay un `.gguf`.

---

## 4. Análisis de interfaz (UX/UI)

Dos pantallas, dos públicos, y la regla de §12 se respeta: la de consulta tiene cuatro decisiones,
el panel tiene el resto. El análisis se centra en lo que se puede mejorar; lo que ya está bien se
resume primero para no tocarlo por error.

### Lo que ya funciona y no hay que estropear

- **El sello de procedencia** es el acierto de diseño del proyecto: cada resultado dice de dónde
  sale, con qué ruta y en qué posición **dentro de su fuente**, y ahora también admite cuándo has
  llegado siguiendo un enlace. Es lo que hace que el archivo se pueda auditar de un vistazo.
- **Los estados honestos**: `ok` / `all_disabled` / `empty` / `unreachable` / `unreadable` son cinco
  situaciones distintas con cinco mensajes distintos, en vez de un «no hay resultados» que mienta.
- **La degradación**: sin modelo, el panel de asistente explica que buscar y leer no dependen de él,
  y el formulario sigue ahí. Sin red, el panel dice qué se puede seguir haciendo.
- **El suelo de §16 está puesto**: foco visible, `prefers-reduced-motion`, paleta declarada,
  tipografías vendorizadas, tablas con desplazamiento propio y cabecera fija, patrón ARIA de
  pestañas que funciona con teclado y se degrada a secciones apiladas sin JavaScript.

### Mejoras propuestas, por impacto

**U1 — Catorce pestañas en una sola barra.** El panel ha crecido hasta catorce secciones y la barra
envuelve en varias líneas para no ocultar ninguna. Ya no es un menú, es un muro. Agruparlas en tres
familias con la misma maquinaria ARIA —**Contenido** (estado, catálogo, descargas, colecciones,
documentos, alcance), **Modelo y chat** (modelos, llama.cpp, chat, búsqueda), **Sistema**
(dependencias, mantenimiento, diagnóstico, manual)— reduce la primera decisión de catorce a tres.

**U2 — Las operaciones largas no dicen cuánto van a tardar.** «Indexar y sellar», «Poner el chat en
marcha» y la verificación integral pueden tardar minutos. Hoy dejan un mensaje de texto y un botón
deshabilitado. Con el trabajo en segundo plano de H3 se puede mostrar progreso real (documento *n*
de *m*, paquete que se está probando), que es lo que convierte una espera en algo tolerable.

**U3 — La subida no informa por fichero.** Se suben varios PDF en serie y el mensaje se sobrescribe
con el que está en curso; al final solo queda el resumen. Una lista con el resultado por fichero
—importado, duplicado, rechazado y por qué— es más útil y no cuesta más.

**U4 — El panel del asistente compite mal con el archivo.** La columna del asistente es fija
(1 parte contra 2,4) y el historial crece hacia abajo sin altura máxima: en una conversación larga
la página se estira y el archivo queda arriba, fuera de vista. Un `max-height` con desplazamiento
propio en `#chat-log`, igual que ya se hace con las tablas, mantiene las dos columnas ancladas.

**U5 — El estado del motor merece la escala de tres estados.** La tarjeta de puesta en marcha usa
texto plano. El proyecto ya tiene un vocabulario visual de estados (`state-ok`, `state-degraded`,
`state-broken`) usado en diagnóstico y colecciones; aplicarlo aquí hace que se lea de un vistazo
si el chat está listo, esperando o agotado.

**U6 — El visor no indica que está cargando.** Al abrir un artículo grande de un ZIM, el `iframe`
queda en blanco hasta que llega. El sello ya está pintado, así que la página parece rota durante
un segundo. Basta un estado de carga en la franja.

**U7 — Accesibilidad, lo que queda tras las regiones vivas.** Ya se anuncian los cambios de estado.
Falta: mover el foco al visor al abrir un resultado (hoy se hace `focus()` sobre el `iframe`, que
en algunos lectores no anuncia el cambio de contexto) y dar a las tablas ordenables un texto que
diga por qué columna están ordenadas, más allá del `aria-sort`.

---

## 5. Veredicto

**Aprobado con hallazgos abiertos.** Lo desarrollado en la fase 6 funciona y está verificado contra
la red real y contra un servidor vivo, no solo contra dobles. Los cinco defectos graves que
aparecieron en la revisión están corregidos y cada uno tiene ahora un test que impide su regreso.

La lección que deja esta tanda, y que conviene no olvidar: **este proyecto tenía una suite verde
sobre funcionalidades que nunca se habían ejercido enteras**. El chat no funcionaba en el
navegador, los documentos importados no se indexaban y el descargador del motor no podía encontrar
un binario — con 461 tests en verde. Las pruebas de costura (H2) valen aquí más que otro test
unitario.

Trabajo pendiente ordenado en `ARCA-plan.md`, fase 7.
