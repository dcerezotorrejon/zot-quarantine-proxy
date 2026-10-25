# 06 · Errores

Cuando algo falla, el proxy responde con un código de error de la API OCI. Podman
y Docker muestran el mensaje tal cual. Esta tabla explica cada código y qué hacer.

## Códigos

| Código HTTP | Código OCI | Cuándo ocurre | Qué hacer |
|---|---|---|---|
| 400 | `NAME_INVALID` | El nombre tiene formato incorrecto: barras dobles, barra final o vacío. | Revisar la ruta de la imagen. |
| 403 | `DENIED` | El registro de origen no está en `UPSTREAM_REGISTRIES`. | Añadir el registro a la lista, o usar otro. |
| 404 | `MANIFEST_UNKNOWN` | No existe ningún tag que cumpla la petición (parcial o flotante), o el digest no está en el almacén. | Comprobar que el tag existe en el registro original. |
| 404 | `BLOB_UNKNOWN` | Un blob pedido no existe en ninguno de los dos namespaces. | Suele ser una descarga cortada. Repetir el `pull`. |
| 502 | `UNKNOWN` | Falló la consulta de tags del upstream (`skopeo list-tags`) al resolver un tag flotante o parcial. | Ver el mensaje de skopeo en el log del proxy. |
| 503 | `UNAVAILABLE` | El proxy no puede hablar con Zot. | Comprobar que Zot está en marcha. |
| 500 | (sin código OCI) | Error interno no contemplado. | Ver el traceback en el log del proxy. |

## Casos frecuentes

### "manifest unknown" con un tag que sí existe en Docker Hub

Si pides un tag **parcial o flotante** y el upstream no tiene ninguno que cumpla,
el error es `MANIFEST_UNKNOWN`. Revisa el tipo de tag en
[03 · Tags y versiones](03-tags-y-versiones.md).

### "unknown" (502) al pedir una versión exacta

Si pides un tag **exacto** que no existe en el upstream, el proxy no lo comprueba
antes: se lo pide a Zot, que intenta la descarga bajo demanda y falla. El error que
recibe el cliente lo devuelve Zot, y puede no coincidir con `MANIFEST_UNKNOWN`.
Este comportamiento está pendiente de comprobar con un Zot real.

Para revisar el motivo, mira el log de Zot:

```bash
journalctl --user -u zot.service --since "10 min ago"
```

### "denied" al pedir una imagen

El registro no está permitido. Mira `UPSTREAM_REGISTRIES` en
[05 · Operación](05-operacion.md#variables-de-configuración).

### La primera descarga tarda mucho

Es normal. La primera descarga de cada imagen baja todas las capas desde el
upstream. Las siguientes salen de la caché local.

### El cliente no conecta

1. Comprobar que el servicio está en marcha: `systemctl --user status proxy.service`.
2. Comprobar que responde: `curl -s http://<servidor>:5001/v2/` debe devolver `{}`.
3. Si responde en el servidor pero no en el cliente, revisar el firewall.
4. Si el cliente da error de certificado, revisar la configuración de registro
   inseguro en [INSTALL.md](../../INSTALL.md#10-configurar-los-clientes).

### "Zot no responde" en los logs del updater

El updater espera hasta 5 minutos a que Zot esté disponible al arrancar. Si no
responde, termina con error. Revisar `journalctl --user -u zot.service`.

## Errores que no son del proxy

| Mensaje | Origen | Qué hacer |
|---|---|---|
| `no space left on device` | Disco lleno. Afecta a Zot o al proxy. | Liberar espacio en `~/zot-data` o ampliar el disco. |
| Errores de la base de datos SQLite | Fichero bloqueado o corrupto. | Parar los servicios y revisar `~/proxy-data/state.sqlite3`. |
| Errores de zli | Zot no ha terminado de escanear, o el binario no está. | Esperar al siguiente ciclo. Si persiste, revisar la imagen del updater. |
