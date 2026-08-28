---
name: architecture-review
description: Usar al tomar decisiones estructurales, evaluar si una funcionalidad corresponde a una Skill, Hook, Subagent o herramienta, o revisar si un cambio respeta los límites entre módulos y el principio de contexto mínimo.
---

# Architecture Review

## Cuándo utilizarla

- Antes de crear una nueva Skill, Hook o Subagent.
- Cuando un cambio pueda afectar a la estructura de carpetas o a los límites entre módulos.
- Cuando exista duda sobre dónde debe vivir un conocimiento (código, docs, data).

## Responsabilidad

Vigilar que cualquier cambio respete la arquitectura general, la dirección de las dependencias y el principio de contexto mínimo.

## Documentación a consultar

- docs/architecture/principles.md
- docs/architecture/dependencies.md
- docs/architecture/structure.md
- docs/architecture/decision-criteria.md
- docs/architecture/i18n.md

## Herramientas

- Read, Grep (no requiere herramientas de escritura)

## Hooks relacionados

- Ninguno directo; esta Skill guía el razonamiento, no automatiza.
