# Idioma del proyecto

## Regla general

Toda la implementación técnica debe estar escrita íntegramente en inglés. Esto incluye:

- Código fuente.
- Variables, funciones, métodos, clases, interfaces, enumeraciones, constantes.
- Archivos y carpetas.
- APIs.
- Tests.
- Documentación técnica.
- Mensajes de commit.
- Pull Requests (cuando sea posible).

No mezcles español e inglés en la implementación.

En el código no se escriben comentarios en ningún idioma: ver `coding-standards.md`.

## Interfaz de usuario

La interfaz es **bilingüe español/inglés desde el inicio**, con el español como idioma por defecto. Todo el contenido dirigido al usuario final (textos de interfaz, botones, etiquetas, mensajes, validaciones, notificaciones, documentación para usuarios) debe existir en ambos idiomas y obtenerse siempre a través del sistema i18n (`docs/architecture/i18n.md`); añadir una cadena de usuario implica añadirla a los dos catálogos a la vez.

## Comunicación con el usuario

Responde siempre al usuario en español.

Genera toda la implementación técnica en inglés, salvo que se indique expresamente lo contrario.
