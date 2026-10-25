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
| Primera petición | Si no está en `verified`, se sirve del upstream y se copia a `quarantine` en segundo plano. |
| Escaneo | `zli` + Trivy embebido en Zot. Sin servidor ni binario Trivy aparte. |
| Imagen de Zot | `ghcr.io/project-zot/zot:latest` (full). No `zot-minimal`: no trae search ni CVEs. |
| Namespaces | Dos namespaces en un único Zot: `verified` y `quarantine`. |
| Topología | Pod `zot-quarantine-proxy` con los tres contenedores. Nombres resolubles con `AddHost` (ver §3). |
| Despliegue | Podman rootless + quadlets (systemd de usuario) en Fedora. Compose solo para desarrollo. |
| Puertos | Proxy `5001` (uso normal) y Zot `5000` (acceso directo). Ambos publicados al host. |

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
- `quarantine/<registro>/<repo>:<tag>`: en espera de escaneo y de los días de
  cuarentena. No se sirve a los clientes.

Ejemplo: `docker pull <servidor>:5001/docker.io/library/nginx:1.27` se resuelve
contra `verified/docker.io/library/nginx`. Si no está, se sirve del registro
público y, en segundo plano, se copia a `quarantine/docker.io/library/nginx`.

### SQLite (`state.sqlite3`, compartido por proxy y updater)

- `refs(registry, path, reference, last_requested)`: tags pedidos por los
  clientes. Sirven para saber qué mantener fresco.
- `quarantine(registry, path, tag, quarantined_at)`: cuándo entró cada tag en
  cuarentena. Sirve para contar los días.

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
3. Si es un tag, registrar la ref en SQLite y resolver el tag concreto
   (con `skopeo list-tags` para flotantes y parciales).
4. Si hay copia en `verified`, servirla desde ahí.
5. Si no, servir la imagen del registro público (reenvío con token anónimo) y, en
   segundo plano, copiarla a `quarantine` si no está ya, para el ciclo de escaneo
   y promoción. El proxy sirve el cuerpo en streaming desde el origen, sin
   serializar descargas.

## 7. Resolución de tags

| Tipo | Ejemplo | Qué se sirve |
|---|---|---|
| Exacto | `1.27.4` | Esa versión. |
| Literal no semver | `alpine` | El tag literal. |
| Parcial | `1`, `1.27` | La mayor versión semver que cumple el prefijo. Sin sufijos. |
| Flotante | `latest`, `stable` | La versión semver más alta de `verified`. Si no hay semver, el literal. |
| Digest | `@sha256:…` | Esa imagen, de `verified` si está; si no, del upstream. |

Los parciales y flotantes se resuelven contra `verified` primero.

## 8. Updater

Un ciclo cada `CHECK_INTERVAL` (6h por defecto). Cada paso es un módulo y un
fallo en uno no detiene los demás.

1. **Limpieza de estado** (`cleanup.py`): un paquete sin imágenes en `verified`
   ni en `quarantine` se borra de la base de datos. Si el paquete tiene imágenes,
   se quitan las entradas de cuarentena y las refs cuyo tag ya no existe en Zot.
   Va primero para que un paquete borrado de Zot no se re-descargue en el paso 2.
2. **Sincronizar upstream** (`upstream_sync.py`): para cada ref, detecta la
   versión nueva y la baja a `quarantine`. Quita de `quarantine` los tags que el
   upstream ya no publica.
3. **Promover** (`promoter.py`): si un tag lleva al menos `QUARANTINE_DAYS` en
   cuarentena, `zli cve list` no tiene CVEs con severidad en
   `CVE_SEVERITY_THRESHOLD` y lo copia a `verified` y lo quita de `quarantine`.
   Si tiene CVEs bloqueantes, lo borra de `quarantine`. Si el escaneo no está
   listo, espera al siguiente ciclo sin promover ni borrar.
4. **Alias y retención** (`retention.py`): `latest`, `stable`, `1.27`… apuntan a
   la versión que les corresponde. Se podan las versiones semver que ninguna ref
   pide. Nunca se borran tags no semver.

## 9. Zot

`config/zot/config.json` activa `search` con `cve` (Trivy embebido, base de datos
actualizada cada 24h) y `storage.gc` con `dedupe`. Zot escucha en el puerto 5000
dentro del pod.

Zot no usa la extensión `sync`: el reenvío al registro público lo hace el proxy, y la
copia a `quarantine` la dispara el proxy con `skopeo` en segundo plano. Zot solo
guarda `verified/` y `quarantine/`.

El escaneo corre en segundo plano cada ~15 min, sin configuración. Por eso un
tag recién insertado puede no tener resultado de CVEs todavía.

## 10. Estructura del repo

```
shared/zqp/        código común: image_ref, versions, state, env,
                   skopeo, zot_api, quarantine
proxy/app/         FastAPI: main, service, resolver, forwarder, upstream, config
updater/app/       main, upstream_sync, promoter, retention, cleanup,
                   scanner, config
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

- Nada se sirve desde `verified` si no ha pasado por cuarentena.
- `quarantine` no se sirve: solo se escanea y se promueve.
- Un tag exacto se sirve tal cual. No se sustituye por otra versión.
- Un tag se promueve solo con el tiempo cumplido y sin CVEs bloqueantes.
- Un digest se sirve de `verified` si está; si no, del upstream (no se cuarentena).
- La retención solo borra versiones semver que nadie pide.

## 13. Validación en entorno real

Validado en un entorno Linux real (Podman rootless, Zot v2.1.22, Trivy):

- El proxy sirve de `verified` cuando está, reenvía al upstream (token anónimo y
  redirect a CDN) cuando no, y copia a `quarantine` en segundo plano con skopeo.
- Promoción completa: cuarentena → escaneo de Trivy → `verified` (con `zli`).
- Etiqueta flotante: `latest` se resuelve a la mayor versión verificada, y
  `upstream_sync` baja la última del upstream para mantenerla al día.

Formato real de `zli cve list -f json` (validado):

| Estado de la imagen | Salida de `zli` | Veredicto |
|---|---|---|
| Sin escanear aún | `Error: tag metadata not found for given repo and tag names` (exit ≠ 0) | pendiente |
| Escaneada, 0 CVEs | `No CVEs found for image` (texto, exit 0) | limpia |
| Escaneada, con CVEs | JSON `{"Tag":…,"CVEList":[…]}` (exit 0) | según severidades |

Pendiente de comprobar en Fedora (producción):

- `DELETE /v2/<repo>/manifests/<digest>` y el GC automático liberan el espacio.
- Resolución de `zot`, `proxy` y `updater` dentro del pod (`podman exec updater getent hosts zot`).
