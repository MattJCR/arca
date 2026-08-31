# Diagnóstico

Módulo: `arca/diagnostics.py`. Tres estados por comprobación: `ok`, `degraded`, `broken`. Cada resultado no correcto lleva resumen, impacto (qué deja de funcionar) y acción (qué hacer), localizados vía i18n.

## Comprobaciones actuales

| id | Qué mira | Estados posibles |
|---|---|---|
| `library_path` | La raíz existe y es legible | ok / broken |
| `free_space` | Espacio libre contra `free_space_warn_gib`; suelo duro de 1 GiB | ok / degraded / broken |
| `write_access` | El volumen admite escritura (`os.access`); en solo lectura la interfaz se degrada, no falla | ok / degraded |
| `clock` | Fecha del sistema anterior a la época de construcción (`BUILD_EPOCH`) → broken; ficheros de la biblioteca con mtime más de 48 h en el futuro → degraded | ok / degraded / broken |
| `zim_integrity` | Por cada fila de `zim_files`: presencia del fichero, presencia del sidecar `.sha256` y estado `corrupt` marcado por la última verificación | ok / degraded |
| `incomplete_downloads` | Ficheros `.part` bajo la raíz sin descarga activa (`queued`/`running`/`paused`/`verifying`) asociada | ok / degraded |
| `remote_sources` | Las fuentes remotas declaradas: cuántas están activas, cuáles no respondieron la última vez que se les preguntó y cuáles no publican hash. Sin ninguna fuente activa también degrada, porque entonces no se puede añadir nada. **No toca la red**: enseña el estado observado (ver `downloads.md`) | ok / degraded |
| `doc_drift` | Versión instalada de cada dependencia directa contra `sources.lock`; lock ilegible también degrada | ok / degraded |
| `undocumented_dependency` | Dependencia directa instalada sin entrada en el lock | ok / degraded |
| `fulltext_index` | Cuáles de los ZIM traen índice de texto completo y cuáles solo títulos | ok / degraded |
| `shard_manifests` | Estado real de cada lote: manifiesto presente y hashes de sus ficheros | ok / degraded |
| `embedding_coherence` | `embed_model` y `embed_dims` de cada lote contra el modelo activo (trampa 1) | ok / degraded |
| `vanished_batches` | Lotes cuyos trabajos de ingesta constan **todos terminados** —ninguno en cola ni corriendo, la misma condición con la que se sella— y cuyo directorio ya no está en disco. Nada de ARCA borra un lote —no hay código que lo haga—, así que si falta, se ha quitado desde fuera; la biblioteca lo daba por importado sin enterarse. Una importación a medias no cuenta: para eso está `provisional_shards` |
| `provisional_shards` | Lotes sin sellar de una importación interrumpida | ok / degraded |
| `embedding_server` | Responde, y **devuelve el número de dimensiones esperado**: si devuelve otro, es roto, no degradado | ok / degraded / broken |
| `chat_model_memory` | Estimación de memoria del GGUF activo con un contexto de referencia de 4096 frente a la RAM del sistema | ok / degraded |
| `llama_binary` | Presencia del binario `llama-server` (configurado, vendorizado o en el `PATH`); su ausencia degrada el chat, nunca el sistema | ok / degraded |
| `llama_server` | `/health` del servidor en marcha, o de `chat_url` si lo gestiona otro | ok / degraded |
| `chat_model` | GGUF de chat: **roto** si el modelo referido por `chat_model` o por el perfil activo no está o no se puede leer; **degradado** si no hay ninguno instalado | ok / degraded / broken |

Con la raíz rota solo se evalúan `library_path` y la parte del reloj que no depende de la raíz: el resto no puede medirse honestamente.

Matiz de `chat_model_memory`: §14 pide una sola fila para el modelo de chat («presente, cabeza legible, cabe en memoria»), pero presencia y holgura de memoria tienen impactos y acciones distintas, así que son dos comprobaciones. La estimación usa un contexto de referencia de 4096 y no el `n_ctx_train` del fichero: con modelos de contexto enorme, estimar sobre el máximo entrenado dispararía el aviso siempre y dejaría de significar nada.

Matiz de `embedding_server`: la comprobación pide un embedding de una palabra para contar las dimensiones de verdad. Es una llamada corta al servidor local en cada diagnóstico; a cambio, la trampa 1 se detecta antes de indexar y no después.

Matiz de `chat_model`: la diferencia entre rojo y ámbar es deliberada. Una biblioteca que nunca ha tenido modelo está degradada —falta algo opcional—; una biblioteca cuyo modelo elegido ha desaparecido está rota, porque el chat que estaba configurado ya no puede arrancar. Es el caso del criterio 4 de §11.4.

Matiz de `zim_integrity`: el diagnóstico comprueba presencia y estado registrado, no recalcula hashes (recorrer cientos de GiB en cada arranque no sería honesto con el tiempo de respuesta). El recálculo completo es `arca verify` o el botón de reverificación del dashboard, que sí actualizan el estado que este check refleja. Las comprobaciones de contenido leen `library.db` en solo lectura: diagnosticar no escribe.

## Verificación integral

`verify_integrity()` vive en este mismo módulo pero **no** es parte del diagnóstico de arranque: es la acción bajo demanda de 12.12, que sí recalcula hashes y recorre lotes y base de datos elemento a elemento. Su contrato está en `maintenance.md`.

## Dónde se consume

- `GET /api/diagnostics` (JSON localizado, `?lang=es|en`).
- La portada `/` (lista con sello de estado coloreado).
- `arca diagnostics` en la CLI (código de salida 1 si hay algo roto).

`worst_state(results)` resume el conjunto; `render(result, catalog)` produce el texto localizado a partir de claves `diag.<id>.<estado>.{summary,impact,action}`.

## Limitación conocida

`os.access(..., W_OK)` es aproximado en Windows para volúmenes de red; se aceptó por no violar el invariante 1 (una prueba real de escritura crearía un fichero bajo la raíz desde el camino de consulta).
