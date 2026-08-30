---
name: verify-anchors
description: Comprueba que el ancla p:PPPP:OO de un formato reflowable (EPUB, FB2, TXT, MOBI/AZW3) es determinista con el layout fijado — dos pases sobre el mismo fichero producen los mismos bloques con las mismas anclas. Usar antes de meter un formato reflowable en la lista blanca, tras tocar el layout fijado o el extractor, y antes de cerrar la fase 10.
allowed-tools: [Read, Bash, Grep, Glob]
---

# Verificar «el ancla no se mueve»

Es la trampa 12 de `ARCA-especificacion.md` §17: **anclas que cambian al
reimportar**. Un lote sellado guarda `p:PPPP:OO` para cada bloque y cada trozo;
si el mismo fichero reimportado reparte su texto en otras páginas, todas las
citas de los lotes anteriores apuntan al sitio equivocado y no hay forma de
notarlo — el texto sigue estando, solo que en otra página.

Un EPUB, un FB2, un TXT o un MOBI **no tienen páginas**: se las inventa el
maquetador con el ancho, el alto y el cuerpo que se le den. ARCA fija esos tres
valores (`arca/formats.py`, `FIXED_LAYOUT`) y los graba en el lote. Esta skill
comprueba que fijarlos basta.

## Cuándo ejecutarla

- Antes de meter un formato reflowable nuevo en la lista blanca.
- Tras tocar `arca/formats.py`, el extractor reflowable o el valor del layout.
- Tras subir de versión PyMuPDF.
- Antes de cerrar la fase 10.

## Cómo se ejecuta

```
.venv/Scripts/python.exe scripts/verify_anchors.py
```

Con muestras propias en vez de las sintéticas:

```
.venv/Scripts/python.exe scripts/verify_anchors.py --samples <carpeta con .epub .fb2 .txt>
```

La regresión equivalente vive en la suite:

```
.venv/Scripts/python.exe -m pytest tests/test_formats.py -q
```

## El contrato exacto

Para cada muestra, dos pases —el segundo **en otro proceso y sobre una copia con
otro nombre**— tienen que dar:

- el mismo número de páginas tras el layout,
- el mismo número de bloques,
- la misma lista de `(ancla, texto)`, y por tanto el mismo sha-256 de esa lista.

El segundo pase corre en un subproceso a propósito: así se descarta que la
igualdad venga de un estado en memoria, y el cambio de nombre descarta que la
ruta del fichero entre en el reparto.

Salida verde: `[anchors] deterministic`. Cualquier otra cosa es la puerta en
rojo.

## Qué hacer si falla

**No se relaja el test y no se sigue con la fase.** Si dos pases discrepan, el
ancla por página no sirve para ese formato y hay que pasar al ancla por ítem del
*spine*, que cambia el formato de ancla y obliga a re-sellar los lotes que ya
tengan ese formato dentro. Antes de tocar nada:

1. Mira **qué** discrepa: si cambia el número de páginas, es el layout; si
   cambian los bloques dentro de una página, es el extractor.
2. Comprueba que el layout se está aplicando: `open_document` solo llama a
   `layout()` cuando `is_reflowable` es cierto.
3. Comprueba que no ha cambiado la versión de PyMuPDF (`arca vendor --verify`).
   Un cambio de motor de maquetado mueve los cortes de página, y eso invalida
   los lotes ya sellados que contengan formatos reflowables — está escrito en
   `docs/manual/documents.md`.

## Registro

La ejecución que cerró la puerta F10.0 está en `docs/gates/f10-anchors.md`.
