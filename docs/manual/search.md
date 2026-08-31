# Búsqueda y fusión de resultados

Módulo: `arca/search.py`. Une lo que devuelve cada colección ZIM (`zim.py`) en una sola lista, sin inventarse una puntuación común.

## Los dos niveles

| Nivel | Alcance | Motor | Estado hoy |
|---|---|---|---|
| Por palabras | Todo el corpus | Índice interno de cada ZIM (Xapian) | **Funciona** |
| Semántica | Subconjunto elegido | Vectores del lote | **Funciona** (ver `documents.md`) |

`mode` acepta `keyword`, `semantic` o `both` (por defecto). Si no hay ningún lote sellado con el modelo activo, la respuesta declara `semantic_available: false` y la franja llega vacía: no se finge, igual que con los ZIM sin índice. Si el servidor de embeddings no responde, la franja queda vacía pero la de palabras contesta igual.

## Cómo se fusiona: intercalado por rango

Los scores de Xapian y los de FTS5 **no son comparables**, y normalizarlos sería inventarse una escala (§9.3, trampa 3). En vez de eso:

1. Cada fuente —colección ZIM por su Xapian, lote sellado por su FTS5— responde su propia lista ordenada por su propio motor.
2. `interleave()` recorre las listas rango a rango: primer resultado de cada colección, luego el segundo de cada una, y así.
3. La lista fusionada se recorta al `limit` pedido.

Consecuencia visible y deliberada: los primeros resultados vienen de colecciones distintas. El usuario ve de dónde sale cada cosa y nadie tiene que calibrar pesos.

`rank` es la posición **dentro de su fuente**, no en la lista fusionada. Es el único número que se compara, y solo contra el de la misma fuente.

## Qué lleva cada resultado

`source_kind` (`zim`), `source_id` (UUID de la colección), `source_title`, `title`, `path`, `url` (`/zim/{uuid}/{path}`, ya percent-codificada) y `rank`. La procedencia viaja con el resultado desde que se produce: es la materia prima del sello de procedencia de §16.

## Qué se sabe de cada fuente

Además de los resultados, la respuesta lleva un parte por colección consultada: modo de índice real (`fulltext` / `title` / `none`), coincidencias estimadas, resultados devueltos y `error` si esa colección no pudo responder. Una colección ilegible **no cancela la búsqueda**: se marca su error y las demás contestan (invariante 5). `unsearchable()` selecciona las que no pueden contestar, para que la interfaz pueda decirlo.

## API

`GET /api/search?q=&scope=&mode=&limit=`

- `q`: consulta. Vacía o en blanco devuelve cero resultados, pero **sí** el parte de fuentes.
- `scope`: lista de UUID separados por comas; vacío = todas las colecciones activas. Una colección desactivada queda fuera aunque se la nombre aquí.
- `mode`: `keyword` | `semantic` | `both`. Un valor desconocido cae a `both` en vez de fallar.
- `limit`: recortado al rango 1–100.

## Estado de la biblioteca

Cada respuesta lleva un bloque `library` con el estado real —`ok`,
`all_disabled`, `empty`, `unreachable` o `unreadable`—, cuántas colecciones
activas hay, cuántas desactivadas, y la lista de ficheros que no se han podido
abrir con su error. Sirve para que la interfaz no confunda «biblioteca vacía»
con «disco desconectado» (ver `interface.md`).

## Comprobado en

`tests/test_search.py` (etiquetado por origen, intercalado rango a rango, alcance, límite, consulta vacía, franja semántica declarada vacía, normalizadores) y `tests/test_api.py` (el endpoint, incluido el filtro por `scope`).
## Ajustes (12.9)

Guardados en la tabla `settings` bajo la clave `search`, editables desde la pestaña «Búsqueda» del dashboard:

| Ajuste | Por defecto | Rango | Qué cambia |
|---|---|---|---|
| `per_source` | 20 | 1–100 | Cuántos resultados se le piden a cada fuente antes de intercalar |
| `zim_weight` | 1 | 1–5 | Cuántos resultados aporta cada colección ZIM por vuelta de intercalado |
| `doc_weight` | 1 | 1–5 | Lo mismo para cada lote de documentos propios |
| `snippet_chars` | 320 | 80–1000 | Longitud del extracto de cada resultado |
| `chunk_min_tokens` | 270 | 100–512 | Troceado mínimo, **solo para importaciones futuras** |
| `chunk_max_tokens` | 450 | 100–512 | Troceado máximo, **solo para importaciones futuras** |
| `semantic_floor` | 82 | 0–100 | Parecido mínimo, en tanto por ciento, para que un resultado semántico se enseñe |

### El suelo semántico, y por qué existe

Una búsqueda por vectores **siempre** tiene respuesta: por lejos que quede la
pregunta, algún fragmento será el menos lejano de todos. Sin un suelo, preguntar
por una receta de cocina a una biblioteca que solo guarda el manual de una moto
devuelve veinte trozos del manual de la moto. No es un fallo del índice —el
índice contesta exactamente lo que se le pregunta— sino la falta de un criterio
para decir «esto no responde a nada».

Con `multilingual-e5-base` los parecidos **no reparten de 0 a 1**: se apiñan
arriba. Medido sobre una biblioteca real de un solo documento, un manual de 190
páginas:

| Consulta | Mejor parecido |
|---|---|
| «cambiar el aceite del motor» | 0,857 |
| «cómo arrancar la moto» | 0,852 |
| «presión de los neumáticos» | 0,846 |
| «tensión de la cadena de transmisión» | 0,844 |
| «síntomas de la diabetes tipo 2» | 0,806 |
| «receta de tortilla de patatas» | 0,784 |
| «historia del imperio romano» | 0,765 |

Lo que responde de verdad queda por encima de 0,819; lo que no, por debajo de
0,807. El hueco está ahí, y por eso el valor de fábrica es **82**. No es un
umbral universal: depende del modelo y del corpus, y por eso se ajusta desde el
panel. A **0** no se filtra nada, que es como se comportaba antes de existir el
ajuste.

El suelo se aplica **por fragmento**, antes de ordenar e intercalar, así que una
consulta sin respuesta devuelve la franja semántica vacía en vez de ruido con
buena pinta. El chat tiene su propio criterio, `MIN_SEMANTIC_SCORE` en
`chat.py`, que no es un suelo sino un atajo de relevancia: ver `chat.md`.

Los pesos son profundidad de intercalado, no puntuaciones: siguen sin compararse scores de fuentes distintas (trampa 3). Un valor fuera de rango no rompe nada — se conserva el que ya había. Si el mínimo llega por encima del máximo, se intercambian.

El troceado no reindexa nada: los lotes sellados son inmutables (invariante 3), así que solo afecta a lo que se importe después. El tope de 512 no es un número redondo: es el contexto de `multilingual-e5-base`. Por encima de él un fragmento no se indexa —o se indexa truncado en silencio, que es peor—, así que el ajuste no deja subir de ahí. El porqué, medido, está en `documents.md`.
