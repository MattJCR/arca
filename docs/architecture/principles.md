# Principios de arquitectura para proyectos con Claude Code

## Objetivo

Organizar el conocimiento de forma modular para minimizar el contexto utilizado por el modelo.

El agente solo debe cargar la información estrictamente necesaria para resolver la tarea actual (*lazy loading*).

## Principios

1. Mantener el contexto lo más pequeño posible.
2. No duplicar información.
3. Separar instrucciones, conocimiento y datos.
4. Dividir el conocimiento en módulos reutilizables.
5. Utilizar Hooks para tareas deterministas.
6. Utilizar Skills para procedimientos reutilizables.
7. Utilizar herramientas únicamente cuando sean necesarias.
8. Cuestionar cualquier decisión arquitectónica si existe una alternativa objetivamente mejor.

## Regla principal

El contexto es un recurso limitado.

Toda la arquitectura debe diseñarse para minimizar el contexto cargado en cada interacción.
