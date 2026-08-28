# Base de datos

Módulo: `arca/db.py`. Fichero: `<raiz>/library.db`, esquema completo de §7.1 (tablas `zim_files`, `downloads`, `shards`, `ingest_jobs`, `deep_index_scope`, `settings`, `llama_profiles`, `events`), versionado con `PRAGMA user_version` (actual: 1).

## Dos modos de apertura

- `open_library_rw(path)` — para descarga, importación y administración. `journal_mode=WAL`, `foreign_keys=ON`, aplica migraciones. Crea el fichero y el esquema si no existen.
- `open_library_ro(path)` — para el proceso de consulta. URI `mode=ro` + `PRAGMA query_only=ON`; cualquier intento de escritura lanza `sqlite3.OperationalError`. Si el fichero no existe lanza `LibraryDatabaseMissingError` (el llamante degrada, no rompe).

## Matiz del invariante 1 (verificado por test)

`library.db` es la excepción sancionada de WAL (§7.1: «WAL solo aquí; los lotes no»): incluso una conexión de solo lectura puede materializar `library.db-wal`/`-shm` junto al fichero, porque SQLite necesita el índice WAL para leer. El test `test_open_ro_leaves_the_database_bytes_untouched` fija el contrato real: los bytes del fichero de datos no cambian y no aparece nada más que esos dos sidecars. Los lotes sellados (hito 3) sí tendrán prohibición absoluta de sidecars (`journal_mode=DELETE`).

## Eventos

- `record_event(conn, level, area, message, detail="")` — inserta con timestamp UTC `...Z`. Los mensajes de evento son datos técnicos y se escriben en inglés; no pasan por i18n.
- `recent_events(conn, limit=20)` — últimos eventos, el más reciente primero (alimentará el dashboard 12.1).
