# 01 · Visión general

## Qué problema resuelve

Las imágenes de contenedor que se descargan de registros públicos (Docker Hub,
GitHub Container Registry, Quay…) pueden llevar vulnerabilidades o cambiar sin
aviso. Este sistema se pone entre tu red y esos registros para:

1. **Guardar una copia local** de las imágenes que usas, para no depender de
   internet en cada descarga.
2. **Retener cada imagen unos días** antes de considerarla segura (la
   *cuarentena*).
3. **Escanear las vulnerabilidades** (CVEs) de cada imagen y promocionarla solo
   si no tiene problemas graves.
4. **Mantener solo las versiones que usas**, para que el almacenamiento no crezca
   sin control.

## Partes del sistema

- **Proxy**: el punto de entrada para los clientes. Responde a `podman pull` y
  `docker pull` como si fuera un registro normal.
- **Zot**: el almacén donde se guardan las imágenes y su escaneo de CVEs.
- **Updater**: un proceso que revisa cada pocas horas qué hay de nuevo, promociona
  las imágenes que cumplen las reglas y limpia lo que ya no se usa.

## Estados de una imagen

Una imagen puede estar en uno de estos estados dentro del almacén:

| Estado | Dónde está | Qué significa |
|---|---|---|
| **Cuarentena** | `quarantine/` | Descargada, pero aún no ha cumplido el plazo ni el escaneo. No se sirve: espera a promoverse o retirarse. |
| **Verificada** | `verified/` | Ha cumplido el plazo y el escaneo sin CVEs bloqueantes. Es la versión de referencia. |
| **Ausente** | ninguno | No se ha pedido nunca, o ya se retiró. |

> **Importante:** solo se sirve desde local lo que está en `verified`. Lo que no
> está verificado se sirve directamente del registro público y, en segundo plano,
> se copia a `quarantine` para escanearlo. Ver
> [04 · Cuarentena y promoción](04-cuarentena-y-promocion.md).

## Flujo de una descarga

```
podman pull <servidor>:5001/docker.io/library/nginx:1.27
        │
        ▼
¿Está el tag 1.27 en verified/?  ──sí──► se sirve desde verified/
        │ no
        ▼
se sirve desde el registro público (upstream)
        │
        ▼
en segundo plano, se copia a quarantine/ el digest al que apunta (si no está ya)
```

Las reglas de cómo se sirve cada tag están en
[03 · Tags y versiones](03-tags-y-versiones.md).

## Casos de uso

### Caso 1 · Primera descarga de una imagen

Un equipo pide `nginx:1.27` por primera vez. El proxy la sirve del registro
público y, en segundo plano, la copia a `quarantine/`. Al cumplir el plazo y con
escaneo limpio, el updater la promociona a `verified/`.

### Caso 2 · Descarga posterior

Otra máquina pide `nginx:1.27`. Si ya se promovió, se sirve desde `verified/`; si
todavía está en cuarentena, se sirve de nuevo del upstream.

### Caso 3 · Aparece un digest nuevo

El upstream re-publica un tag (por ejemplo, mueve `latest` a un digest nuevo). En
el siguiente ciclo del updater (por defecto cada 6 horas) se abre una cuarentena
nueva para ese digest, si alguien ha pedido ese tag. Cuando cumple el plazo y el
escaneo, pasa a `verified/`.

### Caso 4 · Un tag deja de publicarse

Si el upstream retira un tag que estaba en cuarentena (o lo mueve a otro digest),
el updater descarta la cuarentena del digest anterior en el siguiente ciclo.

### Caso 5 · Un paquete deja de usarse

Si un paquete ya no tiene ninguna imagen en `verified/` ni en `quarantine/`, el
updater lo borra de su base de datos de estado.

## Qué no hace

- **No autentica a los clientes.** Cualquiera que llegue a los puertos puede
  descargar. Ver [05 · Operación](05-operacion.md#seguridad-de-acceso).
- **No escanea en el momento de la descarga.** El escaneo se hace en segundo plano
  y puede tardar hasta unos 15 minutos en tener resultado.
- **No cuarentena los digests pedidos directamente.** Un digest que no está
  verificado se sirve del upstream, pero al no llevar tag no pasa por cuarentena
  ni se promueve.
- **No soporta registros privados** con credenciales. Solo registros públicos o
  de acceso anónimo.
