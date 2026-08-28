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
| `doc_drift` | Versión instalada de cada dependencia directa contra `sources.lock`; lock ilegible también degrada | ok / degraded |
| `undocumented_dependency` | Dependencia directa instalada sin entrada en el lock | ok / degraded |
| `llama_binary` | Presencia del binario `llama-server` (configurado, vendorizado o en el `PATH`); su ausencia degrada el chat, nunca el sistema | ok / degraded |
| `llama_server` | `/health` del servidor en marcha, o de `chat_url` si lo gestiona otro | ok / degraded |
| `chat_model` | GGUF de chat: **roto** si el modelo referido por `chat_model` o por el perfil activo no está o no se puede leer; **degradado** si no hay ninguno instalado | ok / degraded / broken |

Con la raíz rota solo se evalúan `library_path` y la parte del reloj que no depende de la raíz: el resto no puede medirse honestamente.

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
