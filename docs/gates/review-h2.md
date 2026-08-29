# Revisión de código H2.2–H2.7

Fecha: 2026-08-29. Deuda pendiente desde la puerta del hito 2 (`docs/gates/milestone-2.md`), que
se cerró sin veredicto de revisión. Checklist aplicado: `docs/development/review.md`.

## Alcance

| Tarea | Qué se revisó |
|---|---|
| H2.2 Servido de artículos | `arca/zim.py`, `_serve_zim` / `_zim_base_href` / `_zim_error` en `arca/api.py` |
| H2.3 Fusión de resultados | `arca/search.py`: `search_library`, `_zim_groups`, `interleave`, `_search_one` |
| H2.4 Frontend y dirección visual | `arca/static/archive.js`, `arca/templates/index.html`, `style.css` |
| H2.5 Invariante «consultar no escribe» | `tests/test_invariants.py` y la skill `verify-no-write` |
| H2.6 Contenido local | `/api/admin/zim`, `_zim_payload`, activar/desactivar |
| H2.7 Estados vacíos y degradados | `_library_state` y su render en `archive.js` |

## Hallazgo corregido

**El sello atribuía a una página una posición que nunca tuvo.** Al seguir un enlace interno dentro
del visor, `trackViewerNavigation()` reescribía la franja con la ruta nueva pero **conservaba el
`rank` del resultado de búsqueda original**. El lector veía `#3` sobre un artículo que no había
salido en la búsqueda: exactamente la clase de afirmación sin respaldo que §9.4 existe para
evitar, y en el componente que el proyecto usa como garantía de procedencia.

Corregido: al detectar que la ruta cargada no es la del resultado, el sello sustituye la posición
por «enlace seguido desde #3», con su explicación en el `title`. Claves `home.seal.followed` y
`home.seal.followed_hint` en `es` y `en`. Documentado en `docs/manual/interface.md`.

## Revisado sin hallazgos

- **Servido de artículos.** `_serve_zim` distingue los tres casos que importan (colección
  desconocida → 404, entrada desconocida → 404, colección ilegible en caliente → 503) y ninguno
  filtra traza. La CSP, `nosniff` y el `sandbox` del `iframe` son dos barreras independientes, y
  la CSP protege también al abrir el artículo en pestaña suelta. `inject_base` elimina un `<base>`
  previo antes de insertar el suyo, así que no puede haber dos.
- **Fusión.** `interleave` no puede entrar en bucle infinito con grupos vacíos, los pesos se
  alinean con los grupos porque estos se construyen antes, y no se normaliza ninguna puntuación
  entre fuentes (trampa 3). `_search_one` captura `ZimError`, del que cuelgan todos los fallos que
  `zim.py` puede levantar, así que una colección rota no cancela a las demás.
- **Caché de archivos ZIM.** La firma `(mtime_ns, size)` reabre el fichero cuando cambia y lo
  suelta cuando desaparece; un fichero que no abre queda en `failures` y se reintenta en el
  siguiente escaneo, sin envenenar la caché.
- **Frontend.** Ni un `innerHTML` en todo el árbol de `static/`, cero peticiones externas, y cada
  cadena visible sale del catálogo — las tres cosas están además atadas por tests en
  `tests/test_frontend.py` y `tests/test_i18n.py`.
- **Colección desactivada.** Sigue sirviéndose por enlace directo. Es lo documentado y lo
  pretendido: desactivar la saca **de las búsquedas**, no la borra ni la esconde.

## Observación sin acción

`_selected()` llama a `library.refresh()` en cada búsqueda, lo que implica un `stat` por fichero
ZIM por consulta. Es deliberado: es lo que hace que un disco desconectado se note en caliente
(§9.5) y el coste es despreciable frente a la propia búsqueda. Se deja como está.

## Veredicto

**Aprobado.** Un hallazgo real, corregido en esta misma revisión. La puerta de calidad queda en
492 tests en verde, `mypy` estricto limpio y `tests/test_invariants.py` 8/8.
