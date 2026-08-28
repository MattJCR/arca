# Jerarquía y flujo de dependencias

## Jerarquía

```text
System Prompt
      │
      ▼
CLAUDE.md
      │
      ▼
Skills
      │
      ▼
Subagents
      │
      ▼
Datos / Documentación
      │
      ▼
Herramientas
```

Cada nivel decide si necesita cargar el siguiente.

## Flujo

```text
Usuario
↓
CLAUDE.md
↓
Seleccionar Skill
↓
Seleccionar Subagent (si procede)
↓
Cargar únicamente los datos necesarios
↓
Ejecutar herramientas necesarias
↓
Responder
```
