# 03 · Tags y versiones

Todos los tags se sirven de forma **literal**: el proxy reenvía al registro
público exactamente el tag que pide el cliente. No interpreta versiones: no
resuelve `latest` a "la mayor versión" ni `1.27` a "la mayor `1.27.x`". El
resultado es el que devuelve el registro público.

## Cómo se sirve un tag

La prioridad es siempre la misma: si el tag está en `verified/`, se sirve de
local; si no, se sirve del upstream y, en segundo plano, se copia a cuarentena
el digest al que apunta.

| Pedido | Qué se sirve |
|---|---|
| `nginx:1.27.4` | Ese tag. De `verified/` si ya está verificado; si no, del upstream (y se copia a cuarentena en segundo plano). |
| `nginx:1.27` | Ese tag literal, tal como lo publica el upstream. No es "la mayor `1.27.x`". |
| `nginx:latest` / `stable` | Ese tag literal, tal como lo publica el upstream. |
| `nginx:alpine` / `bookworm` | Ese tag literal. |
| `nginx@sha256:…` | Esa imagen por digest. De `verified/` si está; si no, del upstream. No pasa por cuarentena. |

## Tags móviles y el plazo de cuarentena

`latest` y `stable` son tags móviles: el upstream puede re-apuntarlos a un digest
nuevo en cualquier momento. Como la cuarentena va por **digest**, cada movimiento
abre una cuarentena nueva para ese digest (con su propio reloj) y se descarta la
del digest anterior.

Una vez verificado, un tag móvil se sirve desde `verified/` (que tiene
prioridad), así que **queda por detrás del upstream hasta un ciclo de cuarentena**
(`QUARANTINE_DAYS`, 7 días por defecto) cada vez que el upstream lo mueve.

## Digests

Un pedido por digest (`nginx@sha256:…`) se sirve de `verified/` si ese contenido
ya está allí; si no, se sirve del upstream. Al no llevar tag, el digest no se
copia a cuarentena ni se promueve.

## Qué se conserva

El almacén conserva en `verified/` los tags que alguien ha pedido. Los tags que
nadie pide se podan en el ciclo de mantenimiento. Ver
[04 · Cuarentena y promoción](04-cuarentena-y-promocion.md#qué-se-conserva).
