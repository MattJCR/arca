# Invariantes verificables

De los siete invariantes de robustez de `ARCA-especificacion.md` §6, el primero
—**consultar no escribe**— es el que se puede romper sin que nadie se entere, así
que tiene suite propia (`tests/test_invariants.py`) y skill propia
(`verify-no-write`). Los demás se verifican en el sitio donde viven: la
reanudabilidad en `downloads.md`, el arranque degradado en `diagnostics.md`.

## Qué ejerce la suite

Una **sesión completa de consulta**, no una ruta suelta: portada en español e
inglés, diagnóstico en ambos idiomas, hoja de estilo, script del archivo, CSS de
tipografías y un WOFF2, `/api/search` con consulta, con alcance restringido, en
modo semántico y con consulta vacía, la portada de cada colección, cuatro
entradas de una colección (artículo, artículo enlazado, redirección y recurso
CSS) y los dos 404 (colección desconocida y entrada inexistente).

## El contrato

Tras esa sesión, bajo la raíz:

| Aspecto | Contrato |
|---|---|
| Ficheros creados | Ninguno, salvo `library.db-wal` y `library.db-shm` |
| Ficheros borrados | Ninguno |
| Ficheros modificados | Ninguno: mismo tamaño y mismo `mtime`, colecciones `.zim` incluidas |
| Temporales | Ninguno con sufijo `.tmp`, `.part`, `.journal`, `.lock` o `.bak` |
| Estado de sesión | El directorio de sesión resuelve fuera de la raíz |

**La excepción de los dos sidecars.** `library.db` es la única base de datos en
WAL del proyecto (§7.1: «WAL solo aquí»), y SQLite necesita el índice WAL en
memoria compartida incluso para leer: una conexión `mode=ro` puede materializar
`library.db-wal` y `library.db-shm`. Es una consecuencia de una decisión cerrada
de la especificación, no un descuido, y está acotada: **sin `library.db` en
disco, una sesión de consulta no crea absolutamente nada**, y eso también está
en la suite. Los lotes sellados del hito 3 no tendrán excepción: `journal_mode=DELETE`
y cero sidecars (trampa 4).

## Degradación en caliente

La misma suite fija qué pasa cuando el soporte desaparece a mitad de uso:

- **Una colección deja de leerse**: 503 con una página que dice qué pasó y qué
  hacer (comprobar que el disco sigue conectado), sin traza y sin tumbar nada.
- **Una colección falla a mitad de búsqueda**: su error se registra en el parte
  de esa fuente y las demás colecciones contestan igual (invariante 5).
- **La biblioteca entera desaparece**: `/api/search` devuelve resultados vacíos
  con código 200, la portada sigue sirviéndose y el diagnóstico pasa a «roto».
  Nada se corrompe porque nada se estaba escribiendo.

## Cómo ejecutarla

```
.venv/Scripts/python.exe -m pytest tests/test_invariants.py -q
```

Se ejecuta tras cualquier cambio en `api.py`, `zim.py`, `search.py`, `db.py`,
`config.py`, `templates/` o `static/`, y antes de cada puerta de hito. La skill
`verify-no-write` lleva el procedimiento y el árbol de decisión de qué hacer
cuando falla.
