#!/usr/bin/env bash
# Rotacion por tamano del log del kiosco de la Raspberry Pi.
#
# La Pi escribe el log via `StandardOutput=append:` de systemd, asi que el
# servicio mantiene el fd abierto: renombrar el fichero dejaria al servicio
# escribiendo en el inode viejo. Por eso se copia y se TRUNCA in-place, que
# es lo que hace `logrotate` con `copytruncate` -- y aqui hace falta a mano
# porque la Pi no tiene logrotate instalado (instalarlo pide sudo).
#
# El fd esta en O_APPEND: tras truncar, la siguiente escritura vuelve al
# offset 0 sola, sin dejar el hueco de ceros del truncado normal.
set -euo pipefail

LOG="${1:-/home/pi/organizer-logs/server.log}"
MAX_BYTES="${MAX_BYTES:-5242880}"   # 5 MiB
COPIAS="${COPIAS:-3}"               # server.log.1 .. .3

[ -f "$LOG" ] || exit 0
[ "$(stat -c%s "$LOG")" -ge "$MAX_BYTES" ] || exit 0

rm -f "$LOG.$COPIAS"
i="$COPIAS"
while [ "$i" -gt 1 ]; do
    prev=$((i - 1))
    if [ -f "$LOG.$prev" ]; then
        mv "$LOG.$prev" "$LOG.$i"
    fi
    i="$prev"
done

cp "$LOG" "$LOG.1"
: > "$LOG"
echo "rotado: $LOG ($(stat -c%s "$LOG.1") bytes -> $LOG.1)"
