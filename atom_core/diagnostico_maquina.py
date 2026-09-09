"""Sonda rápida de la máquina antes de arrancar un organizado.

PORQUÉ EXISTE
-------------
Antes de lanzar un run largo (organizar una planta puede tardar ~20 min) es
útil saber en qué condiciones arranca: si el origen y/o el destino son discos
mecánicos (HDD), si la máquina ya está ocupada con otra carga, y cuánta RAM
libre hay. Con eso el usuario puede decidir si espera, cambia el destino o
sigue adelante sabiendo que irá lento.

ROBUSTEZ: esto NUNCA puede tumbar ni frenar el organizado
-----------------------------------------------------------
Es puramente diagnóstico e informativo. Cualquier fallo (comando ausente,
timeout, permisos, `psutil` no instalado, sistema operativo no soportado)
debe degradar a "desconocido"/0 sin propagar la excepción.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
from typing import Optional

try:
    import psutil
except Exception:  # pragma: no cover - entorno sin psutil
    psutil = None

# Timeout de los comandos externos (PowerShell). Una sonda diagnóstica nunca
# debe quedarse colgada esperando al sistema operativo.
_TIMEOUT_COMANDO_S = 6.0

# Umbral de "máquina ocupada": por encima de este % de CPU (media de todos
# los núcleos) ya hay otros procesos consumiendo recursos de forma notable.
_UMBRAL_CPU_OCUPADA = 25.0

# Flag de Windows para no abrir consola negra al invocar PowerShell.
_CREATE_NO_WINDOW = 0x08000000


def _letra_unidad_windows(ruta: str) -> Optional[str]:
    """Extrae la letra de unidad (`E:`) de una ruta de Windows, o `None`."""
    m = re.match(r"^([A-Za-z]:)", str(ruta))
    return m.group(1).upper() if m else None


def _tipo_disco_windows(ruta) -> dict:
    unidad = _letra_unidad_windows(str(ruta)) or str(ruta)
    letra = _letra_unidad_windows(str(ruta))
    if letra is None:
        return {"tipo": "desconocido", "modelo": None, "unidad": unidad}

    letra_sin_dos_puntos = letra.rstrip(":")
    comando = (
        f"Get-Partition -DriveLetter {letra_sin_dos_puntos} | Get-Disk | "
        "Get-PhysicalDisk | Select-Object MediaType,FriendlyName | "
        "ConvertTo-Json -Compress"
    )
    try:
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = _CREATE_NO_WINDOW
        resultado = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", comando],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_COMANDO_S,
            **kwargs,
        )
        salida = resultado.stdout or ""
    except Exception:
        return {"tipo": "desconocido", "modelo": None, "unidad": letra}

    tipo = "desconocido"
    if re.search(r'"MediaType"\s*:\s*"HDD"', salida, re.IGNORECASE):
        tipo = "HDD"
    elif re.search(r'"MediaType"\s*:\s*"SSD"', salida, re.IGNORECASE):
        tipo = "SSD"

    modelo = None
    m = re.search(r'"FriendlyName"\s*:\s*"([^"]+)"', salida)
    if m:
        modelo = m.group(1)

    return {"tipo": tipo, "modelo": modelo, "unidad": letra}


def _tipo_disco_linux(ruta) -> dict:
    ruta_abs = os.path.abspath(str(ruta))
    try:
        st = os.stat(ruta_abs)
        dev_id = st.st_dev
        mayor, menor = os.major(dev_id), os.minor(dev_id)
        unidad = f"{mayor}:{menor}"
    except Exception:
        return {"tipo": "desconocido", "modelo": None, "unidad": str(ruta)}

    dispositivo = None
    try:
        # Resolver el nombre del dispositivo de bloque a partir de major:minor
        # buscando en /sys/dev/block/<mayor>:<menor>, que es un symlink al
        # dispositivo real (p.ej. .../sda1 -> se sube a .../sda).
        enlace = f"/sys/dev/block/{mayor}:{menor}"
        destino = os.path.realpath(enlace)
        nombre = os.path.basename(destino)
        # Si es una partición (sda1), el disco físico es el directorio padre.
        base = os.path.basename(os.path.dirname(destino))
        candidatos = [nombre, base] if base else [nombre]
        for candidato in candidatos:
            ruta_rotational = f"/sys/block/{candidato}/queue/rotational"
            if os.path.exists(ruta_rotational):
                dispositivo = candidato
                break
    except Exception:
        dispositivo = None

    unidad_legible = f"/dev/{dispositivo}" if dispositivo else unidad

    if dispositivo is None:
        return {"tipo": "desconocido", "modelo": None, "unidad": unidad_legible}

    try:
        with open(f"/sys/block/{dispositivo}/queue/rotational", "r") as f:
            valor = f.read().strip()
        tipo = "HDD" if valor == "1" else ("SSD" if valor == "0" else "desconocido")
    except Exception:
        tipo = "desconocido"

    modelo = None
    try:
        with open(f"/sys/block/{dispositivo}/device/model", "r") as f:
            modelo = f.read().strip() or None
    except Exception:
        modelo = None

    return {"tipo": tipo, "modelo": modelo, "unidad": unidad_legible}


def tipo_disco(ruta) -> dict:
    """Detecta el tipo de disco (HDD/SSD/desconocido) que contiene `ruta`.

    Devuelve `{"tipo": "HDD"|"SSD"|"desconocido", "modelo": str|None, "unidad": str}`.
    Nunca lanza: cualquier fallo degrada a "desconocido".
    """
    try:
        if os.name == "nt":
            return _tipo_disco_windows(ruta)
        return _tipo_disco_linux(ruta)
    except Exception:
        return {"tipo": "desconocido", "modelo": None, "unidad": str(ruta)}


def _recursos_sistema() -> dict:
    """Núcleos, RAM y CPU ocupada. Todo a 0/desconocido si no hay `psutil`."""
    if psutil is None:
        return {
            "nucleos": os.cpu_count() or 0,
            "ram_total_gb": 0.0,
            "ram_libre_gb": 0.0,
            "cpu_ocupada_pct": 0.0,
        }

    try:
        nucleos = psutil.cpu_count(logical=True) or (os.cpu_count() or 0)
    except Exception:
        nucleos = os.cpu_count() or 0

    try:
        mem = psutil.virtual_memory()
        ram_total_gb = mem.total / (1024 ** 3)
        ram_libre_gb = mem.available / (1024 ** 3)
    except Exception:
        ram_total_gb = 0.0
        ram_libre_gb = 0.0

    try:
        cpu_ocupada_pct = psutil.cpu_percent(interval=0.5)
    except Exception:
        cpu_ocupada_pct = 0.0

    return {
        "nucleos": nucleos,
        "ram_total_gb": round(ram_total_gb, 1),
        "ram_libre_gb": round(ram_libre_gb, 1),
        "cpu_ocupada_pct": cpu_ocupada_pct,
    }


def _describe_disco(disco: dict, etiqueta: str) -> str:
    """Fragmento legible tipo `Origen E: HDD (disco mecánico)`."""
    unidad = disco.get("unidad") or "?"
    tipo = disco.get("tipo") or "desconocido"
    if tipo == "HDD":
        return f"{etiqueta} {unidad} HDD (disco mecánico)"
    if tipo == "SSD":
        return f"{etiqueta} {unidad} SSD"
    return f"{etiqueta} {unidad} tipo de disco no detectado"


def _construye_texto(
    disco_origen: dict,
    disco_destino: dict,
    mismo_disco: bool,
    nucleos: int,
    ram_total_gb: float,
    ram_libre_gb: float,
    cpu_ocupada_pct: float,
    maquina_ocupada: bool,
) -> str:
    partes = [
        _describe_disco(disco_origen, "Origen"),
        "·",
        _describe_disco(disco_destino, "Destino"),
        f"· {nucleos} núcleos",
        f"· RAM {ram_libre_gb:.1f}/{ram_total_gb:.1f} GB libre",
        f"· CPU al {cpu_ocupada_pct:.0f}% en reposo",
    ]
    texto = " ".join(partes)
    if maquina_ocupada:
        texto += " (¡hay otros procesos usando la máquina!)"
    else:
        texto += " (máquina libre)"

    if (
        mismo_disco
        and disco_origen.get("tipo") == "HDD"
        and disco_destino.get("tipo") == "HDD"
    ):
        texto += (
            " · ⚠ Origen y destino en el MISMO disco mecánico: leer y "
            "escribir a la vez lo penaliza mucho."
        )

    return texto


def sonda_inicial(origen, destino) -> dict:
    """Diagnóstico rápido de la máquina antes de arrancar un organizado.

    Nunca lanza: cualquier fallo interno degrada los campos afectados a
    "desconocido"/0 y sigue devolviendo el dict completo.
    """
    disco_origen = tipo_disco(origen)
    disco_destino = tipo_disco(destino)

    mismo_disco = (
        disco_origen.get("unidad") is not None
        and disco_origen.get("unidad") == disco_destino.get("unidad")
    )

    recursos = _recursos_sistema()
    nucleos = recursos["nucleos"]
    ram_total_gb = recursos["ram_total_gb"]
    ram_libre_gb = recursos["ram_libre_gb"]
    cpu_ocupada_pct = recursos["cpu_ocupada_pct"]
    maquina_ocupada = cpu_ocupada_pct > _UMBRAL_CPU_OCUPADA

    texto = _construye_texto(
        disco_origen,
        disco_destino,
        mismo_disco,
        nucleos,
        ram_total_gb,
        ram_libre_gb,
        cpu_ocupada_pct,
        maquina_ocupada,
    )

    return {
        "disco_origen": disco_origen,
        "disco_destino": disco_destino,
        "mismo_disco": mismo_disco,
        "nucleos": nucleos,
        "ram_total_gb": ram_total_gb,
        "ram_libre_gb": ram_libre_gb,
        "cpu_ocupada_pct": cpu_ocupada_pct,
        "maquina_ocupada": maquina_ocupada,
        "texto": texto,
    }


# --- Registro global del tipo de disco de ORIGEN detectado ------------------
#
# PORQUÉ EXISTE
# -------------
# `sonda_inicial` tarda varios segundos (sobre todo en Windows, por el
# PowerShell de `_tipo_disco_windows`) y por eso corre en un hilo daemon aparte
# (`atom_core/organize.py`, hilo "sonda-maquina"), no en el hilo del run. El
# `ControladorAdaptativo` de la fase RGB (`atom_core/paralelismo.py`) necesita
# saber si el disco de origen es HDD para capar los trabajadores y evitar
# seek thrashing, pero se crea y empieza a decidir ANTES de que la sonda haya
# terminado. En vez de pasarle el resultado por parámetro (que obligaría a
# organize.py a esperar al hilo, perdiendo el motivo de tenerlo en background),
# se deja aquí un registro global sencillo: la sonda lo rellena en cuanto
# acaba, y el controlador lo consulta en cada ventana. Si todavía no hay dato,
# se comporta como si no fuera HDD (no hay margen para capar sin evidencia).
_lock_tipo = threading.Lock()
_tipo_disco_origen: Optional[str] = None


def registrar_tipo_disco_origen(tipo: Optional[str]) -> None:
    """Guarda el tipo de disco de ORIGEN ("HDD"/"SSD"/"desconocido") detectado
    por la sonda de máquina, para que quien lo necesite (el controlador
    adaptativo de la fase RGB) lo pueda consultar sin esperar al hilo de la
    sonda."""
    global _tipo_disco_origen
    with _lock_tipo:
        _tipo_disco_origen = tipo


def tipo_disco_origen_detectado() -> Optional[str]:
    """Devuelve el último tipo de disco de origen registrado, o `None` si la
    sonda todavía no ha terminado (o nunca se ha lanzado, como en los
    tests)."""
    with _lock_tipo:
        return _tipo_disco_origen


def olvidar_tipo_disco_origen() -> None:
    """Resetea el registro. Se llama al arrancar cada run nuevo (para no
    arrastrar el disco de un run anterior mientras la sonda del run actual
    todavía está corriendo) y desde los tests, para no depender del orden de
    ejecución."""
    global _tipo_disco_origen
    with _lock_tipo:
        _tipo_disco_origen = None
