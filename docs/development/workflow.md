# Flujo de trabajo y principio fundamental

## Principio fundamental

La IA es responsable de implementar el código, no de tomar decisiones de arquitectura.

La arquitectura, los límites entre módulos, los principios de diseño y las restricciones del proyecto deben considerarse requisitos inmutables, salvo que se indique expresamente lo contrario.

## Seguir la especificación

Nunca deduzcas requisitos que puedan modificar el comportamiento de la aplicación.

La implementación debe ajustarse estrictamente a:

- Requisitos funcionales.
- Criterios de aceptación.
- Arquitectura existente.
- Convenciones del proyecto.
- APIs existentes.
- Modelo de dominio.

Si algún requisito es ambiguo, detente y solicita aclaración en lugar de asumir una solución.

## Flujo de trabajo recomendado

Para cada tarea:

1. Comprender completamente la solicitud.
2. Analizar el código existente.
3. Identificar las restricciones del proyecto.
4. Planificar la implementación.
5. Implementar.
6. Crear o actualizar las pruebas.
7. Actualizar el manual de usuario desarrollador (`docs/manual/`, ver `developer-manual.md`).
8. Revisar la solución.
9. Crear o actualizar `.claude/checkpoint.md` indicando brevemente qué sigue.
10. Entregar únicamente cuando cumpla todos los requisitos.

## Checkpoint al cerrar cada tarea

Al terminar una tarea —no solo al pausar o al compactar contexto— se escribe o se
actualiza `.claude/checkpoint.md` con la skill `save-checkpoint`. El checkpoint es
acumulativo: siempre refleja el estado actual del proyecto, no el historial.

Contenido mínimo, breve:

- Tarea recién cerrada (ID del plan) y su resultado.
- **Lo siguiente:** la próxima tarea del plan y la primera acción concreta.
- Bloqueos o decisiones abiertas, si las hay.

No sustituye a `ARCA-plan.md` ni al manual: no duplica su contenido, los referencia.

## Definición de tarea completada

Una tarea solo puede considerarse terminada cuando:

- Cumple todos los requisitos funcionales.
- No modifica comportamientos no solicitados.
- Compila correctamente.
- Todas las pruebas existentes y nuevas pasan.
- No introduce complejidad innecesaria.
- Respeta la arquitectura.
- Mantiene una nomenclatura clara.
- No genera duplicación evidente.
- No introduce vulnerabilidades conocidas.
- Es fácilmente mantenible por otro desarrollador.
- El manual de usuario desarrollador refleja la funcionalidad tal como queda tras el cambio, sin entradas obsoletas.

Si cualquiera de estos puntos no se cumple, la implementación no debe darse por finalizada.

## Organización del conocimiento

Los criterios para decidir entre Skill, Hook, Subagent, herramienta o funcionalidad propia del proyecto están centralizados en `docs/architecture/decision-criteria.md` (fuente única de verdad, para no duplicarlos aquí).
