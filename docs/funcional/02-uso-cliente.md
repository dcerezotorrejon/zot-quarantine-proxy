# 02 · Uso desde los clientes

## Cómo se escribe una imagen

La imagen se pide anteponiendo la dirección del proxy a la ruta completa del
registro original:

```
<servidor>:5001/<registro>/<repositorio>:<tag>
```

| Parte | Ejemplo | Significado |
|---|---|---|
| `<servidor>:5001` | `fedora-server:5001` | Dirección del proxy. |
| `<registro>` | `docker.io`, `ghcr.io`, `quay.io` | Registro público de origen. |
| `<repositorio>` | `library/nginx` | Ruta dentro de ese registro. |
| `:<tag>` | `:1.27` | Versión pedida. Ver [03 · Tags y versiones](03-tags-y-versiones.md). |

### Atajo para Docker Hub

Si el registro es `docker.io`, se puede omitir. Las imágenes oficiales llevan
además el prefijo `library/`, que también se puede omitir:

| Pedido | Equivale a |
|---|---|
| `fedora-server:5001/nginx:alpine` | `fedora-server:5001/docker.io/library/nginx:alpine` |
| `fedora-server:5001/miusuario/app:1.0` | `fedora-server:5001/docker.io/miusuario/app:1.0` |

## Ejemplos

```bash
# Versión exacta
podman pull fedora-server:5001/docker.io/library/nginx:1.27.4

# Cualquier parche de la 1.27: se sirve la más alta disponible
podman pull fedora-server:5001/docker.io/library/nginx:1.27

# La versión más reciente verificada
podman pull fedora-server:5001/docker.io/library/nginx:latest

# Desde GitHub Container Registry
podman pull fedora-server:5001/ghcr.io/owner/herramienta:2.0.0
```

## Qué recibe el cliente

| Situación | Resultado |
|---|---|
| La imagen ya está en el almacén | Se descarga al instante desde la caché local. |
| La imagen no estaba y existe en el upstream | Zot la descarga y la sirve en streaming desde cuarentena: el `pull` empieza sin esperar a que termine la descarga completa. |
| El registro no está permitido | Error `DENIED` (403). |
| El nombre no es válido | Error `NAME_INVALID` (400). |
| No existe ningún tag que cumpla la petición | Error `MANIFEST_UNKNOWN` (404) para parciales y flotantes. Ver [06 · Errores](06-errores.md). |

Para los códigos de error y su significado, ver [06 · Errores](06-errores.md).

## Configurar los clientes

El proxy se sirve por HTTP en la red local, así que el cliente tiene que
tratarlo como registro inseguro. Los pasos están en
[INSTALL.md, sección 10](../../INSTALL.md#10-configurar-los-clientes).

Si se publica detrás de un reverse proxy con HTTPS y un certificado de confianza,
no hace falta esa configuración.

## Acceso directo a Zot

El puerto `5000` da acceso directo al almacén de Zot, sin pasar por el proxy.
Eso significa que se pueden descargar también las imágenes que todavía están en
cuarentena, usando rutas `verified/...` o `quarantine/...`. El uso normal es el
puerto `5001`. Ver [05 · Operación](05-operacion.md#seguridad-de-acceso).
