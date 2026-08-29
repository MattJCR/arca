# Puesta en marcha automática del chat

Módulo: `arca/engine.py`. Sección 13.0 de la especificación.

El `.gguf` de `models/chat/` son **pesos**: un fichero de datos que no se ejecuta. `llama-server`
es el **motor** que los carga y los sirve, un binario de llama.cpp que ARCA lanza como subproceso
y que nunca es dependencia del paquete (§2). Tener el modelo sin el motor es tener el ZIM sin el
lector.

Conseguir ese motor no es trabajo del usuario. Lo único que se le pide es dejar el `.gguf` en
`models/chat/`; el resto lo hace este módulo.

## La secuencia

| Paso | Qué hace | Estado que se ve en el panel |
|---|---|---|
| 1 | Busca un `.gguf` de chat | `waiting_model` si no hay ninguno |
| 2 | Si ya hay binario (`llama_bin`, `vendor/llama.cpp/` o `PATH`), salta a la prueba | — |
| 2b | Si además ya hay un perfil `last_good`, **no toca nada**: el chat ya estaba resuelto | `ready` |
| 3 | Resuelve la última release publicada de llama.cpp **en el momento** | `resolving` |
| 4 | Descarga el paquete que toca por la cola reanudable y verificada | `downloading` |
| 5 | Lo desempaqueta en `vendor/llama.cpp/<tag>-<backend>/` | `deploying` |
| 6 | Crea el perfil y lo somete al arranque de prueba de §13.3 | `testing` |
| 7 | Fija el commit en `sources.lock` y captura `--help` y `--version` | `ready` |

Sin red, `no_network`. Si la release no publica paquete para la plataforma, `unsupported`. Si
ninguno supera la prueba, `exhausted`. Cada estado tiene su mensaje en los dos catálogos, y un
test comprueba que ninguno se queda sin él.

## Nunca una URL guardada

`resolve_release()` pregunta por la release publicada cuando hace falta (trampa 5). No hay ninguna
URL de paquete incrustada en el código: los enlaces salen de la respuesta del momento. El commit
que se fija en `sources.lock` se resuelve aparte, por el tag, porque el campo `target_commitish`
de una release suele traer el nombre de la rama y no un SHA — `_sha()` lo descarta si no es
hexadecimal, así que una rama nunca se cuela como si fuera un commit.

GitHub es, con esto, el **tercer y último origen de red** del proyecto, junto al catálogo de Kiwix
y al hub de modelos.

## Elegir el paquete sin adivinar

`backend_of()` lee el backend del nombre del asset (`cuda`, `hip`, `rocm`, `sycl`, `vulkan`,
`metal`, y `cpu` como caída) y `matches_platform()` cruza plataforma y arquitectura por sus
etiquetas (`win`/`ubuntu`/`macos` y `x64`/`arm64`). `candidates()` devuelve los que encajan
ordenados: **GPU primero, CPU como último recurso**.

No hay heurística de VRAM ni pregunta al usuario. Se prueban en orden y se queda el primero que
arranca y genera: es la reversión de §13.3 generalizada. **El candidato que falla se borra del
disco**, de modo que `find_binary()` no puede encontrarse después con el motor que no funcionaba.

## Desempaquetado

`extract_archive()` valida **todos** los miembros antes de escribir ninguno: rechaza rutas
absolutas, con `..`, con letra de unidad, o que al resolverse caigan fuera del destino. En POSIX
marca los ficheros como ejecutables. Un zip ilegible da `EngineError`, no una traza.

## El perfil que crea

`default_profile()` traslada las decisiones cerradas de §2, no inventa ninguna:

| Bandera | Valor | De dónde sale |
|---|---|---|
| `--model` | ruta del `.gguf` | el modelo encontrado en `models/chat/` |
| `--ctx-size` | `8192` | §2 |
| `--cache-type-k` · `--cache-type-v` | `q8_0` | §2 |
| `--host` · `--port` | de `chat_url` | `arca.json` |
| `--n-gpu-layers` | `999` | solo en paquetes de GPU; §13.4 ya documenta que es la forma habitual de decir «todas» |

La plantilla de chat no se pasa: `llama-server` la toma de la cabecera del propio GGUF.

El perfil se llama `auto` y pasa por `apply_profile()`, así que hereda el `last_good` y la
reversión de los perfiles hechos a mano. Se puede editar después como cualquier otro.

**Y no se vuelve a escribir.** Si al arrancar ya hay binario y ya hay un perfil `last_good`, la
puesta en marcha se declara `ready` y no toca la base de datos ni reinicia el proceso: lo que el
operador dejó configurado a mano manda sobre lo que este módulo habría elegido.

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

## Configuración

| Clave de `arca.json` | Efecto |
|---|---|
| `auto_engine` | `false` desactiva la puesta en marcha al arrancar; el botón del panel sigue funcionando |
| `llama_bin` | Si apunta a un binario propio, se usa ese y no se descarga nada |
| `chat_url` | Host y puerto que se escriben en el perfil |
