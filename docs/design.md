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
| Primera petición (on-demand) | Se descarga a `quarantine` y se sirve desde ahí de inmediato (caché). |
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
│  [proxy]   :5001 ── reverse proxy OCI ──► [zot]  :5000           │
│  (skopeo: fetch e insert)                                        │
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
- `quarantine/<registro>/<repo>:<tag>`: caché en espera de escaneo y de los días
  de cuarentena.

Ejemplo: `docker pull <servidor>:5001/docker.io/library/nginx:1.27` se resuelve
contra `verified/docker.io/library/nginx`. Si no está, se inserta en
`quarantine/docker.io/library/nginx` y se sirve desde ahí.

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
- `GET|HEAD /v2/<name>/manifests/<ref>`: resuelve la copia a servir y la reenvía.
- `GET|HEAD /v2/<name>/blobs/<digest>`: busca primero en `verified` y luego en
  `quarantine`.
- `GET /v2/<name>/tags/list`: lista los tags de `verified`.

Flujo para un manifest:

1. Parsear el nombre. Rechazar si el registro no está en la allowlist
   (`UPSTREAM_REGISTRIES`).
2. Si es un digest, servirlo solo si ya existe en Zot.
3. Si es un tag, registrar la ref en SQLite.
4. Buscar copia local: primero `verified`, después `quarantine`.
5. Si no hay copia, descargar a `quarantine` con `skopeo copy` y servir desde ahí.
   Las descargas se serializan con un lock.

## 7. Resolución de tags

| Tipo | Ejemplo | Qué se sirve |
|---|---|---|
| Exacto | `1.27.4` | Esa versión. |
| Literal no semver | `alpine` | El tag literal. |
| Parcial | `1`, `1.27` | La mayor versión semver que cumple el prefijo. Sin sufijos. |
| Flotante | `latest`, `stable` | La versión semver más alta de `verified`. Si no hay semver, el literal. |
| Digest | `@sha256:…` | Esa imagen, si ya está en Zot. |

Los parciales y flotantes se resuelven contra `verified` primero.

## 8. Updater

Un ciclo cada `CHECK_INTERVAL` (6h por defecto). Cada paso es un módulo y un
fallo en uno no detiene los demás.

1. **Sincronizar upstream** (`upstream_sync.py`): para cada ref, detecta la
   versión nueva y la baja a `quarantine`. Quita de `quarantine` los tags que el
   upstream ya no publica.
2. **Promover** (`promoter.py`): si un tag lleva al menos `QUARANTINE_DAYS` en
   cuarentena, `zli cve list` no tiene CVEs con severidad en
   `CVE_SEVERITY_THRESHOLD` y lo copia a `verified` y lo quita de `quarantine`.
   Si tiene CVEs bloqueantes, lo borra de `quarantine`. Si el escaneo no está
   listo, espera al siguiente ciclo sin promover ni borrar.
3. **Alias y retención** (`retention.py`): `latest`, `stable`, `1.27`… apuntan a
   la versión que les corresponde. Se podan las versiones semver que ninguna ref
   pide. Nunca se borran tags no semver.
4. **Limpieza de estado** (`cleanup.py`): un paquete sin imágenes en `verified`
   ni en `quarantine` se borra de la base de datos.

## 9. Zot

`config/zot/config.json` activa `search` con `cve` (Trivy embebido, base de datos
actualizada cada 24h) y `storage.gc` con `dedupe`. Zot escucha en el puerto 5000
dentro del pod.

El escaneo corre en segundo plano cada ~15 min, sin configuración. Por eso un
tag recién insertado puede no tener resultado de CVEs todavía.

## 10. Estructura del repo

```
shared/zqp/        código común: image_ref, versions, state, env,
                   skopeo, zot_api, quarantine
proxy/app/         FastAPI: main, service, resolver, forwarder, config
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
- Un tag exacto se sirve tal cual. No se sustituye por otra versión.
- Un tag se promueve solo con el tiempo cumplido y sin CVEs bloqueantes.
- Un digest no se descarga a ciegas: solo se sirve si ya existe.
- La retención solo borra versiones semver que nadie pide.

## 13. Pendiente de validar en Fedora

La integración real con Zot, skopeo y zli no se ha probado todavía. Puntos a comprobar:

- Formato exacto de `zli cve list -f json`. El código espera la clave `CVEList`.
- Una imagen recién insertada no debe contar como limpia antes de tener resultado
  de escaneo. Ahora se trata como pendiente si zli falla, pero hay que verificarlo.
- `DELETE /v2/<repo>/manifests/<digest>` y el GC automático liberan el espacio.
- Resolución de `zot`, `proxy` y `updater` dentro del pod (`podman exec updater getent hosts zot`).
- Dos descargas simultáneas de imágenes grandes se esperan entre sí por el lock.
