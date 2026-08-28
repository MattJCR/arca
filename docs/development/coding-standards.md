# Estándares de código

## Respetar la arquitectura

No modifiques la arquitectura salvo que se solicite explícitamente.

Respeta siempre:

- Los límites entre módulos.
- La separación por capas.
- La dirección de las dependencias.
- Las convenciones de nombres.
- La estructura de carpetas.
- Las abstracciones existentes.

No introduzcas nuevos patrones simplemente porque parezcan mejores.

La consistencia del proyecto tiene prioridad sobre la innovación.

## Priorizar la simplicidad

Prefiere siempre:

- Funciones pequeñas.
- Clases con una única responsabilidad.
- Código legible.
- Bajo acoplamiento.
- Alta cohesión.

Evita:

- Código excesivamente ingenioso.
- Sobreingeniería.
- Abstracciones prematuras.
- Patrones de diseño innecesarios.

La legibilidad es más importante que escribir menos líneas de código.

## Preservar el comportamiento existente

Nunca modifiques el comportamiento actual si no se ha solicitado.

Cuando cambies código existente:

- Mantén la compatibilidad.
- Minimiza los efectos secundarios.
- Evita refactorizaciones no relacionadas con la tarea.

Modifica únicamente aquello que sea necesario.

## Minimizar la complejidad

Implementa siempre la solución más sencilla que satisfaga los requisitos.

No añadas:

- Capas adicionales.
- Abstracciones genéricas.
- Factories.
- Services.
- Interfaces.

Salvo que la arquitectura existente las requiera.

## Gestión de errores

Gestiona los errores de forma explícita.

Evita:

- Fallos silenciosos.
- Bloques `catch` vacíos.
- Ignorar excepciones.

Los errores deben aportar información útil para su diagnóstico.

## Rendimiento

Optimiza únicamente cuando exista una necesidad demostrable.

Prioriza siempre:

- Corrección.
- Legibilidad.
- Mantenibilidad.

Evita la optimización prematura.

## Documentación en el código

En el código no se añade ningún comentario. Solo código.

- El código debe explicarse por sí mismo mediante nombres claros y funciones pequeñas.
- Si algo necesita explicación (motivo de una decisión, restricciones, trade-offs), documéntalo en `docs/`, no en el código fuente.

## Restricciones para la IA

Nunca inventes:

- APIs.
- Librerías.
- Funcionalidades del framework.
- Comportamientos no documentados.

Cuando exista incertidumbre, indícalo explícitamente.

Es preferible reconocer una duda que generar una implementación incorrecta.
