# Interfaz de consulta

Ficheros: `arca/templates/index.html`, `arca/static/archive.js`, `arca/static/style.css`, `arca/static/theme.js`, `arca/static/icons.js`, `arca/static/fonts/`.

Es la pantalla del operador que consulta el archivo. La de administración es otra pantalla y otro público (§12, regla de la interfaz de usuario final): aquí no hay ajustes, solo el archivo.

## Tres columnas

Rejilla de tres columnas fijas que ocupan el alto de la ventana: **resultados** (21 rem), **lectura** (el resto) y **asistente** (23 rem). Cada columna hace su propio scroll; la página no se desplaza entera. La izquierda es la que manda; el chat es un panel, no la protagonista (§16).

Lo que cambió respecto a la versión de dos columnas: **buscar y leer ya no son estados excluyentes**. Antes, abrir un resultado ocultaba el formulario y la lista entera; ahora la columna de resultados se queda donde está, el documento abierto se marca con `aria-current="true"` y un filete de acento, y «← Volver a los resultados» solo cierra el visor. Se puede saltar de un resultado a otro sin volver atrás.

La columna central tiene dos estados:

1. **Vacía** — invitación a elegir un resultado (`#viewer-empty`).
2. **Lectura** — el documento en un `<iframe>` a sangre, con su barra propia arriba (`#viewer-pane`).

Por debajo de 78 rem las columnas se estrechan; por debajo de 64 rem se apilan en el orden resultados → lectura → asistente, el alto fijo se suelta y la columna vacía desaparece.

## Controles de lectura

En la barra del visor, a la derecha del sello: **A− / A+** en siete pasos, de 85 % a 150 %. La preferencia se guarda en `localStorage` (`arca.reader-scale`) y se reaplica en cada carga del marco.

Funciona escribiendo la propiedad `--reader-scale` en el `documentElement` del documento enmarcado, dentro de un `try`/`catch`. Solo surte efecto en **nuestras** páginas —el visor legible de un PDF y el manual—, porque son las únicas cuyo CSS declara `font-size: calc(17px * var(--reader-scale, 1))`. Un artículo ZIM trae su propia hoja y la variable no le dice nada: el control se queda sin efecto ahí, y eso es preferible a deformar el CSS ajeno.

## El sello de procedencia

Es el elemento distintivo del diseño (§16), y aparece **en cada resultado** y sobre el artículo abierto. Es una franja monoespaciada con:

`ZIM · <colección> · <ruta dentro de la colección> · #<posición dentro de esa colección>`

La posición lleva un `title` que explica que es el rango **dentro de su fuente** y que no se comparan puntuaciones entre colecciones. Cuando se navega por los enlaces internos del artículo, el sello **se actualiza solo**: el visor escucha la carga del `iframe`, lee su ruta y reescribe la franja. Así la procedencia nunca se queda desfasada respecto a lo que se está leyendo. Y al seguir un enlace **la posición desaparece**: `#3` se sustituye por «enlace seguido desde #3», porque el puesto en el ranking era del artículo que salió en la búsqueda, no de la página a la que se ha llegado. Enseñar ahí un `#3` sería atribuirle a esta página un puesto que nunca tuvo (§9.4). Este mismo sello es el que viaja al chat: al seleccionar texto en el visor, el panel recibe el texto con su documento y su ancla, y lo envía con la pregunta (ver `chat.md`).

## Visor

`<iframe sandbox="allow-same-origin allow-scripts">`. Y ese `allow-scripts` es deliberado, con su
razón y su coste. Sin él **no se ejecuta ningún script dentro del marco, tampoco los nuestros**: con
un solo sandbox para las tres fuentes que el visor muestra, `selection.js` nunca corría —el texto
señalado no llegaba al asistente— y PDF.js tampoco, así que «ver el original» abría un marco en
blanco. Ambas cosas estaban muertas.

Lo que separa ahora al ZIM de nuestras páginas **no es el sandbox, es la CSP**, que es la barrera
que el proyecto siempre dijo que mandaba. Cada respuesta de `/zim/...` lleva
`script-src 'nonce-…'` con un nonce nuevo, y **solo el lector que ARCA inyecta lleva ese nonce**:
ningún script del ZIM se ejecuta, ni en línea, ni por `src`, ni desde nuestro propio origen —la
directiva no incluye `'self'`—. La CSP protege además cuando un artículo se abre en una pestaña
suelta, fuera de este marco.

**El coste, dicho claro:** antes el ZIM tenía dos barreras (sandbox y CSP) y ahora tiene una, la
CSP. A cambio, el asistente puede leer y hablar de lo que tienes delante, que es la mitad del
producto. `tests/test_api.py` fija la propiedad que sostiene el trato: la directiva es solo un
nonce, cambia en cada respuesta, y en el cuerpo hay exactamente un `nonce=`. Los enlaces internos se resuelven contra el `<base>` que inyecta `/zim/...` y navegan dentro del propio marco, sin salir de la aplicación.

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

Paleta de §16 en variables CSS, con dos temas completos. Claro: base `#d7d3c8`, papel `#e6e2d8`, tarjeta `#f4f2ec`, tinta `#171512`. Oscuro: base `#0f0e0d`, papel `#1a1917`, tarjeta `#211f1d`, tinta `#ece8df`. Señales, en claro/oscuro: acento `#b04408`/`#e8763a` (acción), rojo `#a1160f`/`#f0685c` (destructivo y roto), ámbar `#8a5a06`/`#d9a63f` (degradado), verde `#23663a`/`#5fb377` (correcto), azul `#1f4f6b`/`#6aaed0` (informativo). Todos los pares texto-fondo cumplen AA en ambos temas.

Un solo nombre de variable sirve a los dos temas: `--accent` es naranja en claro y naranja claro en oscuro, y ninguna regla fuera del bloque de tokens escribe un color literal. La única excepción es `--on-accent`, el color del texto **sobre** una superficie de acento: blanco en claro, `#1a0d05` en oscuro, porque el blanco sobre el naranja claro se queda en 2,96:1 y no llega a AA.

## Tema claro y oscuro

`arca/static/theme.js`, cargado en el `<head>` de todas las páginas —también las que van dentro del marco: el visor legible, el visor de PDF y el manual— para que el tema esté puesto antes del primer pintado.

Tres estados, en este orden:

1. **Elección explícita** — `data-theme="light"` o `"dark"` en `<html>`, guardado en `localStorage` bajo `arca.theme`. Manda sobre todo lo demás.
2. **Sin elección** — no hay atributo y decide `prefers-color-scheme` mediante `:root:not([data-theme="light"])` dentro de la media query. El sistema operativo sigue mandando y sus cambios se reflejan en vivo.
3. **Sin JavaScript o sin `localStorage`** — igual que el anterior. Todos los accesos al almacenamiento van en `try`/`catch`: el modo privado del navegador o el almacenamiento bloqueado degradan a la preferencia del sistema, nunca a una excepción.

El conmutador vive en la cabecera de las dos pantallas y es un `role="group"` de dos botones con `aria-pressed`. Las páginas enmarcadas no lo llevan: leen la elección y la aplican, pero no la ofrecen, porque su cabecera es la de la página que las contiene.

**El contenido ajeno no se tematiza.** Un artículo ZIM trae su propio CSS, escrito para fondo claro; pintarle un fondo oscuro debajo deja texto negro sobre negro. Así que el visor mantiene el papel claro para lo que no es nuestro, con tres capas que se refuerzan: `--frame` (blanco en los dos temas) en el `iframe` y en el panel que lo sostiene, `color-scheme: light` en el elemento `iframe`, y un `<meta name="color-scheme" content="light">` inyectado en la cabecera del propio artículo por `inject_colour_scheme` (`arca/zim.py`). La tercera es la que manda: sin ella el navegador propaga al marco el `color-scheme` del documento que lo contiene y pinta el lienzo oscuro **por debajo** del fondo del `iframe`, donde el CSS del padre ya no llega. Nuestras propias páginas enmarcadas —el visor legible y el manual— sí siguen el tema, porque cargan `style.css` y pintan su propio fondo encima.

Los tokens del tema oscuro están escritos dos veces a propósito —una en la media query y otra en `:root[data-theme="dark"]`— para que el tema funcione sin JavaScript y la elección explícita pueda además ir en contra del sistema. `tests/test_frontend.py` comprueba que cada color oscuro aparece al menos dos veces.

Tipografías **vendorizadas** en `static/fonts/`, servidas desde el propio servidor (cero CDN):

| Uso | Familia | Ficheros |
|---|---|---|
| Interfaz (botones, tablas, formularios) | Archivo | `archivo-{latin,latin-ext}.woff2` |
| Display (cabeceras, rótulos) | Archivo Narrow | `archivo-narrow-{latin,latin-ext}.woff2` |
| Cuerpo de lectura | Source Serif 4 | `source-serif-4-{latin,latin-ext}.woff2` |
| Datos, rutas, sellos, hashes | IBM Plex Mono | `ibm-plex-mono-{latin,latin-ext}.woff2` |

Archivo, Archivo Narrow y Source Serif 4 son variables: un solo fichero por subconjunto cubre todo su rango de pesos, declarado con `font-weight: 400 700` (o `400 600`) en `fonts.css`. Archivo y Archivo Narrow son la misma superfamilia de Omnibus-Type, así que la interfaz y sus rótulos comparten esqueleto sin que la estrecha tenga que hacer de texto corrido. Las cuatro son SIL Open Font License; el texto de cada licencia está junto a los ficheros (`OFL-*.txt` / `OFL-*.md`). Cada `@font-face` lleva su `unicode-range`, así que el navegador solo baja `latin-ext` cuando hace falta.

**Regenerar o añadir una fuente** (única operación con red) es un script del repositorio, `scripts/vendor_font.py`, no un procedimiento a mano:

```
.venv/Scripts/python.exe scripts/vendor_font.py Archivo "wght@400..700" archivo "400 700"
```

Pide el CSS de `fonts.googleapis.com` con un `User-Agent` de navegador, se queda con los subconjuntos `latin` y `latin-ext`, baja los WOFF2 a `static/fonts/`, guarda la OFL desde el repositorio de Google Fonts y añade los `@font-face` a `fonts.css` con rutas locales. Si la familia ya estaba declarada, reemplaza los WOFF2 y deja el CSS como está. Falla en vez de seguir si el subconjunto no existe, si lo que llega no es un WOFF2 o si la licencia no es una OFL. No hay ninguna referencia a Google en el código servido: `tests/test_frontend.py` falla si aparece una URL externa en cualquier plantilla, hoja o script.

Suelo de calidad: responsive hasta móvil, `:focus-visible` de 3 px en todo, `prefers-reduced-motion` respetado, y contraste de la paleta de §16.

## Bilingüe

Igual que el dashboard: el servidor incrusta como JSON las claves `home.*` del catálogo activo y el JS las aplica a los atributos `data-i18n`, `data-i18n-placeholder` y `data-i18n-title`. Ninguna cadena de usuario vive en el JavaScript. `tests/test_i18n.py` comprueba las tres cosas: que toda clave usada por plantillas y scripts existe en **ambos** catálogos, que las familias de claves dinámicas están completas, y que ningún texto largo del catálogo aparece incrustado en un `.js`.

## Comprobado en

`tests/test_frontend.py` (cero red externa, tipografías con licencia, `@font-face` apuntando a ficheros presentes, paleta, sandbox del visor, ningún `innerHTML`), `tests/test_i18n.py` (cobertura de claves) y `tests/test_api.py` (portada en ambos idiomas).

## Probado en un navegador de verdad

`tests/test_browser.py` conduce el Chrome instalado en la máquina con Playwright —sin descargar
ningún navegador, `channel="chrome"`— y ejerce lo que ninguna otra prueba veía: que el visor abre
el artículo dentro del marco, que **el lector inyectado se ejecuta** dentro de un ZIM, que
seleccionar texto llega al panel del asistente con su procedencia, que la pregunta viaja con la
selección y con la página abierta, y que pulsar «Preguntar» hace un **POST** a `/api/chat` y pinta
la respuesta.

Existe por una razón concreta: tres defectos de esta serie —el chat que devolvía 405, `PDF.js` que
no arrancaba y la selección que nunca llegaba— eran invisibles para una suite verde porque nadie
ejercía la página. Si Playwright o Chrome no están, el módulo se salta solo.
