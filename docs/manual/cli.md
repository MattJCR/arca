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

## Dónde vive

`build_parser()` y `main()` en `arca/cli.py`; los textos, en `arca/locale/{es,en}.json`.
