# Puerta del hito 3 — Registro de ejecución

Fecha: 2026-08-28. Criterios de aceptación de `ARCA-especificacion.md` §10.3, ejecutados de principio a fin sobre una biblioteca limpia con **50 PDF reales generados para la prueba** (cinco temas técnicos, dos páginas cada uno).

| # | Criterio | Resultado | Evidencia |
|---|---|---|---|
| 1 | Importar 50 PDF, matar el proceso a mitad, relanzar: termina y solo repite el documento en curso | ✅ | Primera pasada con el servidor de embeddings cayéndose a mitad: **27 documentos hechos, 23 reintentables, lote sin sellar**. Al relanzar: **27 saltados sin volver a pedir un solo embedding**, 23 completados, **el mismo lote** reutilizado y sellado. 27+23 = 50 |
| 2 | Un resultado de búsqueda semántica lleva al párrafo exacto, no al documento | ✅ | El primer resultado semántico trae `anchor=p:0001:01`, `page=1` y `url=/api/doc/<sha256>#p:0001:01`; abrir esa URL devuelve 200 y el HTML **contiene ese ancla** (`id="p:0001:01"`). Búsqueda completa (palabras + semántica) en **22 ms** |
| 3 | El enlace al original abre el PDF en la página correcta | ✅ | `original_url = /api/doc/<sha256>/original#page=1`, coherente con la página del fragmento citado; la ruta devuelve 200 y **bytes idénticos** al original en disco |
| 4 | El sha256 del original es idéntico antes y después de todo el proceso | ✅ | 10 originales comprobados contra el fichero de entrada: **0 discrepancias**. El identificador del documento *es* su sha256, así que la comprobación es tautológica por diseño y aun así se verifica leyendo el fichero guardado |
| 5 | Copiar la carpeta entera a otra máquina: busca sin reconstruir nada | ✅ | `shutil.copytree` de la raíz completa a otra ruta y `create_app` sobre la copia: **10 resultados por palabras y 20 semánticos** a la primera consulta, con el lote en estado `sealed`. Nada se reindexa ni se reconstruye |

Además, una sesión de consulta sobre la biblioteca con lotes sellados **no crea ni borra nada** bajo la raíz (solo cambia `library.db-shm`, la excepción de WAL ya sancionada en la puerta del hito 2).

Suite en el momento de la puerta: **334 tests en verde**, `mypy` estricto limpio (43 ficheros), `ruff` limpio.

## La salvedad que hay que leer

**Los embeddings de esta puerta los produjo un servidor de pruebas, no `multilingual-e5-base`.** `llama-server` no está instalado en la máquina de desarrollo, así que el servidor falso devuelve vectores derivados de la longitud del texto. Consecuencia directa y visible: en el criterio 2, la consulta «dipole antenna wavelength» devolvió como primer resultado semántico un párrafo sobre paneles fotovoltaicos.

Lo que esto sí demuestra: **toda la tubería** —prefijo `passage: ` al indexar y `query: ` al consultar, cuantización int8, memmap, `vectors.ids`, manifiesto, fusión entre lotes, ancla, enlace al consultable y enlace al original en su página— funciona de extremo a extremo y es reanudable.

Lo que **no** demuestra: que la búsqueda semántica *acierte*. Eso solo se puede verificar con el modelo real, y queda pendiente para el hito 4, donde `llama-server` entra en escena de todos modos. Está anotado como deuda explícita: no se debe leer esta puerta como una validación de la calidad de recuperación.

## Medido aparte, con datos sintéticos a escala real

- **Rendimiento (§7.4, umbral < 200 ms):** 500.000 fragmentos de 768 dimensiones se buscan en **43 ms** (mediana de 5 ejecuciones), 4,6× por debajo del umbral. La primera implementación daba 338 ms; el perfilado mostró que el 94% del tiempo era convertir int8→float32, no multiplicar, y se sustituyó por un kernel entero (`einsum` int32 contra la matriz int8) repartido en hilos.
- **Fidelidad de la cuantización int8 (decisión cerrada de §2):** sobre vectores agrupados —como son los embeddings reales—, el recall@10 *exacto* frente a float32 es **5,6/10**, pero **9,5 de los 10 devueltos están en el top-50 real** y la pérdida media de coseno es **0,0011**. Es decir: int8 conserva la puntuación casi intacta y no conserva el orden entre casi-empates. Es el coste inherente de una decisión cerrada, no un defecto corregible; queda medido para que el número esté sobre la mesa.

## Lo que salió de ejercitar el sistema, no de leerlo

Tres defectos reales aparecieron durante el hito y se corrigieron antes de firmar:

1. **Un lote se sellaba perdiendo documentos.** `unfinished_jobs` solo contaba trabajos en cola o en curso, así que un lote con documentos fallidos por un servidor de embeddings caído se sellaba dejándolos fuera **para siempre**. Ahora se distingue el fallo transitorio (el trabajo vuelve a la cola y **mantiene el lote provisional**) del rechazo permanente (un PDF ilegible no bloquea el lote). Es lo que hace pasar el criterio 1.
2. **Fragmentos por encima del límite del modelo.** Con un párrafo muy largo, el troceado producía fragmentos de hasta 966 tokens; `multilingual-e5-base` trunca a 512, así que se habría perdido texto **en silencio**. Ahora nunca se supera el tope, cortando solo entre frases.
3. **Conexión SQLite compartida entre hilos.** Los lotes abiertos se reutilizaban entre peticiones, y FastAPI atiende cada petición síncrona en un hilo distinto del pool: `sqlite3.ProgrammingError` en producción a la primera consulta concurrente. Corregido con `check_same_thread=False` más un cerrojo por lote.

También se validó el camino de seguridad: `doc_id` viene de la URL y se usa para construir una ruta en disco, así que se exige que sea exactamente 64 caracteres hexadecimales. `tests/test_documents.py::test_a_forged_document_identifier_is_refused` prueba `../../arca.json` y variantes.

## Estado tras la puerta

Los cinco criterios de §10.3 pasan. El hito 3 queda cerrado y F4 (chat) puede abrirse, con dos deudas declaradas: la calidad de recuperación semántica sin verificar con el modelo real, y la revisión de código de H2.2–H2.7 que quedó sin veredicto por límite de sesión.
