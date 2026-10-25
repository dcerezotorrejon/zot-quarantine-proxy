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
  clientes. Resuelve el tag pedido: si está en `verified`, lo sirve desde ahí; si
  no, lo sirve del registro público y, en segundo plano, lo copia a `quarantine`.
- **updater** (Python, Fedora) cada 6 h detecta versiones nuevas, purga lo que
  el upstream retiró, promueve lo que cumple las reglas, mantiene los alias
  (`latest`, `stable`, `1.27`…) y limpia la base de datos de paquetes huérfanos.

Ejemplo:

```bash
podman pull <servidor>:5001/docker.io/library/nginx:1.27
```

## Reglas de versiones

| Petición | Qué se sirve |
|---|---|
| `nginx:1.27.4` (exacto) | esa versión (de `verified` si está; si no, del upstream) |
| `nginx:1.27` (parcial) | la **mayor** versión `1.27.x` disponible (primero `verified`) |
| `nginx:1` (parcial) | la mayor versión `1.x.y` disponible |
| `nginx:latest` / `stable` | la versión verificada más reciente |
| `nginx@sha256:…` | esa imagen por digest (de `verified` si está; si no, del upstream) |
| `alpine` (sin semver) | el tag literal |

Solo se conservan las versiones que alguien pide: una por cada ref parcial o
flotante. Las demás se retiran para no acumular almacenamiento.

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
│  [updater] Fedora + Python + skopeo + zli ────► zli cve list      │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
        ▲ puertos publicados al host: 5001 (proxy, uso normal) y 5000 (Zot directo).
          Los nombres zot, proxy y updater se resuelven dentro del pod con AddHost.
```

Estructura del repositorio:

```
config/zot/            configuración de Zot
config/containers/     registries inseguros (Zot se sirve por HTTP en el pod)
shared/zqp/            código compartido: nombres, tags, SQLite, skopeo, API de Zot
proxy/                 Containerfile + app FastAPI (endpoints OCI)
updater/               Containerfile + app (un módulo por paso del ciclo)
deploy/quadlets/       red y contenedores systemd de usuario (producción, Fedora)
deploy/compose.yaml    entorno de desarrollo
tests/                 tests de la lógica de versiones y nombres
```

## Configuración

| Variable | Contenedor | Por defecto | Descripción |
|---|---|---|---|
| `ZOT_URL` | proxy, updater | `http://zot:5000` | URL interna de Zot dentro del pod |
| `UPSTREAM_REGISTRIES` | proxy | `docker.io,ghcr.io,quay.io,registry.k8s.io` | Registros permitidos (allowlist) |
| `QUARANTINE_DAYS` | updater | `7` | Días en cuarentena antes de promover |
| `CVE_SEVERITY_THRESHOLD` | updater | `HIGH,CRITICAL` | Severidades que bloquean la promoción |
| `CHECK_INTERVAL` | updater | `6h` | Frecuencia del ciclo (formatos `30m`, `6h`, `7d`) |
| `STATE_DIR` | proxy, updater | `/var/lib/proxy` | Dónde está `state.sqlite3` |
| `SKOPEO_MULTI_ARCH` | proxy, updater | `system` | Plataformas que copia skopeo: `system`, `all` o `index-only` |
| `LOG_LEVEL` | proxy, updater | `INFO` | Nivel de logging |

## Instalación

Ver [INSTALL.md](INSTALL.md) para la guía completa en Fedora Server.

## Tests

```bash
PYTHONPATH=shared python -m unittest discover -s tests -v
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
- Un tag parcial o flotante se resuelve contra `verified` primero; si no hay nada
  verificado, se resuelve contra el upstream (la mayor versión publicada).
