# Diagnóstico

Módulo: `arca/diagnostics.py`. Tres estados por comprobación: `ok`, `degraded`, `broken`. Cada resultado no correcto lleva resumen, impacto (qué deja de funcionar) y acción (qué hacer), localizados vía i18n.

## Comprobaciones actuales

| id | Qué mira | Estados posibles |
|---|---|---|
| `library_path` | La raíz existe y es legible | ok / broken |
| `free_space` | Espacio libre contra `free_space_warn_gib`; suelo duro de 1 GiB | ok / degraded / broken |
| `write_access` | El volumen admite escritura (`os.access`); en solo lectura la interfaz se degrada, no falla | ok / degraded |
| `clock` | Fecha del sistema anterior a la época de construcción (`BUILD_EPOCH`) → broken; ficheros de la biblioteca con mtime más de 48 h en el futuro → degraded | ok / degraded / broken |

Con la raíz rota solo se evalúan `library_path` y la parte del reloj que no depende de la raíz: el resto no puede medirse honestamente.

## Dónde se consume

- `GET /api/diagnostics` (JSON localizado, `?lang=es|en`).
- La portada `/` (lista con sello de estado coloreado).
- `arca diagnostics` en la CLI (código de salida 1 si hay algo roto).

`worst_state(results)` resume el conjunto; `render(result, catalog)` produce el texto localizado a partir de claves `diag.<id>.<estado>.{summary,impact,action}`.

## Limitación conocida

`os.access(..., W_OK)` es aproximado en Windows para volúmenes de red; se aceptó por no violar el invariante 1 (una prueba real de escritura crearía un fichero bajo la raíz desde el camino de consulta).
