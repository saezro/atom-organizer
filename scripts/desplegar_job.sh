#!/usr/bin/env bash
# Versiona en el repo la configuracion completa del Cloud Run Job
# "atom-organizer-pipeline" (europe-west4, proyecto aerotools-484814).
#
# Que es: el job que corre el pipeline batch de organizacion de datos
# (organize_cli.py) contra el bucket datos_para_organizar. Hasta ahora
# su configuracion solo vivia aplicada en GCP, sin ningun fichero en el
# repo que la reflejase.
#
# Por que se quita el volumen gcsfuse: el codigo Python ya habla `gs://`
# nativamente (URIs directas al bucket), asi que el montaje FUSE en /gcs
# deja de hacer falta. Este script aplica --clear-volumes/--clear-volume-mounts
# para eliminarlo.
#
# Rollback (volver a montar el volumen gcsfuse tal cual estaba):
#
#   gcloud run jobs update atom-organizer-pipeline \
#     --project aerotools-484814 --region europe-west4 \
#     --account atom-compute-ops@aerotools-484814.iam.gserviceaccount.com \
#     --add-volume=name=gcs,type=cloud-storage,bucket=datos_para_organizar,readonly=false,mount-options="implicit-dirs,metadata-cache-ttl-secs=600,stat-cache-max-size-mb=128,type-cache-max-size-mb=64" \
#     --add-volume-mount=volume=gcs,mount-path=/gcs
#
# execution-environment (gen2): NO existe flag --execution-environment en
# `gcloud run jobs update` (verificado contra --help en gcloud/gcloud beta,
# SDK 564.0.0; tampoco existe en `gcloud run jobs deploy`). Como `update` es
# incremental y no toca campos que no se le pasan, el job se queda en gen2
# sin necesidad de fijarlo aqui. Si algun dia hiciera falta forzarlo, se
# hace via `gcloud run jobs replace` con YAML y la anotacion
# `run.googleapis.com/execution-environment: gen2`. gen2 era obligatorio
# precisamente por el volumen gcsfuse; al quitar el mount deja de ser un
# requisito estricto, pero se conserva a proposito en este cambio -- un
# cambio a la vez, no tocar el execution-environment en el mismo paso que
# se quita el mount.
#
# Coherencia con CI: .github/workflows/release.yml ya hace su propio
# `gcloud run jobs update --image=...` en cada release estable (solo toca
# la imagen). Este script y ese workflow deben mantenerse coherentes: si
# cambia aqui algo mas alla de la imagen (cpu, memory, volumenes...),
# revisar tambien el workflow.
#
# Uso:
#   scripts/desplegar_job.sh [imagen] [--aplicar]
#   scripts/desplegar_job.sh --imagen=IMAGEN [--aplicar]
#
# Por defecto (sin --aplicar) es DRY-RUN: solo imprime el comando gcloud
# completo que se ejecutaria (y el de verificacion posterior), sin tocar
# nada. Con --aplicar, lo ejecuta de verdad.
set -euo pipefail

PROJECT="aerotools-484814"
REGION="europe-west4"
ACCOUNT="atom-compute-ops@aerotools-484814.iam.gserviceaccount.com"
JOB="atom-organizer-pipeline"

IMAGEN_DEFAULT="europe-west4-docker.pkg.dev/${PROJECT}/atom-organizer/pipeline:v3.4.62"
BUCKET="datos_para_organizar"
MOUNT_OPTIONS="implicit-dirs,metadata-cache-ttl-secs=600,stat-cache-max-size-mb=128,type-cache-max-size-mb=64"

imagen="$IMAGEN_DEFAULT"
aplicar="false"

for arg in "$@"; do
    case "$arg" in
        --aplicar)
            aplicar="true"
            ;;
        --imagen=*)
            imagen="${arg#--imagen=}"
            ;;
        --*)
            echo "Flag desconocido: $arg" >&2
            exit 1
            ;;
        *)
            imagen="$arg"
            ;;
    esac
done

# OJO: NO se pasa --execution-environment: ese flag no existe en
# `gcloud run jobs update` (ver cabecera). Como `update` es incremental y
# no toca campos que no se le pasan, el job se queda en gen2 sin fijarlo
# aqui explicitamente.
update_cmd=(
    gcloud run jobs update "$JOB"
    --project "$PROJECT"
    --region "$REGION"
    --account "$ACCOUNT"
    --cpu 8
    --memory 16Gi
    --tasks 1
    --max-retries 0
    --task-timeout 21600s
    --service-account 217557350193-compute@developer.gserviceaccount.com
    --command python
    --args organize_cli.py
    --image "$imagen"
    --clear-volumes
    --clear-volume-mounts
)

describe_cmd=(
    gcloud run jobs describe "$JOB"
    --project "$PROJECT"
    --region "$REGION"
    --account "$ACCOUNT"
)

print_cmd() {
    local -n arr="$1"
    local out="${arr[0]}"
    for ((i = 1; i < ${#arr[@]}; i++)); do
        out+=" ${arr[$i]}"
    done
    echo "$out"
}

if [ "$aplicar" != "true" ]; then
    echo "== DRY-RUN (no se ejecuta nada; usa --aplicar para aplicarlo de verdad) =="
    echo
    echo "Comando de actualizacion:"
    print_cmd update_cmd
    echo
    echo "Comando de verificacion posterior:"
    print_cmd describe_cmd
    exit 0
fi

echo "Aplicando configuracion a $JOB..."
"${update_cmd[@]}"

echo
echo "Verificando configuracion resultante..."
"${describe_cmd[@]}"
