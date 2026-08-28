# Estructura genérica de proyecto (Claude Code)

```text
project/
│
├── CLAUDE.md
│
├── .claude/
│   ├── settings.json      # configuración global, incluidos los Hooks
│   ├── hooks/              # scripts invocados desde settings.json
│   ├── skills/
│   │   └── <skill-name>/
│   │       └── SKILL.md
│   └── agents/
│       └── <agent-name>.md
│
├── docs/
│   ├── architecture/       # este mismo documento y los de su carpeta (genérico, reutilizable)
│   └── development/        # reglas de desarrollo (genérico, reutilizable)
│
├── data/                   # conocimiento estructurado (JSON, YAML, SQLite...)
├── scripts/
└── src/
```

Cada proyecto añade sus propias subcarpetas de dominio dentro de `docs/` (p. ej. `docs/<dominio>/`) y dentro de `src/`, sin modificar este esqueleto genérico.

Nota: la convención real de Claude Code usa `.claude/agents/` para los subagentes (no `.claude/subagents/`); cada subagente es un archivo Markdown individual con frontmatter YAML, no una carpeta.
