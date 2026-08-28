# Interfaz web y API

Módulos: `arca/api.py`, `arca/templates/`, `arca/static/`, `arca/i18n.py`.

## Rutas

| Ruta | Qué sirve |
|---|---|
| `GET /` | Portada: estado real de la biblioteca (escaneo de raíz + diagnóstico). Con cero ZIM muestra la invitación a descargar (invariante 5) |
| `GET /admin` | Marcador de posición del dashboard; se construye en los siguientes hitos |
| `GET /api/diagnostics` | Diagnóstico en JSON localizado |
| `GET /static/*` | CSS y activos vendorizados |

`/docs`, `/redoc` y `/openapi.json` están desactivados: la API no expone esquemas.

## Bilingüe

Toda ruta acepta `?lang=es|en`; sin él manda `language` de `arca.json`. Los textos salen de `arca/locale/{es,en}.json` (mismo juego de claves en ambos, verificado por test). El HTML declara `lang` y ofrece el cambio de idioma en la cabecera. Añadir una cadena de usuario = añadirla a los dos catálogos.

## Renderizado

Plantillas HTML con `string.Template` (biblioteca estándar, sin Jinja): sustitución de `$placeholders`, todo valor dinámico escapado con `html.escape`. El CSS usa la paleta de §16 (base `#E7E9E4`, papel `#F1F2EF`, tinta `#17242B`, acento `#2F6F5E`, ámbar `#9A6B1F`, rojo `#8E2B22`), foco visible y `prefers-reduced-motion`. Las tipografías definitivas WOFF2 se vendorizan en H2.4.

## Invariante 1

El proceso de servicio no escribe bajo la raíz: verificado en la suite por `tests/test_api.py::test_query_routes_write_nothing_under_the_root`, que fotografía el árbol de ficheros de la raíz, recorre todas las rutas de consulta en ambos idiomas y comprueba que el árbol no cambió.
