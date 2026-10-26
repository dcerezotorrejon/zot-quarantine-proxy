# Diseño — zot-quarantine-proxy

Este documento es la referencia de diseño. Si el código y este documento no
coinciden, el código manda: actualiza este documento en el mismo cambio.

## 1. Objetivo

Proxy de imágenes de contenedores (pull-through cache) para un homelab Fedora,
con cuarentena y escaneo de CVEs antes de considerar una imagen verificada.
Se despliega con Podman rootless y quadlets.

## 2. Decisiones

| Tema | Decisión |
|---|---|
| Primera petición | Si no está en `verified`, se sirve del upstream; en segundo plano se copia a `quarantine` y se escanea con ClamAV (vía rápida). |
| Escaneo | Malware con ClamAV (proxy, vía rápida, y updater). CVEs con `zli` + Trivy embebido en Zot (solo updater, opcional). |
| Imagen de Zot | `ghcr.io/project-zot/zot:latest` (full). No `zot-minimal`: no trae search ni CVEs. |
| Namespaces | Dos namespaces en un único Zot: `verified` y `quarantine`. |
| Topología | Pod `zot-quarantine-proxy` con los tres contenedores. Nombres resolubles con `AddHost` (ver §3). |
| Despliegue | Podman rootless + quadlets (systemd de usuario) en Fedora. Compose solo para desarrollo. |
| Puertos | Proxy `5001` (uso normal) y Zot `5000` (acceso directo). Ambos publicados al host. |
| Multi-arch | Se almacena el índice completo (`--multi-arch all`). El digest de identidad es el del índice; el escaneo de malware extrae solo la plataforma del servidor (`--multi-arch system`). Ver §9. |

## 3. Topología

```
clientes (docker/podman pull)
        │  <servidor>:5001
        ▼
┌──────────── pod zot-quarantine-proxy (red compartida) ────────────┐
│  [proxy]   :5001 ── reverse proxy OCI ──► [zot] :5000 (verified) │
│            └────────────────► registros públicos (upstream)       │
│  [updater] skopeo + zli ───────────────────► zli cve list        │
└──────────────────────────────────────────────────────────────────┘
```

Los contenedores se hablan por nombre (`http://zot:5000`). Dentro de un pod todos
comparten la misma pila de red, así que los nombres se resuelven con entradas
`AddHost` en la unidad `.pod`, que apuntan a `127.0.0.1`. Por eso la comunicación
no sale del pod. Del pod se publican al host los puertos 5001 (proxy) y 5000 (Zot).
Zot en 5000 deja ver `verified/` y `quarantine/` sin la lógica del proxy, así que
la exposición directa es una decisión deliberada, sin autenticación.

**Limitación.** Los nombres son alias de `127.0.0.1`, no hosts distintos. Dos
contenedores que escuchen el mismo puerto darían conflicto. Por eso Zot escucha
en 5000 y el proxy en 5001. El orden de arranque se controla con `Requires=` y
`After=` en los quadlets.

## 4. Modelo de datos

### Zot

- `verified/<registro>/<repo>:<tag>`: lo que se considera seguro.
- `quarantine/<registro>/<repo>:<sha256-<hex>>`: cada digest en espera de escaneo
  y de los días de cuarentena, guardado bajo un tag derivado de su propio digest.
  No se sirve a los clientes.

Ejemplo: `docker pull <servidor>:5001/docker.io/library/nginx:1.27` se mira
primero en `verified/docker.io/library/nginx`. Si el tag no está, se sirve del
registro público y, en segundo plano, se copia a
`quarantine/docker.io/library/nginx` el digest al que apunta, bajo su tag
`sha256-<hex>`.

### SQLite (`state.sqlite3`, compartido por proxy y updater)

- `refs(registry, path, reference, last_requested)`: tags pedidos por los
  clientes. Sirven para saber qué mantener fresco.
- `quarantine(registry, path, digest, quarantined_at, published_at)`: cuándo entró
  cada digest en cuarentena y su fecha de build en el origen (`Created`). El plazo
  de cuarentena se cuenta desde `published_at` cuando se conoce; si no, desde
  `quarantined_at`. La identidad es el digest (inmutable), no el tag.
- `quarantine_refs(registry, path, digest, reference)`: qué tag de cliente
  satisface cada digest en cuarentena. Se usa al promover para re-apuntar el tag.

## 5. Nombres y parseo

Un nombre `<name>` se interpreta como en Docker/Podman:

- El primer segmento es registro si contiene `.` o `:`, o si es `localhost`.
  Si no, el registro es `docker.io`.
- En `docker.io`, un repo sin `/` pertenece a `library/`.

Así `nginx` equivale a `docker.io/library/nginx`.

## 6. Proxy

Reverse proxy que habla la API OCI Distribution. Endpoints:

- `GET /v2/`: responde `200 {}`.
- `GET|HEAD /v2/<name>/manifests/<ref>`: resuelve el origen y reenvía.
- `GET|HEAD /v2/<name>/blobs/<digest>`: busca primero en `verified` y luego en el
  upstream.
- `GET /v2/<name>/tags/list`: lista los tags de `verified`.

Flujo para un manifest:

1. Parsear el nombre. Rechazar si el registro no está en la allowlist
   (`UPSTREAM_REGISTRIES`).
2. Si es un digest, servirlo de `verified` si está; si no, del upstream.
3. Si es un tag, registrar la ref en SQLite y servir el tag **literal**: si el
   tag está en `verified`, se sirve de ahí.
4. Si el tag no está en `verified`, servir la imagen del registro público
   (reenvío con token anónimo) y, en segundo plano, copiar a `quarantine` el
   digest al que apunta ese tag (bajo su tag `sha256-<hex>`), escanearlo con
   ClamAV y, si está limpio, promoverlo a `verified` al momento (**vía rápida**,
   sin CVEs ni plazo). El proxy sirve el cuerpo en streaming desde el origen, sin
   serializar descargas.

## 7. Resolución de tags

No hay resolución de versiones: todo tag es **literal** y se reenvía tal cual al
registro público. No se usa `skopeo list-tags` en el camino de servicio.

| Petición | Qué se sirve |
|---|---|
| `1.27.4` | Ese tag, de `verified` si está; si no, del upstream. |
| `1.27` | Ese tag literal (no "la mayor `1.27.x`"). |
| `latest`, `stable` | Ese tag literal, tal como lo publica el upstream. |
| `alpine`, `bookworm` | Ese tag literal. |
| `@sha256:…` | Esa imagen, de `verified` si está; si no, del upstream (sin cuarentena). |

La prioridad es siempre `verified` primero; si el tag no está, el upstream.

## 8. Updater

Un ciclo cada `CHECK_INTERVAL` (6h por defecto). Cada paso es un módulo y un
fallo en uno no detiene los demás.

El updater gestiona el **camino largo** (actualizaciones): las imágenes nuevas
ya se promueven por la vía rápida del proxy. El camino largo aplica a los digests
que siguen en cuarentena (p. ej. un tag verificado que movió a otro digest).

1. **Limpieza de estado** (`cleanup.py`): un paquete sin imágenes en `verified`
   ni en `quarantine` se borra de la base de datos. Si el paquete tiene imágenes,
   se quitan las entradas de cuarentena cuyo digest ya no está en Zot y las refs
   cuyo tag ya no está en `verified` ni en `quarantine`. Va primero para que un
   paquete borrado de Zot no se re-descargue en el paso 2.
2. **Sincronizar upstream** (`upstream_sync.py`): para cada ref pedida, mira el
   digest actual de ese tag en el upstream; si es un digest nuevo (no está en
   `verified` ni en `quarantine`), lo baja a `quarantine` bajo su tag
   `sha256-<hex>` y registra la ref. Quita de `quarantine` los digests que el
   upstream ya no publica bajo ninguna ref pedida.
3. **Actualizar firmas ClamAV** (`clamav.py`): ejecuta `freshclam` para mantener
   al día la base de firmas, antes de promover.
4. **Promover** (`promoter.py`): si un digest tiene al menos `QUARANTINE_DAYS` de
   antigüedad (fecha de build en el origen; si no se conoce, desde su entrada en
   cuarentena), no tiene CVEs con severidad en `CVE_SEVERITY_THRESHOLD` (según
   `zli cve list`) y ClamAV no detecta malware, lo copia a `verified` re-apuntando
   cada tag de cliente que lo referencia, y lo quita de `quarantine`. Si tiene
   CVEs bloqueantes o malware, lo borra de `quarantine`. Si algún escaneo no está
   listo, espera al siguiente ciclo sin promover ni borrar.
5. **Retención** (`retention.py`): conserva en `verified` los tags que alguien ha
   pedido y poda el resto. No hay alias.

## 9. Zot

`config/zot/config.json` activa `search` con `cve` (Trivy embebido, base de datos
actualizada cada 24h) y `storage.gc` con `dedupe`. Zot escucha en el puerto 5000
dentro del pod.

Zot no usa la extensión `sync`: el reenvío al registro público lo hace el proxy, y la
copia a `quarantine` la dispara el proxy con `skopeo` en segundo plano. Zot solo
guarda `verified/` y `quarantine/`.

El escaneo corre en segundo plano cada ~15 min, sin configuración. Por eso un
tag recién insertado puede no tener resultado de CVEs todavía.

### Multi-arch

Se almacena la imagen multi-arch **completa** (índice + manifests de todas las
arquitecturas + blobs). Así el digest que usa el sistema como identidad es el
**digest del índice**, que coincide con el que devuelve `skopeo inspect` y con el
que Zot guarda. Si se copiara solo la plataforma del servidor
(`--multi-arch system`), el digest inspeccionado (índice) y el almacenado
(manifest de plataforma) no coincidirían, y fallarían la limpieza de cuarentena y
la detección de actualizaciones.

El escaneo de malware extrae solo la plataforma del servidor
(`--multi-arch system` en `skopeo.export_dir`) porque `umoci` no desempaqueta un
índice. Por tanto, se escanea la plataforma que realmente se ejecuta en el
homelab; si algún día hubiera clientes de otra arquitectura, habría que escanear
cada plataforma.

## 10. Estructura del repo

```
shared/zqp/        código común: image_ref, state, env, skopeo, zot_api, quarantine
proxy/app/         FastAPI: main, service, resolver, forwarder, upstream, config
updater/app/       main, upstream_sync, promoter, retention, cleanup,
                   scanner, malware, clamav, config
config/zot/        config.json de Zot
config/containers/ registries inseguros para skopeo
deploy/quadlets/   zot-quarantine-proxy.pod, zot, proxy y updater (systemd de usuario)
deploy/compose.yaml entorno de desarrollo
tests/             tests unitarios de la lógica pura
docs/design.md     este documento
```

## 11. Principios de código

- Un módulo, una responsabilidad. `main.py` solo hace el arranque.
- Funciones pequeñas. La lógica de tags y nombres no tiene I/O y es testeable.
- Type hints en las firmas públicas. Docstrings en español; identificadores en inglés.
- Logging con `logging.getLogger(__name__)`. No `print`.
- Errores explícitos. Nada de `except` silenciosos.
- Configuración solo por variables de entorno.

## 12. Invariantes

- Nada se sirve desde `verified` que no haya pasado el escaneo de malware (vía
  rápida) o el ciclo completo (actualizaciones).
- `quarantine` no se sirve: solo se escanea y se promueve.
- Todo tag se sirve literal, tal como lo publica el upstream. No se resuelve a
  otra versión.
- Una imagen nueva se promueve si ClamAV está limpio (sin CVEs ni plazo). Una
  actualización se promueve tras el plazo y los escaneos (CVE opcional + malware).
- Un digest se sirve de `verified` si está; si no, del upstream (no se cuarentena).
- La retención solo conserva los tags de `verified` que alguien ha pedido.

## 13. Validación en entorno real

Validado en un entorno Linux real (Podman rootless, Zot v2.1.22, Trivy):

- El proxy sirve de `verified` cuando está, reenvía al upstream (token anónimo y
  redirect a CDN) cuando no, y copia a `quarantine` en segundo plano con skopeo.
- Promoción completa: cuarentena → escaneo de Trivy → `verified` (con `zli`).
- Etiqueta móvil: `latest` se sirve tal como lo publica el upstream; al moverse a
  un digest nuevo, se abre una cuarentena nueva para ese digest.

Formato real de `zli cve list -f json` (validado):

| Estado de la imagen | Salida de `zli` | Veredicto |
|---|---|---|
| Sin escanear aún | `Error: tag metadata not found for given repo and tag names` (exit ≠ 0) | pendiente |
| Escaneada, 0 CVEs | `No CVEs found for image` (texto, exit 0) | limpia |
| Escaneada, con CVEs | JSON `{"Tag":…,"CVEList":[…]}` (exit 0) | según severidades |

Pendiente de comprobar en Fedora (producción):

- `DELETE /v2/<repo>/manifests/<digest>` y el GC automático liberan el espacio.
- Resolución de `zot`, `proxy` y `updater` dentro del pod (`podman exec updater getent hosts zot`).
