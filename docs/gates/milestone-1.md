# Puerta del hito 1 — Registro de ejecución

Fecha: 2026-08-28. Criterios de aceptación de `ARCA-especificacion.md` §8.4, ejecutados sobre una biblioteca temporal limpia con red real.

| # | Criterio | Resultado | Evidencia |
|---|---|---|---|
| 1 | `arca search wikipedia --lang spa` lista resultados reales | ✅ | 38 entradas reales del catálogo remoto, con UUID, tamaño y artículos |
| 2 | `arca add <uuid>` encola, `arca run` descarga con progreso visible | ✅ | `wikipedia_es_mathematics_mini_2026-07.zim` (21,3 MiB) encolado y descargado con líneas de progreso (porcentaje, bytes, velocidad) |
| 3 | Matar el proceso a mitad y relanzar reanuda sin volver a bajar lo ya bajado | ✅ | Proceso matado con `.part` en 21.757.952 bytes (fila `running` huérfana, `bytes_done` persistido en 16 MiB); al relanzar, `run_queue` re-encoló la fila huérfana, mandó el tamaño real del `.part`, descargó solo la cola restante (~514 KiB) y el sha-256 verificó |
| 4 | Corromper un byte y ejecutar `arca verify` lo detecta y lo marca | ✅ | Un byte sobrescrito en el fichero final → `CORRUPTO` en la salida, `status='corrupt'` en `zim_files`, código de salida 1 |
| 5 | Desconectar la red a mitad y reconectar: pasa al siguiente espejo y termina | ✅* | Verificado por equivalente automatizado (`tests/test_fetch.py::test_mirror_failover_continues_on_the_same_part_file`): el servidor corta la conexión a mitad de transferencia, el motor avanza de espejo sobre el mismo `.part` (petición `Range` del segundo espejo desde el byte cortado) y termina con hash correcto. No se desconectó físicamente la interfaz de red de la máquina de desarrollo |
| 6 | `arca vendor --verify` funciona sin red | ✅ | Tras `arca vendor` (5 sdists reales de PyPI, 110 MiB), `--verify` devolvió `correcto` en las 5 piezas con código 0; el camino de código de `verify_offline` no contiene ninguna llamada de red. `--check-drift`: las 5 dependencias alineadas |

Además, el dashboard (`/admin`) se verificó en vivo sobre la misma biblioteca: estado general (que reflejó honestamente `degraded` mientras existía el ZIM corrupto del criterio 4), cola con la descarga completada, vendorizado con las 5 piezas `ok`, acción inválida rechazada con 400 y reverificación detectando el corrupto.

Suite en el momento de la puerta: 124 tests en verde, `mypy` estricto limpio (30 ficheros), `ruff` limpio.

**Revisión posterior a la puerta (mismo día):** la revisión de código detectó dos vectores de seguridad sobre datos remotos, corregidos antes de cerrar el hito: (1) path traversal a través del nombre de fichero derivado del `href` del catálogo — ahora `safe_filename` rechaza separadores y `..`, y `queue_zim_download` lo re-valida antes de construir la ruta de destino; (2) URLs sin restricción de esquema — ahora el feed, el `.meta4` y cada espejo exigen `http`/`https` (un `.meta4` con `file://` ya no puede leer ficheros locales). También se unificó el encolado CLI/dashboard en `queue_zim_download`, con el evento de encolado registrado por ambos caminos. Con las correcciones y sus tests de regresión: 129 tests en verde, `mypy` y `ruff` limpios.

\* El criterio 5 exige literalmente desconectar la red física; se aceptó el equivalente automatizado porque ejercita el mismo camino de código (fallo de espejo en caliente con failover sobre el mismo `.part`). Si se quiere la prueba física, basta deshabilitar el adaptador de red durante un `arca run` largo.
