# Criterios de decisión: Skills, Hooks, Subagents, Datos y Herramientas

## Skills

Cada Skill representa una capacidad reutilizable.

Ejemplos: navegación, planificación, despliegues, testing, análisis.

Una Skill debe actuar como índice. No debe contener documentación extensa. Debe indicar:

- cuándo usarla
- qué hace
- qué documentación consultar
- qué herramientas utilizar

Crear una Skill cuando:

- la tarea requiera razonamiento;
- el procedimiento vaya a reutilizarse;
- sea necesario cargar conocimiento específico;
- permita reducir el contexto de otras tareas.

No crear una Skill para automatizar tareas deterministas.

## Subagents

Los Subagents encapsulan tareas complejas o especializadas.

Ejemplos: análisis, validación, generación, extracción.

Permiten aislar contexto.

No crear Subagents innecesarios.

## Datos

Los datos representan conocimiento estructurado (JSON, SQLite, YAML, CSV, bases de datos, documentación técnica).

Siempre que sea posible, el conocimiento debe almacenarse aquí y no dentro de las instrucciones del modelo.

## Documentación

La documentación explica cómo utilizar el sistema. Debe dividirse por temas. Nunca crear documentos enormes.

Preferir:

```text
docs/
    tema_a.md
    tema_b.md
    tema_c.md
```

frente a:

```text
docs/
    todo.md
```

## Hooks

Los Hooks automatizan acciones que siempre deben ejecutarse.

Utilizar un Hook cuando:

- la acción sea determinista;
- no requiera razonamiento;
- deba ejecutarse siempre bajo las mismas condiciones;
- elimine trabajo repetitivo.

Los Hooks no sustituyen a las Skills ni el razonamiento del modelo.

Ejemplos: lint, formatter, tests, comprobación de secretos, generación automática, logging, validaciones.

## Herramientas

Solo deben invocarse cuando aporten valor.

Ejemplos: MCP, Bash, Git, Docker, APIs, Ollama.

## Criterio de decisión

Antes de implementar una nueva funcionalidad deberá evaluarse si corresponde a:

- una Skill;
- un Hook;
- un Subagent;
- una herramienta;
- una biblioteca;
- una funcionalidad propia del proyecto.

La solución elegida deberá ser la más simple que satisfaga los requisitos.

## Principio de simplicidad

Antes de introducir una nueva abstracción deberá comprobarse si puede resolverse mediante una solución más simple.

Orden de preferencia, siempre que sea técnicamente adecuado:

1. Biblioteca o funcionalidad existente.
2. Herramienta de línea de comandos (CLI).
3. Hook.
4. Skill.
5. Subagent.
6. Nueva infraestructura.

No deberá añadirse complejidad sin una justificación objetiva.

## Evolución de la arquitectura

Durante el desarrollo deberá revisarse continuamente la organización del proyecto.

Cuando se detecten:

- responsabilidades mezcladas;
- duplicación;
- archivos demasiado grandes;
- procedimientos repetitivos;
- exceso de contexto;
- bajo desacoplamiento;

se deberá proponer una refactorización. Las posibles acciones incluyen:

- crear una nueva Skill;
- dividir una Skill existente;
- crear un Hook;
- crear un Subagent;
- mover conocimiento a datos estructurados;
- dividir documentación;
- simplificar la arquitectura.

Todas las propuestas deberán estar justificadas técnicamente.
