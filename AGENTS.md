# Instrucciones para agentes IA

Proxy OCI pull-through con cuarentena y escaneo de CVEs para un homelab Fedora,
basado en Podman rootless, Zot y Python. Lee `README.md` para el uso e
`INSTALL.md` para el despliegue antes de tocar la infraestructura.

## Mapa del repo

```
shared/zqp/        código común: nombres (image_ref), tags (versions), SQLite (state),
                   skopeo, cliente de la API de Zot (zot_api), cuarentena (quarantine)
proxy/app/         FastAPI: endpoints OCI, resolución del origen (verified/upstream),
                   reenvío a Zot y a registros públicos
updater/app/       ciclo de mantenimiento, un módulo por paso
config/zot/        config.json de Zot (search + cve; gc habilitado)
config/containers/ registries inseguros para skopeo (Zot se sirve por HTTP en el pod)
deploy/quadlets/   unidades systemd de usuario para producción
deploy/compose.yaml entorno de desarrollo
tests/             tests unitarios de la lógica pura
```

La documentación tiene dos niveles y cada uno va en su sitio:

- `docs/design.md`: diseño técnico (arquitectura, flujos internos, decisiones).
- `docs/funcional/`: qué hace el sistema para quien lo usa y lo opera
  (`02-uso-cliente`, `03-tags-y-versiones`, `04-cuarentena-y-promocion`,
  `05-operacion`, `06-errores`).

Si un cambio altera el comportamiento visible (tags, plazos, códigos de error,
variables, puertos, mensajes del updater), actualiza también `docs/funcional/`
en el mismo cambio. Si hay conflicto entre la documentación y el código, el
código manda.

## Invariantes del dominio

No cambies estas reglas sin confirmarlo con el usuario:

- **Namespaces en Zot**: `verified/<registro>/<repo>` es lo que se considera
  seguro; `quarantine/<registro>/<repo>` es la zona de espera para escanear y
  promover. Nada se sirve desde `verified` que no haya pasado la cuarentena.
- **Servir**: solo se sirve desde local lo que está en `verified`. Si no está,
  se sirve directamente del registro público (upstream) y, en segundo plano, se
  copia a `quarantine` (si no está ya) para el ciclo de escaneo y promoción.
  `quarantine` no se sirve a los clientes.
- **Prioridad al servir**: `verified` primero; si no, el upstream.
- **Plazo y veredicto**: al cumplir `QUARANTINE_DAYS` (7 por defecto), si
  `zli cve list` no tiene CVEs con severidad en `CVE_SEVERITY_THRESHOLD` la imagen
  se promueve a `verified`; si los tiene, se **elimina** de cuarentena. Si el
  escaneo no está listo (PENDING), no se promueve ni se elimina: se reintenta.
- **Tags**: `latest`/`stable` son flotantes y apuntan a la versión verificada
  más alta. `1` y `1.27` son parciales y apuntan a la mayor versión que cumplen.
  Un tag exacto se sirve tal cual. Los tags no semver se tratan como literales.
- **Retención**: solo se podan versiones semver que nadie pide. Nunca se borran
  tags no semver (`alpine`, etc.) ni digests que aún referencia un tag protegido.
- **Limpieza de estado**: un paquete sin imágenes en `verified` ni en
  `quarantine` se borra de la base de datos.
- **Digests**: se sirven de `verified` si ya existen; si no, se reenvían al
  upstream como un tag exacto. Al no tener tag, no se copian a cuarentena.

## Comandos

```bash
# Tests de la lógica pura (desde la raíz del repo)
PYTHONPATH=shared python -m unittest discover -s tests -v

# Comprobación de sintaxis
python -m compileall -q shared proxy updater tests

# Build de las imágenes (siempre desde la raíz: el contexto incluye shared/)
podman build -t localhost/zot-proxy:latest   -f proxy/Containerfile   .
podman build -t localhost/zot-updater:latest -f updater/Containerfile .
```

Los imports de `proxy/app` y `updater/app` necesitan `httpx` y, en el proxy,
`fastapi`. Usa un venv (`python -m venv .venv`) y no instales en el sistema.
El directorio `.venv/` está en `.gitignore`.

## Convenciones de código Python

- **Modular y legible**: un módulo, una responsabilidad. `main.py` solo hace
  wiring y arranque. La lógica va en módulos con nombre explícito.
- **Funciones pequeñas y puras** cuando sea posible. La lógica de tags y nombres
  debe seguir sin I/O, para poder testearla.
- **Type hints** en las firmas públicas y **docstrings** en módulos, clases y
  funciones públicas.
- **Formato de docstrings**: estilo Google, en español. Primera línea = resumen
  en una frase, en modo imperativo o descriptivo. Si la función tiene parámetros
  no triviales, devuelve algo distinto de `None` o lanza excepciones, añade
  `Args:`, `Returns:` y `Raises:`. Ejemplo:

  ```python
  def pick_tag(spec: TagSpec, candidates: Iterable[str]) -> str | None:
      """Elige el tag concreto que satisface una referencia.

      Args:
          spec: Referencia pedida por el cliente, ya clasificada.
          candidates: Tags disponibles en el registro.

      Returns:
          El tag elegido, o None si ninguno cumple la referencia.
      """
  ```

  Las funciones privadas (`_nombre`) de una línea no necesitan docstring.
- **Identificadores en inglés; comentarios y docstrings en español.**
- **Logging** con `logging.getLogger(__name__)`. Nada de `print`.
- **Errores explícitos**: excepciones propias o de la librería. Nada de
  `except: pass` ni `except Exception` silencioso. En los bucles de mantenimiento,
  un fallo se registra con `logger.exception` y no detiene los demás pasos.
- **Configuración solo por variables de entorno**, con defaults documentados en
  `config.py` y en la tabla del `README.md`.
- No añadas dependencias sin motivo. Hoy: `fastapi`, `uvicorn`, `httpx` y la CLI
  de `skopeo`, `zli`. SQLite es de la librería estándar.

## Qué no tocar sin preguntar

- Pod: los tres contenedores van en el pod `zot-quarantine-proxy`
  (`deploy/quadlets/zot-quarantine-proxy.pod`). Se hablan por nombre (`zot`, `proxy`,
  `updater`), resueltos con `AddHost=` en el `.pod` apuntando a `127.0.0.1`. Si
  añades un contenedor, añade también su `AddHost=` en el `.pod`.
- Puertos: Zot escucha en `5000` y el pod lo publica al host, igual que el proxy
  en `5001`. Ambos son accesibles desde fuera del pod y no tienen autenticación. Si cambias uno, actualiza `deploy/quadlets/`,
  `deploy/compose.yaml`, `INSTALL.md`, `README.md`, `docs/design.md` y los
  defaults de `ZOT_URL` en `proxy/app/config.py` y `updater/app/config.py`.
- La imagen de Zot debe ser `ghcr.io/project-zot/zot` (full). No uses
  `zot-minimal`, porque no incluye search ni CVEs.
- Los directorios de datos (`zot-data/`, `proxy-data/`) nunca se versionan ni se
  borran desde el agente.
- Los quadlets usan `%h` y `:Z` a propósito (rootless y SELinux). No los
  "simplifiques" sin probar en Fedora.

## Verificación

- Antes de dar un cambio por bueno: tests + `compileall`.
- Si cambias la lógica de versiones, añade o ajusta tests en `tests/test_shared.py`.
- La integración real (Zot, skopeo, zli, Trivy) solo se puede probar en el
  servidor Fedora. Di explícitamente cuándo algo no está verificado en un
  entorno real.

## Git y entorno

- No hagas `commit`, `push` ni `reset --hard` sin que el usuario lo pida.
- El entorno de desarrollo del usuario es Windows con PowerShell; el servidor
  de destino es Fedora. Los comandos de despliegue van en `INSTALL.md` en bash.
- Los planes de diseño van en `C:\Users\daniel.cerezo\.opencode\plan`, no en el repo.
