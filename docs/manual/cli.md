# CLI `arca`

Módulo: `arca/cli.py`. Entrypoint instalado: `arca` (definido en `pyproject.toml`).

## Opciones globales

- `--root <ruta>` — raíz de la biblioteca. Si se omite, se usa la variable de entorno `ARCA_ROOT`. Sin ninguna de las dos, el comando falla con código 2 y un mensaje que lo explica.
- `--lang es|en` — idioma de los mensajes. Sin él, se usa el idioma de `arca.json` (por defecto español).
- `--version` — imprime `arca 0.1.0` y sale.

## Comandos

### `arca init`

Crea la estructura completa de la biblioteca bajo la raíz (§5 de la especificación), el `arca.json` con los valores por defecto si no existía, el `library.db` con el esquema completo, y registra un evento `init`. Es idempotente: sobre una biblioteca ya inicializada no cambia nada y lo dice.

```
arca --root D:\biblioteca init
Biblioteca inicializada en D:\biblioteca.
```

### `arca serve`

Levanta el servidor web (uvicorn) con la interfaz y la API. `--host` y `--port` puntuales; sin ellos manda `arca.json` (por defecto `127.0.0.1:8042`). Arranca siempre, también sobre una raíz vacía o sin inicializar (invariante 5).

Comportamiento ante fallos: el proceso de servicio no escribe nada bajo la raíz; sus logs van al directorio de sesión en el temporal del sistema.

### `arca diagnostics`

Imprime el diagnóstico completo (ver `diagnostics.md`), una línea `[estado] resumen` por comprobación, con impacto y acción indentados cuando el estado no es correcto. Código de salida: 0 sin comprobaciones rotas, 1 con alguna rota, 2 si falta la raíz.

### `arca search [texto] [--lang xxx] [--category c] [--name n]`

Consulta el catálogo OPDS de Kiwix **en el momento** (nunca se persisten URLs de descarga) y lista una línea por entrada: UUID, título, idioma, sabor, tamaño y número de artículos. `--lang` acepta códigos de tres letras (`spa`, `eng`, listas separadas por comas). Sin red, falla con mensaje claro y código 1.

```
arca search wikipedia --lang spa
```

### `arca add <uuid>`

Resuelve la entrada del catálogo por UUID, descarga su `.meta4` (con `?country=ES`) para obtener tamaño, sha-256 y espejos por prioridad, registra el ZIM en `zim_files` (estado `missing`) y encola la descarga en `downloads`. Si el `.meta4` no existe, degrada: encola sin hash de origen, avisa, y el hash calculado al terminar se guarda para verificaciones locales futuras.

### `arca run [--concurrency N]`

Atiende la cola: reclama descargas `queued` (concurrencia por defecto 2, configurable en `arca.json` o con `--concurrency`) y las ejecuta con el motor reanudable (ver `downloads.md`). Muestra progreso, porcentaje y velocidad. Al empezar, re-encola las filas `running`/`verifying` huérfanas de un proceso muerto: el tamaño real del `.part` manda sobre lo que diga la base de datos. Código de salida 1 si alguna descarga terminó `failed`.

### `arca verify`

Recomprueba, **sin red**, el hash de cada ZIM registrado contra su `.sha256` guardado. Marca `corrupt`/`missing`/`present` en `zim_files` y registra eventos. Código de salida 1 si hay corruptos o ausentes.

### `arca vendor [--verify | --check-drift]`

- Sin banderas: descarga las piezas de `sources.lock` que falten o no verifiquen (motor reanudable, kind `vendor`), verifica hashes contra el lock y captura `llama-server --help` si el binario está en el PATH (si no, lo dice y sigue).
- `--verify`: recomprueba hashes locales contra el lock **sin tocar la red**. Código 1 si hay piezas ausentes o con hash distinto.
- `--check-drift`: compara la versión instalada de cada dependencia directa con la documentada en el lock (`alineado`/`deriva`/`sin documentar`/`no instalado`). Código 1 si hay deriva o dependencias sin documentar.

## Dónde vive

`build_parser()` y `main()` en `arca/cli.py`; los textos, en `arca/locale/{es,en}.json`.
