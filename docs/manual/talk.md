# Conversación libre con el modelo

Módulos: `arca/history.py` (almacén), `arca/talk.py` (motor), pantalla `/talk`. Fase 8 del plan.

Es un **segundo cliente del mismo motor**, no una variante del asistente del archivo. El de `arca/chat.py` está construido para negarse sin evidencia y citar el párrafo exacto; éste responde con lo que el modelo sepa, sin archivo de por medio. Por eso `ChatEngine` no se reutiliza ni se parametriza: doblarlo lo estropearía en su trabajo.

Si el motor no está, la pantalla sigue navegable y lo declara. Nada del núcleo depende de ella.

## Dónde vive el historial, y por qué no en la raíz

Un historial persistente escribe en cada mensaje, y escribe desde el camino de consulta. El invariante 1 prohíbe escribir un byte bajo la raíz de la biblioteca, nombrando el chat expresamente. La salida ya estaba escrita en §6: ese estado va al **directorio de sesión**, que `arca/config.py` resuelve y que **rechaza cualquier candidato que caiga dentro de la raíz**, cayendo al temporal del sistema.

- `talk_dir(root, config)` = `session_dir(root, config)/talk`.
- Base de datos: `talk.sqlite`, con su propio `user_version` (`TALK_SCHEMA_VERSION`), **independiente** del `SCHEMA_VERSION` de `library.db`. Son dos ciclos de vida distintos.
- Tablas: `conversations`, `messages`, `attachments`, `talk_settings`.
- `library.db` no se abre para escribir desde esta pantalla. Solo se lee, y solo para saber si el motor ve imágenes (más abajo).

Las imágenes son **ficheros**, no BLOB: `<session_dir>/talk/<conversación>/<id>.<ext>`. Así el borrado se verifica mirando el disco, no confiando en la base.

**Sin `session_dir` en `arca.json`, el directorio de sesión es una carpeta compartida del sistema**, no una por biblioteca: dos ARCA distintas en la misma máquina compartirían un solo `talk.sqlite` y mezclarían sus conversaciones. Es el comportamiento de `session_dir` desde siempre, pero esta pantalla es la primera que guarda ahí contenido del usuario que importa. Si hay más de una biblioteca en la máquina, dale a cada una su `session_dir`.

`TalkStore` mantiene una sola conexión (`check_same_thread=False`) protegida por un `RLock`, porque FastAPI sirve estos endpoints síncronos desde un pool de hilos.

### Degradación

Si el directorio no se puede crear o abrir, el almacén cae a `sqlite3.connect(":memory:")`, marca `persistent = False`, guarda el motivo en `detail` y `attach()` devuelve `None`. La pantalla lo declara (`talk.memory_only`), esconde el botón de imagen y sigue funcionando. Es el invariante 5 aplicado a un subsistema más.

## Imágenes

Lista blanca de tres formatos y **detección por número mágico**, nunca por extensión: `image_mimetype()` mira los primeros bytes (PNG, JPEG, WebP). Un `.png` que no sea una imagen se rechaza con código `not_an_image`. Hay tope de tamaño por imagen y tope de imágenes por mensaje.

Se suben **antes** de enviar el mensaje y quedan con `message_id NULL`; al enviar, `bind_attachments` las ata al mensaje recién creado. Así el usuario ve la miniatura antes de pulsar «Enviar».

### Cómo se sabe si el modelo ve

`vision_support(argv, root)` en `arca/llama.py`: hay visión si el comando lleva `--mmproj` **y** el proyector existe en disco. Devuelve un motivo estable — `ready`, `no_engine`, `no_projector`, `missing_projector` — que la pantalla traduce.

El `argv` sale primero de `llama.status().command`, es decir de lo que ARCA arrancó de verdad; solo si no hay proceso propio (servidor externo) se cae al perfil activo leído de `library.db` **en solo lectura**. La capacidad del motor no es un ajuste, y no hay otra fuente cuando el servidor es de otro.

Sin visión, el botón de adjuntar **no existe** en la pantalla, y el endpoint de subida responde `409 no_vision` de todas formas.

## Motor

`TalkEngine` llama a `chat_completion` / `chat_stream` y nada más: sin evidencia, sin marcadores, sin `STATE_UNGROUNDED`.

Ajustes propios en `talk_settings` de `talk.sqlite`, **nunca** en `settings` de `library.db`: prompt de sistema, temperatura, `top_p`, `n_predict` y presupuesto de contexto. Se editan en la propia pantalla, en el plegable de ajustes.

Recorte de ventana (`window`): se descartan los mensajes **más viejos**, con `estimate_tokens` y un coste fijo por imagen (`IMAGE_TOKEN_COST`). El prompt de sistema no se descarta jamás, y si el último mensaje solo ya excede el presupuesto se envía igual — más vale una respuesta mala que ninguna. Lo que se quedó fuera se cuenta y la pantalla lo dice (`talk.dropped`).

Estados de la respuesta: `ok`, `unavailable`, `truncated`, `empty`, `cancelled`.

## Respuesta token a token

En local, esperar en blanco a que llegue la respuesta entera es la diferencia entre parecer un chat y no parecerlo. El camino por defecto es SSE.

**Transporte** (`chat_stream`, `arca/llama.py`): pide `stream: true`, lee la respuesta línea a línea y traduce cada `data:` a un `Chunk` con el trozo de texto. El último `Chunk` lleva `done`, el `finish_reason` y los contadores de tokens.

**Dos vueltas atrás, cada una en su capa:**

1. *El servidor no sabe transmitir.* Si responde con un HTTP de error, o si contesta algo que no es `text/event-stream` (un servidor compatible que ignore `stream`), el transporte emite un único `Chunk(fallback=True)` y el motor **repite la llamada en modo bloqueante**. El usuario recibe la respuesta entera de golpe, pero la recibe.
2. *El navegador no sabe leerla.* `talk.js` comprueba `ReadableStream` y `TextDecoder` antes de elegir ruta; si faltan, usa el endpoint bloqueante `POST /api/talk/{id}/message` de siempre. Los dos endpoints conviven a propósito.

**Cancelación.** `CancelBoard` (en `arca/talk.py`) es un registro de qué conversaciones están respondiendo ahora, protegido por un `Lock` porque el endpoint de parada llega por otro hilo. La conversación se registra en el tablero **antes** de guardar la pregunta, no cuando empieza a llegar texto: si no, habría un hueco —guardar el mensaje, atar las imágenes, leer el historial— en el que «Detener» no encontraría nada que detener y no diría nada. `POST /api/talk/{id}/stop` marca la conversación; el generador lo comprueba entre línea y línea, deja de leer y cierra la conexión con `llama-server`, que es lo que de verdad hace que el modelo deje de generar. Lo que ya había escrito **se guarda**: una respuesta a medias es una respuesta, y el estado queda en `cancelled`.

El tablero se indexa por conversación, no por petición: dos pestañas escribiendo en la **misma** conversación comparten entrada, y detener una detendría a la otra. Dentro de una pestaña no puede pasar —la pantalla no deja enviar dos veces a la vez— y ARCA es de un solo operador local, así que se deja así a propósito.

El cliente no aborta la petición al pulsar «Detener»: sigue leyendo hasta el evento `done`, de modo que el mensaje del asistente se cierra y se guarda igual. Si el navegador se va de verdad (pestaña cerrada), el generador se cierra, la conexión con `llama-server` se cierra con él y el tablero se limpia en su `finally`.

## Endpoints

| Método | Ruta | Qué hace |
| --- | --- | --- |
| `GET` | `/talk` | La pantalla |
| `GET` | `/api/talk` | Conversaciones, ajustes, estado del motor, visión y si el almacén es persistente |
| `POST` | `/api/talk` | Crea una conversación |
| `DELETE` | `/api/talk` | Borra todas, con sus mensajes y sus imágenes |
| `GET` / `PUT` | `/api/talk/settings` | Ajustes de la conversación |
| `GET` | `/api/talk/{id}` | Abre una conversación con sus mensajes |
| `PUT` | `/api/talk/{id}` | Renombra |
| `DELETE` | `/api/talk/{id}` | Borra la conversación, sus mensajes y su directorio de imágenes |
| `POST` | `/api/talk/{id}/message` | Pregunta y respuesta, bloqueante |
| `POST` | `/api/talk/{id}/message/stream` | Pregunta y respuesta por SSE |
| `POST` | `/api/talk/{id}/stop` | Detiene la respuesta en curso |
| `POST` | `/api/talk/{id}/image` | Sube una imagen, bytes en crudo, sin *multipart* |
| `GET` | `/api/talk/{id}/image/{aid}` | Devuelve la imagen con su tipo real |

`/api/talk/settings` se declara **antes** que `/api/talk/{conversation_id}`, o la ruta con parámetro se la comería.

Errores con código estable de la familia `talk.` (F6.1): `unknown_conversation`, `empty_title`, `empty_message`, `no_store`, `no_vision`, `image_too_large`, `not_an_image`, `unknown_attachment`. Cada uno tiene su clave en los dos catálogos.

### Los eventos del stream

Tres tipos, uno por línea `data:`:

- `asked` — el mensaje del usuario ya guardado, con su id y sus imágenes.
- `delta` — un trozo de texto.
- `done` — la conversación actualizada, el mensaje del asistente ya guardado y el estado de la respuesta.

## Pantalla

Tercer enlace del `seg` de navegación, entre Archivo y Administración, coherente en las tres plantillas. `templates/talk.html` + `static/talk.js`: sin framework, sin `innerHTML`, iconos clonados de `#icon-set`, cero red fuera del origen.

Dos columnas: lista de conversaciones e hilo. Prefijo i18n `talk.` — no colisiona con el `chat.` del asistente del archivo — con los catálogos `es` y `en` completos. Ningún color nuevo: solo los tokens de §16.

Mientras el modelo escribe, la pantalla no deja cambiar de conversación ni borrar: el hilo que se está escribiendo es el que se está mirando.

## Qué lo prueba

- `tests/test_talk.py` — almacén, borrado verificado en disco, degradación a memoria, número mágico, detección de visión, recorte de ventana, transporte SSE, las dos vueltas al modo bloqueante, cancelación y los endpoints.
- `tests/test_invariants.py` — nueve tests. `_query_session` barre `/talk`, `/static/talk.js` y una conversación entera (crear → renombrar → mensaje → mensaje por SSE → detener → imagen → leerla → borrar → borrar todo). Un test aparte afirma que la ruta del almacén nunca cae dentro de la raíz, ni con `session_dir` apuntando a ella a propósito.
- `tests/test_i18n.py` — la familia `talk.error` con sus ocho códigos, y los dos catálogos con el mismo juego de claves.
