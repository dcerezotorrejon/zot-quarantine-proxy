# Instalación en Fedora Server (homelab)

Despliegue con **Podman rootless + quadlets** (servicios de usuario de systemd).
Todo se ejecuta con tu usuario normal, sin `sudo`, salvo los pasos marcados.

## 1. Instalar Podman (sudo)

```bash
sudo dnf install -y podman git
podman --version
```

## 2. Mantener los servicios activos tras cerrar sesión (sudo)

```bash
sudo loginctl enable-linger "$USER"
```

## 3. Abrir los puertos en el firewall (sudo)

- `5001`: proxy. Es el endpoint al que apuntan los clientes.
- `5000`: Zot directo. Sirve el almacén completo, incluidas `verified/` y
  `quarantine/`, sin pasar por la lógica del proxy.

```bash
sudo firewall-cmd --permanent --add-port=5000/tcp
sudo firewall-cmd --permanent --add-port=5001/tcp
sudo firewall-cmd --reload
```

## 4. Clonar el repositorio

El quadlet de Zot monta la configuración desde `~/zot-quarantine-proxy`, así que
la ruta debe ser esa.

```bash
git clone git@github.com:dcerezotorrejon/zot-quarantine-proxy.git ~/zot-quarantine-proxy
cd ~/zot-quarantine-proxy
```

## 5. Construir las imágenes

Hay que construir desde la **raíz** del repo (el contexto incluye `shared/`):

```bash
podman build -t localhost/zot-proxy:latest   -f proxy/Containerfile   .
podman build -t localhost/zot-updater:latest -f updater/Containerfile .
```

El `updater` descarga el binario `zli` del release de Zot. Para cambiar de
versión: `podman build --build-arg ZOT_VERSION=v2.1.x ...`.

## 6. Volúmenes de datos

Los datos van en volúmenes nombrados de Podman (`zot-data` y `proxy-data`), que se
crean automáticamente al primer arranque. No hace falta crearlos a mano.

- `zot-data`: almacenamiento de Zot (imágenes y base de datos de Trivy, ~3 GB).
- `proxy-data`: estado SQLite compartido por `proxy` y `updater`.

Se guardan en `~/.local/share/containers/storage/volumes/`.

## 7. Instalar los quadlets

```bash
mkdir -p ~/.config/containers/systemd
cp deploy/quadlets/*.pod deploy/quadlets/*.container ~/.config/containers/systemd/
```

Para ajustar la política (días de cuarentena, severidades bloqueantes, frecuencia)
edita las variables `Environment=` de `~/.config/containers/systemd/updater.container`.

## 8. Arrancar los servicios

```bash
systemctl --user daemon-reload
systemctl --user enable --now zot-quarantine-proxy-pod.service zot.service proxy.service updater.service
```

Los nombres salen del fichero: `zot-quarantine-proxy.pod` genera
`zot-quarantine-proxy-pod.service`; `zot.container` genera `zot.service`, etc.
`proxy` y `updater` arrancan después de `zot` (`Requires=` y `After=`). Los
nombres `zot`, `proxy` y `updater` se resuelven dentro del pod gracias a las
entradas `AddHost=` de `zot-quarantine-proxy.pod`.

## 9. Verificar

```bash
podman ps --pod
podman exec updater getent hosts zot       # debe resolver a 127.0.0.1
curl -s http://localhost:5001/v2/          # debe responder {}
systemctl --user status proxy.service updater.service --no-pager
```

Logs en vivo:

```bash
journalctl --user -u proxy.service -f
journalctl --user -u updater.service -f
```

## 10. Configurar los clientes

Los endpoints son HTTP, así que cada cliente debe tratarlos como registry inseguro.
Sustituye `fedora-server` por el nombre o IP de tu servidor. Para usar el proxy,
basta con `5001`. Añade `5000` solo si necesitas acceder a Zot directamente.

**Podman** (`~/.config/containers/registries.conf.d/zqp.conf` en el cliente):

```toml
[[registry]]
location = "fedora-server:5001"
insecure = true

[[registry]]
location = "fedora-server:5000"
insecure = true
```

**Docker** (`/etc/docker/daemon.json` en el cliente, luego `sudo systemctl restart docker`):

```json
{
  "insecure-registries": ["fedora-server:5001", "fedora-server:5000"]
}
```

## 11. Probar

```bash
podman pull fedora-server:5001/docker.io/library/nginx:1.27
podman pull fedora-server:5001/docker.io/library/nginx:latest
podman pull fedora-server:5001/nginx:alpine
curl -s http://fedora-server:5000/v2/      # Zot directo, debe responder {}
```

La primera descarga de cada tag se sirve del registro público y se copia a
`quarantine` en segundo plano. Cuando el `updater` la promueve a `verified` (tras
`QUARANTINE_DAYS` con escaneo limpio), se sirve desde local. Para ver el estado:

```bash
podman exec zot ls /var/lib/zot
```

## Actualizar

```bash
cd ~/zot-quarantine-proxy && git pull
podman build -t localhost/zot-proxy:latest   -f proxy/Containerfile   .
podman build -t localhost/zot-updater:latest -f updater/Containerfile .
```

> **Aviso: esta versión cambia el esquema de `state.sqlite3`** (la cuarentena
> pasa a identificarse por digest). Si vienes de una versión anterior, purga el
> fichero de estado **con los servicios parados**, antes de arrancarlos:
>
> ```bash
> systemctl --user stop proxy.service updater.service
> rm ~/.local/share/containers/storage/volumes/proxy-data/_data/state.sqlite3
> ```
>
> No se pierde ninguna imagen: están en el volumen `zot-data`, así que
> `verified/` y `quarantine/` quedan intactas. Solo se pierde el estado —las refs
> pedidas y los plazos de cuarentena—, que vuelven a contar desde cero.

```bash
systemctl --user restart proxy.service updater.service
```

## Desinstalar

```bash
systemctl --user disable --now proxy.service updater.service zot.service zot-quarantine-proxy-pod.service
rm -f ~/.config/containers/systemd/zot-quarantine-proxy.pod ~/.config/containers/systemd/zot.container \
      ~/.config/containers/systemd/proxy.container ~/.config/containers/systemd/updater.container
systemctl --user daemon-reload
# Datos (opcional, borra todas las imágenes cacheadas):
podman volume rm zot-data proxy-data
```

## Desarrollo local (sin quadlets)

```bash
podman compose -f deploy/compose.yaml up -d --build
```

Expone el proxy en `localhost:5001` y Zot en `localhost:5000`.
