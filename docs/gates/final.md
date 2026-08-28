# Verificación integral final — Registro de ejecución

Fecha: 2026-08-28. Los **10 puntos de `ARCA-especificacion.md` §18** ejecutados como una sola sesión de aceptación, sobre tres estados de biblioteca —**vacía**, **a medias** (con ZIM y sin documentos) y **llena** (ZIM + cuatro PDF importados y sellados + GGUF de chat)— más una copia de la llena a otra ruta y una versión mutilada sin modelo, sin servidor de embeddings y sin ZIM.

| # | Punto de §18 | Resultado | Evidencia |
|---|---|---|---|
| 1 | Se instala pidiendo una sola ruta | ✅ | `arca --root <ruta> init` → 0, raíz con sus 7 entradas y `arca.json` legible por `load_config` |
| 2 | Arranca con el disco lleno, vacío o a medias, y dice en qué estado está | ✅ | Portada 200 en los tres; estado de biblioteca `empty` / `ok` / `ok`; las 11 comprobaciones de diagnóstico en los tres casos |
| 3 | Se puede desenchufar el soporte durante una consulta sin corromper nada | ✅ | Sesión completa de consulta (portada, búsqueda por palabras, búsqueda semántica, apertura de cada documento, diagnóstico): **0 creaciones, 0 borrados y 0 cambios** bajo la raíz salvo los sidecars WAL de `library.db`; `PRAGMA integrity_check` = `ok` |
| 4 | Una importación interrumpida se reanuda y cuesta repetir, como mucho, un elemento | ✅ | Con el servidor de embeddings cayéndose tras el primer documento: **1 hecho, 3 reintentables, lote sin sellar**. Al relanzar: **1 saltado sin volver a pedir un solo embedding**, 3 completados, **el mismo lote** sellado |
| 5 | La biblioteca copiada a otra máquina funciona sin reconstruir ningún índice | ✅ | `copytree` de la raíz completa y `create_app` sobre la copia: 2 resultados por palabras y 4 semánticos a la primera consulta, lote en estado `sealed` |
| 6 | Sin modelo, sin servidor de embeddings y sin ZIM: sigue arrancando y el diagnóstico explica qué falta | ✅ | Portada 200; `chat_model=broken`, `llama_server=degraded`, servidor de embeddings caído reportado en 12.4; **3 comprobaciones con impacto y acción** redactados |
| 7 | Un resultado semántico lleva al párrafo exacto, y el original está a un clic | ✅ | Primer resultado semántico con `anchor=p:0001:01`; la versión consultable contiene ese `id`; `/api/doc/<sha256>/original` devuelve **bytes idénticos** al fichero en disco |
| 8 | El chat cita o admite que no sabe, y en riesgo nunca improvisa | ✅ | Con corpus: `citations=[1]`. Fuera del corpus: `no_evidence`. Tema de riesgo sin fuente: `no_evidence` (declara que no la tiene). Tema de riesgo **con** evidencia y respuesta sin citar: `warning=risk_without_citation` |
| 9 | La documentación de lo que usa el proyecto se consulta sin red | ✅ con salvedad | Las 5 piezas del lock se evalúan **sin una sola llamada de red**, con veredicto por pieza; del binario compilado se capturan `--help` y `--version` (que lleva el commit exacto) con su `.sha256`. Ver la salvedad |
| 10 | Un cambio de configuración de llama.cpp que no arranca revierte solo | ✅ | Perfil roto: falla en la fase `health`, vuelve automáticamente a «good», queda activo y con `/health` respondiendo |

**10/10 ciertos a la vez.**

Suite en el momento del cierre: **423 tests en verde**, `mypy` estricto limpio (48 ficheros), `ruff` limpio.

## Salvedades que hay que leer

**Punto 9.** La verificación offline funciona y reporta pieza a pieza, pero en la biblioteca sintética de esta sesión las cinco piezas del lock salen como `missing`: nunca se descargaron, porque la máquina de desarrollo no tiene red para ello. Lo que se demuestra aquí es que la comprobación **no necesita red** y nombra cada pieza; que una pieza presente y con hash correcto sale `ok`, y una alterada sale `mismatch`, lo cubre `tests/test_vendor.py::test_verify_offline_reports_each_state`. De llama.cpp se vendoriza lo que de verdad identifica al binario compilado —su `--help` y su `--version`, que imprime el commit— con sus hashes; el tarball de documentación upstream lo coloca el operador en `vendor/docs/`.

**Puntos 8 y 10.** El modelo de chat sigue siendo un doble que habla el protocolo de `llama-server`, no un modelo de lenguaje (misma salvedad que la puerta del hito 4). La tubería de citación, el suelo de relevancia, el aviso de riesgo y la reversión de perfiles están verificados de extremo a extremo; la calidad de la prosa de un modelo real, no.

**Punto 3.** «Desenchufar el soporte» se verifica por su consecuencia comprobable: la sesión de consulta no escribe nada bajo la raíz —así que no hay nada a medio escribir que pueda corromperse— y la base de datos supera `integrity_check` después. Arrancar físicamente un disco durante una lectura no es reproducible en una suite; el invariante que lo hace seguro sí lo es, y es el que se mide.

**Punto 4.** La reanudación se provoca con el servidor de embeddings, que es el fallo realista y el que rompe a mitad de lote. Matar el proceso a mitad está cubierto por la puerta del hito 3.

## Añadido después de esta sesión, y vuelto a pasar

El 2026-08-29, a petición explícita, se añadieron cinco cosas que §18 no cubre y que no estaban:
las cinco comprobaciones de §14 que faltaban en el panel de diagnóstico (más la de holgura de
memoria del modelo), el buscador y descargador de modelos GGUF con sus cuantizaciones, el fijado
del commit de llama.cpp para vendorizar su código y su documentación, el manual consultable desde
el propio panel, y la navegación (barra de pestañas que envuelve sin ocultar nada, enlaces en los
dos sentidos entre archivo y administración).

**Los diez puntos se volvieron a ejecutar enteros después de esos cambios y siguen ciertos a la
vez**, con la suite en **456 tests verdes**, `mypy` estricto limpio (52 ficheros) y `ruff` limpio.

## Estado tras la sesión

Los diez puntos de §18 son ciertos a la vez. Con eso, la definición de «terminado» del proyecto se cumple: quedan las deudas declaradas en las puertas de los hitos 3 y 4 (calidad de recuperación y de citación con los modelos reales) y la revisión de código pendiente de H2.2–H2.7.
