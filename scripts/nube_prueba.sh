#!/usr/bin/env bash
# Prueba end-to-end del organizador en Cloud Run con jobs por perfil.
#
# Uso:
#   scripts/nube_prueba.sh <origen_gs> <destino_gs> <estadillo_gs> [N_rgb] [N_termica] [opciones organizer...] [--dry-run]
#   (indice: --n-indice=N, defecto auto = ceil(imagenes/9000) tope 32)
#
#   N_rgb / N_termica: numero de tasks; omitidos o "auto" = ceil(imagenes/300),
#   tope 32 (imagenes = listado `gcloud storage ls` del origen, solo lectura).
#   Opciones propias (en cualquier posicion): --dry-run, --con-rotacion
#   (no pasa --sin-miniaturas-termica --sin-rotacion), --job-ligero=X, --job-apply=X,
#   --job-indice=X, --n-indice=N|auto.
#   Todo lo demas se pasa tal cual al organizer en TODAS las fases, p.ej.
#   `--giro-tiff -90` (con --sin-rotacion, si no hay criterio previo `auto` da
#   error: pasalo explicitamente).
#
# Flujo: indice map/reduce (atom-organizer-indice-map, N_IDX tasks; la ultima en
#        terminar hace el reduce y sube el manifiesto; si no hay telemetria
#        reduce=true se lanza el respaldo `--fase indice-reduce` en ligero, 1 task)
#        -> rgb || termica (apply, N tasks, a la vez) -> cierre (ligero, 1 task).
#        Jobs creados con `desplegar_job.sh --perfiles`.
# Salida: tabla resumen + /tmp/nube_prueba_<ts>.json.
# Coste = sum(tasks x duracion_task x (vCPU*0.000024 + GiB*0.0000025)) USD;
# duracion_task = pared_s de la telemetria; sin telemetria, duracion de la ejecucion.
# Tambien en EUR: EUR_POR_USD es un tipo APROXIMADO fijo (no consulta cambio real).
set -euo pipefail

PROJECT="aerotools-484814"
REGION="europe-west4"
ACCOUNT="atom-compute-ops@aerotools-484814.iam.gserviceaccount.com"
JOB_LIGERO="atom-organizer-ligero"; LIGERO_CPU="${LIGERO_CPU:-2}"; LIGERO_MEM_GIB="${LIGERO_MEM_GIB:-8}"
JOB_APPLY="atom-organizer-apply";   APPLY_CPU="${APPLY_CPU:-4}";   APPLY_MEM_GIB="${APPLY_MEM_GIB:-8}"
JOB_INDICE="atom-organizer-indice-map"; IDX_CPU="${IDX_CPU:-1}"; IDX_MEM_GIB="${IDX_MEM_GIB:-2}"
IMG_POR_TASK=300; IMG_POR_TASK_IDX=9000; TOPE_TASKS=32; POLL_S="${POLL_S:-5}"
EUR_POR_USD="${EUR_POR_USD:-0.92}"  # tipo de cambio APROXIMADO, solo para orientar el coste en EUR

dry="false"; rjpeg="true"; pos=(); extra=(); N_IDX="auto"
for a in "$@"; do
    case "$a" in
        --dry-run) dry="true" ;;
        --con-rotacion) rjpeg="false" ;;
        --job-ligero=*) JOB_LIGERO="${a#*=}" ;;
        --job-apply=*) JOB_APPLY="${a#*=}" ;;
        --job-indice=*) JOB_INDICE="${a#*=}" ;;
        --n-indice=*) N_IDX="${a#*=}" ;;
        *) if [ "${#pos[@]}" -lt 3 ]; then pos+=("$a"); else extra+=("$a"); fi ;;
    esac
done
[ "${#pos[@]}" -eq 3 ] || { sed -n 2,20p "$0" >&2; exit 1; }
ORIGEN="${pos[0]}"; DESTINO="${pos[1]}"; ESTADILLO="${pos[2]}"
N_RGB="auto"; N_TER="auto"
if [ "${#extra[@]}" -gt 0 ] && [[ "${extra[0]}" =~ ^([0-9]+|auto)$ ]]; then
    N_RGB="${extra[0]}"; extra=("${extra[@]:1}")
    if [ "${#extra[@]}" -gt 0 ] && [[ "${extra[0]}" =~ ^([0-9]+|auto)$ ]]; then
        N_TER="${extra[0]}"; extra=("${extra[@]:1}")
    else N_TER="$N_RGB"; fi
fi
[[ "$N_IDX" =~ ^([0-9]+|auto)$ ]] && { [ "$N_IDX" = "auto" ] || [ "$N_IDX" -ge 1 ]; } || { echo "--n-indice invalido: $N_IDX" >&2; exit 1; }
OPTS=("${extra[@]}")
if [ "$rjpeg" = "true" ]; then OPTS+=(--sin-miniaturas-termica --sin-rotacion); fi

TS="$(date +%Y%m%d_%H%M%S)"
OUT_JSON="/tmp/nube_prueba_${TS}.json"
WORK="$(mktemp -d /tmp/nube_prueba_work.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
madrid() { TZ=Europe/Madrid date -d "$1" '+%Y-%m-%d %H:%M:%S'; }

GC=(gcloud --account "$ACCOUNT")

run() {  # imprime y (si no dry-run) ejecuta
    echo "+ $*" >&2
    [ "$dry" = "true" ] || "$@"
}

# --- numero de tasks --------------------------------------------------------
if [ "$N_RGB" = "auto" ] || [ "$N_TER" = "auto" ] || [ "$N_IDX" = "auto" ]; then
    if [ "$dry" = "true" ]; then
        echo "+ ${GC[*]} storage ls --recursive \"$ORIGEN/**\" | grep -ciE '\\.(jpe?g|tiff?|png)\$'   # N=ceil(n/$IMG_POR_TASK) tope $TOPE_TASKS" >&2
        n_img="?"; auto_n="<auto>"; auto_idx="<auto>"
    else
        n_img="$("${GC[@]}" storage ls "$ORIGEN/**" 2>/dev/null | grep -ciE '\.(jpe?g|tiff?|png)$' || true)"
        [ "$n_img" -gt 0 ] || { echo "No se encontraron imagenes en $ORIGEN" >&2; exit 1; }
        auto_n=$(( (n_img + IMG_POR_TASK - 1) / IMG_POR_TASK ))
        [ "$auto_n" -le "$TOPE_TASKS" ] || auto_n="$TOPE_TASKS"
        auto_idx=$(( (n_img + IMG_POR_TASK_IDX - 1) / IMG_POR_TASK_IDX ))
        [ "$auto_idx" -le "$TOPE_TASKS" ] || auto_idx="$TOPE_TASKS"
    fi
    [ "$N_RGB" != "auto" ] || N_RGB="$auto_n"
    [ "$N_TER" != "auto" ] || N_TER="$auto_n"
    [ "$N_IDX" != "auto" ] || N_IDX="$auto_idx"
else n_img="n/d"; fi
echo "Imagenes en origen: $n_img | tasks indice=$N_IDX rgb=$N_RGB termica=$N_TER" >&2

# --- lanzar / esperar --------------------------------------------------------
# lanzar <job> <fase> <tasks>  -> imprime el nombre de la ejecucion
lanzar() {
    local job="$1" fase="$2" tasks="$3" adicional="${4:-}"
    local args="^|^--origen|$ORIGEN|--destino|$DESTINO|--estadillo|$ESTADILLO|--fase|$fase"
    [ -z "$adicional" ] || args+="|$adicional"
    local o; for o in "${OPTS[@]}"; do args+="|$o"; done
    local cmd=("${GC[@]}" run jobs execute "$job" --project "$PROJECT" --region "$REGION"
               --async --tasks "$tasks" "--args=$args" --format "value(metadata.name)")
    echo "+ ${cmd[*]}" >&2
    if [ "$dry" = "true" ]; then echo "EJEC_${fase}"; return; fi
    "${cmd[@]}"
}

estado_json() {  # estado_json <ejecucion>
    "${GC[@]}" run jobs executions describe "$1" --project "$PROJECT" --region "$REGION" --format json
}

# esperar <ejecucion>...  (hasta que todas tengan completionTime)
esperar() {
    [ "$dry" = "true" ] && { echo "+ (poll cada ${POLL_S}s: gcloud run jobs executions describe $* hasta completionTime)" >&2; return 0; }
    local pend=("$@") e nuevo
    while [ "${#pend[@]}" -gt 0 ]; do
        nuevo=()
        for e in "${pend[@]}"; do
            if estado_json "$e" > "$WORK/$e.json" 2>/dev/null &&
               python3 -I -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("status",{}).get("completionTime") else 1)' "$WORK/$e.json"; then
                echo "  fin: $e" >&2
            else nuevo+=("$e"); fi
        done
        pend=("${nuevo[@]}")
        [ "${#pend[@]}" -eq 0 ] || sleep "$POLL_S"
    done
}

fallos=0
EJECS=()  # "ejecucion|job|fase|cpu|memgib"
reg() { EJECS+=("$1|$2|$3|$4|$5"); }

ABORTADA_EN=""
# fallo_en <ejecucion>...: 0 si alguna tiene tasks fallidas/canceladas
fallo_en() {
    [ "$dry" = "true" ] && return 1
    local e
    for e in "$@"; do
        python3 -I -c 'import json,sys; s=json.load(open(sys.argv[1])).get("status",{}); sys.exit(0 if int(s.get("failedCount",0) or 0)>0 or int(s.get("cancelledCount",0) or 0)>0 else 1)' "$WORK/$e.json" && return 0
    done
    return 1
}

echo "== 1/3 indice map/reduce ($JOB_INDICE, $N_IDX tasks) ==" >&2
E_IDX="$(lanzar "$JOB_INDICE" indice "$N_IDX")"; reg "$E_IDX" "$JOB_INDICE" indice "$IDX_CPU" "$IDX_MEM_GIB"
esperar "$E_IDX"
if fallo_en "$E_IDX"; then ABORTADA_EN="indice"; echo "!! fase indice FALLO: se aborta (no se lanzan rgb/termica/cierre)" >&2; fi

# El reduce lo hace la ultima task del map (deja telemetria con "reduce": true) y sube el
# manifiesto. Si ninguna task lo registra (N=1 no hay reduce aparte: ya es el indice
# completo), respaldo: `--fase indice-reduce --ejecucion-indice <ejec>` en ligero, 1 task.
if [ -z "$ABORTADA_EN" ] && [ "$N_IDX" != "1" ]; then
    if [ "$dry" = "true" ]; then
        echo "+ ${GC[*]} storage cat \"$DESTINO/.organizado/telemetria/${E_IDX}-indice-*.json\" | grep -cE '\"reduce\": ?true'   # 0 => respaldo" >&2
    else
        n_reduce="$("${GC[@]}" storage cat "$DESTINO/.organizado/telemetria/${E_IDX}-indice-*.json" 2>/dev/null | grep -cE '"reduce": ?true' || true)"
        if [ "${n_reduce:-0}" -lt 1 ]; then
            echo "!! ninguna task del map registra reduce=true: respaldo indice-reduce ($JOB_LIGERO, 1 task)" >&2
            E_RED="$(lanzar "$JOB_LIGERO" indice-reduce 1 "--ejecucion-indice|$E_IDX")"; reg "$E_RED" "$JOB_LIGERO" indice-reduce "$LIGERO_CPU" "$LIGERO_MEM_GIB"
            esperar "$E_RED"
            if fallo_en "$E_RED"; then ABORTADA_EN="indice-reduce"; echo "!! respaldo indice-reduce FALLO: se aborta" >&2; fi
        fi
    fi
fi

if [ -z "$ABORTADA_EN" ]; then
echo "== 2/3 rgb ($N_RGB) || termica ($N_TER) ($JOB_APPLY) ==" >&2
E_RGB="$(lanzar "$JOB_APPLY" rgb "$N_RGB")";  reg "$E_RGB" "$JOB_APPLY" rgb "$APPLY_CPU" "$APPLY_MEM_GIB"
E_TER="$(lanzar "$JOB_APPLY" termica "$N_TER")"; reg "$E_TER" "$JOB_APPLY" termica "$APPLY_CPU" "$APPLY_MEM_GIB"
esperar "$E_RGB" "$E_TER"
if fallo_en "$E_RGB" "$E_TER"; then ABORTADA_EN="rgb/termica"; echo "!! fase rgb/termica FALLO: se aborta (no se lanza cierre)" >&2; fi
fi

if [ -z "$ABORTADA_EN" ]; then
echo "== 3/3 cierre ($JOB_LIGERO, 1 task) ==" >&2
E_CIE="$(lanzar "$JOB_LIGERO" cierre 1)"; reg "$E_CIE" "$JOB_LIGERO" cierre "$LIGERO_CPU" "$LIGERO_MEM_GIB"
esperar "$E_CIE"
fi

if [ "$dry" = "true" ]; then
    echo "+ telemetria por ejecucion: ${GC[*]} logging read 'resource.type=\"cloud_run_job\" AND labels.\"run.googleapis.com/execution_name\"=\"<EJEC>\" AND (textPayload:\"pared_s\" OR jsonPayload.pared_s:*)' --project $PROJECT --format json" >&2
    echo "+ fallback: ${GC[*]} storage cat \"$DESTINO/.organizado/telemetria/*.json\"" >&2
    echo "+ (si el map no deja reduce=true) respaldo: lanzar $JOB_LIGERO --fase indice-reduce --ejecucion-indice <EJEC_indice>, 1 task" >&2
    echo "+ resumen -> $OUT_JSON (coste en USD y EUR, EUR_POR_USD=$EUR_POR_USD aprox.)" >&2
    exit 0
fi

# --- telemetria + resumen ----------------------------------------------------
for def in "${EJECS[@]}"; do
    e="${def%%|*}"
    [ -s "$WORK/$e.json" ] || estado_json "$e" > "$WORK/$e.json"
    "${GC[@]}" logging read \
        "resource.type=\"cloud_run_job\" AND labels.\"run.googleapis.com/execution_name\"=\"$e\" AND (textPayload:\"pared_s\" OR jsonPayload.pared_s:*)" \
        --project "$PROJECT" --freshness=7d --limit 2000 --format json > "$WORK/$e.logs.json" 2>/dev/null || echo "[]" > "$WORK/$e.logs.json"
done
# fallback: telemetria que el CLI deje en el destino
"${GC[@]}" storage cat "$DESTINO/.organizado/telemetria/*.json" > "$WORK/destino_telemetria.txt" 2>/dev/null || : > "$WORK/destino_telemetria.txt"
printf '%s\n' "${EJECS[@]}" > "$WORK/ejecs.txt"

python3 -I - "$WORK" "$OUT_JSON" "$n_img" "$ABORTADA_EN" "$EUR_POR_USD" <<'PY'
import json, sys, os, re
from datetime import datetime
from zoneinfo import ZoneInfo
work, out, n_img, abortada_en, eur_usd = sys.argv[1:6]
eur_usd = float(eur_usd)
MAD = ZoneInfo("Europe/Madrid")
def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
def mad(d):
    return d.astimezone(MAD).strftime("%Y-%m-%d %H:%M:%S") if d else None
def objetos(texto):
    dec, i, res = json.JSONDecoder(), 0, []
    while True:
        i = texto.find("{", i)
        if i < 0: return res
        try:
            o, j = dec.raw_decode(texto, i); res.append(o); i = j
        except ValueError:
            i += 1
def telem_de(o):
    return isinstance(o, dict) and "pared_s" in o
ejecs, tot_cost, tot_fallidas = [], 0.0, 0
dest_tel = objetos(open(os.path.join(work, "destino_telemetria.txt")).read())
dest_tel = [o for o in dest_tel if telem_de(o)]
for linea in open(os.path.join(work, "ejecs.txt")).read().split("\n"):
    if not linea: continue
    e, job, fase, cpu, mem = linea.split("|"); cpu = float(cpu); mem = float(mem)
    st = json.load(open(os.path.join(work, e + ".json")))
    s = st.get("status", {})
    ini, fin = ts(s.get("startTime")), ts(s.get("completionTime"))
    dur = (fin - ini).total_seconds() if ini and fin else 0.0
    tasks = int(st.get("spec", {}).get("taskCount") or s.get("succeededCount", 0) + s.get("failedCount", 0) or 1)
    fall = int(s.get("failedCount", 0) or 0)
    tel = []
    try:
        for ent in json.load(open(os.path.join(work, e + ".logs.json"))):
            jp = ent.get("jsonPayload")
            if telem_de(jp): tel.append(jp); continue
            tel += [o for o in objetos(ent.get("textPayload", "") or "") if telem_de(o)]
    except Exception: pass
    if not tel:  # fallback destino, solo las de esta ejecucion si traen el campo
        tel = [o for o in dest_tel if o.get("ejecucion") in (e, None) and o.get("fase", fase) == fase]
    # dedup por (task) si viene
    vistos, uniq = set(), []
    for o in tel:
        k = json.dumps(o, sort_keys=True)
        if k not in vistos: vistos.add(k); uniq.append(o)
    tel = uniq
    if tel:
        suma_pared = sum(float(o["pared_s"]) for o in tel)
        origen_dur = "telemetria"
        # tasks sin telemetria (p.ej. fallidas) se estiman con la media
        faltan = max(tasks - len(tel), 0)
        suma_pared += faltan * (suma_pared / len(tel))
        utils = [float(o["utilizacion"]) for o in tel if o.get("utilizacion") is not None]
        imgs = sum(int(o.get("imagenes") or 0) for o in tel)
    else:
        suma_pared, origen_dur, utils, imgs = tasks * dur, "duracion_ejecucion", [], 0
    coste = suma_pared * (cpu * 0.000024 + mem * 0.0000025)
    tot_cost += coste; tot_fallidas += fall
    ejecs.append(dict(ejecucion=e, job=job, fase=fase, inicio=mad(ini), fin=mad(fin),
        duracion_s=round(dur, 1), tasks=tasks, tasks_fallidas=fall, vcpu=cpu, gib=mem,
        task_segundos=round(suma_pared, 1), fuente_duracion=origen_dur,
        telemetrias=len(tel), imagenes=imgs,
        utilizacion_media=round(sum(utils) / len(utils), 3) if utils else None,
        reduce_tasks=sum(1 for o in tel if o.get("reduce") is True),
        coste_usd=round(coste, 4), coste_eur=round(coste * eur_usd, 4), telemetria=tel))
todas = [u for x in ejecs for u in ([t["utilizacion"] for t in x["telemetria"] if t.get("utilizacion") is not None])]
resumen = dict(abortada_en=abortada_en or None, imagenes_origen=n_img, ejecuciones=ejecs, coste_total_usd=round(tot_cost, 4),
    coste_total_eur=round(tot_cost * eur_usd, 4), eur_por_usd_aprox=eur_usd,
    tasks_fallidas_total=tot_fallidas,
    utilizacion_media=round(sum(map(float, todas)) / len(todas), 3) if todas else None)
json.dump(resumen, open(out, "w"), indent=1, ensure_ascii=False)
print(f"{'fase':8} {'ejecucion':34} {'inicio':19} {'fin':19} {'dur_s':>7} {'tasks':>5} {'fall':>4} {'util':>5} {'red':>3} {'USD':>8} {'EUR':>8}")
for x in ejecs:
    u = "-" if x["utilizacion_media"] is None else f"{x['utilizacion_media']:.2f}"
    print(f"{x['fase']:8} {x['ejecucion']:34} {x['inicio'] or '-':19} {x['fin'] or '-':19} {x['duracion_s']:7.0f} {x['tasks']:5d} {x['tasks_fallidas']:4d} {u:>5} {x['reduce_tasks']:3d} {x['coste_usd']:8.4f} {x['coste_eur']:8.4f}")
if abortada_en: print(f"ABORTADA en fase: {abortada_en}")
print(f"TOTAL coste={tot_cost:.4f} USD = {tot_cost * eur_usd:.4f} EUR (tipo aprox. {eur_usd}) | fallidas={tot_fallidas} | utilizacion media={resumen['utilizacion_media']}")
print("JSON:", out)
sys.exit(1 if (tot_fallidas or abortada_en) else 0)
PY
