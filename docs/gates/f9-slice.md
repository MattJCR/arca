# Puerta F9.0 — ¿Suena a música?

Fecha: 2026-08-31. Rama `music`. Primera tarea de la fase 9 de `ARCA-plan.md` y **la única que
puede cerrar la fase entera**: si lo que sale no suena a biblioteca de producción por ninguno de los
dos caminos, F9 se cierra como no viable y no se escribe una línea más.

**El veredicto no está en este documento.** Lo da el operador escuchando. Aquí está lo que hay que
escuchar, cómo ponerlo a sonar y lo que sí se ha podido medir sin oídos.

## Lo que se construyó

La rodaja más fina de extremo a extremo, por los dos orígenes de material del anexo A, tocando
**la misma pieza** con el mismo motor, la misma progresión, la misma semilla (184729) y la misma
reverberación. Lo único que cambia entre las dos versiones es el material.

- `arca/music/material.py` — el manifiesto (instrumento, nota, capa, `sha256`, origen, licencia,
  procedencia), lectura y escritura de WAV PCM mono, curación (mono, remuestreo, recorte de
  silencios, normalización) y validación por hash. Es el único contrato que el navegador conoce.
- `arca/music/render.py` — el renderizador. Campanas por **síntesis modal** con la serie de
  parciales de un tubo real (2 : 2,95 : 4,05 : 5,35 sobre la nota escrita), cuerda percutida modal
  con inarmonicidad, pads y drone aditivos con deriva lenta, y una respuesta de impulso sintética
  por bandas. `numpy` y `wave`; ninguna dependencia nueva.
- `arca/music/slice/` — la página suelta y el motor Web Audio: voces con desafinación leve,
  tres buses (grave, medio, agudo) con envío a una convolución compartida, limitador en el maestro
  y planificador con anticipación. Sin perfiles, sin estilos, sin i18n, sin tocar ARCA.
- `scripts/f9_slice.py` — `render`, `fetch`, `verify` y `serve`.

Nada de esto está enlazado desde ARCA. No hay ruta, ni plantilla, ni enlace: la página se sirve
aparte y el material vive fuera del repositorio.

## Cómo escucharlo

```
python scripts/f9_slice.py --out <carpeta> render     # material sintetizado (~15 MB, 4 s)
python scripts/f9_slice.py --out <carpeta> fetch      # material muestreado (~85 MB de descarga)
python scripts/f9_slice.py --out <carpeta> verify     # hashes y alturas declaradas
python scripts/f9_slice.py --out <carpeta> serve      # http://127.0.0.1:8931/
```

`fetch` es el único paso que toca la red. `render` no la necesita, y las dos versiones se pueden
escuchar por separado: si solo se ha renderizado, la pestaña de muestreado lo dice y no rompe nada.

En la página: elegir origen, **Play five minutes**, y escuchar. La tirada guionizada mantiene tres
intensidades de cien segundos cada una y para a los cinco minutos. **Render 5 min to WAV** deja el
mismo tramo como fichero para compararlos en un reproductor cualquiera, sin navegador de por medio.

## Las dos decisiones que la escucha tiene que resolver

1. **¿Suena a música o a politono?** Si ninguna de las dos versiones pasa, F9 se cierra aquí.
2. **¿Qué origen manda?** Si pasa el sintetizado, el renderizador es el suelo y las muestras son un
   lujo opcional. Si solo pasa el muestreado, F9.3 (descarga desde el panel) deja de ser opcional y
   pasa a ser el camino principal, con lo que eso implica para un sistema que presume de funcionar
   sin red.

## Lo que sí se midió

Las dos versiones se renderizaron sin tiempo real con `OfflineAudioContext` en Chromium, con el
mismo motor de la página, y se midió la señal resultante. Cinco minutos, 306 segundos con la cola.

| | sintetizado | muestreado |
|---|---|---|
| Pico | 0,807 | 0,809 |
| Muestras recortadas | 0 | 0 |
| RMS global | 0,207 | 0,150 |
| RMS por tramo (20 % / 55 % / 90 %) | 0,176 · 0,206 · 0,234 | 0,121 · 0,156 · 0,173 |
| Salto máximo entre muestras contiguas | 0,076 | 0,086 |
| Ventanas de 50 ms por debajo de −48 dBFS | 4 de 6019 | 57 de 6019 |
| Hueco más largo por debajo de ese umbral | 0,2 s | 0,8 s |
| Excepciones en la página | ninguna | ninguna |

Qué dice cada fila:

- **Nada recorta.** El limitador del maestro no llega a trabajar de más.
- **El salto máximo entre muestras contiguas** es el indicador barato de clic. 0,08–0,09 es lo que
  produce contenido normal a esas frecuencias y amplitudes; un clic de verdad da saltos de otro
  orden. No hay evidencia de clics en ninguna de las dos.
- **La intensidad sube el RMS suavemente y de forma monótona** en los dos orígenes: se traduce en
  densidad de arreglo, no en un mando de volumen. Es lo que pide A.5.
- **Los huecos.** El sintetizado solo se queda por debajo del umbral en el fundido de entrada. El
  muestreado tiene además unas cuantas respiraciones cortas, casi todas en el tramo del 20 %, donde
  solo suenan el drone y dos pads de vibráfono con arco, que decaen más que un pad sintetizado. La
  más larga es de 0,8 s. Es la diferencia real entre los dos materiales en el tramo más desnudo, y
  conviene escucharla sabiendo que está ahí: puede leerse como respiración o como bache.

Esto **no** dice que suene bien. Dice que no está roto: ni recorta, ni se corta, ni lanza
excepciones, y la intensidad hace lo que promete. Lo demás lo deciden los oídos.

## Dos cosas que casi salen mal

**Las campanas sintetizadas sonaban diez semitonos por debajo de su nombre.** La tabla de parciales
inicial daba la mayor amplitud al parcial de zumbido (0,56 × la fundamental), que es el que fija la
altura percibida. Ahora la serie está escrita relativa a la nota golpeada y el parcial dominante cae
en 2,0 ×, como en un tubo real. `verify` lo comprueba y `tests/test_music.py` lo fija.

**Las muestras de VCSL no suenan en la nota de su nombre.** Medido parcial a parcial: el vibráfono
con arco y las campanas tubulares suenan **una octava por encima** de su nombre de fichero —el
`chimes_C3` tiene sus parciales en 524, 775, 1068, 1401 y 1768 Hz, que es exactamente la serie
2 : 3 : 4,2 : 5,4 : 6,8 sobre **do4**, no sobre do3—, mientras que el piano sí está en su nota. Sin
esa medición el arreglo entero habría estado una octava fuera de sitio en dos de las tres capas, y
el error se habría atribuido a la composición. `verify` compara ahora el parcial dominante de cada
fichero con su altura declarada y acepta solo relaciones reconocibles (armónicos y parciales de
campana), en vez de exigir que coincidan.

## Material muestreado: procedencia y licencia

Versilian Community Sample Library (`sgossner/VCSL`), commit `c1ea7bcc`, **CC0 1.0 Universal**.
Veinte ficheros recortados de tres instrumentos: vibráfono con arco (pad y drone), piano Steinway B
sostenido en su dinámica más suave (teclado) y campanas tubulares (campanas). De ~85 MB descargados
quedan ~14 MB curados. Cada fichero anota su licencia y su origen en el manifiesto, `SOURCES.json`
guarda la ruta remota y el `sha256` del original, y el texto de la licencia viaja junto al material.

## Invariantes

`arca/`, la plantilla y `arca/static/` no cambian: Music no existe todavía para ARCA. El material se
escribe fuera del repositorio, `render` y `fetch` son reanudables y verificados por hash
(invariantes 2 y 7), y un fichero corrupto o ausente lo detecta `validate_material` antes de usarlo.
La página no arranca sola: la primera reproducción la pide el usuario.

`verify-no-write` no se ejerce aquí: no hay camino de consulta que tocar todavía. Es la puerta de
F9.11.
