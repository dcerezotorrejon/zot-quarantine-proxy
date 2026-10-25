# 04 · Cuarentena y promoción

Este documento describe el ciclo de vida de una imagen desde que se descarga
hasta que pasa a `verified/`, o hasta que se retira.

## Ciclo de vida

```
          pedido por un cliente
                  │
                  ▼
        ¿está en verified/?  ──sí──► se sirve desde verified/
                  │ no
                  ▼
        se sirve del upstream; en segundo plano
        se copia a quarantine/ (si no está ya)
                  │
                  ▼
        ┌──────────────────┐
        │   CUARENTENA     │  en espera (no se sirve)
        │  (quarantine/)   │
        └────────┬─────────┘
                 │ pasan QUARANTINE_DAYS (7 por defecto)
                 │ y el escaneo no tiene CVEs bloqueantes
                 ▼
        ┌──────────────────┐
        │   VERIFICADA     │  versión de referencia
        │   (verified/)    │
        └──────────────────┘

  En cualquier momento: si el upstream retira el tag, sale de cuarentena.
```

## Plazo de cuarentena

- Por defecto, **7 días** (`QUARANTINE_DAYS`).
- El plazo cuenta desde el momento en que la imagen **entra en cuarentena**, no
  desde la fecha en que el upstream publicó la versión.
- Una imagen que lleva menos días no se evalúa aunque ya esté escaneada.

## Cuándo se ejecuta el mantenimiento

El updater ejecuta un ciclo al arrancar y después cada **6 horas**
(`CHECK_INTERVAL`). Cada ciclo hace cuatro cosas en este orden:

1. **Limpiar** la base de datos: borra los paquetes sin imágenes y las refs o
   cuarentenas cuyo tag ya no está en Zot.
2. **Buscar versiones nuevas** de los paquetes pedidos y descargarlas a cuarentena.
3. **Promover** las imágenes que cumplen el plazo y el escaneo.
4. **Actualizar alias y retención** (`latest`, `1.27`… y las versiones que se borran).

Si un paso falla, el ciclo registra el error y sigue con el siguiente. Los
errores aparecen en los logs del updater.

## Escaneo de CVEs

El escaneo lo hace Zot en segundo plano, con Trivy. Zot lo ejecuta cada 15 minutos
aproximadamente, así que puede tardar hasta ese tiempo después de la descarga. Mientras no hay resultado, la imagen queda **pendiente**: no se
promueve, pero tampoco se marca como vulnerable. Se vuelve a comprobar en el
siguiente ciclo.

El veredicto puede ser:

| Veredicto | Significado | Qué pasa |
|---|---|---|
| **Limpia** | Sin CVEs con severidad bloqueante. | Se promueve a `verified/` si ya cumple el plazo. |
| **Vulnerable** | Hay CVEs con severidad bloqueante (por defecto `HIGH` o `CRITICAL`). | Se **elimina de la cuarentena** al cumplir el plazo. Se registra un aviso con los IDs. |
| **Pendiente** | Zot aún no tiene el resultado, o zli no ha respondido. | Se reintenta en el siguiente ciclo. |

Las severidades bloqueantes se configuran con `CVE_SEVERITY_THRESHOLD`.
Ver [05 · Operación](05-operacion.md#variables-de-configuración).

## Qué pasa con una imagen vulnerable

Si una imagen tiene CVEs bloqueantes **cuando se cumple el plazo de cuarentena**,
el updater la **elimina**. Deja de estar disponible en el almacén y no se
promueve.

Antes de cumplir el plazo, una imagen vulnerable sigue en cuarentena (sin
servirse). El escaneo solo decide al final del plazo: si tiene CVEs bloqueantes,
se elimina de la cuarentena.

Si un cliente vuelve a pedir la misma imagen, el proxy la sirve del upstream y, en
segundo plano, la copia otra vez a cuarentena, empezando un nuevo plazo. Si sigue
siendo vulnerable, se volverá a eliminar al cumplir el plazo.

## Imágenes con escaneo pendiente

Si el escaneo sigue **pendiente** al cumplir el plazo (Zot no tiene resultado o
`zli` falla), la imagen **no se elimina ni se promueve**. Se vuelve a comprobar en
cada ciclo hasta que haya veredicto. Si el escaneo nunca llega, la imagen se queda
en cuarentena indefinidamente. Conviene revisar los avisos de `zli` en los logs.

## Promoción

Cuando una imagen cumple las tres condiciones, el updater:

1. La copia de `quarantine/` a `verified/`.
2. Borra la copia de `quarantine/`.
3. La quita del registro de cuarentena.

Durante la promoción, la imagen sigue en `quarantine/` sin servirse; pasa a
servirse desde `verified/` cuando termina la copia.

## Qué se conserva

Después de cada promoción, el updater calcula qué versiones se conservan:

- **Alias.** Los tags flotantes y parciales que se han pedido (`latest`, `1.27`…)
  se crean o actualizan en `verified/` para que apunten a la versión que les
  corresponde. Un alias es solo un nombre que apunta a una versión existente: no
  ocupa espacio extra.
- **Versiones pedidas.** Se conserva la versión que cumple cada tag pedido.
- **Tags que no son versiones.** Nunca se borran (`alpine`, `bookworm`…).
- **Todo lo demás.** Las versiones semver que nadie pide se borran.

Ejemplo: si los clientes solo han pedido `1.27` y `latest`, y en `verified/` hay
`1.26.9`, `1.27.3`, `1.27.4` y `2.1.0`, se conservan `1.27.4` y `2.1.0`. Se borran
`1.26.9` y `1.27.3`.

## Limpieza de la base de datos

Un paquete deja de estar en la base de datos cuando no tiene ninguna imagen en
`verified/` ni en `quarantine/`. Eso ocurre, por ejemplo, cuando todas sus
versiones se han purgado porque el upstream las retiró.

Mientras quede al menos una imagen de cualquiera de los dos namespaces, el
paquete sigue activo y el updater sigue vigilando sus tags.
