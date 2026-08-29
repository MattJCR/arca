# Interfaz de consulta

Ficheros: `arca/templates/index.html`, `arca/static/archive.js`, `arca/static/style.css`, `arca/static/fonts/`.

Es la pantalla del operador que consulta el archivo. La de administración es otra pantalla y otro público (§12, regla de la interfaz de usuario final): aquí no hay ajustes, solo el archivo.

## Pantalla partida

Rejilla de dos columnas: **archivo a la izquierda** (2,4 partes) y **asistente a la derecha** (1 parte, pegajosa al hacer scroll). Por debajo de 60 rem las columnas se apilan y el asistente pasa debajo. La izquierda es la que manda; el chat es un panel, no la protagonista (§16).

La columna del archivo tiene dos estados excluyentes:

1. **Búsqueda y resultados** — formulario (texto + colección) y la lista fusionada.
2. **Lectura** — el artículo abierto en un `<iframe>`, con «← Volver a los resultados».

## El sello de procedencia

Es el elemento distintivo del diseño (§16), y aparece **en cada resultado** y sobre el artículo abierto. Es una franja monoespaciada con:

`ZIM · <colección> · <ruta dentro de la colección> · #<posición dentro de esa colección>`

La posición lleva un `title` que explica que es el rango **dentro de su fuente** y que no se comparan puntuaciones entre colecciones. Cuando se navega por los enlaces internos del artículo, el sello **se actualiza solo**: el visor escucha la carga del `iframe`, lee su ruta y reescribe la franja. Así la procedencia nunca se queda desfasada respecto a lo que se está leyendo. Y al seguir un enlace **la posición desaparece**: `#3` se sustituye por «enlace seguido desde #3», porque el puesto en el ranking era del artículo que salió en la búsqueda, no de la página a la que se ha llegado. Enseñar ahí un `#3` sería atribuirle a esta página un puesto que nunca tuvo (§9.4). Este mismo sello es el que viaja al chat: al seleccionar texto en el visor, el panel recibe el texto con su documento y su ancla, y lo envía con la pregunta (ver `chat.md`).

## Visor

`<iframe sandbox="allow-same-origin">`. El sandbox bloquea el JavaScript que traiga el ZIM: los artículos se leen con sus estilos e imágenes, pero ningún script de la colección puede tocar la aplicación ni llamar a la API. La segunda barrera, y la que de verdad manda, es la `Content-Security-Policy` que acompaña a cada respuesta de `/zim/...` (ver `zim.md`): protege también cuando un artículo se abre en una pestaña suelta, fuera de este marco. Los enlaces internos se resuelven contra el `<base>` que inyecta `/zim/...` y navegan dentro del propio marco, sin salir de la aplicación.

## Estados honestos

La regla es no confundir dos situaciones distintas solo porque se parezcan en la
pantalla. `GET /api/search` devuelve un bloque `library` con el estado real:

| Estado | Cuándo | Qué dice la pantalla |
|---|---|---|
| `ok` | Hay al menos una colección legible y activa | Nada; se buscan |
| `all_disabled` | Hay colecciones, pero todas están desactivadas | «Siguen en el disco: vuelve a activar la que quieras» + enlace al dashboard |
| `empty` | No hay ningún `.zim` en `zim/` | «No hay ninguna colección» + enlace al catálogo |
| `unreachable` | La carpeta `zim/` no existe o no se puede leer | «Comprueba que el soporte sigue conectado» + enlace al diagnóstico |
| `unreadable` | Hay ficheros, pero ninguno abre | «Puede que estén corruptos» + enlace al diagnóstico |

Los ficheros que no abren se listan uno a uno con su error, aunque el resto de la
biblioteca funcione: una colección corrupta no esconde a las demás ni las demás
la esconden a ella.

El resto de estados:

- **Colección sin índice de texto completo**: nota bajo los resultados diciendo
  que ahí solo se busca por título; sin índice ninguno, que no aparece en los
  resultados.
- **Colección que no responde**: su error se muestra como nota y las demás
  contestan igual.
- **Franja semántica**: visible y declarada vacía mientras no exista índice
  profundo, con la aclaración de que la búsqueda por palabras funciona igual.
- **Panel de asistente**: distintivo de estado alimentado por el diagnóstico
  («modelo activo» / «sin modelo»); sin modelo, el panel explica que buscar,
  abrir artículos y seguir sus enlaces no dependen de él, y el formulario
  sigue ahí (ver `chat.md`).
- **Diagnóstico degradado o roto**: banda en la cabecera del archivo con enlace
  al dashboard. El detalle completo vive en la pestaña «Diagnóstico» de `/admin`
  (§12.11).
- **Artículo que no se puede servir**: página de error propia, en el idioma
  activo, con su código correcto (404 o 503) y sin traza (ver `zim.md`).
- **Sin JavaScript**: `<noscript>` que lo dice y remite al dashboard.

## Dirección visual

Paleta de §16 en variables CSS: base `#E7E9E4`, papel `#F1F2EF`, tinta `#17242B`, acento `#2F6F5E`, ámbar `#9A6B1F` (degradado), rojo `#8E2B22` (roto).

Tipografías **vendorizadas** en `static/fonts/`, servidas desde el propio servidor (cero CDN):

| Uso | Familia | Ficheros |
|---|---|---|
| Display (cabeceras, rótulos) | Archivo Narrow | `archivo-narrow-{latin,latin-ext}.woff2` |
| Cuerpo de lectura | Source Serif 4 | `source-serif-4-{latin,latin-ext}.woff2` |
| Datos, rutas, sellos, hashes | IBM Plex Mono | `ibm-plex-mono-{latin,latin-ext}.woff2` |

Archivo Narrow y Source Serif 4 son variables: un solo fichero por subconjunto cubre los pesos 400–600, declarado con `font-weight: 400 600` en `fonts.css`. Las tres son SIL Open Font License; el texto de cada licencia está junto a los ficheros (`OFL-*.txt` / `OFL-*.md`). Cada `@font-face` lleva su `unicode-range`, así que el navegador solo baja `latin-ext` cuando hace falta.

**Regenerar las fuentes** (única operación con red): pedir el CSS de `fonts.googleapis.com` con un `User-Agent` de navegador, quedarse con los subconjuntos `latin` y `latin-ext`, bajar los WOFF2 a `static/fonts/` y reconstruir `fonts.css`. No hay ninguna referencia a Google en el código servido: `tests/test_frontend.py` falla si aparece una URL externa en cualquier plantilla, hoja o script.

Suelo de calidad: responsive hasta móvil, `:focus-visible` de 3 px en todo, `prefers-reduced-motion` respetado, y contraste de la paleta de §16.

## Bilingüe

Igual que el dashboard: el servidor incrusta como JSON las claves `home.*` del catálogo activo y el JS las aplica a los atributos `data-i18n`, `data-i18n-placeholder` y `data-i18n-title`. Ninguna cadena de usuario vive en el JavaScript. `tests/test_i18n.py` comprueba las tres cosas: que toda clave usada por plantillas y scripts existe en **ambos** catálogos, que las familias de claves dinámicas están completas, y que ningún texto largo del catálogo aparece incrustado en un `.js`.

## Comprobado en

`tests/test_frontend.py` (cero red externa, tipografías con licencia, `@font-face` apuntando a ficheros presentes, paleta, sandbox del visor, ningún `innerHTML`), `tests/test_i18n.py` (cobertura de claves) y `tests/test_api.py` (portada en ambos idiomas).
