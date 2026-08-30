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

### `arca sources`

Lista las fuentes declaradas en `arca.json` —identificador, nombre, tipo, contenido y dirección base— y pregunta a cada una que esté activa para decir si responde y si verifica hash. Ver `downloads.md` para el modelo de fuentes.

### `arca search [texto] [--lang xxx] [--category c] [--source id]`

Consulta **en el momento** (nunca se persisten URLs de descarga) todas las fuentes activas, o solo una con `--source`, y lista una línea por resultado: identificador, fuente, título, idioma, sabor, tamaño y número de artículos. `--lang` acepta lo que acepte la fuente (Kiwix, códigos de tres letras: `spa`, `eng`, listas separadas por comas) y se omite en las fuentes que no filtren por idioma. Una fuente caída se anuncia por `stderr` y no impide ver las demás; el código de salida es 1 solo si **ninguna** contestó.

```
arca search wikipedia --lang spa
arca search --source standard-ebooks meditations
```

### `arca add <uuid> [--source id] [--query texto] [--index]`

Vuelve a preguntar a la fuente, localiza el elemento por su identificador y lo encola. **Repite con `--query` la misma búsqueda con la que lo encontraste**: una fuente que solo responde a búsquedas —Standard Ebooks, sin ir más lejos— no ofrece nada al listado desnudo, y sin la consulta el elemento no aparece. Es el mismo motivo por el que no se persiste ninguna URL de descarga: se re-resuelve en el momento. Para una fuente de colecciones ZIM descarga su `.meta4` (con `?country=ES`) para obtener tamaño, sha-256 y espejos por prioridad, y registra el ZIM en `zim_files` (estado `missing`). Si el `.meta4` no existe, degrada: encola sin hash de origen y avisa. Para una fuente de libros encola a `<raíz>/books/`, y con `--index` deja anotado que al terminar hay que ingerirlo. Sin `--source` se usa `kiwix`.

### `arca add --url <dirección> [--name f] [--sha256 h] [--kind book|zim] [--index]`

La fuente manual desde la línea de órdenes: mete una dirección directa en la cola. Sin `--name`, el nombre de fichero sale de la propia dirección; sin `--sha256`, la descarga se hace igual y se declara no verificada. Rechaza con código 2 —y sin encolar nada— un esquema que no sea `http`/`https`, una dirección de la que no sale un nombre de fichero seguro, un sha-256 que no sean 64 hexadecimales o un `--kind` desconocido.

### `arca run [--concurrency N]`

Atiende la cola: reclama descargas `queued` (concurrencia por defecto 2, configurable en `arca.json` o con `--concurrency`) y las ejecuta con el motor reanudable (ver `downloads.md`). Muestra progreso, porcentaje y velocidad. Al empezar, re-encola las filas `running`/`verifying` huérfanas de un proceso muerto: el tamaño real del `.part` manda sobre lo que diga la base de datos. Un libro encolado con `--index` se ingiere al terminar su descarga y se dice en qué lote quedó; si el servidor de embeddings no responde, se avisa y el fichero se queda descargado para indexarlo después. Código de salida 1 si alguna descarga terminó `failed`.

### `arca import <rutas>`

Mete documentos propios en la biblioteca. Acepta ficheros sueltos y carpetas (recursivo, recogiendo
toda extensión que ARCA conozca, aceptable o no). Cada fichero se copia por su sha256 y el original
no se toca. Un formato que no entra se rechaza **diciendo cuál es y por qué** —con los mismos
códigos y mensajes que el panel, incluido «falta `ocrmypdf`» para las imágenes— y **no detiene** a
los demás. Código de salida 1 si hubo algún rechazo.

**Importar no indexa.** Los documentos quedan guardados y pendientes; quien los indexa y sella es
el botón «Indexar y sellar» del panel (`documents.md`).

### `arca verify`

Recomprueba **sin red** dos cosas:

- **Cada ZIM registrado**: hash contra su `.sha256` guardado. Marca `corrupt`/`missing`/`present` en
  `zim_files` y registra eventos.
- **Cada lote de documentos**: estado del manifiesto y **firma del maquetado** con el que se selló,
  contrastada con la activa. Un lote sin sellar se informa y no cuenta como problema; uno corrupto o
  incompatible, sí.

Código de salida 1 si hay ZIM corruptos o ausentes, lotes en mal estado o lotes sellados con otro
maquetado.

### `arca vendor [--verify | --check-drift]`

- Sin banderas: descarga las piezas de `sources.lock` que falten o no verifiquen (motor reanudable, kind `vendor`), verifica hashes contra el lock y captura `llama-server --help` si el binario está en el PATH (si no, lo dice y sigue).
- `--verify`: recomprueba hashes locales contra el lock **sin tocar la red**. Código 1 si hay piezas ausentes o con hash distinto.
- `--check-drift`: compara la versión instalada de cada dependencia directa con la documentada en el lock (`alineado`/`deriva`/`sin documentar`/`no instalado`). Código 1 si hay deriva o dependencias sin documentar.

## Dónde vive

`build_parser()` y `main()` en `arca/cli.py`; los textos, en `arca/locale/{es,en}.json`.

## `arca vendor --llama-commit <sha>`

Fija en `sources.lock` el commit de llama.cpp con el que has compilado `llama-server` y deja su tarball listo para descargar con `arca vendor` (ver `vendor.md`). Un valor que no sea de 7 a 40 caracteres hexadecimales se rechaza sin tocar el lock.
