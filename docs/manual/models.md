# Modelos: buscar, descargar y elegir

Módulos: `arca/hub.py` (cliente del hub), `arca/llama.py` (lectura de cabeceras GGUF), `arca/api.py` (rutas). Sección 12.6 del dashboard.

## Lo que hay en la biblioteca

`GET /api/admin/models` lista los GGUF presentes en `models/chat/` y `models/embed/` con arquitectura, cuantización, `n_ctx_train`, capas y tamaño **leídos de la cabecera del propio fichero**, nunca del nombre. Un fichero que no sea GGUF o cuya cabecera no se pueda leer sale con su error, no desaparece.

## Buscar modelos en el hub

Con red, el panel busca modelos GGUF en el hub configurado (`hub_url`, por defecto Hugging Face) y muestra, por cada resultado, descargas, «me gusta», fecha y si su licencia hay que aceptarla.

Al abrir un modelo se listan **sus variantes**: cuantización deducida del nombre del fichero (`Q4_K_M`, `Q8_0`, `IQ2_XXS`, `BF16`…), número de parte cuando el modelo viene troceado, tamaño y hash. Solo se listan ficheros `.gguf`: el resto del repositorio no interesa aquí.

| Endpoint | Qué hace |
|---|---|
| `GET /api/admin/hub?q=&limit=` | Busca modelos GGUF. Sin `q` no hace ninguna llamada de red |
| `GET /api/admin/hub/{model_id}` | Variantes del modelo, con cuantización, tamaño y hash, más los ficheros que ya están en la biblioteca |
| `POST /api/admin/hub/download` | Encola una variante concreta (`model_id`, `path`, `kind`) |

Sin red, la búsqueda responde 502 con el error y **el resto del panel sigue igual**: buscar en el archivo, importar, chatear y diagnosticar no dependen del hub.

## Descargar

La descarga no es un camino nuevo: la variante elegida entra en la **misma cola reanudable y verificada** que los ZIM (ver `downloads.md`), con destino `models/chat/` o `models/embed/` según lo que hayas elegido. Se hereda todo: reanudación por `Range`, espejos, reintentos, pausa y verificación por hash.

El hash viene del propio hub cuando el fichero está en LFS —el `oid` es su sha-256— y entonces la descarga se verifica contra él. Cuando no lo publica, se descarga sin hash de origen, se avisa en pantalla y se guarda el calculado para verificaciones locales futuras: es la trampa 6 de §17, resuelta igual que en el catálogo Kiwix.

La URL de descarga **se construye en el momento** (trampa 5): no se guarda ninguna URL en la base de datos más allá del espejo de la descarga en curso.

## Licencias que hay que aceptar

Algunos modelos —Gemma entre ellos— exigen aceptar sus términos en el hub antes de poder descargarlos. El buscador los marca, y sin credenciales el hub responde 401 o 403, que ARCA traduce a un mensaje claro en vez de a un fallo genérico. Para descargarlos desde el panel hace falta poner un token en `hub_token` de `arca.json`; ese token **nunca sale en la exportación de configuración** ni se escribe en el log. La alternativa siempre disponible es descargar el fichero a mano y dejarlo en `models/chat/`.

## Seguridad de las entradas

El identificador del modelo se valida contra `autor/nombre` (letras, dígitos, punto, guion y guion bajo) y el nombre del fichero se reduce a su base: una ruta con `..`, absoluta o con separadores raros se rechaza con 400 antes de tocar el disco. El destino siempre es `models/<chat|embed>/<fichero>.gguf`.

## Elegir el modelo activo

`chat_model` de `arca.json` nombra el GGUF de chat que se usa; vacío significa «el primero legible». El diagnóstico distingue los dos casos que importan: **no hay ningún modelo** (degradado, falta algo opcional) y **el modelo elegido ha desaparecido** (roto, el chat configurado ya no puede arrancar). Ver `diagnostics.md`.
