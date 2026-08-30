# Puerta F10.0 — ¿Es determinista el ancla?

Fecha: 2026-08-30. Trampa 12 de `ARCA-especificacion.md` §17 («anclas que cambian al reimportar»)
aplicada a los formatos reflowables de la fase 10.

## La pregunta

Un EPUB, un FB2 o un TXT no tienen páginas propias: se las inventa el maquetador con el ancho, el
alto y el cuerpo que se le den. ARCA ancla cada bloque como `p:PPPP:OO` y graba ese ancla en un lote
sellado que ya no se toca. Si dos importaciones del mismo fichero repartieran el texto de otra
manera, cada cita de los lotes anteriores apuntaría al párrafo equivocado sin que nada lo delate.

La decisión previa a la puerta era: **fijar el layout y grabarlo en el lote**. La puerta comprueba
si eso basta o si hay que pasar al ancla por ítem del *spine*, que cambia el formato de ancla.

## Lo que se fijó

`arca/formats.py`, `FIXED_LAYOUT`: **595 × 842 puntos, cuerpo 11**. Firma `595x842@11`. Es A4 a 72
ppp con el mismo cuerpo que ya usaban las muestras de PDF de la suite. Se aplica solo cuando
`document.is_reflowable` es cierto, así que un PDF o un XPS pasan por `open_document` sin que nada
les cambie.

## Cómo se midió

`scripts/verify_anchors.py`. Para cada muestra hace dos pases y compara:

1. El primero, en el proceso del script, sobre el fichero original.
2. El segundo, **en otro proceso** (`--report`), sobre una **copia con otro nombre**.

El subproceso descarta que la igualdad venga de un estado en memoria; el cambio de nombre descarta
que la ruta del fichero entre en el reparto de páginas. De cada pase se saca la lista completa de
`(ancla, texto)` y su sha-256.

PyMuPDF **1.27.2.3** (MuPDF 1.27.2), Python 3.12.7, Windows 11.

## Resultado

```
[anchors] layout 595x842@11
[anchors] sample.epub: 25 blocks on 2 pages -> same
[anchors]   pass 1 8fe34793a00ab3988246e5afef0890cb51b62ee6350afc76ec51a65a219ff4ca
[anchors]   pass 2 8fe34793a00ab3988246e5afef0890cb51b62ee6350afc76ec51a65a219ff4ca
[anchors] sample.fb2: 25 blocks on 2 pages -> same
[anchors]   pass 1 b1448f013042a8e169eb84e5861329ca5b66bc686764de576ea9777cc5120148
[anchors]   pass 2 b1448f013042a8e169eb84e5861329ca5b66bc686764de576ea9777cc5120148
[anchors] sample.txt: 13 blocks on 2 pages -> same
[anchors]   pass 1 2cc9f0b202fc10f7dda568f2b56a84de75bb526bfd324522e58f99ed0840ac47
[anchors]   pass 2 2cc9f0b202fc10f7dda568f2b56a84de75bb526bfd324522e58f99ed0840ac47
[anchors] deterministic
```

| Formato | Bloques | Páginas tras el layout | Dos pases |
|---|---|---|---|
| EPUB | 25 | 2 | idénticos |
| FB2 | 25 | 2 | idénticos |
| TXT | 13 | 2 | idénticos |

**Puerta en verde: el ancla por página vale para los reflowables mientras el layout esté fijado.**
La fase sigue con el formato de ancla `p:PPPP:OO` intacto. Ni el visor, ni el chat, ni los lotes ya
sellados se enteran del cambio.

## La condición que la puerta deja escrita

Determinista **dado un layout y un motor de maquetado**. Las dos cosas que invalidan un lote con
formatos reflowables dentro son:

- **cambiar `FIXED_LAYOUT`**, y
- **cambiar la versión de PyMuPDF/MuPDF**, porque el reparto de páginas es suyo.

Por eso el layout se graba en el manifiesto del lote (F10.2) y no se deduce: un lote sellado con
otro layout se puede detectar, y lo que no se puede detectar es lo que hace daño. La consecuencia
—re-sellar— queda documentada en `docs/manual/documents.md`.

## Regresión permanente

- Skill `verify-anchors` (turn-based, la del bucle de la fase).
- `tests/test_formats.py`: los dos pases, el cambio de nombre, que otro layout **sí** mueve las
  anclas (si no lo moviera, la prueba no estaría midiendo nada) y que aplicar el layout a un PDF no
  cambia su paginación.
