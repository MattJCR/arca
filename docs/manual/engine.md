# Puesta en marcha automática del chat y de los embeddings

Módulo: `arca/engine.py`. Sección 13.0 de la especificación.

El `.gguf` de `models/chat/` son **pesos**: un fichero de datos que no se ejecuta. `llama-server`
es el **motor** que los carga y los sirve, un binario de llama.cpp que ARCA lanza como subproceso
y que nunca es dependencia del paquete (§2). Tener el modelo sin el motor es tener el ZIM sin el
lector.

Conseguir ese motor no es trabajo del usuario. Lo único que se le pide es dejar los `.gguf` en
`models/chat/` y `models/embed/`; el resto lo hace este módulo, incluidos **los dos servidores**
que ARCA necesita (ver «Los dos servidores» más abajo).

## La secuencia

| Paso | Qué hace | Estado que se ve en el panel |
|---|---|---|
| 1 | Busca un `.gguf` de chat y otro de embeddings | `waiting_model` si no hay **ninguno de los dos** |
| 2 | Si ya hay binario (`llama_bin`, `vendor/llama.cpp/` o `PATH`), salta a la prueba | — |
| 2b | Si además ya hay un perfil `last_good`, **no toca nada**: el chat ya estaba resuelto | `ready` |
| 3 | Resuelve **en el momento** la primera release de llama.cpp con paquete para esta plataforma | `resolving` |
| 4 | Descarga el paquete que toca por la cola reanudable y verificada | `downloading` |
| 5 | Lo desempaqueta en `vendor/llama.cpp/<tag>-<backend>/` | `deploying` |
| 6 | Crea el perfil y lo somete al arranque de prueba de §13.3 | `testing` |
| 7 | Fija el commit en `sources.lock` y captura `--help` y `--version` | `ready` |

Sin red, `no_network`. Si la release no publica paquete para la plataforma, `unsupported`. Si
ninguno supera la prueba, `exhausted`. Cada estado tiene su mensaje en los dos catálogos, y un
test comprueba que ninguno se queda sin él.

## Nunca una URL guardada, y nunca «la última»

`list_releases()` pide la lista de releases publicadas cuando hace falta (trampa 5). No hay
ninguna URL de paquete incrustada en el código: los enlaces salen de la respuesta del momento.

**No se usa `/releases/latest`**, y esto no es un detalle: llama.cpp publica sus builds como
*prereleases*, así que ese endpoint las salta y devuelve una release de versión (`v0.3.0` cuando
se escribió esto) cuyo único adjunto es un `nightly-tag.txt`. Un descargador que confiara en
«latest» no encontraría jamás un binario. `pick_release()` recorre la lista y **se queda con la
primera release que publique un paquete para esta plataforma**, que es la pregunta que de verdad
importa.

El commit sale de `target_commitish` cuando es un SHA de 40 hex — que es lo que traen las builds —
y si trae el nombre de una rama, `_sha()` lo descarta y se resuelve por el tag con
`resolve_commit()`. Una rama no puede colarse como si fuera un commit.

GitHub es, con esto, el **tercer y último origen de red** del proyecto, junto al catálogo de Kiwix
y al hub de modelos.

## Elegir el paquete sin adivinar

`matches_platform()` cruza plataforma y arquitectura por sus etiquetas (`win`/`ubuntu`/`macos` y
`x64`/`arm64`), y `backend_of()` lee el backend del nombre (`cuda`, `hip`, `rocm`, `sycl`,
`vulkan`, `metal`, y `cpu` como caída).

`is_server_package()` descarta lo que **no lleva servidor dentro**, que en una release real es
bastante: los `cudart-*` (solo el runtime de CUDA), `xcframework`, `ui`, y los backends que ARCA
no conduce (`openvino`, `opencl`, `s390x`). Sin ese filtro se descargarían cientos de megas para
descubrir después que no hay binario.

El orden lo da `backend_order()`, y depende de una evidencia, no de una corazonada: si hay
`nvidia-smi` en el `PATH` hay driver NVIDIA, y CUDA va primero; si no lo hay, va primero **Vulkan**,
que es el backend de GPU que funciona en NVIDIA, AMD e Intel sin runtime externo y pesa una décima
parte. La CPU siempre queda la última.

No hay heurística de VRAM ni pregunta al usuario. Se prueban en orden y se queda el primero que
arranca y genera: es la reversión de §13.3 generalizada. **El candidato que falla se borra del
disco**, de modo que `find_binary()` no puede encontrarse después con el motor que no funcionaba.

## El acompañante de CUDA

Los paquetes de CUDA de llama.cpp **no traen el runtime**: sin él, el binario no arranca en una
máquina que no tenga instalado el toolkit. `companion_of()` empareja cada paquete `cuda-<versión>`
con su `cudart-...-cuda-<misma versión>` y lo despliega **en el mismo directorio**, antes del
arranque de prueba. Si el acompañante no se puede bajar, se avisa y se prueba igual: puede que la
máquina ya tenga el runtime.

## Desempaquetado

Windows publica `.zip` y Linux y macOS `.tar.gz`; `extract_archive()` maneja los dos, porque
soportar solo `.zip` dejaba a Linux y macOS sin ningún candidato. En ambos casos valida **todos**
los miembros antes de escribir ninguno: rechaza rutas absolutas, con `..`, con letra de unidad, o
que al resolverse caigan fuera del destino. En POSIX marca los ficheros como ejecutables. Un
archivo ilegible da `EngineError`, no una traza.

## El perfil que crea

`default_profile()` traslada las decisiones cerradas de §2, no inventa ninguna:

| Bandera | Valor | De dónde sale |
|---|---|---|
| `--model` | ruta del `.gguf` | el modelo encontrado en `models/chat/` |
| `--ctx-size` | `8192` | §2 |
| `--cache-type-k` · `--cache-type-v` | `q8_0` | §2 |
| `--host` · `--port` | de `chat_url` | `arca.json` |
| `--n-gpu-layers` | `999` | solo en paquetes de GPU; §13.4 ya documenta que es la forma habitual de decir «todas» |

| `--reasoning-budget` | `0` | solo si el binario la ofrece; ver `chat.md` |

La plantilla de chat no se pasa: `llama-server` la toma de la cabecera del propio GGUF. Las
banderas opcionales se comprueban contra el `--help` del binario con `supported_flags()`, de modo
que un binario antiguo que no las conozca no recibe nada que le impida arrancar.

Si el perfil `auto` se queda por detrás de lo que el binario ofrece —porque se actualizó el motor o
porque ARCA aprendió una bandera nueva—, la puesta en marcha lo **rehace y lo vuelve a probar** con
la reversión de §13.3. Los perfiles del operador no se tocan nunca.

El perfil se llama `auto` y pasa por `apply_profile()`, así que hereda el `last_good` y la
reversión de los perfiles hechos a mano. Se puede editar después como cualquier otro.

**Y no se vuelve a escribir.** Si al arrancar ya hay binario y ya hay un perfil `last_good`, la
puesta en marcha **lo reanuda**: no repite el arranque de prueba ni toca la base de datos, pero sí
comprueba la salud y **arranca el proceso si no está en marcha**. Lo que el operador dejó
configurado manda sobre lo que este módulo habría elegido; lo que no puede pasar es declarar
`ready` con el chat muerto, que es lo que ocurría hasta el 2026-08-29: tras reiniciar el servidor
el panel decía «listo» y no había ningún proceso detrás.

## Que nunca bloquee el arranque

`EngineBringup.start()` lanza un hilo demonio y vuelve enseguida. La aplicación lo arranca en el
**lifespan** de FastAPI, no al construirla, y solo si `auto_engine` está activo en `arca.json`
(por defecto sí). Crear la aplicación no toca la red — hay un test que lo comprueba, porque de eso
depende que la suite no dependa de internet y que el invariante 5 se sostenga.

El estado se consulta con `GET /api/admin/engine` y se puede pedir a mano con
`POST /api/admin/engine/start`, que es lo que hace el botón «Poner el chat en marcha» de la
pestaña de llama.cpp.

## Qué queda vendorizado

- El paquete `.zip` tal cual se descargó, con su sidecar `.sha256`, en `vendor/llama.cpp/`. Con él
  se vuelve a desplegar el motor **sin red**.
- El commit exacto en `sources.lock`, que encola el tarball de código y documentación de llama.cpp
  por la vía de siempre (ver `vendor.md`).
- La captura de `--help` y `--version` de ese binario concreto (§8.3).

## Los dos servidores

ARCA gestiona **dos** procesos de `llama-server`, no uno, y comparten el mismo binario:

| Proceso | Modelo | Puerto | Cómo se prueba |
|---|---|---|---|
| Chat | el `.gguf` de `models/chat/` | `chat_url` | Arranca, responde a `/health` y **genera texto** (§13.3) |
| Embeddings | el primer `.gguf` legible de `models/embed/` | `embed_url` | Arranca, responde a `/health` y **devuelve un vector con las dimensiones esperadas** |

Cuando un arranque falla, el detalle que se muestra **es la línea del propio motor**, no una
frase genérica: `_explain()` recorre el log y prefiere lo que explica —«unknown model architecture»,
«out of memory»— sobre un «error» pelado. Es la diferencia entre «no respondió al chequeo de salud»
y saber que el GGUF que bajaste está convertido con una arquitectura que tu binario no carga.

La prueba del segundo no puede ser la del primero: un modelo de embeddings no conversa. Lo que se
comprueba es lo que de verdad importa, que es lo mismo que mira la comprobación `embedding_server`
de §14: que el vector tenga `embed_dims` dimensiones. Un modelo que devuelve otras se rechaza con
el número concreto en el mensaje, porque sellar un lote con dimensiones equivocadas es la trampa 1.

El modelo de embeddings **se descubre por carpeta**, no por `embed_model`: esa clave nombra el
modelo declarado en los manifiestos de lote, no un fichero. Si hay un `.gguf` en `models/embed/`,
ese es.

Cada servidor tiene su propio `LlamaServer`, así que uno puede estar en marcha y el otro no. El
panel muestra los dos estados por separado y el chat sigue funcionando aunque los embeddings estén
caídos: lo único que se pierde es la mitad semántica de la búsqueda y la posibilidad de sellar
lotes nuevos.

## Configuración

| Clave de `arca.json` | Efecto |
|---|---|
| `auto_engine` | `false` desactiva la puesta en marcha al arrancar; el botón del panel sigue funcionando |
| `llama_bin` | Si apunta a un binario propio, se usa ese y no se descarga nada |
| `chat_url` | Host y puerto que se escriben en el perfil |
