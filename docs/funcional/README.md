# Documentación funcional

Esta carpeta describe **qué hace el sistema** desde el punto de vista de quien lo
usa y de quien lo opera. No explica cómo está hecho por dentro.

| Documento | Para quién | Contenido |
|---|---|---|
| [01 · Visión general](01-vision-general.md) | Todos | Qué problema resuelve, flujo de una imagen y casos de uso. |
| [02 · Uso desde los clientes](02-uso-cliente.md) | Quien hace `pull` | Cómo pedir imágenes, qué se recibe en cada caso y ejemplos. |
| [03 · Tags y versiones](03-tags-y-versiones.md) | Quien hace `pull` y quien opera | Cómo se sirven los tags literalmente (`latest`, `1.27`, `1.27.4`, `alpine`) y digests. |
| [04 · Cuarentena y promoción](04-cuarentena-y-promocion.md) | Operador | Ciclo de vida de una imagen, plazos, veredictos y retención. |
| [05 · Operación](05-operacion.md) | Operador | Variables de configuración, logs, datos y mantenimiento. |
| [06 · Errores](06-errores.md) | Todos | Qué significa cada código de error y qué hacer. |

Para instalar, ver [INSTALL.md](../../INSTALL.md). Para el diseño técnico, ver
[docs/design.md](../design.md).
