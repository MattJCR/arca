# Servidor llama.cpp

Módulo: `arca/llama.py`. Sección 13 de la especificación. `llama-server` se gestiona como subproceso: nunca es una dependencia del paquete, y su ausencia degrada el chat sin tocar nada más (invariante 5).

## Encontrar el binario

`find_binary(root, configured)` busca en este orden y devuelve también de dónde salió:

1. `llama_bin` de `arca.json`, si apunta a un fichero o a un nombre resoluble en el `PATH` (`source = configured`).
2. Cualquier `llama-server` o `llama-server.exe` bajo `<raíz>/vendor/llama.cpp/`, recursivo y ordenado (`source = vendor`).
3. El `PATH` del sistema (`source = path`).

Sin binario no hay excepción: `inspect_binary()` devuelve `present = False` y el resto del sistema sigue igual.

**Conseguir ese binario no es trabajo del usuario**: si no hay ninguno, la puesta en marcha
automática lo descarga y lo despliega en `vendor/llama.cpp/` sola (ver `engine.md`). Cuando
el panel intenta arrancar o probar sin binario, la respuesta no es solo un 409: lleva
`code = no_binary`, y el panel lo traduce por un mensaje que ofrece el botón «Poner el chat
en marcha» y deja el camino manual como alternativa (ver «Errores con código» en `web.md`).
Lo mismo con `no_profile`, `unknown_profile`, `invalid_name` y `unknown_mode`.

`inspect_binary()` ejecuta además dos sondas cortas sobre el propio binario —`--version` y `--list-devices`— y de ahí saca la versión, la lista de dispositivos y si el build trae soporte de GPU (`has_gpu`: algún dispositivo que no empiece por `CPU`, o un marcador conocido —CUDA, ROCm, HIP, Vulkan, Metal, SYCL, MUSA, CANN— en la salida). Ese dato es lo que sostiene el aviso de la trampa 9. Las sondas son caras (dos procesos), así que la API las cachea por aplicación y solo las repite con `?refresh=1`.

## El proceso

`LlamaServer` envuelve `subprocess.Popen` con un cerrojo reentrante:

- `start(command)` mata lo anterior, lanza el nuevo `argv` con `stdout`+`stderr` unificados y arranca un hilo lector que vuelca las líneas en un anillo de 400 (`log(limit)`). Un binario que no existe o un `argv` vacío no lanzan excepción: se registran en `status().error`.
- `stop(timeout)` pide `terminate`, espera, y solo entonces `kill`. El log sobrevive a la parada: es lo que se lee para entender por qué murió.
- `restart()` repite el último `argv`.
- `health()` consulta `/health`: `200` → `ok`, `503` → `loading` (el modelo se está cargando, no está roto), cualquier otra cosa o error de red → `down`. `wait_healthy(timeout)` sondea hasta que responde, hasta que el proceso muere, o hasta agotar el plazo — no espera 60 segundos a un proceso que ya ha salido.
- `status()` / `probe()` devuelven `running`, `pid`, `exit_code`, la URL base y la **línea de comandos efectiva** (`command_line`), que es visible siempre, antes y después de arrancar (§13.2).

La URL base se deriva del propio `argv` (`--host`, `--port`, en forma separada o con `=`); `0.0.0.0` se sondea como `127.0.0.1`. Sin comando arrancado se usa `chat_url` de `arca.json`.

ARCA no arranca el servidor sola al iniciarse: lo hace el operador desde el panel o probando un perfil. Un arranque automático convertiría un modelo mal configurado en un fallo de arranque del sistema, que es justo lo que §13.3 existe para evitar.

## El formulario sale del binario, no del código

`read_help(command)` ejecuta `<binario> --help` y `parse_help(text)` convierte esa salida en opciones. No hay ninguna lista de banderas incrustada: si el binario cambia de versión y renombra una opción, el formulario cambia con él y la bandera nueva aparece sin tocar código.

Cada `HelpOption` lleva la bandera canónica, sus alias (`-ngl`, `--gpu-layers`), el marcador de valor (`N`, `FNAME`, `{none,linear,yarn}`; vacío = interruptor), la descripción con sus continuaciones, la sección que declara el propio `--help`, el valor por defecto (`(default: …)`) y la variable de entorno (`(env: …)`).

Sobre esa lista se aplica la **capa curada** (`CURATED_GROUPS`): modelo, contexto y memoria, cómputo, GPU, memoria del sistema, RoPE (incluida la familia `--yarn-*`), concurrencia, plantilla, servidor, embeddings, registro, muestreo y **otras**. La capa curada solo agrupa y ordena; no decide qué opciones existen. Lo que el binario reporta y la capa no reconoce cae en «otras» y se muestra, nunca se oculta.

El grupo `sampling` se marca aparte y **no** se pinta en el formulario de arranque: el muestreo va por petición y se configura en la pestaña Chat (§13.1), con una nota que lo dice en pantalla.

Si no hay binario, el formulario se genera a partir de la captura vendorizada `vendor/llama.cpp/llama-server-help.txt` (ver `vendor.md`), y la interfaz declara que viene de ahí. Sin binario y sin captura, lo declara también.

## Perfiles

Tabla `llama_profiles` de `library.db`. Cada perfil guarda su nombre, su modo, los argumentos del formulario (`args`, JSON bandera→valor; valor vacío = interruptor presente), la línea cruda, y dos marcas: `is_default` (el que se usa al arrancar) y `last_good` (el último que superó una prueba completa). Ambas son exclusivas: marcar una limpia la anterior.

**Modo crudo** (§13.2): un campo con el `argv` completo que **sustituye** al formulario, nunca se mezcla con él. `profile_argv()` ignora `args` cuando el modo es `raw`. Guardar un perfil no toca sus marcas: editar el perfil activo no lo desactiva.

Los nombres se validan (`is_profile_name`): letras, dígitos, guion, punto, espacio y guion bajo, hasta 64 caracteres.

## Arranque de prueba con reversión

`trial_start()` ejecuta la secuencia de §13.3 y `apply_profile()` la envuelve con la reversión:

1. Se guarda el perfil candidato.
2. Se arranca con él y se espera a `/health`, con tope de 60 s.
3. Si responde, se lanza una generación de prueba corta (`POST /v1/chat/completions` con un mensaje mínimo).
4. Si todo va bien, el perfil queda marcado `last_good` y activo.
5. Si falla —no arranca, no responde a tiempo, o responde a `/health` pero no genera— se mata el proceso, **se vuelve automáticamente al último perfil bueno** y se devuelve el log del intento fallido.

El resultado dice en qué fase murió (`start`, `health`, `generation`), con qué error, a qué perfil se volvió y si esa reversión funcionó. Sin perfil bueno anterior el servidor queda parado y se dice: es preferible a dejarlo arrancado con una configuración que no genera.

## Validaciones previas

`preflight(argv, binary)` se calcula sobre la línea efectiva y la cabecera GGUF del modelo indicado, y **avisa sin bloquear** (§13.4):

| Código | Cuándo |
|---|---|
| `context_over_trained` | `--ctx-size` mayor que el `n_ctx_train` de la cabecera (trampa 8) |
| `memory_over_available` | Estimación de memoria por encima de la VRAM (si hay capas en GPU y el build la soporta) o de la RAM total, con los dos números concretos |
| `gpu_layers_over_blocks` | `--n-gpu-layers` mayor que las capas del modelo: se acepta, es la forma habitual de decir «todas», pero se indica |
| `gpu_layers_without_gpu` | Capas en GPU pedidas a un binario compilado sin GPU (trampa 9): el síntoma es lentitud extrema y no apunta a la causa |
| `model_missing` | El `--model` indicado no existe en disco |

La estimación es `pesos + caché KV`, con la caché calculada como `capas × contexto × dimensión × (bytes de K + bytes de V)` según `--cache-type-k/-v`. Es deliberadamente aproximada y conservadora —ignora la agrupación de cabezas KV, que la reduce— porque la red de seguridad real es el arranque de prueba con reversión, no la aritmética. La RAM total se lee por `GlobalMemoryStatusEx` en Windows y por `sysconf` en el resto; si no se puede leer, el aviso simplemente no se emite.

## API

| Ruta | Qué hace |
|---|---|
| `GET /api/admin/llama` | Binario (ruta, origen, versión, dispositivos, GPU), estado del proceso, salud, línea efectiva, log en vivo, perfiles, activo, último bueno, avisos previos y modelo de chat presente. `?refresh=1` vuelve a sondear el binario |
| `GET /api/admin/llama/help` | Opciones del binario parseadas y agrupadas por la capa curada, con el origen del texto (`binary` / `capture`) |
| `GET /api/admin/llama/profiles` | Perfiles con su línea efectiva y sus avisos previos |
| `PUT /api/admin/llama/profiles/{name}` | Crea o actualiza; el primero queda activo. Nombre inválido → 400, modo desconocido → 400 |
| `DELETE /api/admin/llama/profiles/{name}` | Borra; desconocido → 404 |
| `POST /api/admin/llama/profiles/{name}/test` | La secuencia de §13.3 completa con reversión |
| `POST /api/admin/llama/{start\|stop\|restart}` | Sobre el perfil activo; acción desconocida → 400, sin binario o sin perfil → 409 |

## Interfaz

Pestaña «llama.cpp» del dashboard: estado del binario y del proceso, botones arrancar/parar/reiniciar, línea de comandos efectiva, avisos previos, log en vivo, y una tarjeta por perfil con su selector de modo (formulario o crudo), el formulario generado agrupado en desplegables, la línea efectiva del perfil, sus avisos, y los botones guardar / probar y activar / borrar. El resultado de una prueba fallida se muestra con la fase, el error y el perfil al que se ha vuelto.

## Diagnóstico

Tres comprobaciones (ver `diagnostics.md`): `llama_binary`, `llama_server` y `chat_model`.
