# 06 · Errores

Cuando algo falla, el proxy responde con un código de error de la API OCI. Podman
y Docker muestran el mensaje tal cual. Esta tabla explica cada código y qué hacer.

## Códigos

| Código HTTP | Código OCI | Cuándo ocurre | Qué hacer |
|---|---|---|---|
| 400 | `NAME_INVALID` | El nombre tiene formato incorrecto: barras dobles, barra final o vacío. | Revisar la ruta de la imagen. |
| 403 | `DENIED` | El registro de origen no está en `UPSTREAM_REGISTRIES`. | Añadir el registro a la lista, o usar otro. |
| 404 | `MANIFEST_UNKNOWN` | El tag pedido no existe en el registro original (el upstream responde 404 y se reenvía tal cual). | Comprobar que el tag existe en el registro original. |
| 502 | `UNKNOWN` | Falló el reenvío al registro público, o el token anónimo del upstream. | Ver el mensaje en el log del proxy. |
| 503 | `UNAVAILABLE` | El proxy no puede hablar con Zot. | Comprobar que Zot está en marcha. |
| 500 | (sin código OCI) | Error interno no contemplado. | Ver el traceback en el log del proxy. |

## Casos frecuentes

### "manifest unknown" con un tag que no existe

Con tags literales no hay resolución previa: el proxy reenvía el tag tal cual al
registro público. Si ese tag no existe, el upstream responde `404 MANIFEST_UNKNOWN`
(o su equivalente) y el proxy lo reenvía tal cual al cliente. Asegúrate de que el
tag existe exactamente (los tags distinguen mayúsculas y minúsculas). Ver
[03 · Tags y versiones](03-tags-y-versiones.md).

### "denied" al pedir una imagen

El registro no está permitido. Mira `UPSTREAM_REGISTRIES` en
[05 · Operación](05-operacion.md#variables-de-configuración).

### La primera descarga tarda mucho

Es normal. Cada imagen que no está en `verified` se sirve desde el upstream. Una
vez promovida a `verified`, las siguientes salen de local.

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
| `no space left on device` | Disco lleno. Afecta a Zot o al proxy. | Liberar espacio en el volumen `zot-data` o ampliar el disco. |
| Errores de la base de datos SQLite | Fichero bloqueado o corrupto. | Parar los servicios y revisar el volumen `proxy-data`. |
| Errores de zli | Zot no ha terminado de escanear, o el binario no está. | Esperar al siguiente ciclo. Si persiste, revisar la imagen del updater. |
