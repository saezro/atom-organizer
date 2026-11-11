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
#   scripts/desplegar_job.sh --perfiles [--imagen=IMAGEN] [--aplicar]
#
# --perfiles: en vez de tocar atom-organizer-pipeline (NO se modifica), crea o
# actualiza con la MISMA imagen y la MISMA SA/env (clonados con
# `gcloud run jobs describe --format json` del job pipeline) tres jobs:
#   atom-organizer-ligero  2 vCPU / 2Gi  (fases indice y cierre) retries 0
#   atom-organizer-apply   4 vCPU / 2Gi  (fases rgb y termica)   retries 1
#   atom-organizer-indice-map 1 vCPU / 2Gi (fase indice map/reduce, N tasks) retries 1
# (Cloud Run no deja cambiar cpu/memoria por ejecucion: un job por perfil.)
# Sin --imagen usa la imagen que tenga hoy el job pipeline. Memoria del ligero
# sobreescribible con LIGERO_MEM (ej. 4Gi si el cierre no cabe en 2Gi).
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
perfiles="false"
imagen_explicita="false"
JOB_LIGERO="atom-organizer-ligero"
JOB_APPLY="atom-organizer-apply"
LIGERO_CPU="${LIGERO_CPU:-2}"
LIGERO_MEM="${LIGERO_MEM:-2Gi}"
APPLY_CPU="${APPLY_CPU:-4}"
APPLY_MEM="${APPLY_MEM:-2Gi}"
# Fase indice map/reduce (v3.4.160): N tasks leen cabeceras EXIF de su tramo (I/O de red,
# 32 hilos, casi sin RAM) y la ULTIMA en terminar hace ademas el reduce. El reduce carga
# en RAM los N parciales (~60k registros JSON en la planta completa -> lista de dicts +
# _MetadatosImagen: orden de 0,2-0,5 GiB) y el manifiesto SQLite va a disco local, no a
# RAM. 2Gi cabe con holgura >4x; 1 vCPU basta (el reduce es secuencial y el cuello era
# la lectura de cabeceras, ya repartida). 1 vCPU admite hasta 4Gi (>4Gi exigiria >=2 vCPU).
JOB_INDICE_MAP="atom-organizer-indice-map"
INDICE_MAP_CPU="${INDICE_MAP_CPU:-1}"
INDICE_MAP_MEM="${INDICE_MAP_MEM:-2Gi}"

for arg in "$@"; do
    case "$arg" in
        --aplicar)
            aplicar="true"
            ;;
        --perfiles)
            perfiles="true"
            ;;
        --imagen=*)
            imagen="${arg#--imagen=}"
            imagen_explicita="true"
            ;;
        --*)
            echo "Flag desconocido: $arg" >&2
            exit 1
            ;;
        *)
            imagen="$arg"
            imagen_explicita="true"
            ;;
    esac
done

print_cmd() {
    local -n arr="$1"
    local out="${arr[0]}"
    for ((i = 1; i < ${#arr[@]}; i++)); do
        out+=" ${arr[$i]}"
    done
    echo "$out"
}

# ---------------------------------------------------------------------------
# Modo --perfiles: clona el job pipeline en dos jobs con otro cpu/mem/retries.
# ---------------------------------------------------------------------------
# Transforma el JSON del job pipeline (stdin) en el de un job nuevo.
# Uso: transformar_job NOMBRE CPU MEM RETRIES [IMAGEN]
transformar_job() {
    python3 -I -c '
import json, sys
nombre, cpu, mem, retries, imagen = sys.argv[1:6]
d = json.load(sys.stdin)
md = d["metadata"]
d["metadata"] = {"name": nombre, "namespace": md.get("namespace"),
                 "labels": md.get("labels", {})}
d["metadata"] = {k: v for k, v in d["metadata"].items() if v}
d.pop("status", None)
tpl = d["spec"]["template"]
tpl.get("metadata", {}).pop("name", None)
ts = tpl["spec"]["template"]["spec"]
ts["maxRetries"] = int(retries)
ts["timeoutSeconds"] = 21600
c = ts["containers"][0]
c.setdefault("resources", {}).setdefault("limits", {}).update(cpu=cpu, memory=mem)
if imagen:
    c["image"] = imagen
# Valvula persistente: sin ella el Job aborta en Cloud Run si falta telemetria.
_pisa = ("ORGANIZER_TELEMETRIA_OPCIONAL", "ATOM_IO_WORKERS", "PYTHONUNBUFFERED")
env = [e for e in c.get("env", []) if e.get("name") not in _pisa]
env.append({"name": "ORGANIZER_TELEMETRIA_OPCIONAL", "value": "1"})
env.append({"name": "ATOM_IO_WORKERS", "value": "32"})
env.append({"name": "PYTHONUNBUFFERED", "value": "1"})
c["env"] = env
d["spec"]["template"]["spec"]["taskCount"] = 1
json.dump(d, sys.stdout, indent=1)
' "$@"
}

if [ "$perfiles" = "true" ]; then
    img_arg=""
    [ "$imagen_explicita" = "true" ] && img_arg="$imagen"
    tmpdir="$(mktemp -d)"
    trap 'rm -rf "$tmpdir"' EXIT
    export_cmd=(gcloud run jobs describe "$JOB" --project "$PROJECT" --region "$REGION"
                --account "$ACCOUNT" --format json)
    perfil_defs=("$JOB_LIGERO|$LIGERO_CPU|$LIGERO_MEM|0" "$JOB_APPLY|$APPLY_CPU|$APPLY_MEM|1"
                 "$JOB_INDICE_MAP|$INDICE_MAP_CPU|$INDICE_MAP_MEM|1")
    if [ "$aplicar" != "true" ]; then
        echo "== DRY-RUN perfiles (usa --aplicar para aplicarlo; $JOB NO se modifica) =="
        echo "Lectura de la config base:"; print_cmd export_cmd
        for def in "${perfil_defs[@]}"; do
            IFS='|' read -r n c m r <<<"$def"
            echo
            echo "Job $n (cpu=$c mem=$m maxRetries=$r timeout=21600s imagen=${img_arg:-<la de $JOB>}):"
            echo "  <JSON de $JOB transformado> | gcloud run jobs replace <fichero.json> --project $PROJECT --region $REGION --account $ACCOUNT"
            echo "  (si no existe, replace lo crea; mismos env y SA que $JOB, mas ORGANIZER_TELEMETRIA_OPCIONAL=1)"
        done
        exit 0
    fi
    "${export_cmd[@]}" > "$tmpdir/base.json"
    for def in "${perfil_defs[@]}"; do
        IFS='|' read -r n c m r <<<"$def"
        echo "Aplicando $n (cpu=$c mem=$m maxRetries=$r)..."
        transformar_job "$n" "$c" "$m" "$r" "$img_arg" < "$tmpdir/base.json" > "$tmpdir/$n.json"
        gcloud run jobs replace "$tmpdir/$n.json" --project "$PROJECT" --region "$REGION" --account "$ACCOUNT"
    done
    exit 0
fi

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
    # command/args VACIOS a proposito: el ENTRYPOINT del Dockerfile ya es
    # ["python","src/organize_cli.py"], y la Suite lanza el job con
    # containerOverrides.args (organizerJob.js), que REEMPLAZA los args del
    # job. Si aqui se fijara --command python --args src/organize_cli.py, ese
    # override borraria "src/organize_cli.py" y el contenedor arrancaria como
    # `python --origen ...` -> "unknown option --origen", exit(2).
    --command ""
    --args ""
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
