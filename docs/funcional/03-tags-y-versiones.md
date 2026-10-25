# 03 · Tags y versiones

El tag es la parte que va detrás de los dos puntos (`:1.27`). El sistema lo
clasifica en uno de cuatro tipos y decide qué versión concreta servir.

## Los cuatro tipos

| Tipo | Ejemplos | Qué se sirve |
|---|---|---|
| **Exacto** | `1.27.4`, `1.27.4-alpine` | Esa versión. Si no existe, se descarga tal cual. |
| **Parcial** | `1`, `1.27` | La versión más alta que empieza por ese prefijo. |
| **Flotante** | `latest`, `stable` | La versión verificada más alta. |
| **Literal** | `alpine`, `bookworm` | El tag exacto con ese nombre. No se interpreta como versión. |

## Ejemplos con un caso concreto

Supón que el almacén tiene verificadas estas versiones de `nginx`:

```
1.26.9   1.27.3   1.27.4   2.0.0   2.1.0
```

| Pedido | Se sirve | Por qué |
|---|---|---|
| `1.27.3` | `1.27.3` | Exacto: esa versión. |
| `1.27` | `1.27.4` | Parcial: la 1.27 más alta. |
| `1` | `1.27.4` | Parcial: la 1.x más alta. Aquí `2.x` no cuenta. |
| `latest` | `2.1.0` | Flotante: la más alta de todas las verificadas. |

## Sufijos

Un tag con sufijo, como `1.27.4-alpine`, es una variante distinta de la versión.

- Un pedido **exacto** con sufijo (`1.27.4-alpine`) se sirve tal cual.
- Los pedidos **parciales y flotantes** ignoran los tags con sufijo. Por eso
  `latest` no devuelve `2.1.0-alpine` aunque exista.

## Digests

Un pedido por digest (`nginx@sha256:…`) se sirve solo si ese contenido ya está en
el almacén, en `verified/` o en `quarantine/`. Si no está, el sistema no lo
descarga, porque el digest no indica qué tag descargar.

## Tags que no son versiones

Los tags como `alpine` o `bookworm` se tratan como literales: se sirven tal cual.
Si el repositorio no tiene ningún tag con formato de versión, `latest` apunta al
tag literal `latest` si existe.

## Qué versiones se conservan

El almacén no guarda todas las versiones que han existido. Solo conserva:

- Una versión por cada tag parcial o flotante que alguien haya pedido.
- Cualquier tag exacto que alguien haya pedido.
- Todos los tags que no son versiones (`alpine`, etc.).

Las demás versiones semver se borran en el ciclo de mantenimiento. Por ejemplo, si
nadie pide `1.26`, la `1.26.9` se borra en cuanto hay una versión del mismo
paquete que sí se conserva. Ver
[04 · Cuarentena y promoción](04-cuarentena-y-promocion.md#qué-se-conserva).
