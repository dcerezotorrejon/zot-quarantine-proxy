# 05 · Operación

Esta guía es para quien mantiene el servidor: configuración, logs, datos y tareas
periódicas.

## Variables de configuración

Se definen en los quadlets de `~/.config/containers/systemd/`.

### Proxy (`proxy.container`)

| Variable | Por defecto | Qué controla |
|---|---|---|
| `ZOT_URL` | `http://zot:5000` | Dirección interna de Zot. No cambiar salvo que cambies el nombre del contenedor. |
| `UPSTREAM_REGISTRIES` | `docker.io,ghcr.io,quay.io,registry.k8s.io` | Registros que se permiten. Cualquier otro devuelve `DENIED`. |
| `SKOPEO_MULTI_ARCH` | `all` | Plataformas que copia skopeo. Dejar en `all` (multi-arch completo) para que el digest almacenado coincida con el inspeccionado. |
| `LOG_LEVEL` | `INFO` | Detalle de los logs: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |

### Updater (`updater.container`)

| Variable | Por defecto | Qué controla |
|---|---|---|
| `QUARANTINE_DAYS` | `7` | Días mínimos en cuarentena antes de promover. |
| `CVE_SEVERITY_THRESHOLD` | (vacío) | Severidades que impiden la promoción de actualizaciones. Vacío = no bloquea por CVEs. |
| `CHECK_INTERVAL` | `6h` | Frecuencia del ciclo. Formatos: `30m`, `6h`, `7d`. |
| `LOG_LEVEL` | `INFO` | Detalle de los logs. |

Por defecto `CVE_SEVERITY_THRESHOLD` está **vacío**, así que los CVEs no bloquean
la promoción de actualizaciones (las imágenes nuevas ya no se escanean por CVEs).
Para reactivar el bloqueo, pon `Environment=CVE_SEVERITY_THRESHOLD=HIGH,CRITICAL`
(o las severidades que quieras).

Después de cambiar una variable:

```bash
systemctl --user daemon-reload
systemctl --user restart proxy.service updater.service
```

## Ver el estado

```bash
# Contenedores del pod
podman ps --pod

# Logs del proxy en vivo
journalctl --user -u proxy.service -f

# Logs del updater en vivo
journalctl --user -u updater.service -f

# Tags verificados de una imagen
curl -s http://localhost:5000/v2/verified/docker.io/library/nginx/tags/list

# Tags en cuarentena de una imagen
curl -s http://localhost:5000/v2/quarantine/docker.io/library/nginx/tags/list
```

En `quarantine/` los tags son `sha256-<hex>` (el digest de la imagen), no los
tags que pidió el cliente.

## Qué significan los mensajes

| Mensaje | Significado | Qué hacer |
|---|---|---|
| `Descargando … a cuarentena (digest …)` | Un digest nuevo se copia a cuarentena bajo su tag `sha256-<hex>`. | Nada. |
| `Escaneo pendiente para …` | Zot aún no tiene el resultado de CVEs. | Nada. Se reintenta. |
| `Eliminado … al terminar la cuarentena por CVEs: …` | Al cumplir el plazo el digest tenía CVEs bloqueantes. Se ha borrado de la cuarentena. | Revisar los IDs. Ver [04](04-cuarentena-y-promocion.md#qué-pasa-con-una-imagen-vulnerable). |
| `Escaneo de malware pendiente para …` | No se pudo desempaquetar o escanear la imagen con ClamAV. | Nada. Se reintenta. |
| `Eliminado … al terminar la cuarentena por malware: …` | ClamAV detectó malware al cumplir el plazo. Se ha borrado de la cuarentena. | Revisar las rutas afectadas. |
| `Promovido … a verified` | El digest se ha promovido y el tag se ha re-apuntado en `verified/` (camino largo). | Nada. |
| `Promovido … a verified (vía rápida)` | El proxy escaneó una imagen nueva con ClamAV y la promovió al momento. | Nada. |
| `Eliminado … por malware (vía rápida)` | El proxy detectó malware en una imagen nueva y la borró de cuarentena. | Revisar las rutas. |
| `Purgado … de cuarentena: ya no lo publica el upstream` | El upstream ya no publica ese digest bajo ninguna ref pedida. | Nada. |
| `Retirada versión antigua …` | Se ha podado un tag de `verified/` que nadie pide. | Nada. |
| `Paquete … eliminado de la base de datos` | El paquete ya no tiene imágenes. | Nada. |
| `El paso '…' falló` | Un paso del ciclo ha dado error. El resto sigue. | Ver el traceback en el log y buscar la causa. |

Un error repetido en `sincronizar upstream` suele indicar que el registro
upstream no responde, o que las credenciales o la red han cambiado.

## Datos y dónde están

| Qué | Dónde | Notas |
|---|---|---|
| Imágenes (verificadas y cuarentena) | volumen `zot-data` | Puede ocupar varios GB. La base de datos de Trivy también está aquí. |
| Base de datos de estado | volumen `proxy-data` | Refs pedidas y cuarentena por digest (plazos). |

Los volúmenes se guardan en `~/.local/share/containers/storage/volumes/<nombre>/_data/`.

### Copia de seguridad

La base de datos de estado es pequeña. Para copiarla sin riesgo, para primero
los servicios que la escriben:

```bash
systemctl --user stop proxy.service updater.service
cp ~/.local/share/containers/storage/volumes/proxy-data/_data/state.sqlite3 ~/backup/state-$(date +%F).sqlite3
systemctl --user start proxy.service updater.service
```

Las imágenes en el volumen `zot-data` se pueden reconstruir: si se pierden, el sistema
vuelve a descargarlas cuando se pidan. Sin embargo, se pierden los tags
verificados y el plazo de cuarentena vuelve a contar desde cero.

### Vaciar la caché

Para empezar de cero, para los servicios y borra los datos. Esto **borra todas
las imágenes** y el estado:

```bash
systemctl --user stop proxy.service updater.service zot.service
podman volume rm zot-data proxy-data
systemctl --user start zot.service proxy.service updater.service
```

## Seguridad de acceso

Ni el proxy ni Zot piden usuario ni contraseña. Cualquiera que llegue a los
puertos puede descargar imágenes.

| Puerto | Qué expone | Riesgo si está abierto a internet |
|---|---|---|
| `5001` (proxy) | Solo las imágenes que pasan por el proxy (tags literales). | Medio: cualquiera puede consumir la caché. |
| `5000` (Zot) | El almacén completo, incluidas las imágenes **en cuarentena**, sin reglas. | Alto: se pueden descargar imágenes sin el filtro del proxy. |

Recomendaciones:

- No abras `5000` a internet. Si no lo necesitas desde fuera, ciérralo en el
  firewall.
- Si expones el proxy fuera de la red de casa, ponlo detrás de un reverse proxy
  con HTTPS y restringe el acceso.

## Tareas periódicas

| Tarea | Frecuencia | Cómo |
|---|---|---|
| Revisar imágenes eliminadas por CVEs | Semanal | `journalctl --user -u updater.service --since "7 days ago" \| grep -i "eliminado"` |
| Comprobar espacio en disco | Mensual | `du -sh ~/.local/share/containers/storage/volumes/{zot-data,proxy-data}` |
| Copia de seguridad del estado | Mensual | Ver [copia de seguridad](#copia-de-seguridad). |
| Actualizar el sistema | Cuando haya versión nueva | Ver [INSTALL.md, sección Actualizar](../../INSTALL.md#actualizar). |

## Parar y arrancar

```bash
# Parar todo
systemctl --user stop proxy.service updater.service zot.service

# Arrancar todo
systemctl --user start zot.service proxy.service updater.service

# Estado
systemctl --user status proxy.service updater.service zot.service --no-pager
```
