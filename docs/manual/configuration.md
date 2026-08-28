# Configuración y estructura en disco

Módulo: `arca/config.py`.

## Resolución de la raíz

Prioridad: `--root` en la CLI → variable de entorno `ARCA_ROOT` → error. La raíz es la única ruta que el sistema necesita conocer.

## `arca.json`

Texto plano editable a mano, en la raíz. Claves reconocidas y valores por defecto:

| Clave | Por defecto | Notas |
|---|---|---|
| `language` | `"es"` | `es` o `en`; cualquier otro valor vuelve a `es` |
| `host` | `"127.0.0.1"` | |
| `port` | `8042` | fuera de 1–65535 se ignora |
| `free_space_warn_gib` | `10.0` | umbral del aviso de espacio; debe ser > 0 |
| `session_dir` | `""` | ver abajo |
| `download_concurrency` | `2` | descargas simultáneas de `arca run` y del worker del servidor; entero > 0 |
| `download_max_attempts` | `5` | reintentos por descarga antes de marcarla `failed`; entero > 0 |
| `embed_url` | `"http://127.0.0.1:8080"` | servidor de embeddings (ver `documents.md`) |
| `embed_model` | `"multilingual-e5-base"` | modelo declarado en los manifiestos de lote |
| `embed_dims` | `768` | dimensiones esperadas; un servidor que devuelva otras se rechaza |
| `chat_url` | `"http://127.0.0.1:8081"` | dónde buscar el chat cuando el servidor no lo gestiona ARCA; si lo arranca ARCA, la URL sale del propio `argv` |
| `chat_model` | `""` | nombre del GGUF de `models/chat/`; vacío = el primero legible |
| `llama_bin` | `""` | ruta o nombre del binario `llama-server`; vacío = buscar en `vendor/llama.cpp/` y en el `PATH` (ver `llama.md`) |
| `hub_url` | `"https://huggingface.co"` | hub de modelos que consulta el buscador de 12.6; admite un espejo propio |
| `hub_token` | `""` | credencial para modelos cuya licencia hay que aceptar. **No sale en la exportación de configuración ni en el log** |

Robustez deliberada: un `arca.json` ausente, corrupto o con tipos equivocados nunca impide arrancar — cada valor inválido cae a su defecto (invariante 5). Las claves desconocidas se ignoran.

## Directorio de sesión

Estado de sesión, caché y logs del proceso de consulta van **fuera de la raíz** (invariante 1): por defecto a `<temporal del sistema>/arca`. `session_dir` permite otra ubicación, pero solo absoluta y fuera de la raíz; un valor relativo o dentro de la raíz se descarta y se usa el defecto.

Borde conocido: el defecto es compartido entre bibliotecas — dos raíces servidas en la misma máquina sin `session_dir` propio entrelazan sus logs de sesión en el mismo fichero. Si sirves varias bibliotecas, da a cada `arca.json` su `session_dir`.

## Estructura creada por `init`

`zim/`, `docs/originals/`, `docs/shards/`, `docs/vendor/`, `models/chat/`, `models/embed/`, `vendor/wheelhouse/`, `vendor/llama.cpp/`, `vendor/docs/`, `logs/` — más `arca.json` y `library.db`.

## Detección de entorno

`scan_root()` mira lo que hay de verdad en cada arranque (invariante 6): existencia de raíz, `arca.json` y `library.db`, y recuento de `*.zim` y `*.gguf` presentes. La portada y el estado se construyen a partir de este escaneo, no de lo que diga la configuración.

## Logging

`setup_logging(directorio)` — `arca.log` rotativo (5 MB × 3). Los comandos administrativos (`init`) escriben en `<raiz>/logs/`; el servidor escribe en el directorio de sesión, nunca en la raíz.
