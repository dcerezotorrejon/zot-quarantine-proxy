#!/bin/sh
# Arranca clamd (daemon de ClamAV) en segundo plano y, en cuanto su socket está
# listo, ejecuta el comando del contenedor (proxy o updater).
#
# Si clamd no arranca, se avisa y se sigue: los escaneos de malware quedarán
# PENDING y se reintentarán, sin tumbar el contenedor.
set -eu

mkdir -p /run/clamav

clamd -c /etc/clamd.d/scan.conf &

i=0
while [ ! -S /run/clamav/clamd.sock ] && [ "$i" -lt 30 ]; do
    sleep 1
    i=$((i + 1))
done

if [ ! -S /run/clamav/clamd.sock ]; then
    echo "AVISO: clamd no arrancó (sin socket en /run/clamav/clamd.sock); el malware quedará PENDING" >&2
fi

exec "$@"
