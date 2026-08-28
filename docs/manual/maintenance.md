# Mantenimiento

Sección 12.12 del dashboard. Rutas en uso, registro, verificación integral y exportar/importar configuración. Todo vive en `arca/api.py` sobre `verify_integrity()` de `arca/diagnostics.py`.

## Rutas en uso

`GET /api/admin/maintenance` reporta lo que el proceso está usando de verdad, no lo que diga la configuración: raíz, `arca.json`, `library.db`, carpeta de logs y **temporal de sesión**. Ese último se muestra precisamente para poder comprobar de un vistazo que está fuera de la raíz, que es lo que exige el invariante 1 (ver `invariants.md`).

## Registro

La misma respuesta lista los ficheros de log presentes con su tamaño y la política de rotación en vigor (5 MB por fichero, 3 copias). La rotación la aplica `logging.handlers.RotatingFileHandler` desde `config.setup_logging()`; aquí solo se muestra.

## Verificación integral

`POST /api/admin/maintenance/verify` recorre la biblioteca entera y devuelve **un elemento por línea**, no un veredicto global:

| Tipo | Qué comprueba | Resultados |
|---|---|---|
| `zim` | Recalcula el sha256 de cada colección registrada y lo contrasta con su sidecar; actualiza el estado en `library.db` | `ok`, `corrupt`, `missing`, `no_hash` |
| `shard` | Estado real del lote: manifiesto presente, hashes de sus ficheros contra el manifiesto y coherencia del modelo de embeddings con el activo | `ok`, `corrupt`, `incompatible`, `provisional` |
| `document` | Que el original de cada documento del lote siga en `docs/originals/` | `missing` (solo aparece si falta) |
| `database` | `PRAGMA integrity_check`, filas de `zim_files` cuyo fichero no está, y trabajos de importación que apuntan a lotes que ya no existen | `ok`, `corrupt`, `orphan` |

Es una acción de mantenimiento, no de consulta: se ejecuta bajo demanda, escribe el estado verificado de cada ZIM y registra un evento. Lee todos los ficheros, así que tarda lo que tarde el disco. El diagnóstico de arranque (`diagnostics.md`) no hace esto: comprueba presencia y estado registrado, porque recorrer cientos de GiB en cada arranque no sería honesto con el tiempo de respuesta.

Los originales no se tocan (invariante 4): la verificación abre en lectura y compara.

## Exportar e importar configuración

`GET /api/admin/maintenance/config` devuelve `arca.json` completo más los ajustes guardados en la base de datos (chat y búsqueda). `PUT` con ese mismo objeto **aplica solo los ajustes**: la ruta de la biblioteca, el puerto y el resto de `arca.json` no se tocan desde la web. Mover una biblioteca de máquina es copiar la carpeta; importar la configuración es traer los topes del chat, el prompt, los temas de riesgo y los ajustes de búsqueda, que es lo que de verdad se afina con el uso.

Un objeto que no traiga `settings` se rechaza con 400. Cada sección se valida con las mismas reglas que su endpoint propio: un valor fuera de rango vuelve al que ya había, nunca rompe la importación entera.

## Interfaz

Pestaña «Mantenimiento»: rutas, ficheros de log con su tamaño y la política de rotación, botón de verificación integral con su tabla elemento a elemento y su recuento, y el área de exportar/importar configuración. La verificación avisa antes de lanzarse de que puede tardar.
