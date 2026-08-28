# Chat con citación

Módulo: `arca/chat.py`. Sección 11 de la especificación. El chat es un panel, no la protagonista: si el modelo desaparece, el archivo sigue entero (invariante 5).

## Qué ve el modelo

Nunca HTML crudo. El estado de pantalla llega estructurado y recortado (`read_screen` / `screen_state`):

```json
{
  "query": "…",
  "results": [{"title": "…", "source": "…", "snippet": "…"}],
  "open_document": {"doc_id": "…", "title": "…", "page": 7},
  "selection": {"text": "…", "doc_id": "…", "anchor": "p:0007:03"}
}
```

Todo campo se valida por tipo y se trunca: cinco resultados, 320 caracteres de extracto, 4000 de pregunta. Un cliente que mande basura no rompe nada — los campos inválidos se descartan.

La selección la produce el visor: `static/selection.js` publica `arca:selection` con el texto, el `doc_id` y el ancla del párrafo seleccionado, y el panel la envía con la pregunta. Ese es el sello de procedencia de §16 viajando al chat.

## Política de contexto

Topes duros de §11.2, no negociables desde la configuración: **5 fragmentos** y **~1500 tokens** de evidencia. La configuración puede bajarlos, nunca subirlos (`_positive` recorta contra la constante). El contador de tokens es el mismo de la ingesta (palabras × 1,4).

Orden de búsqueda: primero la selección del visor si la hay, luego la pregunta, luego la consulta que hubiera en pantalla. Los resultados llegan con los semánticos delante (párrafo exacto) y los de palabras detrás.

## Suelo de relevancia

Un buscador siempre devuelve algo: el vecino más cercano existe aunque no tenga nada que ver. Sin un suelo, «no lo tengo» no se dispararía nunca. Un resultado entra como evidencia si **una** de estas dos cosas es cierta:

- su puntuación semántica llega a `MIN_SEMANTIC_SCORE` (0,75 de coseno), o
- comparte al menos un término de cuatro letras o más con la consulta, comparado sin acentos y en minúsculas.

La primera condición es la que salva la recuperación entre idiomas y por sinónimos, donde no hay palabras compartidas; la segunda es la que sostiene el criterio cuando la puntuación no es fiable. Sin evidencia que pase el suelo, el motor **no llama al modelo**: responde «no lo tengo» y ofrece buscar de otra manera. Es determinista, no depende de la honestidad del modelo.

## La herramienta de búsqueda

En vez de depender del *function calling* —que varía según modelo y plantilla— el protocolo es texto plano: si el modelo necesita más evidencia responde con una sola línea `SEARCH: <consulta>`. El motor la detecta, busca, añade lo que pase el suelo de relevancia y vuelve a preguntar. Máximo **dos** llamadas por turno; agotadas, se responde con lo que haya o se dice que no se tiene.

## Citación y temas de riesgo

Cada fragmento entra al contexto etiquetado con su procedencia: `[n] fuente · ancla · página`, seguido del texto. La respuesta cita con `[n]` y la interfaz convierte cada marcador en un enlace al párrafo exacto (el visor se abre en su ancla). Los marcadores que no correspondan a ninguna evidencia se ignoran: no se inventan enlaces.

La regla de riesgo va por partida doble (§11.3):

- **En el prompt de sistema**, que exige citar o declarar que no hay fuente en salud, medicina, química, sustancias peligrosas y electricidad.
- **Verificada en la interfaz**: si la pregunta toca un tema marcado como de riesgo y la respuesta no lleva ninguna cita, el panel muestra un aviso visible (`risk_without_citation`). No se confía en que el modelo cumpla: se comprueba.

Los temas por defecto salen de la clave `chat.risk_topics` de **los dos catálogos** —la detección no depende del idioma de la interfaz— y son editables desde el dashboard.

## Ajustes (12.8)

Guardados en la tabla `settings` bajo la clave `chat`:

| Ajuste | Por defecto | Tope |
|---|---|---|
| `max_chunks` | 5 | 5 |
| `max_evidence_tokens` | 1500 | 1500 |
| `max_tool_calls` | 2 | 2 |
| `system_prompt` | vacío = el del catálogo | — |
| `risk_topics` | los de ambos catálogos | — |
| `sampling` | `{"temperature": 0.2, "top_p": 0.9, "n_predict": 512}` | — |

El muestreo viaja **por petición** al servidor, nunca en la línea de arranque (§13.1).

## Estados de la respuesta

| Estado | Cuándo | Qué ve el usuario |
|---|---|---|
| `ok` | Hubo evidencia y el modelo respondió | La respuesta con sus citas y las fuentes citadas |
| `no_evidence` | Nada pasó el suelo de relevancia, o el presupuesto de búsquedas se agotó | «No lo tengo», con la invitación a buscar de otra manera |
| `unavailable` | `/health` no responde, o la generación falla | El aviso de que no hay modelo, y que buscar sigue funcionando |
| `error` | Pregunta vacía | Petición de escribir una pregunta |

Apagar `llama-server` a mitad de conversación cae en `unavailable` en la petición siguiente: el panel lo dice, la búsqueda no se entera.

## API

`POST /api/chat` con `{"message": …, "screen": {…}, "lang": "es|en"}` devuelve texto, estado, evidencia con su procedencia y URL, marcadores citados, búsquedas realizadas, si el tema es de riesgo y el aviso si procede. Nunca devuelve error de servidor por culpa del modelo: la degradación viaja en el propio cuerpo.

`GET /api/admin/settings` y `PUT /api/admin/settings` (`{"chat": {…}}`) leen y escriben los ajustes; también reportan las rutas en uso.

## Interfaz

Panel derecho de la portada (`static/chat.js`): insignia de estado del modelo alimentada por el diagnóstico, aviso cuando no hay modelo, franja con la selección del visor y botón para quitarla, historial de la conversación, respuestas con las citas ya enlazadas, lista de fuentes citadas, las búsquedas realizadas y el aviso de riesgo cuando toca. Hacer clic en una cita abre el visor en ese párrafo, igual que un resultado de búsqueda.
