# zot-quarantine-proxy

Proxy de imágenes de contenedores (pull-through cache) para homelab, basado en
**Podman** y **Zot**. Antes de considerar una imagen "verificada", la mantiene en
**cuarentena** un número de días y solo la promueve si el escaneo de CVEs
(Trivy embebido en Zot) sale limpio.

## Cómo funciona

- Un **pod de Podman** (`zot-quarantine-proxy`) con tres contenedores: `zot`,
  `proxy` y `updater`. Se hablan por nombre (`http://zot:5000`).
- Zot es el almacén OCI y usa dos *namespaces*:
  - `verified/<registro>/<repo>`: lo que se sirve como versión segura.
  - `quarantine/<registro>/<repo>`: en espera de escaneo y promoción. No se
    sirve a los clientes.
- **proxy** (Python, Fedora minimal) es el endpoint OCI al que apuntan los
  clientes. Sirve el tag pedido tal cual: si está en `verified`, lo sirve desde
  ahí; si no, lo sirve del registro público y, en segundo plano, lo copia a
  `quarantine` y lo escanea con ClamAV. Si está limpio, lo promueve a `verified`
  al momento (**vía rápida** para imágenes nuevas, sin CVEs ni plazo).
- **updater** (Python, Fedora) cada 6 h detecta las actualizaciones (un tag ya
  verificado que mueve a otro digest) y las somete al **camino largo**: 7 días de
  antigüedad (desde la build en el origen) + escaneo de CVEs (Trivy, opcional) y
  de malware (ClamAV). También actualiza las firmas de ClamAV, poda lo que nadie
  usa y limpia la base de datos.

Ejemplo:

```bash
podman pull <servidor>:5001/docker.io/library/nginx:1.27
```

## Cómo se sirven los tags

El proxy sirve cada tag **tal cual** lo pide el cliente. No interpreta versiones:
no resuelve `latest` a "la mayor versión" ni `1.27` a "la mayor `1.27.x`". El
resultado es el que devuelve el registro público.

| Petición | Qué se sirve |
|---|---|
| `nginx:1.27.4` | ese tag, de `verified` si está; si no, del upstream (y se copia a cuarentena en segundo plano) |
| `nginx:1.27` | ese tag literal (no "la mayor `1.27.x`") |
| `nginx:latest` / `stable` | ese tag literal, tal como lo publica el upstream |
| `nginx:alpine` | ese tag literal |
| `nginx@sha256:…` | esa imagen por digest, de `verified` si está; si no, del upstream (sin cuarentena) |

La prioridad al servir es siempre `verified` primero; si el tag no está, se sirve
del upstream y, en segundo plano, se copia a `quarantine` el digest al que apunta.

Hay dos caminos para llegar a `verified`:

- **Imagen nueva** (vía rápida): se escanea con ClamAV en el proxy y, si está
  limpia, se promueve al momento. Sin CVEs ni plazo.
- **Actualización** (camino largo): un tag verificado que mueve a otro digest
  espera a que el nuevo digest tenga `QUARANTINE_DAYS` de antigüedad (desde su
  fecha de build) y pasa por CVEs (opcional) y malware.

> **Nota:** `latest` es un tag móvil. Una vez verificado se sirve desde
> `verified`, que tiene prioridad, así que queda por detrás del upstream hasta
> que el nuevo digest cumpla el plazo (`QUARANTINE_DAYS`, 7 días por defecto
> contados desde su fecha de build) cada vez que el tag se mueve.

Solo se conservan en `verified` los tags que alguien ha pedido; los que nadie
pide se podan para no acumular almacenamiento.

Si un paquete no tiene ninguna imagen en `verified` ni en `quarantine`, se borra
de la base de datos.

## Arquitectura

```
clientes (docker/podman pull)
        │  <servidor>:5001
        ▼
┌──────────── pod zot-quarantine-proxy (rootless, red compartida) ───┐
│                                                                   │
│  [proxy] :5001 ── reverse proxy OCI ──► [zot] :5000               │
│     │  Fedora minimal + Python + skopeo         ghcr.io/project-zot/zot
│     │                                           search + cve (Trivy)
│  [updater] Fedora + Python + skopeo + zli + ClamAV ─► CVE + malware │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
        ▲ puertos publicados al host: 5001 (proxy, uso normal) y 5000 (Zot directo).
          Los nombres zot, proxy y updater se resuelven dentro del pod con AddHost.
```

Estructura del repositorio:

```
config/zot/            configuración de Zot
config/containers/     registries inseguros (Zot se sirve por HTTP en el pod)
shared/zqp/            código compartido: nombres y referencias, SQLite, skopeo, API de Zot, cuarentena
proxy/                 Containerfile + app FastAPI (endpoints OCI)
updater/               Containerfile + app (un módulo por paso del ciclo)
deploy/quadlets/       red y contenedores systemd de usuario (producción, Fedora)
deploy/compose.yaml    entorno de desarrollo
tests/                 tests de la lógica de nombres y del estado de cuarentena
```

## Configuración

| Variable | Contenedor | Por defecto | Descripción |
|---|---|---|---|
| `ZOT_URL` | proxy, updater | `http://zot:5000` | URL interna de Zot dentro del pod |
| `UPSTREAM_REGISTRIES` | proxy | `docker.io,ghcr.io,quay.io,registry.k8s.io` | Registros permitidos (allowlist) |
| `QUARANTINE_DAYS` | updater | `7` | Antigüedad mínima (días desde la build en el origen) antes de promover una actualización |
| `CVE_SEVERITY_THRESHOLD` | updater | (vacío) | Severidades que bloquean la promoción de actualizaciones. Vacío = no bloquea por CVEs |
| `CHECK_INTERVAL` | updater | `6h` | Frecuencia del ciclo (formatos `30m`, `6h`, `7d`) |
| `STATE_DIR` | proxy, updater | `/var/lib/proxy` | Dónde está `state.sqlite3` |
| `SKOPEO_MULTI_ARCH` | proxy, updater | `all` | Plataformas que copia skopeo. Dejar en `all` para que el digest almacenado coincida con el inspeccionado |
| `LOG_LEVEL` | proxy, updater | `INFO` | Nivel de logging |

## Instalación

Ver [INSTALL.md](INSTALL.md) para la guía completa en Fedora Server.

## Tests

```bash
PYTHONPATH=shared:proxy python -m unittest discover -s tests -v
```

## Limitaciones conocidas

- El escaneo de Zot corre en segundo plano (~15 min). Una imagen recién
  descargada puede tardar en tener resultado de CVEs.
- Sin autenticación: pensado para la red local de casa.
- Solo HTTP en el pod. Los clientes deben configurar el registry como inseguro.
- Sin autenticación en ninguno de los dos puertos. Zot en `5000` expone
  `verified/` y `quarantine/` sin pasar por la lógica del proxy. Si la red no es
  de confianza, no publiques `5000` o restringe el acceso en el firewall.
- Los digests que no están verificados se sirven del upstream; al no tener tag, no pasan por cuarentena.
- Los registros upstream privados (con credenciales) no están soportados todavía.
