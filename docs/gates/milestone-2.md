# Puerta del hito 2 — Registro de ejecución

Fecha: 2026-08-28. Criterios de aceptación de `ARCA-especificacion.md` §9.5, ejecutados de principio a fin sobre una biblioteca real con tres ZIM de Kiwix descargados por el propio ARCA (`wikipedia_es_mathematics_mini`, `wikipedia_es_chemistry_mini`, `wikipedia_es_climate-change_mini`; 44 MiB, 36.650 artículos).

| # | Criterio | Resultado | Evidencia |
|---|---|---|---|
| 1 | Buscar un término y obtener resultados de al menos tres ZIM distintos, cada uno etiquetado con su origen | ✅ | «efecto invernadero» → 9 resultados de **3 colecciones distintas** en 48 ms. Cada resultado lleva `source_kind`, `source_id`, `source_title`, `path`, `url` y `rank`, y la interfaz los pinta como sello de procedencia. El intercalado se ve en el orden: los tres primeros resultados vienen de tres colecciones distintas |
| 2 | Abrir un artículo de Wikipedia con sus estilos e imágenes internas, y navegar por sus enlaces internos sin salir de la aplicación | ✅ | «Efecto invernadero» abre con 33.773 bytes, **13 de 13 hojas de estilo** servidas desde el propio ZIM por `/zim/{uuid}/...`, más sus imágenes y SVG. **7 de 7 enlaces internos** de muestra (incluidos los percent-codificados como `Deforestaci%C3%B3n`) resuelven contra el `<base>` inyectado y cargan dentro de la aplicación |
| 3 | Desconectar el disco externo durante una búsqueda: el sistema da un error claro y no corrompe nada | ✅* | Colección ilegible en caliente → **503** con página en el idioma activo: «No se ha podido leer la colección … Comprueba que el disco de la biblioteca sigue conectado y vuelve a intentarlo», **sin traza**. Al restablecer el acceso el mismo artículo vuelve a servirse con 200 y nada quedó modificado. La variante «la biblioteca entera desaparece» está en `tests/test_invariants.py`: `/api/search` degrada a resultados vacíos con 200, la portada sigue sirviéndose y el diagnóstico pasa a «roto» |
| 4 | Comprobar que no se ha creado ningún fichero bajo la raíz durante una sesión completa de consulta | ✅** | Sesión de **25 peticiones** (portada en los dos idiomas, diagnóstico en los dos, hoja de estilo, script, CSS de tipografías y un WOFF2, `/api/search` con consulta, con alcance, en modo semántico y vacía, los 9 artículos de los resultados y 4 enlaces internos): **creados = ninguno, borrados = ninguno**; el único fichero modificado es `library.db-shm` |

Suite en el momento de la puerta: **216 tests en verde**, `mypy` estricto limpio (34 ficheros), `ruff` limpio.

\* El criterio 3 exige literalmente desconectar el disco físico. Se aceptó el equivalente automatizado —el mismo camino de código: `ZimError` desde la capa de lectura— igual que se hizo en la puerta del hito 1. Para la prueba física basta expulsar el soporte durante un `arca serve`.

\*\* **El matiz de los dos sidecars.** `library.db-wal` y `library.db-shm` son la excepción sancionada en F0.3 y documentada en `docs/manual/invariants.md`: §7.1 obliga a WAL en `library.db`, y SQLite necesita el índice WAL en memoria compartida **incluso para leer**, así que una conexión `mode=ro` puede materializarlos. Está acotado y verificado en los dos sentidos: `tests/test_invariants.py` comprueba que lo único que puede aparecer son esos dos ficheros, y que **sin `library.db` en disco una sesión de consulta no crea absolutamente nada**. Los lotes sellados del hito 3 no tendrán excepción ninguna (`journal_mode=DELETE`, trampa 4).

## Lo que salió de ejercitar el sistema, no de leerlo

Tres defectos reales aparecieron durante este hito y se corrigieron antes de firmar la puerta:

1. **Bloqueo de fichero en Windows (invariante 2).** Un ZIM cacheado por `ZimLibrary` mantiene el fichero abierto, de modo que el `os.replace` final de una descarga reintentada sobre esa misma colección fallaba con un error del sistema: la descarga se completaba y verificaba entera, y aun así quedaba marcada como fallida. Corregido con `ZimLibrary.release(filename)` enganchado al `before_replace` del motor de descargas, más tres reintentos cortos del reemplazo. Regresión cubierta por `tests/test_zim.py::test_release_frees_the_file_so_it_can_be_replaced_in_place` y `tests/test_fetch.py::test_download_releases_an_open_zim_before_replacing_it`. Lo detectó la revisión de código de H2.1.

2. **Contenido de terceros ejecutándose en nuestro origen.** El visor sandboxea el `iframe`, pero un resultado es un enlace normal: abrirlo en una pestaña suelta cargaba el HTML del ZIM **fuera** del marco, donde su JavaScript habría corrido en nuestro propio origen con acceso a `/api/admin/*`. Corregido enviando `Content-Security-Policy: default-src 'none'; …` y `X-Content-Type-Options: nosniff` con **cada** respuesta de `/zim/...`, incluidas las de error: la política viaja con el contenido en vez de depender del marco, y de paso hace verificable la promesa de cero llamadas de red en tiempo de consulta.

3. **Un estado deshonesto.** Con todas las colecciones desactivadas, la portada habría mostrado una clave i18n sin traducir. Añadido el estado `all_disabled`, distinto de `empty`, `unreachable` y `unreadable`: la pantalla nunca confunde «biblioteca vacía» con «disco desconectado» ni con «todo desactivado».

## Además, verificado en vivo

- **§12.3 contenido local**: la pestaña del dashboard lista las tres colecciones con idioma, sabor, artículos, tamaño, **modo de índice real leído del fichero** y estado; desactivar una la saca de los resultados en la búsqueda siguiente y el fichero sigue en disco.
- **Cero red externa**: no hay ninguna URL `http(s)://` en plantillas, hojas ni scripts (`tests/test_frontend.py`), y las tres tipografías de §16 se sirven vendorizadas desde `static/fonts/` con sus licencias OFL al lado.
- **Riesgo nº1 del plan cerrado**: `libzim` 3.12.0 funciona en Windows, lector y escritor. No hizo falta ni WSL ni el plan B de `kiwix-serve`.

## Estado tras la puerta

Los cuatro criterios de §9.5 pasan. El hito 2 queda cerrado y F3 (ingesta de documentos propios) puede abrirse.

**Deuda declarada:** la revisión de código de H2.2–H2.7 se lanzó pero se cortó por límite de sesión antes de emitir veredicto; la revisión de seguridad se completó a mano y produjo el hallazgo 2 de arriba. Conviene relanzarla al abrir F3.
