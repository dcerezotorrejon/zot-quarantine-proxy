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
| **Cuarentena** | `quarantine/` | Descargada, pero aún no ha cumplido el plazo ni el escaneo. Se sirve como caché. |
| **Verificada** | `verified/` | Ha cumplido el plazo y el escaneo sin CVEs bloqueantes. Es la versión de referencia. |
| **Ausente** | ninguno | No se ha pedido nunca, o ya se retiró. |

> **Importante:** una imagen en cuarentena **se sirve a los clientes** igual que
> una verificada. El estado de cuarentena no bloquea la descarga. Lo que hace es
> marcar que la imagen aún no es la versión de referencia. Ver
> [04 · Cuarentena y promoción](04-cuarentena-y-promocion.md#qué-pasa-con-una-imagen-vulnerable).

## Flujo de una descarga

```
podman pull <servidor>:5001/docker.io/library/nginx:1.27
        │
        ▼
¿Hay una versión verificada que cumpla 1.27?  ──sí──► se sirve (verificada)
        │ no
        ▼
¿Hay una versión en cuarentena que cumpla 1.27? ──sí──► se sirve (cuarentena)
        │ no
        ▼
Se descarga del upstream a cuarentena y se sirve desde ahí
```

Las reglas de qué versión cumple cada petición están en
[03 · Tags y versiones](03-tags-y-versiones.md).

## Casos de uso

### Caso 1 · Primera descarga de una imagen

Un equipo pide `nginx:1.27` por primera vez. El sistema la descarga a cuarentena,
la sirve de inmediato y la deja allí hasta cumplir el plazo. Después, el updater
la promociona a `verified/` si el escaneo está limpio.

### Caso 2 · Descarga posterior

Otra máquina pide `nginx:1.27`. El sistema responde desde la caché sin volver a
descargar nada.

### Caso 3 · Aparece una versión nueva

Una versión nueva de `nginx` se publica en el upstream. En el siguiente ciclo del
updater (por defecto cada 6 horas) se descarga a cuarentena, si alguien ha pedido
esa línea de versiones. Cuando cumple el plazo y el escaneo, pasa a `verified/`.

### Caso 4 · Una versión deja de publicarse

Si el upstream retira un tag que estaba en cuarentena, el updater lo borra de
cuarentena en el siguiente ciclo.

### Caso 5 · Un paquete deja de usarse

Si un paquete ya no tiene ninguna imagen en `verified/` ni en `quarantine/`, el
updater lo borra de su base de datos de estado.

## Qué no hace

- **No autentica a los clientes.** Cualquiera que llegue a los puertos puede
  descargar. Ver [05 · Operación](05-operacion.md#seguridad-de-acceso).
- **No escanea en el momento de la descarga.** El escaneo se hace en segundo plano
  y puede tardar hasta unos 15 minutos en tener resultado.
- **No descarga por digest.** Un digest solo se sirve si la imagen ya está en el
  almacén.
- **No soporta registros privados** con credenciales. Solo registros públicos o
  de acceso anónimo.
