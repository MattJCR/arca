# Pruebas obligatorias

Toda modificación funcional debe ir acompañada de pruebas.

Prioriza:

- Tests unitarios.
- Tests de integración.
- Tests de regresión.

Las pruebas deben cubrir:

- Casos esperados.
- Casos límite.
- Casos de error.

Una tarea no se considera terminada si no existen pruebas adecuadas.

## Ver las pruebas de navegador en vivo

`tests/test_browser.py` conduce un Chrome real con Playwright. Por defecto va sin ventana, para que
la suite corra igual en cualquier sitio. Para **verlas**:

```
ARCA_HEADED=1 .venv/Scripts/python.exe -m pytest tests/test_browser.py -q
```

Abre el navegador de verdad y ralentiza cada acción 250 ms para que se siga con la vista.
`ARCA_SLOW_MO=<ms>` cambia esa pausa (`ARCA_SLOW_MO=0` la quita). Para mirar solo una parte:

```
ARCA_HEADED=1 ARCA_SLOW_MO=400 .venv/Scripts/python.exe -m pytest tests/test_browser.py -q -k language
```

Las dos variables solo afectan al arranque del navegador: los tests son los mismos y las
aserciones no cambian, así que lo que se ve es exactamente lo que se verifica.
