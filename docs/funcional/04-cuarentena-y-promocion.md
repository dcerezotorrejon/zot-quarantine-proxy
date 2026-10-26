# 04 · Cuarentena y promoción

Este documento describe el ciclo de vida de una imagen desde que se descarga
hasta que pasa a `verified/`, o hasta que se retira.

Hay dos caminos para llegar a `verified/`:

- **Imagen nueva** (vía rápida): el proxy la escanea con ClamAV en segundo plano
  y, si está limpia, la promueve al momento. Sin CVEs ni plazo.
- **Actualización** (camino largo): un tag ya verificado que mueve a otro digest
  espera `QUARANTINE_DAYS` y pasa por CVEs (opcional) y malware.

## Ciclo de vida

```
pedido por un cliente
   │
   ▼
¿está el tag en verified/? ──sí──► se sirve desde verified/
   │ no
   ▼
se sirve del upstream; en segundo plano se copia a quarantine/
el digest (tag sha256-<hex>) y se escanea con ClamAV
   │
   ├── imagen NUEVA: ClamAV limpio ──► se promueve al momento (vía rápida)
   │
   └── actualización: QUARANTINE_DAYS (7 días) + CVEs (opcional) + malware
                                      └──► se promueve (camino largo)
```

## Plazo de cuarentena (camino largo)

El plazo solo aplica a las **actualizaciones**. Las imágenes nuevas se promueven
al momento por la vía rápida (solo ClamAV), sin esperar el plazo.

- Por defecto, **7 días** (`QUARANTINE_DAYS`).
- El plazo cuenta desde la fecha de **build** de la imagen en el origen (el campo
  `Created` de su config), no desde que entró en cuarentena. Así una imagen
  publicada hace tiempo no vuelve a "madurar" cuando el proxy la descarga por
  primera vez.
- Si no se puede obtener esa fecha (imagen sin `Created`, fallo de skopeo…), el
  plazo cuenta desde la entrada en cuarentena.
- Cada digest tiene su propio reloj: un tag móvil que cambia de digest abre una
  cuarentena nueva para ese digest. Si el nuevo digest ya es antiguo, puede
  promoverse antes de los 7 días.
- Un digest que aún no cumple el plazo no se evalúa aunque ya esté escaneado.

## Cuándo se ejecuta el mantenimiento

El updater ejecuta un ciclo al arrancar y después cada **6 horas**
(`CHECK_INTERVAL`). Cada ciclo hace cinco cosas en este orden:

1. **Limpiar** la base de datos: borra los paquetes sin imágenes y las refs o
   cuarentenas cuyo digest ya no está en Zot.
2. **Sincronizar con el upstream**: para cada tag pedido, si el digest al que
   apunta ahora es nuevo, lo descarga a cuarentena.
3. **Actualizar las firmas de ClamAV** con `freshclam`.
4. **Promover** los digests que cumplen el plazo y pasan los escaneos de CVEs y
   malware.
5. **Retener**: conserva los tags de `verified/` que alguien pide y poda el resto.

Si un paso falla, el ciclo registra el error y sigue con el siguiente. Los
errores aparecen en los logs del updater.

## Escaneo de CVEs (camino largo)

El escaneo de CVEs solo aplica a las **actualizaciones**. Las imágenes nuevas no
se escanean por CVEs (solo por malware, en la vía rápida).

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

Para no bloquear por CVEs, déjalo vacío (`CVE_SEVERITY_THRESHOLD=`): en ese caso
la imagen se promueve en cuanto el escaneo está disponible, sin importar los CVEs
encontrados. La imagen sigue esperando a que el escaneo de Zot termine (~15 min).

## Escaneo de malware

El escaneo de malware se hace en los dos caminos:

- **Vía rápida (proxy)**: en cuanto se copia a cuarentena, el proxy desempaqueta
  la imagen (con `umoci`) y la escanea con **ClamAV**. Si está limpia, la promueve
  al momento.
- **Camino largo (updater)**: si el digest sigue en cuarentena al cumplir el
  plazo, el updater repite el escaneo antes de promover.

El veredicto puede ser:

| Veredicto | Significado | Qué pasa |
|---|---|---|
| **Limpia** | ClamAV no detectó nada. | Continúa hacia la promoción (si los CVEs también están limpios). |
| **Infectada** | ClamAV detectó malware. | Se **elimina de la cuarentena** al cumplir el plazo. Se registran las rutas afectadas. |
| **Pendiente** | No se pudo desempaquetar o escanear. | Se reintenta en el siguiente ciclo. |

Igual que con los CVEs: si se detecta malware, la imagen no se promueve; se elimina.

La base de firmas de ClamAV se actualiza con `freshclam` en cada ciclo de
mantenimiento (justo antes de promover). Si `freshclam` falla, se registra un
aviso y el escaneo continúa con la base de firmas disponible.

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

Cuando un digest cumple las tres condiciones, el updater:

1. Copia el digest de `quarantine/` a `verified/`, re-apuntando cada tag de
   cliente que lo referencia.
2. Borra la copia de `quarantine/`.
3. Lo quita del registro de cuarentena.

Durante la promoción, la imagen sigue en `quarantine/` sin servirse; pasa a
servirse desde `verified/` cuando termina la copia.

## Qué se conserva

Después de cada promoción, el updater calcula qué tags se conservan en `verified/`:

- **Tags pedidos.** Se conserva cada tag que alguien ha pedido (los que están en
  la base de datos de estado).
- **Todo lo demás.** Los tags de `verified/` que nadie pide se podan.

Ya no hay alias: `latest` no apunta a otra versión; es un tag más, verificado por
su propio digest.

Ejemplo: si los clientes solo han pedido `1.27.4` y `latest`, y en `verified/`
hay `1.26.9`, `1.27.3`, `1.27.4` y `2.1.0`, se conservan `1.27.4` y `latest`. Se
podan `1.26.9`, `1.27.3` y `2.1.0`.

## Limpieza de la base de datos

Un paquete deja de estar en la base de datos cuando no tiene ninguna imagen en
`verified/` ni en `quarantine/`. Eso ocurre, por ejemplo, cuando todos sus tags
se han purgado porque el upstream los retiró.

Mientras quede al menos una imagen de cualquiera de los dos namespaces, el
paquete sigue activo y el updater sigue vigilando sus tags.
