# El manual dentro de la aplicación

Módulo: `arca/manual.py`. Sección 12.13 del dashboard. El manual del proyecto se lee desde la propia aplicación, sin red y sin salir de la interfaz.

## De dónde salen las páginas

`manual_dir()` busca en este orden:

1. `arca/manual_pages/`, que es donde el empaquetado deja el manual (`force-include` de `docs/manual` en `pyproject.toml`). Es lo que existe en una instalación real.
2. `docs/manual/` del repositorio, que es lo que existe mientras se desarrolla.

Si no hay ninguno, el panel lo dice y no se rompe nada.

## Rutas

| Ruta | Qué sirve |
|---|---|
| `GET /api/manual` | Índice: nombre, título leído del `# ` de cada página, tamaño y URL. `README` va siempre primero |
| `GET /api/manual/{page}` | La página renderizada como documento HTML completo, con la hoja de estilos y las tipografías vendorizadas |

El nombre de página se valida contra minúsculas, dígitos, punto, guion y guion bajo, y se comprueba además que el fichero resuelto siga dentro del directorio del manual: `../../pyproject` no llega a abrir nada. Una página que no existe devuelve 200 con el aviso localizado, no una traza.

## El renderizador

No hay dependencia de Markdown: son cinco dependencias directas y ninguna se gasta en esto. `render_markdown()` cubre exactamente el subconjunto que el manual usa —encabezados, párrafos, listas ordenadas y sin ordenar, tablas, bloques de código, reglas, `código`, **negrita** y enlaces— y **escapa todo antes de formatear**, así que un `<script>` dentro del texto sale como texto. La página se sirve con la misma política de contenido que los documentos propios (`default-src 'none'`), y el panel la muestra en un `iframe`: el JavaScript de la interfaz nunca escribe HTML (regla verificada por `tests/test_frontend.py`).

Los enlaces entre páginas del manual (`[algo](search.md)`) se convierten en enlaces internos a `/api/manual/search`, así que la navegación funciona dentro del propio visor. Los enlaces a cualquier otra cosa se muestran como texto con su destino a la vista: un archivo offline no ofrece un clic que necesitaría red.

## Interfaz

Pestaña «Manual»: índice a la izquierda con el título de cada página y su tamaño, contenido a la derecha. Por debajo de 52 rem el índice pasa arriba y el contenido debajo.
