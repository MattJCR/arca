# Puerta del hito 4 — Registro de ejecución

Fecha: 2026-08-28. Criterios de aceptación de `ARCA-especificacion.md` §11.4, ejecutados de principio a fin sobre una biblioteca limpia: tres PDF reales (bomba de alimentación, antena dipolo, batería de plomo) importados y sellados, servidor de embeddings de pruebas, y `llama-server` sustituido por un doble que habla su mismo protocolo (`/health`, `/v1/chat/completions`, `--help`, `--list-devices`).

| # | Criterio | Resultado | Evidencia |
|---|---|---|---|
| 0 | Arranque de prueba con reversión (§13.3) | ✅ | El perfil candidato arranca, responde a `/health` y **genera texto**: queda `last_good` y activo. Fases recorridas: `start` → `health` → `generation` → `ok` |
| 1 | Preguntar algo del corpus: responde citando, y las citas llevan al párrafo correcto | ✅ | `state=ok`, `citations=[1, 2]`, primera evidencia con `anchor=p:0001:01`; abrir `/api/doc/<sha256>` devuelve 200 y **el HTML contiene ese ancla** (`id="p:0001:01"`) |
| 2 | Preguntar algo que no está: dice que no lo tiene, en vez de inventar | ✅ | «¿Periodo orbital de Kepler-22b?» → `state=no_evidence`, texto «No lo tengo…», **sin evidencia y sin una sola llamada al modelo** |
| 3 | Apagar `llama-server` a mitad de conversación: la interfaz lo indica, la búsqueda sigue | ✅ | Tras `POST /api/admin/llama/stop`: `server.health=down`, siguiente turno `state=unavailable`, y la misma consulta de búsqueda devuelve **1 resultado por palabras y 3 semánticos** |
| 4 | Borrar el GGUF y reiniciar: el sistema arranca, el diagnóstico lo marca en rojo, todo lo demás funciona | ✅ | Con el fichero borrado y la aplicación reconstruida: portada 200, `chat_model=broken` («El modelo de chat configurado no está: chat.gguf») con su impacto y su acción, `llama_server=degraded`, y la búsqueda devuelve **4 resultados** |

Suite en el momento de la puerta: **410 tests en verde**, `mypy` estricto limpio (47 ficheros), `ruff` limpio.

## Lo que salió de ejercitar el sistema, no de leerlo

**El criterio 2 falló la primera vez, y con razón.** Un buscador siempre devuelve algo: el vecino más cercano existe aunque no tenga nada que ver con la pregunta. El motor entregaba esos fragmentos como evidencia, el modelo los recibía, y «no lo tengo» no se disparaba nunca. La corrección es el **suelo de relevancia** (`docs/manual/chat.md`): un fragmento entra como evidencia solo si su coseno llega a 0,75 **o** comparte un término de cuatro letras con la consulta. Sin nada que pase el suelo, el motor ni siquiera llama al modelo. Es determinista: no depende de que el modelo sea honesto, que es exactamente lo que §11.3 exige.

Ese fallo también obligó a mejorar el doble del servidor de embeddings. El anterior devolvía vectores derivados de la longitud del texto: todos los cosenos salían 1,0 y ningún suelo de relevancia podía medirse. Ahora tiene un modo `hashed` que produce un vector de bolsa de palabras determinista, con el que la similitud significa algo. El modo por defecto no cambia, para no alterar las pruebas que dependían del anterior.

## La salvedad que hay que leer

**El modelo de chat de esta puerta no es un modelo de lenguaje.** No hay `llama-server` en la máquina de desarrollo, así que el doble responde con un eco del contexto que recibe. Consecuencia directa y visible en el criterio 1: los marcadores `[1]` y `[2]` que la interfaz enlaza salen del eco de la evidencia, no de un razonamiento.

Lo que esto **sí** demuestra: la tubería completa —estado de pantalla estructurado, recuperación con tope duro de 5 fragmentos y 1500 tokens, suelo de relevancia, protocolo `SEARCH:` con máximo dos llamadas, etiquetado de procedencia de cada fragmento, marcadores convertidos en enlaces al párrafo exacto, aviso de riesgo sin citas, y la degradación honesta cuando el servidor cae— funciona de extremo a extremo, y el arranque de prueba con reversión hace lo que promete.

Lo que **no** demuestra: que un modelo real cite bien, ni que respete la regla de riesgo en su prosa. Eso solo se verifica con un GGUF real; queda como deuda explícita junto con la decisión pendiente del modelo definitivo (Qwen3-14B vs Gemma-3-12B, §2). El aviso de la interfaz está precisamente para el caso en que el modelo no cumpla.

Sigue abierta la deuda del hito 3: la **calidad** de la recuperación semántica con `multilingual-e5-base` real está sin medir.

## Estado tras la puerta

Los cuatro criterios de §11.4 pasan. El hito 4 queda cerrado y F5 (cierre) puede abrirse.
