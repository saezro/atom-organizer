#!/usr/bin/env bash
# Smoke del producto Linux REAL: arranca el AppImage, dispara una corrida vacía
# por la API local y exige que el cierre genere un Excel válido.
set -euo pipefail

APP="${1:?uso: smoke_appimage_runtime.sh <AppImage>}"
APP="$(cd "$(dirname "$APP")" && pwd)/$(basename "$APP")"
TMP="$(mktemp -d)"
PORT=18765
LOG="$TMP/appimage.log"
PID=""

cleanup() {
  if [[ -n "$PID" ]]; then
    kill -TERM -- "-$PID" 2>/dev/null || true
    sleep 1
    kill -KILL -- "-$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
  fi
  rm -rf -- "$TMP"
}
trap cleanup EXIT INT TERM

mkdir -p "$TMP/origen" "$TMP/PLANTA_SMOKE"
chmod +x "$APP"
setsid env APPIMAGE_EXTRACT_AND_RUN=1 ATOM_LOG_LEVEL=INFO \
  "$APP" --server --host 127.0.0.1 --port "$PORT" >"$LOG" 2>&1 &
PID=$!

ready=false
for _ in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
    ready=true
    break
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "ERROR: el AppImage murió durante el arranque" >&2
    tail -80 "$LOG" >&2
    exit 1
  fi
  sleep 1
done
if [[ "$ready" != true ]]; then
  echo "ERROR: el servidor del AppImage no quedó listo" >&2
  tail -80 "$LOG" >&2
  exit 1
fi

response="$(curl -fsS -H 'Content-Type: application/json' \
  -d "{\"args\":[{\"origen\":\"$TMP/origen\",\"destino\":\"$TMP/PLANTA_SMOKE\",\"rename\":false}]}" \
  "http://127.0.0.1:$PORT/api/run_organize")"
if [[ "$response" != *'"started": true'* ]]; then
  echo "ERROR: run_organize no arrancó: $response" >&2
  exit 1
fi

INDEX="$TMP/PLANTA_SMOKE/INDICE_PLANTA_SMOKE.xlsx"
for _ in $(seq 1 120); do
  if [[ -s "$INDEX" ]]; then
    python - "$INDEX" <<'PY'
import sys
from openpyxl import load_workbook

ws = load_workbook(sys.argv[1], read_only=True)["Imagenes"]
cabecera = next(ws.iter_rows(values_only=True))
if not cabecera or cabecera[0] != "PB":
    raise SystemExit("cabecera inesperada en el índice")
PY
    echo "OK: AppImage ejecutó el pipeline y generó un índice Excel válido"
    exit 0
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    break
  fi
  sleep 1
done

echo "ERROR: el pipeline no generó $INDEX" >&2
tail -100 "$LOG" >&2
exit 1
