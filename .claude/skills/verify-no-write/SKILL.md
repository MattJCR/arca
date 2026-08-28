---
name: verify-no-write
description: Comprueba el invariante 1 de ARCA — consultar no escribe — ejerciendo una sesión completa de consulta y verificando que no aparece ni cambia nada bajo la raíz de la biblioteca. Usar tras CUALQUIER cambio que toque el camino de consulta (api.py, zim.py, search.py, db.py, config.py, plantillas o estáticos) y antes de cerrar una puerta de hito.
allowed-tools: [Read, Bash, Grep, Glob]
---

# Verificar «consultar no escribe»

Es el invariante 1 de `ARCA-especificacion.md` §6 y uno de los criterios de
aceptación del hito 2 (§9.5). Si se rompe, la biblioteca deja de poder vivir en
un soporte de solo lectura y deja de ser fiable como archivo.

## Cuándo ejecutarla

Siempre que se haya tocado el camino de consulta: `arca/api.py`, `arca/zim.py`,
`arca/search.py`, `arca/db.py`, `arca/config.py`, `arca/templates/` o
`arca/static/`. También antes de cualquier puerta de hito.

## Cómo se ejecuta

```
.venv/Scripts/python.exe -m pytest tests/test_invariants.py -q
```

Los ocho tests deben pasar. No basta con que pase la suite completa: si esta
skill se invoca, se ejecuta y se mira el resultado.

## El contrato exacto

Una sesión completa de consulta (portada en ambos idiomas, diagnóstico,
estáticos y tipografías, `/api/search` con y sin alcance y en los tres modos,
portada de cada colección, artículos, redirecciones, recursos internos y los dos
404) deja la raíz así:

- **Nada creado**, con una única excepción sancionada: `library.db-wal` y
  `library.db-shm`. `library.db` es la excepción de WAL de §7.1 y SQLite
  necesita el índice WAL incluso para leer. Está documentado en
  `docs/manual/database.md`. Sin `library.db` en disco, no se crea absolutamente
  nada.
- **Nada borrado y nada modificado**: mismo tamaño y mismo `mtime` en cada
  fichero, incluidos todos los `.zim`.
- **Ningún temporal**: nada que acabe en `.tmp`, `.part`, `.journal`, `.lock`
  o `.bak`.
- El directorio de sesión resuelve **fuera** de la raíz.

Cuando llegue el hito 3, los lotes sellados no tendrán excepción ninguna
(`journal_mode=DELETE`): ahí el contrato es cero ficheros, sin matices.

## Qué hacer si falla

1. Mira **qué** fichero apareció o cambió: el test lo nombra.
2. Si es un sidecar de SQLite distinto de los dos sancionados, o cualquier
   sidecar de un lote, la causa es una conexión abierta en modo escritura desde
   una ruta de consulta. Búscala: `grep -n "open_library_rw" arca/api.py` y
   comprueba que solo la usan rutas `/api/admin/*`.
3. Si es un `.zim` que cambió de `mtime`, algo está escribiendo en una
   colección: `libzim` abre en solo lectura, así que revisa qué código nuevo
   toca esa ruta.
4. Si es un temporal, revisa que nada escriba en `tempfile.gettempdir()`
   resuelto **dentro** de la raíz (`config.session_dir`).

No relajes el test para que pase. El contrato de arriba es el producto.

## Degradación en caliente

La misma suite cubre los otros dos criterios relacionados de §9.5: una colección
que deja de leerse a mitad de sesión responde 503 con un mensaje accionable (sin
traza), una colección que falla a mitad de búsqueda no cancela a las demás, y
una biblioteca que desaparece entera degrada a resultados vacíos con el
diagnóstico en «roto», sin tumbar el servidor.
