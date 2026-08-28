# Vendorizado (`sources.lock`)

Módulo: `arca/vendor.py`. Fichero de control: `sources.lock` en la raíz del repositorio, un array JSON con el formato de la especificación §8.3 (`key`, `version`, `kind`, `url`, `commit`, `sha256`, `license`, `captured_at`, `notes`).

## Contenido actual del lock

Los sdists de las cinco dependencias directas (`fastapi`, `uvicorn`, `libzim`, `pymupdf`, `numpy`), con versión y sha-256 tomados de `uv.lock`. El test `test_check_drift_against_the_real_lock_is_clean` falla si el lock deriva de lo instalado, y `test_direct_dependencies_match_pyproject` mantiene honesta la constante `DIRECT_DEPENDENCIES`.

## Destinos en disco

Por `kind` de la entrada, bajo la raíz de la biblioteca:

- `pypi` → `vendor/wheelhouse/<fichero>`
- `git` → `vendor/<key>/<fichero>`
- resto → `vendor/docs/<key>/<fichero>`

Cada pieza descargada deja su sidecar `.sha256` al lado.

## Operaciones

- `vendor_download()` — encola en `downloads` (kind `vendor`, `ref` = key del lock) solo lo ausente o no verificable y lo baja con el motor de `fetch.py`; el hash del lock es el hash esperado de la descarga.
- `verify_offline()` — sin red: por entrada, `ok` / `missing` / `mismatch` / `no_hash` / `no_url`.
- `check_drift()` — versión instalada (`importlib.metadata`) contra versión documentada por cada dependencia directa: `ok` / `drift` / `undocumented` / `not_installed`.
- `capture_llama_help()` — si `llama-server` está en el PATH, vuelca su `--help` a `vendor/llama.cpp/llama-server-help.txt` con sidecar. Su ausencia degrada, no rompe.

El sellado del lote vendor indexado (§8.3, «sella un lote con el texto extraído e indexado») queda para la tarea H3.10, como fija el plan.

## Regenerar el lote desde el dashboard

`arca vendor` captura además, del binario de `llama-server` que encuentre (configurado, vendorizado o en el `PATH`), su `--help` **y su `--version`** —que imprime el commit exacto con el que se compiló— en `vendor/llama.cpp/`, cada uno con su sidecar `.sha256`. Eso es lo que hace consultable sin red la documentación del binario que se está usando de verdad, y lo que alimenta el formulario de arranque cuando el binario no está (ver `llama.md`).

`POST /api/admin/vendor/index` (botón «Regenerar lote de documentación») vuelve a indexar lo que hay vendorizado en disco y sella un lote nuevo, la misma tubería que `arca vendor --index`. Sin servidor de embeddings responde diciéndolo y no toca nada: el vendorizado sigue verificable sin red, solo su índice semántico queda pendiente.
