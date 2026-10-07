"""ATOM Organizer — nuevo entry point de la UI React (pywebview).

Roadmap paso 2: la pantalla "Organizar" (4 controles + Ejecutar) llama al bridge,
que dispara el core headless (`atom_core.organize`) en un hilo y empuja el
progreso del pipeline a React como eventos `atom:progress`. Pipeline intacto.

Uso:
  Dev  (HMR, requiere `npm run dev` en webui/):
        python app_webview.py --dev
  Prod (usa webui/dist buildeado con `npm run build`):
        python app_webview.py
"""
from __future__ import annotations

import argparse
import ast
import base64
import datetime
import glob
import hashlib
import importlib
import json
import logging
import multiprocessing
import os
import platform
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from zoneinfo import ZoneInfo

from atom_core import cancelacion
from atom_core import cola_subidas
from atom_core.credencial import (
    ESTADO_OK, ESTADO_SIN_CREDENCIAL, ESTADO_SIN_CONEXION,
    EstadoCredencial, clasificar,
)
from atom_core.event_sink import WebviewSink
from atom_core.google_auth import AuthError
from atom_core import estado_lan
from atom_core.manifiesto import destino_organizado, fallidas_reintentables
from atom_core import pin_kiosco
from atom_core import precarga
from atom_core import render_state, window_state
from atom_core import sesion_remota

# Hora local de la Pi/kiosco (regla del equipo: SIEMPRE Europe/Madrid en lo
# que se enseña al operario). Solo para el modo "esperando estadillo"
# (`Api.estadillo_espera_*`): `caduca_en` viaja como ISO en esta zona.
_TZ_ESTADILLO = ZoneInfo("Europe/Madrid")

# Fichero de telemetria de intentos de PIN (ver `Api.pin_telemetria`), dentro
# de la misma carpeta que ya usa el historial de procesos (`user_log_dir()`).
# Nombre sin el prefijo `atom-organizer-run_*` para que `logs_listar` (que
# filtra por ese patron) no lo confunda con un log de corrida.
_LOG_TELEMETRIA_PIN = "pin_telemetria.jsonl"
_LIMITE_TELEMETRIA_PIN = 1 * 1024 * 1024  # 1 MB, rotacion simple a .1

logger = logging.getLogger(__name__)

# Cada cuanto se reconsulta GitHub buscando version nueva (segundos). 30 min:
# suficiente para enterarse el mismo dia sin gastar el rate limit anonimo de la API.
INTERVALO_CHEQUEO_UPDATE = 1800.0

# SSID del hotspot de configuracion de la Pi. Lo usan tanto el propio hotspot
# como el listado de redes, que debe excluirlo de las redes conectables.
_AP_SSID = "ATOM-Organizer"

# Reintento a NIVEL DE LOTE de `cloud_upload.upload_plan`: si el wifi se cae a
# mitad de una subida de horas, los objetos que agotan sus reintentos
# internos quedan en `res.failed` y nadie hay delante para pulsar "Subir" de
# nuevo. `RONDAS_SUBIDA_MAX` rondas con backoff entre `ESPERA_RONDA_INICIAL` y
# `ESPERA_RONDA_MAX` segundos cubren la caída sola.
RONDAS_SUBIDA_MAX = 8
ESPERA_RONDA_INICIAL = 15
ESPERA_RONDA_MAX = 300


def _import_webview():
    """Importa pywebview solo cuando de verdad se va a abrir una ventana.

    En Raspberry Pi (ARM64) no hay wheel de PySide6 6.4.2, asi que el import
    revienta. Como el modo `--server` no necesita ventana, el import no puede
    estar en la cabecera del modulo o el proceso muere antes de arrancar.
    """
    try:
        return importlib.import_module("webview")
    except ImportError as exc:
        raise RuntimeError(
            f"[app_webview] No se pudo cargar pywebview/Qt: {exc}\n"
            "Si estas en Raspberry Pi u otro ARM64, arranca en modo servidor:\n"
            "    python app_webview.py --server\n"
            "y abre http://127.0.0.1:8765 en Chromium."
        )


def _base_dir() -> Path:
    """Dir base de recursos: bajo PyInstaller onefile los datas se extraen a
    ``sys._MEIPASS``; en ejecución normal, la raíz del repo (padre de src/). (Espeja
    ``external_tools.app_base_dir`` para que la UI buildeada se encuentre en el exe.)"""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else Path(__file__).resolve().parent.parent


ROOT = _base_dir()
DIST_INDEX = ROOT / "webui" / "dist" / "index.html"
DEV_URL = "http://localhost:5173"


def _disco_externo() -> str | None:
    """Ruta del primer disco externo montado, o None si no hay ninguno.

    Solo tiene sentido en Linux (Raspberry Pi): las inspecciones llegan por
    disco USB, montado por udisks2/gvfs en ``/media/<usuario>/<etiqueta>``,
    por algunos gestores en ``/media/<etiqueta>`` a secas, o manualmente
    (fstab, script de arranque) en ``/mnt/<lo-que-sea>``.
    """
    candidatos = set()
    for patron in ("/media/*/*", "/media/*", "/mnt/*"):
        candidatos.update(glob.glob(patron))

    raiz_dev = os.stat("/").st_dev
    validos = []
    for cand in candidatos:
        try:
            if not os.path.isdir(cand):
                continue
            # Un disco "extra" es, por definicion, uno en un dispositivo
            # distinto al de la raiz del sistema. Este criterio no depende
            # de nombres ni de convenciones de montaje, asi que es robusto
            # ante cualquier gestor de discos (udisks2, gvfs, montaje
            # manual...).
            if os.stat(cand).st_dev == raiz_dev:
                continue
            if not os.access(cand, os.R_OK):
                continue
            # Basta con la primera entrada para saber que no esta vacio:
            # os.scandir es perezoso, a diferencia de os.listdir (que en un
            # disco USB con miles de fotos de inspeccion lee el directorio
            # entero solo para tirarlo).
            with os.scandir(cand) as it:
                if next(iter(it), None) is None:
                    continue  # disco montado pero vacio: no sirve de nada
        except OSError:
            continue  # disco a medio montar, desconectado, etc.
        validos.append(cand)

    if validos:
        return sorted(validos)[0]
    return None


# Carpetas de "ruidos de sistema" de los discos externos que llegan al kiosco
# (fabricadas por Windows/macOS al formatear/usar el disco, o por el propio
# Linux): no aportan nada a quien busca la carpeta de la inspeccion y en una
# pantalla de 480x320 son puro ruido. No empiezan por "." (eso ya se filtra
# aparte), asi que necesitan lista explicita. Comparacion en minusculas.
_CARPETAS_SISTEMA_OCULTAS = {
    "$recycle.bin", "system volume information", "lost+found",
    ".trashes", ".spotlight-v100", ".fseventsd",
}


def _disco_que_contiene(ruta_real: str, discos: list[dict]) -> dict | None:
    """¿`ruta_real` (ya resuelta con `os.path.realpath`) cae dentro de alguno
    de `discos` (shape de `estado_lan.discos_externos`)? Devuelve el disco
    (con su `realpath` añadido) o `None`.

    Es la puerta de confinamiento del selector de carpetas en el kiosco: el
    backend, no el front, decide que es "dentro de un disco externo" — un
    front hostil o con bug no puede colarse fuera reescribiendo la ruta.
    """
    for disco in discos:
        try:
            raiz_real = os.path.realpath(disco["punto_montaje"])
        except OSError:
            continue
        if ruta_real == raiz_real or ruta_real.startswith(raiz_real + os.sep):
            con_realpath = dict(disco)
            con_realpath["realpath"] = raiz_real
            return con_realpath
    return None


_NOMBRE_LOG_RUN = re.compile(r"^atom-organizer-run_(\d{8})_(\d{6})_pid(\d+)\.log$")
_CABECERA_LOG_RUN = re.compile(r"^\[run\] version=(\S+) pid=\d+ task=(\S+) ctx=(\{.*\})\s*$")


def _resumir_log_run(ruta: str, nombre: str, match_nombre: "re.Match") -> dict | None:
    """Resumen de UN log de corrida para el Historial de procesos, leído línea
    a línea (nunca `read()` completo: estos ficheros pueden llevar miles de
    líneas de `[log]` por corrida larga y solo hace falta la cabecera, el
    nombre de planta, el recuento de `[error]` y si hubo `[done]`).

    Devuelve None si el fichero no se puede ni siquiera `stat()`-ear; el resto
    de fallos (cabecera rara, `ctx`/`[done]` no parseables) degradan campo a
    campo, no hacen saltar todo el resumen — un log parcialmente ilegible
    sigue siendo mejor que ninguno en la lista.
    """
    try:
        st = os.stat(ruta)
    except OSError:
        return None

    try:
        fecha = datetime.datetime.strptime(
            match_nombre.group(1) + match_nombre.group(2), "%Y%m%d%H%M%S"
        ).isoformat()
    except ValueError:
        fecha = datetime.datetime.fromtimestamp(st.st_mtime).isoformat()

    version = task = origen = destino = estadillo = ""
    planta = ""
    errores = 0
    done_visto = False
    duracion = None
    try:
        with open(ruta, "r", encoding="utf-8", errors="replace") as f:
            for linea in f:
                linea = linea.rstrip("\n")
                if linea.startswith("[run] "):
                    cab = _CABECERA_LOG_RUN.match(linea)
                    if cab:
                        version, task, ctx_txt = cab.group(1), cab.group(2), cab.group(3)
                        try:
                            ctx = ast.literal_eval(ctx_txt)
                        except (ValueError, SyntaxError):
                            ctx = {}
                        if isinstance(ctx, dict):
                            origen = str(ctx.get("origen") or "")
                            destino = str(ctx.get("destino") or "")
                            estadillo = str(ctx.get("estadillo") or "")
                elif linea.startswith("[plant] ") and not planta:
                    planta = linea[len("[plant] "):].strip()
                elif linea.startswith("[error]"):
                    errores += 1
                elif linea.startswith("[done] "):
                    done_visto = True
                    try:
                        payload = ast.literal_eval(linea[len("[done] "):].strip())
                        if isinstance(payload, dict):
                            duracion = payload.get("elapsed")
                    except (ValueError, SyntaxError):
                        pass
    except OSError:
        return None

    if not planta and destino:
        planta = os.path.basename(str(destino).rstrip("/\\"))

    # incompleto = ni [done] ni [error]: la corrida se quedó a medias (el
    # proceso murió/se cerró la app) y no hay forma de saber si acabó bien.
    if errores > 0:
        estado = "error"
    elif done_visto:
        estado = "ok"
    else:
        estado = "incompleto"

    return {
        "nombre": nombre,
        "fecha": fecha,
        "planta": planta,
        "task": task,
        "origen": origen,
        "destino": destino,
        "estadillo": estadillo,
        "version": version,
        "bytes": st.st_size,
        "errores": errores,
        "estado": estado,
        "duracion": duracion,
    }


# Diálogo de carpeta MODERNO en Windows (IFileOpenDialog + FOS_PICKFOLDERS): el del
# Explorador — barra de direcciones, árbol lateral y recuerda la última ubicación.
# Sustituye a System.Windows.Forms.FolderBrowserDialog (el árbol legacy feo que no
# recordaba carpeta). Se declara vía Add-Type C#; sólo se usan SetOptions/Show/GetResult
# y IShellItem.GetDisplayName — el resto de la vtable son stubs para preservar el orden
# de slots COM. Verificado en Win10 (PICKED=[C:\Users\...]). El here-string @"…"@ exige
# que "@ vaya a inicio de línea → el cuerpo va a columna 0 a propósito.
_MODERN_FOLDER_SRC = '''using System;
using System.Runtime.InteropServices;
public static class ModernFolder {
  [ComImport, ClassInterface(ClassInterfaceType.None), Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")]
  private class Dlg { }
  [ComImport, Guid("42f85136-db7e-439c-85f1-e4075d135fc8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  private interface IFileOpenDialog {
    [PreserveSig] int Show(IntPtr parent);
    void SetFileTypes(uint c, IntPtr rg);
    void SetFileTypeIndex(uint i);
    void GetFileTypeIndex(out uint i);
    void Advise(IntPtr p, out uint c);
    void Unadvise(uint c);
    void SetOptions(uint o);
    void GetOptions(out uint o);
    void SetDefaultFolder(IntPtr psi);
    void SetFolder(IntPtr psi);
    void GetFolder(out IntPtr psi);
    void GetCurrentSelection(out IntPtr psi);
    void SetFileName(string s);
    void GetFileName(out string s);
    void SetTitle(string s);
    void SetOkButtonLabel(string s);
    void SetFileNameLabel(string s);
    void GetResult(out IShellItem psi);
    void AddPlace(IntPtr psi, int a);
    void SetDefaultExtension(string s);
    void Close(int hr);
    void SetClientGuid(ref Guid g);
    void ClearClientData();
    void SetFilter(IntPtr f);
    void GetResults(out IntPtr e);
    void GetSelectedItems(out IntPtr e);
  }
  [ComImport, Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  private interface IShellItem {
    void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
    void GetParent(out IShellItem ppsi);
    void GetDisplayName(uint sigdn, [MarshalAs(UnmanagedType.LPWStr)] out string name);
    void GetAttributes(uint mask, out uint attribs);
    void Compare(IShellItem psi, uint hint, out int order);
  }
  public static string Pick(IntPtr owner, string title) {
    var d = (IFileOpenDialog)(new Dlg());
    uint o; d.GetOptions(out o);
    d.SetOptions(o | 0x20 | 0x40);
    if (title != null) d.SetTitle(title);
    int hr = d.Show(owner);
    if (hr != 0) return null;
    IShellItem it; d.GetResult(out it);
    string p; it.GetDisplayName(0x80058000, out p);
    return p;
  }
}
'''

# El here-string PowerShell que compila el C# en caliente. Sólo se usa como
# respaldo: la ruta normal carga el DLL ya compilado y cacheado en disco
# (ver `_dll_carpeta_moderna`), porque compilar con csc.exe en cada apertura
# del diálogo costaba entre uno y dos segundos.
_MODERN_FOLDER_CS = 'Add-Type @"\n' + _MODERN_FOLDER_SRC + '"@;\n'


# Compilar el C# anterior con csc.exe cuesta entre uno y dos segundos, y se pagaba
# ENTERO en cada apertura del selector de carpeta (proceso PowerShell nuevo cada vez).
# Se compila una sola vez a un DLL cacheado en la carpeta de configuración del
# usuario; a partir de ahí el diálogo sólo hace `Add-Type -Path`, que son unas
# decenas de milisegundos. El nombre lleva el hash del fuente, así que un cambio
# en el C# invalida la caché sin tener que borrarla a mano.
_candado_dll_carpeta = threading.Lock()
_dll_carpeta: str | None = None


def _ruta_dll_carpeta() -> Path:
    try:
        from external_tools import _user_config_path
        base = Path(_user_config_path()).parent
    except Exception:
        base = Path.home()
    firma = hashlib.sha1(_MODERN_FOLDER_SRC.encode("utf-8")).hexdigest()[:10]
    return base / f"modern-folder-{firma}.dll"


def _lit_ps(valor) -> str:
    """Escapa un valor para meterlo en un literal PowerShell de comillas simples.

    Sin esto, un `%APPDATA%` que cuelgue de un usuario con apóstrofe (`O'Brien`)
    rompe la sintaxis del script y el DLL no se compila nunca.
    """
    return str(valor).replace("'", "''")


def _invalidar_dll_carpeta(log=None) -> None:
    """Descarta el DLL cacheado (corrupto o ilegible) para que se recompile."""
    global _dll_carpeta
    with _candado_dll_carpeta:
        _dll_carpeta = None
        try:
            _ruta_dll_carpeta().unlink(missing_ok=True)
        except Exception:
            if log:
                log("_invalidar_dll_carpeta: no se pudo borrar el DLL cacheado")


def _dll_carpeta_moderna(log=None) -> str | None:
    """Ruta del DLL con el diálogo moderno, compilándolo la primera vez.

    Devuelve None si no se pudo compilar; el llamante debe recurrir entonces al
    camino antiguo (compilar en caliente dentro del propio diálogo).
    """
    global _dll_carpeta
    if platform.system() != "Windows":
        return None
    with _candado_dll_carpeta:
        if _dll_carpeta:
            return _dll_carpeta
        destino = _ruta_dll_carpeta()
        try:
            if destino.is_file() and destino.stat().st_size > 0:
                _dll_carpeta = str(destino)
                return _dll_carpeta
        except OSError:
            pass
        # Se compila a un fichero temporal propio de este proceso y sólo al final
        # se mueve al nombre definitivo (os.replace es atómico en Windows sobre el
        # mismo volumen). Si matan la app a mitad de compilación, o dos instancias
        # compilan a la vez, nunca queda un DLL truncado en el nombre bueno: un
        # `Add-Type -Path` sobre un ensamblado inválido es error terminante de
        # PowerShell y dejaría el selector de carpeta muerto para siempre.
        temporal = destino.with_name(f"{destino.stem}.{os.getpid()}.tmp")
        script = (
            "$src = @\"\n" + _MODERN_FOLDER_SRC + "\"@\n"
            f"Add-Type -TypeDefinition $src -OutputAssembly '{_lit_ps(temporal)}' -OutputType Library\n"
        )
        try:
            destino.parent.mkdir(parents=True, exist_ok=True)
            enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-EncodedCommand", enc],
                capture_output=True, text=True, timeout=120,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
            if proc.returncode != 0 or not temporal.is_file() or temporal.stat().st_size == 0:
                if log:
                    log(f"_dll_carpeta_moderna rc={proc.returncode} err={proc.stderr!r}")
                temporal.unlink(missing_ok=True)
                return None
            os.replace(temporal, destino)
        except Exception:
            if log:
                import traceback
                log("_dll_carpeta_moderna EXC:\n" + traceback.format_exc())
            try:
                temporal.unlink(missing_ok=True)
            except Exception:
                pass
            return None
        _dll_carpeta = str(destino)
        return _dll_carpeta


def precalentar_dialogo_carpeta() -> None:
    """Compila el DLL del selector en segundo plano para que la primera apertura
    del diálogo ya lo encuentre hecho. No bloquea ni propaga errores."""
    if platform.system() != "Windows":
        return
    threading.Thread(target=_dll_carpeta_moderna, daemon=True).start()


class Api:
    """Objeto puente expuesto a JS como `window.pywebview.api`."""

    def __init__(self, *, broker: bool = False) -> None:
        self._window = None
        self._sink = None
        self._running = False
        self._downloading = False
        # Código de salida del último PowerShell de diálogo: distingue "el usuario
        # canceló" (0, sin stdout) de "el script abortó" (!= 0), que es lo que
        # delata un DLL de selector inservible.
        self._rc_dialogo: int | None = 0
        self._update_path: str | None = None
        self._update_sha256: str | None = None
        self._update_asset_url: str | None = None
        # Último aviso "hay versión nueva" del chequeo automático (ver
        # `start_update_check`). El modal (`UpdateModal.jsx`) no está montado
        # mientras el usuario no ha entrado, así que el evento `atom:update`
        # puede emitirse al vacío; esto le da algo que consultar al montarse.
        self._ultimo_update: dict | None = None
        # Resultado crudo del ultimo chequeo de actualizacion (incluidos los
        # fallos), para poder mirarlo desde Ajustes en vez de perderlo en silencio.
        self._ultimo_chequeo: dict | None = None
        # Subida al bucket (ver más abajo). `_auth` se crea perezoso: sin él, el
        # arranque tendría que leer el fichero de credenciales aunque nadie vaya
        # a subir nada en toda la sesión.
        self._auth = None
        self._credencial = EstadoCredencial()
        # El PIN del kiosco es del dispositivo, no de la sesion: se abre su
        # propio store para no depender de que haya credencial configurada.
        self._pin_store = None
        self._pin_intentos = pin_kiosco.ControlIntentos()
        # Store propio del catálogo de perfiles (pantalla de entrada tipo
        # Netflix). Mismo motivo que `_pin_store`: perezoso, y con su propio
        # atributo para que los tests puedan sustituirlo sin tocar `_auth`.
        self._perfiles_store = None
        # `broker`: modo Raspberry Pi (`main()`, rama `--server`). Sin cliente
        # OAuth propio, `_get_auth` construye un `GoogleAuth` broker_only en
        # vez de devolver `None`. El escritorio (Windows) nunca pasa esto:
        # `broker=False` por defecto deja el comportamiento intacto.
        self._broker = bool(broker)
        self._logging_in = False
        self._verifying = False
        self._uploading = False
        self._cancel_upload = False
        # Subida del resultado al bucket (atom_core.subida_resultado). Flag y bandera
        # propios: no compiten con `_uploading` (subida «en crudo» a datos_para_organizar).
        self._subiendo_resultado = False
        self._cancel_resultado = False
        self._resultado_lock = threading.Lock()
        # `estadillo_subir` no tenía mutex propio (a diferencia de
        # `cloud_upload`/`self._uploading`): dos clicks seguidos (doble-tap
        # del tactil resistivo, o un `call()` del bridge que reintenta tras
        # un plazo vencido cuyo hilo de verdad seguía vivo) arrancaban dos
        # hilos escribiendo los mismos objetos del bucket a la vez. La UI ya
        # se protegía con `estadSubiendo` (`PasoEstadillo.jsx`), pero eso es
        # solo un candado en el CLIENTE: no protege contra dos pestañas, dos
        # dispositivos remotos (móvil + kiosco) o una llamada colada por
        # detrás del candado de React.
        self._estadillo_subiendo = False
        self._analizando = False
        self._cancel_analisis = False
        # Batcher de eventos de progreso (ver `_push`). El pipeline emite DOS
        # eventos por imagen y cada uno era un `evaluate_js` bloqueante: en un
        # vuelo de 5.000 fotos, 10.000 viajes Python->Qt->Chromium con el worker
        # parado en un semaforo. Se acumulan aqui y se sueltan de golpe.
        self._push_buf: list[dict] = []
        self._push_lock = threading.Lock()
        self._push_last = 0.0
        # Inventario del prefijo destino, calculado en background (ver
        # `_inventario_precalentar`). Listar 50.000 objetos son ~20 s: pedirlo
        # síncrono dejaba la pantalla previa bloqueada justo después de elegir
        # carpeta. Se calcula mientras el operario lee el estadillo y elige
        # inspección, y la subida lo reutiliza si sigue siendo del mismo
        # prefijo y no ha caducado.
        # Va por prefijo: si el operario cambia de inspección deprisa quedan
        # dos listados vivos, y con un solo hueco el que tardara más (el de la
        # inspección que ya abandonó) pisaba al recién calculado.
        self._inv: dict[str, dict] = {}    # prefix -> {remotos, t}
        self._inv_lock = threading.Lock()
        self._inv_hilos: set[str] = set()  # prefijos que se están calculando ya
        # Hotspot de configuración wifi (red_ap_*): token efímero (nunca a
        # disco) y timer de autoapagado para no dejar la Pi sin red si nadie
        # completa el flujo desde el móvil.
        self._ap_token: str = ""
        self._ap_timer: threading.Timer | None = None
        self._ap_conexion_previa: str = ""
        # Modo "esperando estadillo" (app Electron "Estadillo Digital" de
        # Christian, LAN, sin token). `None` = no hay espera activa; con
        # espera activa, dict con carpeta/inspeccion/fotos/recibido/rutas/
        # errores (ver `estadillo_espera_iniciar`). Lock porque lo tocan el
        # hilo HTTP (webserver) y el hilo de fondo que calcula el EXIF.
        self._estadillo_espera: dict | None = None
        self._estadillo_espera_lock = threading.Lock()
        # Log de actividad remota del modo espera (lo enseña el kiosco, no la
        # app "Estadillo Digital"): últimos 10 eventos y el último contacto,
        # para saber si "alguien está ahí" aunque no haya llegado el
        # estadillo todavía. Vive fuera de `_estadillo_espera` a propósito:
        # sobrevive a `estadillo_espera_cancelar()` (el 'cancelado' queda en
        # el propio log) y a que se reinicie la espera.
        self._estadillo_eventos: list[dict] = []
        self._estadillo_ultimo_contacto: dict | None = None
        # Indicador "¿hay estadillo en esta carpeta?" (selector del kiosco):
        # caché por carpeta (`os.path.normpath` -> {encontrado, nombre,
        # buscando}) para no repetir el escaneo en cada poll de
        # `estadillo_espera_estado`, y el set de carpetas con un escaneo en
        # marcha (evita lanzar dos hilos para la misma carpeta).
        self._estadillo_carpeta_cache: dict[str, dict] = {}
        self._estadillo_carpeta_lock = threading.Lock()
        self._estadillo_carpeta_hilos: set[str] = set()
        # Reloj inyectable (para tests de caducidad sin `sleep`): por defecto
        # la hora real de Madrid. `datetime.datetime.now(_TZ_ESTADILLO)`.
        self._estadillo_reloj = lambda: datetime.datetime.now(_TZ_ESTADILLO)
        # Carpeta de trabajo del panel de control remoto (`/api/control/*`,
        # `atom_core/webserver.py`), fijada por `carpeta_trabajo_fijar`. Vive
        # en `Api` (no en una closure del handler HTTP) para que
        # `/api/control/carpeta`, `/api/control/estado` y
        # `/api/control/organizar` compartan siempre la misma carpeta, y para
        # que el kiosco (React) pueda fijarla tambien via `METODOS_EXPUESTOS`
        # el dia que deje de vivir solo como estado local.
        self._carpeta_trabajo: str | None = None
        # Ultimo evento de progreso/fase/error del run en curso (o del
        # ultimo terminado), para que `GET /api/control/estado` pueda
        # informar sin necesitar SSE: se actualiza en `_push`, el mismo sitio
        # que emite `atom:progress` (ver `_flush_push`). Se reinicia al
        # arrancar un run nuevo (`run_task`).
        self._control_fase: dict | None = None
        self._control_progreso: int | None = None
        self._control_ultimo_error: str | None = None

    def bind_window(self, window) -> None:
        self._window = window
        self._sink = WebviewSink(window)

    def bind_sink(self, sink) -> None:
        """Modo servidor: no hay ventana, solo un canal de eventos."""
        self._sink = sink

    # ---- utilidades / prueba de vida --------------------------------------
    def ping(self, who: str = "?") -> dict:
        return {
            "ok": True,
            "msg": f"pong desde Python para «{who}»",
            "python": platform.python_version(),
            "platform": platform.system(),
        }

    def sesion_remota(self) -> dict:
        try:
            datos = sesion_remota.activa()
        except Exception:
            return {"activa": False, "motivo": None, "desde": None}
        if not datos:
            return {"activa": False, "motivo": None, "desde": None}
        return {"activa": True, "motivo": datos.get("motivo"), "desde": datos.get("desde")}

    # ---- diálogos de archivo ----------------------------------------------
    # En Linux el backend Qt de pywebview abre el diálogo desde el hilo del
    # js_api sin problema. En Windows el backend es WebView2 y los métodos del
    # js_api corren en un hilo worker que NO es el "foreground thread": Windows
    # impide que una ventana creada por ese hilo se muestre al frente, así que
    # `create_file_dialog` (y también `SHBrowseForFolder`/`GetOpenFileNameW`
    # llamados directo, probado en v3.5) no aparecen — sin lanzar excepción.
    # Solución: lanzar el diálogo en un PROCESO SEPARADO (PowerShell + WinForms),
    # que tiene su propio foreground y usa un owner TopMost para quedar delante.
    def _log_picker(self, msg: str) -> None:
        """Traza a fichero persistente para diagnosticar en Windows sin consola."""
        try:
            from external_tools import _user_config_path
            logpath = Path(_user_config_path()).parent / "atom-picker.log"
        except Exception:
            logpath = Path.home() / "atom-picker.log"
        try:
            os.makedirs(os.path.dirname(logpath), exist_ok=True)
            with open(logpath, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    # Owner invisible TopMost fuera de pantalla → arrastra el diálogo al frente
    # aunque lo dispare un proceso lanzado desde un hilo no-foreground.
    _WIN_OWNER = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$o=New-Object System.Windows.Forms.Form;"
        "$o.TopMost=$true;$o.ShowInTaskbar=$false;$o.FormBorderStyle='None';"
        "$o.StartPosition='Manual';$o.Location=New-Object System.Drawing.Point(-3000,-3000);"
        "$o.Size=New-Object System.Drawing.Size(1,1);$o.Show();$o.Activate();"
    )

    def _win_dialog(self, ps_body: str) -> str | None:
        """Ejecuta un diálogo WinForms en un proceso PowerShell -STA aparte y
        devuelve por stdout la ruta elegida (vacío = cancelado)."""
        script = self._WIN_OWNER + ps_body + "$o.Close();"
        # -EncodedCommand (UTF-16LE b64): el cuerpo lleva un here-string C# con comillas
        # y saltos; pasarlo por -Command es frágil. Codificado es a prueba de escaping.
        enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden",
                 "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
                capture_output=True, text=True, timeout=600,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
            self._rc_dialogo = proc.returncode
            if proc.returncode != 0:
                self._log_picker(f"_win_dialog rc={proc.returncode} err={proc.stderr!r}")
            out = (proc.stdout or "").strip()
            return out or None
        except Exception:
            self._rc_dialogo = None
            import traceback
            self._log_picker("_win_dialog EXC:\n" + traceback.format_exc())
            return None

    def _win_pick_folder(self) -> str | None:
        # Diálogo MODERNO del Explorador (IFileOpenDialog + FOS_PICKFOLDERS), con el
        # owner TopMost de _WIN_OWNER para quedar al frente desde el hilo no-foreground.
        cuerpo = (
            "$p=[ModernFolder]::Pick($o.Handle,'Selecciona la carpeta');"
            "if($p){[Console]::Out.Write($p)}"
        )
        dll = _dll_carpeta_moderna(self._log_picker)
        # Con el DLL cacheado nos ahorramos compilar el C# en cada apertura.
        prefijo = f"Add-Type -Path '{_lit_ps(dll)}';" if dll else _MODERN_FOLDER_CS
        self._rc_dialogo = 0
        elegido = self._win_dialog(prefijo + cuerpo)
        if elegido is None and dll and self._rc_dialogo not in (0, None):
            # PowerShell abortó: lo más probable es un DLL ilegible (borrado a
            # medias, antivirus, disco lleno). Se tira la caché y se reintenta por
            # el camino antiguo, que compila en caliente, para no dejar al usuario
            # sin selector de carpeta.
            self._log_picker("selector: DLL cacheado inservible, se invalida y se reintenta")
            _invalidar_dll_carpeta(self._log_picker)
            return self._win_dialog(_MODERN_FOLDER_CS + cuerpo)
        return elegido

    def _win_pick_file(self, filtro: str | None = None) -> str | None:
        # `filtro='csv_xlsx'` es el único valor soportado hoy (elegir estadillo
        # a mano en escritorio, `EstadilloField.jsx`/`PasoEstadillo.jsx`); sin
        # filtro se mantiene "Todos los archivos", igual que antes.
        filtro_ps = (
            "Estadillos (*.csv;*.xlsx;*.xls)|*.csv;*.xlsx;*.xls|Todos los archivos (*.*)|*.*"
            if filtro == "csv_xlsx"
            else "Todos los archivos (*.*)|*.*"
        )
        return self._win_dialog(
            "$d=New-Object System.Windows.Forms.OpenFileDialog;"
            f"$d.Title='Selecciona el archivo';$d.Filter='{filtro_ps}';"
            "if($d.ShowDialog($o) -eq [System.Windows.Forms.DialogResult]::OK)"
            "{[Console]::Out.Write($d.FileName)}"
        )

    def pick_folder(self) -> str | None:
        try:
            if platform.system() == "Windows":
                return self._win_pick_folder()
            webview = _import_webview()
            res = self._window.create_file_dialog(webview.FOLDER_DIALOG)
            return res[0] if res else None
        except Exception as exc:  # noqa: BLE001 — se traza y se avisa al front
            import traceback
            self._log_picker("pick_folder ERROR:\n" + traceback.format_exc())
            self._push({"kind": "error",
                        "text": f"No se pudo abrir el diálogo de carpeta: {type(exc).__name__}: {exc}"})
            return None

    def pick_file(self, filtro: str | None = None) -> str | None:
        try:
            if platform.system() == "Windows":
                return self._win_pick_file(filtro)
            webview = _import_webview()
            file_types = (
                ("Estadillos (*.csv;*.xlsx;*.xls)", "Todos los archivos (*.*)")
                if filtro == "csv_xlsx"
                else ()
            )
            res = self._window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False, file_types=file_types
            )
            return res[0] if res else None
        except Exception as exc:  # noqa: BLE001 — se traza y se avisa al front
            import traceback
            self._log_picker("pick_file ERROR:\n" + traceback.format_exc())
            self._push({"kind": "error",
                        "text": f"No se pudo abrir el diálogo de archivo: {type(exc).__name__}: {exc}"})
            return None

    def _listado_raiz_discos_pi(self) -> dict:
        """Nivel superior del selector en el kiosco: la lista de discos
        externos montados, no el sistema de ficheros. `path=None` en Linux
        entra siempre por aqui (ver `list_dir`)."""
        discos = estado_lan.discos_externos()
        dirs = [
            {
                "name": disco["nombre"],
                "path": os.path.realpath(disco["punto_montaje"]),
                "libre_gb": disco.get("libre_gb"),
                "total_gb": disco.get("total_gb"),
            }
            for disco in discos
        ]
        return {
            "ok": True, "path": None, "parent": None,
            "dirs": dirs, "files": [],
            "is_root": True, "disk_name": None, "rel_parts": [],
        }

    def _list_dir_confinado_pi(self, path: str | None) -> dict:
        """`list_dir` para Linux/kiosco: confinado a discos externos.

        Sin `path`, la lista de discos (`_listado_raiz_discos_pi`). Con
        `path`, solo se sirve si cae dentro de un disco montado ahora mismo
        (comprobado con `realpath`, asi que un symlink que escape del disco
        tambien se rechaza, tanto como raiz como colandose entre las
        entradas listadas).
        """
        if not path:
            return self._listado_raiz_discos_pi()

        discos = estado_lan.discos_externos()
        try:
            real = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
        except OSError:
            return {"ok": False, "error": f"Ruta inválida: {path}"}

        disco = _disco_que_contiene(real, discos)
        if disco is None:
            return {"ok": False, "error": "Fuera de los discos externos montados."}
        if not os.path.isdir(real):
            return {"ok": False, "error": f"No es una carpeta: {real}"}

        dirs, files = [], []
        try:
            entradas = sorted(os.listdir(real), key=str.lower)
        except OSError as exc:
            return {"ok": False, "error": f"No se pudo leer: {exc}"}
        for nombre in entradas:
            if nombre.startswith(".") or nombre.lower() in _CARPETAS_SISTEMA_OCULTAS:
                continue  # ocultos y ruido de sistema (RECYCLE.BIN, etc.) fuera
            completo = os.path.join(real, nombre)
            try:
                completo_real = os.path.realpath(completo)
                if _disco_que_contiene(completo_real, discos) is None:
                    continue  # symlink que se escapa del disco: fuera
                if os.path.isdir(completo):
                    dirs.append({"name": nombre, "path": completo})
                else:
                    files.append({"name": nombre, "path": completo,
                                  "size": os.stat(completo).st_size})
            except OSError:
                continue  # permisos, enlace roto, unidad desconectada

        raiz_disco = disco["realpath"]
        # En la raiz del disco no hay ".. subir": subir mas es salirse del
        # confinamiento. Volver a la lista de discos lo hace el front por el
        # breadcrumb, no por esta fila.
        parent = None if real == raiz_disco else os.path.dirname(real)
        rel = os.path.relpath(real, raiz_disco)
        rel_parts = [] if rel == "." else rel.split(os.sep)
        return {
            "ok": True,
            "path": real,
            "parent": parent,
            "dirs": dirs,
            "files": files,
            "is_root": False,
            "disk_name": disco["nombre"],
            "rel_parts": rel_parts,
        }

    def list_dir(self, path: str | None = None) -> dict:
        """Lista un directorio para el explorador de la UI.

        En modo servidor no hay dialogo nativo de ficheros (eso lo daba Qt), y
        en una pantalla de 480x320 manejada con el dedo tampoco seria usable.
        El explorador vive en la webui y esto es lo que lo alimenta.

        En Linux (kiosco Raspberry Pi) el listado queda CONFINADO a los
        discos externos montados: sin `path` devuelve la lista de discos (el
        nivel superior es esa lista, no el sistema de ficheros), y con `path`
        solo se sirve si la ruta -resuelta con `realpath`, symlinks incluidos-
        cae dentro de un disco montado ahora mismo. Es autoridad de backend:
        un front con bug o manipulado no puede escapar reescribiendo la ruta.
        En Windows/escritorio el comportamiento no cambia.
        """
        if estado_lan.es_raspberry():
            return self._list_dir_confinado_pi(path)
        destino = os.path.abspath(os.path.expanduser(path or "~"))
        if not os.path.isdir(destino):
            return {"ok": False, "error": f"No es una carpeta: {destino}"}
        dirs, files = [], []
        try:
            entradas = sorted(os.listdir(destino), key=str.lower)
        except OSError as exc:
            return {"ok": False, "error": f"No se pudo leer: {exc}"}
        for nombre in entradas:
            if nombre.startswith("."):
                continue  # ocultos fuera: ruido en una pantalla diminuta
            completo = os.path.join(destino, nombre)
            try:
                if os.path.isdir(completo):
                    dirs.append({"name": nombre, "path": completo})
                else:
                    files.append({"name": nombre, "path": completo,
                                  "size": os.stat(completo).st_size})
            except OSError:
                continue  # permisos, enlace roto, unidad desconectada
        padre = os.path.dirname(destino)
        return {
            "ok": True,
            "path": destino,
            "parent": None if padre == destino else padre,
            "dirs": dirs,
            "files": files,
        }

    def default_dir(self) -> dict:
        """Carpeta con la que arranca el selector, YA con su listado.

        Devuelve el mismo shape que list_dir() para que el front no tenga
        que encadenar una segunda llamada HTTP tras esta (evita el "no ha
        respondido" del kiosco: dos peticiones secuenciales duplican la
        latencia percibida).

        En Windows (pywebview/Qt, produccion actual) el comportamiento debe
        quedar EXACTAMENTE igual que antes: arranca en el home. En Linux
        (Raspberry Pi) arranca en la lista de discos externos montados
        (nivel superior del confinamiento de `list_dir`): el operador nunca
        empieza navegando el sistema de ficheros.
        """
        if not estado_lan.es_raspberry():
            return {"ok": True, "path": os.path.expanduser("~")}
        return self.list_dir(None)

    def folder_is_empty(self, path: str, origen: str = "") -> dict:
        """¿Está vacía la carpeta de salida? El front avisa al elegirla (una
        corrida sobre residuos genera duplicados `_1/_2` y errores de recorte).
        El backend igualmente la rechaza al arrancar; esto es feedback previo.
        Devuelve {exists, empty, count, organizado, fallidas}. `fallidas` = filas
        'fallido' del manifiesto SOLO si es del mismo `origen` (opción
        "Reintentar fallidas"); 0 en cualquier otro caso. `organizado=True` = destino
        con manifiesto válido de una tanda previa: se puede añadir otra tanda
        aunque no esté vacío (misma regla que el guard de `organize.run_task`).
        Carpeta inexistente = válida (vacía)."""
        try:
            if not path or not os.path.isdir(path):
                return {"exists": False, "empty": True, "count": 0, "organizado": False}
            # Misma regla que el guard de `organize.run_task`: `.organizado` y
            # `LOGS` los crea el propio Organizer y no cuentan como residuo.
            entries = [n for n in os.listdir(path) if n not in (".organizado", "LOGS")]
            organizado = destino_organizado(path)
            fallidas = 0
            fallidas_error = None
            if organizado and origen:
                info = fallidas_reintentables(path, origen)
                fallidas = info["fallidas"] if info["mismo_origen"] else 0
                fallidas_error = info.get("error")
            resultado = {"exists": True, "empty": len(entries) == 0, "count": len(entries),
                         "organizado": organizado, "fallidas": fallidas}
            if fallidas_error:
                # El manifiesto existe pero no se pudo leer: la UI lo muestra.
                resultado["fallidas_error"] = fallidas_error
            return resultado
        except Exception as exc:  # noqa: BLE001 — se reenvía al front
            return {"exists": True, "empty": True, "count": 0, "organizado": False,
                    "error": f"{type(exc).__name__}: {exc}"}

    # ---- lectura del estadillo (modal previo al procesado) ----------------
    def read_estadillo_info(self, path: str) -> dict:
        """Info básica de vuelo del estadillo para el modal previo: pilotos,
        dron(es), nº de vuelos y franjas horarias. Sincrónico (no arranca hilo);
        `atom_core.estadillo` solo usa pandas + utils (no arrastra gui/PySide)."""
        try:
            # El candado de `precargar_pandas` serializa el primer import de pandas
            # con el resto de hilos (ver atom_core/precarga.py).
            precarga.precargar_pandas()
            from atom_core.estadillo import read_estadillo_info
            return read_estadillo_info(path)
        except Exception as exc:  # noqa: BLE001 — se reenvía al front
            return {"error": f"{type(exc).__name__}: {exc}"}

    def estadillos_detectar(self, carpeta: str, incluir_recibidos: bool = False) -> dict:
        """Escanea `carpeta` buscando estadillos sin que el operario tenga que
        elegirlos a mano: base de "detectados N estadillos, M días de vuelo..."
        antes de subir. Sincrónico, como `read_estadillo_info` (mismo módulo,
        no arrastra gui/PySide).

        `incluir_recibidos=True` (solo `PasoEstadillo.jsx`, escritorio; el
        kiosco nunca lo pasa) suma como candidatos los CSV/XLSX sueltos en
        `estadillos_recibidos_dir()`, ver `atom_core.estadillo.detectar_estadillos`.

        No encontrar ninguno NO es un error (el operario aún puede elegir a
        mano): `{"rutas": [], "n_estadillos": 0, "info": None, "error": None}`.

        `candidatos_padre` (rutas completas) son estadillos válidos sueltos en
        la carpeta PADRE de `carpeta` (ver
        `atom_core.estadillo.detectar_estadillos_en_padre`): NUNCA se mezclan
        con `rutas` ni se usan en automático — el front los muestra con su
        ruta completa y solo entran si el operario confirma uno a mano (caso
        caso de campo: estadillos antiguos sacados a propósito al padre).
        """
        try:
            # El candado de `precargar_pandas` serializa el primer import de pandas
            # con el resto de hilos (ver atom_core/precarga.py).
            precarga.precargar_pandas()
            from atom_core.estadillo import (
                aviso_estadillos_misma_carpeta,
                detectar_estadillos,
                detectar_estadillos_en_padre,
                read_estadillo_info,
            )
            detectado = detectar_estadillos(carpeta, incluir_recibidos=incluir_recibidos)
            if detectado.get("no_existe"):
                # Carpeta aún no disponible (montaje sincronizando): el front
                # reintenta, no es lo mismo que "sin estadillo".
                return {"rutas": [], "n_estadillos": 0, "info": None, "error": None,
                        "no_existe": True, "candidatos_padre": [],
                        "aviso_misma_carpeta": None}
            rutas = detectado["rutas"]
            candidatos_padre = detectar_estadillos_en_padre(carpeta)["rutas"]
            info = read_estadillo_info(rutas) if rutas else None
            # Aviso temprano (no bloqueante): 2+ estadillos detectados en la
            # MISMA carpeta abortarán el run más tarde en `construir_indice`
            # (`ErrorEstadillosMismaCarpeta`) — mejor que el operario lo vea
            # ya aquí y separe los ficheros antes de arrancar nada.
            aviso_misma_carpeta = aviso_estadillos_misma_carpeta(rutas)
            return {"rutas": rutas, "n_estadillos": len(rutas), "info": info, "error": None,
                    "candidatos_padre": candidatos_padre,
                    "aviso_misma_carpeta": aviso_misma_carpeta}
        except Exception as exc:  # noqa: BLE001 — se reenvía al front
            return {"error": f"{type(exc).__name__}: {exc}"}

    def estadillos_detectar_start(self, carpeta: str) -> dict:
        """Igual que `estadillos_detectar` pero en un hilo: el `os.walk` +
        parseo con pandas de cada candidato de una carpeta de vuelo grande
        congelaba la ventana. El resultado llega por `atom:analisis` (scope
        `estadillos`, kind `done`)."""
        if self._analizando:
            return {"started": False, "reason": "Ya hay un análisis en curso."}
        self._analizando = True

        def worker() -> None:
            try:
                # El candado de `precargar_pandas` serializa el primer import de pandas
                # con el resto de hilos (ver atom_core/precarga.py).
                precarga.precargar_pandas()
                from atom_core.estadillo import (
                    aviso_estadillos_misma_carpeta,
                    detectar_estadillos,
                    detectar_estadillos_en_padre,
                    read_estadillo_info,
                )
                detectado = detectar_estadillos(carpeta)
                if detectado.get("no_existe"):
                    self._push_analisis({"kind": "done", "scope": "estadillos", "data": {
                        "rutas": [], "n_estadillos": 0, "info": None, "error": None,
                        "no_existe": True, "candidatos_padre": [],
                        "aviso_misma_carpeta": None}})
                    return
                rutas = detectado["rutas"]
                candidatos_padre = detectar_estadillos_en_padre(carpeta)["rutas"]
                info = read_estadillo_info(rutas) if rutas else None
                # Mismo aviso temprano que `estadillos_detectar` (ver ahí el porqué).
                aviso_misma_carpeta = aviso_estadillos_misma_carpeta(rutas)
                data = {"rutas": rutas, "n_estadillos": len(rutas), "info": info, "error": None,
                        "candidatos_padre": candidatos_padre,
                        "aviso_misma_carpeta": aviso_misma_carpeta}
                self._push_analisis({"kind": "done", "scope": "estadillos", "data": data})
            except Exception as exc:  # noqa: BLE001 - llega a la UI como error
                self._push_analisis({"kind": "error", "scope": "estadillos", "text": str(exc)})
            finally:
                self._analizando = False

        threading.Thread(target=worker, daemon=True).start()
        return {"started": True}

    # ---- autodetección del sufijo de separación ---------------------------
    def detect_suffixes(self, origen: str) -> dict:
        """Recomienda el sufijo térmico/RGB escaneando los nombres de la carpeta
        origen (DJI: térmicas `_T`). Sincrónico; `atom_core.suffixes` solo usa
        `os` (no arrastra gui/PySide ni el pipeline)."""
        try:
            from atom_core.suffixes import detect_suffixes
            return detect_suffixes(origen)
        except Exception as exc:  # noqa: BLE001 — se reenvía al front
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                    "thermal": "", "rgb": "", "tokens": {}, "total": 0, "no_suffix": 0}

    def detect_suffixes_start(self, origen: str) -> dict:
        """Igual que `detect_suffixes` pero en un hilo: el `os.walk` de una
        carpeta de vuelo grande congelaba la ventana. El resultado llega por
        `atom:analisis` (kind `done`), el avance por kind `scan`."""
        if self._analizando:
            return {"started": False, "reason": "Ya hay un análisis en curso."}
        self._analizando = True

        def worker() -> None:
            try:
                from atom_core.suffixes import detect_suffixes
                data = detect_suffixes(
                    origen,
                    on_progress=lambda n: self._push_analisis(
                        {"kind": "scan", "scope": "suffixes", "done": n}),
                    should_stop=lambda: self._cancel_analisis,
                )
                if self._cancel_analisis:
                    self._push_analisis({"kind": "cancelled", "scope": "suffixes"})
                else:
                    self._push_analisis({"kind": "done", "scope": "suffixes", "data": data})
            except Exception as exc:  # noqa: BLE001 - llega a la UI como error
                self._push_analisis({"kind": "error", "scope": "suffixes", "text": str(exc)})
            finally:
                self._analizando = False

        threading.Thread(target=worker, daemon=True).start()
        return {"started": True}

    def analisis_cancel(self) -> dict:
        """Pide parar el análisis en curso (escaneo de sufijos o plan de subida)."""
        self._cancel_analisis = True
        return {"ok": True}

    def analisis_reset(self) -> dict:
        """Limpia la bandera de cancelación antes de un análisis nuevo."""
        self._cancel_analisis = False
        return {"ok": True}

    # ---- aceleración gráfica (ver atom_core/render_state.py) ----------------
    def render_estado(self) -> dict:
        """Estado actual de la aceleración gráfica, para la pantalla de Ajustes."""
        estado = render_state.leer()
        flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        estado["activa"] = "--disable-gpu" not in flags
        return estado

    def render_confirmar(self) -> dict:
        """Lo llama el frontend en cuanto ha pintado su primer frame: el arranque
        con GPU fue bueno, así que se limpia el marcador `pendiente` (si no, el
        siguiente arranque lo interpretaría como pantalla negra y degradaría)."""
        try:
            render_state.guardar(render_state.confirmar_render(render_state.leer()))
        except Exception:  # noqa: BLE001 — nunca debe romper el arranque
            pass
        return {"ok": True}

    def render_set_modo(self, modo: str) -> dict:
        """Cambia el modo (auto|gpu|software). Surte efecto al reiniciar la app."""
        try:
            render_state.guardar(render_state.set_modo(render_state.leer(), modo))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "reiniciar": True}

    # ---- configuración persistente (ruta ThermoViewer + % recorte por dron) -
    def read_config(self) -> dict:
        """Lee la config editable de usuario. Devuelve
        {ruta_thermoviewer: str, percentage_by_models: {MODELO: int}}.
        Ruta persistente (NO _MEIPASS efímero del onefile), ver external_tools."""
        try:
            from external_tools import load_config_or_default, _user_config_path
            return load_config_or_default(_user_config_path())
        except Exception as exc:  # noqa: BLE001 — se reenvía al front
            return {"error": f"{type(exc).__name__}: {exc}",
                    "ruta_thermoviewer": "", "percentage_by_models": {}}

    def write_config(self, data: dict) -> dict:
        """Reescribe Config.ini completo (mismo comportamiento que la ConfigWindow
        del Qt: reescritura total, no merge) en la ruta persistente. La próxima
        corrida del pipeline lo relee al construir su config_obj.
        data = {ruta_thermoviewer: str, percentage_by_models: {MODELO: int|str}}."""
        try:
            import configparser
            from external_tools import _user_config_path
            cfg = configparser.ConfigParser()
            cfg.optionxform = str  # no forzar minúsculas en las claves de modelo
            cfg["paths"] = {"ruta_thermoviewer": str(data.get("ruta_thermoviewer", "") or "")}
            pbm = {str(k).upper(): str(v) for k, v in (data.get("percentage_by_models") or {}).items()}
            if pbm:
                cfg["percentage_by_models"] = pbm
            path = _user_config_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                cfg.write(f)
            return {"ok": True, "path": path}
        except Exception as exc:  # noqa: BLE001 — se reenvía al front
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # ---- actualizaciones ---------------------------------------------------
    # Patrón del atom-migrador: comprobar al arrancar, avisar en un modal y, si
    # el usuario acepta, descargar el instalador y lanzarlo en silencio. Aquí no
    # hay electron-updater: la lógica vive en atom_core/updater.py.
    def app_version(self) -> dict:
        from atom_core import updater

        return {"version": updater.current_version(), "platform": platform.system()}

    def check_update(self) -> dict:
        from atom_core import updater

        res = updater.check()
        # Sólo se descarga lo que `check()` anunció, y sólo si es del repo propio.
        self._update_asset_url = None
        url = (res or {}).get("asset_url")
        if url:
            try:
                updater.validate_asset_url(url)
                self._update_asset_url = url
            except updater.UpdateSecurityError as exc:
                res = dict(res, asset_url=None, error=str(exc))
        return res

    def download_update(self, url: str = "", size: int = 0) -> dict:
        """Descarga en un hilo; el progreso llega a JS como `atom:update`.

        `url` viene del JS y NO se usa para descargar: se descarga únicamente el
        `asset_url` que devolvió `check_update`. Si difiere, error visible."""
        permitido = getattr(self, "_update_asset_url", None)
        if not permitido:
            msg = "No hay una actualización verificada: vuelve a comprobar las actualizaciones."
            self._push_update({"kind": "error", "text": msg})
            return {"started": False, "reason": msg}
        if url and url != permitido:
            msg = "La URL de descarga no coincide con la de la release anunciada. Descarga cancelada."
            self._push_update({"kind": "error", "text": msg})
            return {"started": False, "reason": msg}
        url = permitido
        if self._downloading:
            return {"started": False, "reason": "Ya se está descargando."}
        self._downloading = True

        def worker() -> None:
            from atom_core import updater

            last = -1

            def progress(pct: int, done: int, total: int) -> None:
                # No inundar el bridge: sólo cuando cambia el entero de %.
                nonlocal last
                if pct != last:
                    last = pct
                    self._push_update({"kind": "progress", "value": pct,
                                       "done": done, "total": total})

            res = updater.download(url, size, progress)
            self._downloading = False
            self._update_path = res.get("path") if res.get("ok") else None
            self._update_sha256 = res.get("sha256") if res.get("ok") else None
            self._push_update({"kind": "downloaded" if res.get("ok") else "error",
                               "path": res.get("path"), "text": res.get("error")})

        threading.Thread(target=worker, daemon=True).start()
        return {"started": True}

    def install_update(self, path: str | None = None) -> dict:
        """Lanza el instalador silencioso. Él cierra esta instancia
        (/FORCECLOSEAPPLICATIONS) y la reabre al terminar (entrada [Run] del .iss).

        Si el instalador falla y esta instancia sigue viva, el aviso llega a la UI
        por el mismo canal `atom:update`: sin esto el modal se quedaba en
        «Instalando…» para siempre (el Popen es DETACHED y nadie miraba el código)."""
        from atom_core import updater

        def on_failure(code: int, msg: str) -> None:
            self._push_update({"kind": "error", "text": f"No se pudo instalar: {msg}"})

        # `path` (del JS) se ignora: sólo el fichero descargado y verificado.
        if not self._update_path or not getattr(self, "_update_sha256", None):
            msg = "No hay un instalador verificado. Vuelve a descargar la actualización."
            self._push_update({"kind": "error", "text": msg})
            return {"ok": False, "error": msg}
        res = updater.install(self._update_path, on_failure=on_failure,
                              expected_sha256=self._update_sha256)
        if not res.get("ok"):
            self._push_update({"kind": "error", "text": res.get("error")})
        return res

    def _push_update(self, detail: dict) -> None:
        if not self._sink:
            return
        self._sink.dispatch("atom:update", detail)

    def start_update_check(self, delay: float = 3.0,
                           intervalo: float = INTERVALO_CHEQUEO_UPDATE) -> None:
        """Chequeo automático diferido tras el arranque (como el migrador: 3 s),
        para no competir con la carga de la UI, y REPETIDO cada `intervalo`.

        La repetición no es un lujo: hasta la v3.4.76 esto era un disparo único, así
        que quien dejaba la app abierta (lo normal en una jornada de organizado) no
        se enteraba jamás de una versión publicada esa misma mañana — había que
        cerrar y reabrir. El intervalo es largo a propósito: es una llamada HTTP a
        GitHub, que aplica rate limit por IP a las peticiones sin token.

        Silencioso si no hay novedad o si no hay red; el resultado crudo del último
        intento queda en `self._ultimo_chequeo` para poder verlo desde Ajustes."""
        def worker() -> None:
            time.sleep(delay)
            avisado: str | None = None   # versión ya empujada, para no repetir el aviso
            while True:
                try:
                    res = self.check_update()
                except Exception as exc:  # noqa: BLE001 — nunca romper el arranque
                    res = {"ok": False, "error": str(exc)}
                self._ultimo_chequeo = {**res, "cuando": time.time()}
                if res.get("ok") and res.get("update_available"):
                    latest = res.get("latest")
                    # Un solo aviso por versión: el modal ya está montado y el
                    # usuario decide; re-empujarlo cada media hora sería acoso.
                    if latest != avisado:
                        avisado = latest
                        detail = {"kind": "available", "data": res}
                        self._ultimo_update = detail
                        self._push_update(detail)
                if not intervalo or intervalo <= 0:
                    return          # intervalo 0 → disparo único (tests)
                time.sleep(intervalo)

        threading.Thread(target=worker, daemon=True).start()

    def estado_update(self) -> dict:
        """Resultado del último chequeo de actualización, para el botón de Ajustes.

        Devuelve `{pendiente: True}` si aún no ha corrido ninguno (los primeros
        segundos tras arrancar), en vez de mentir diciendo que está al día."""
        if self._ultimo_chequeo is None:
            return {"pendiente": True}
        return self._ultimo_chequeo

    def get_ultimo_update(self) -> dict | None:
        """Último aviso de actualización disponible, o None si no hay ninguno.

        Para el modal que se monta tarde (tras el login): el chequeo automático
        ya pudo haber pasado y disparado `atom:update` al vacío."""
        return self._ultimo_update

    # ---- subida al bucket «datos para organizar» ---------------------------
    # Cuenta de Google del operador + IAM del bucket. La app no lleva ninguna
    # credencial de servicio: quién puede subir se decide fuera, en el IAM.
    def _get_auth(self, *, solo_password: bool = False):
        """`GoogleAuth` cacheado, o None si no hay cliente OAuth configurado.

        Sin `google_client.json` el modo password (que no usa OAuth) sigue
        siendo posible: se construye una instancia `sin_cliente` que solo se
        devuelve si `solo_password` o si ya hay una sesión password activa. El
        login Google sigue viendo None (ayuda de "falta el cliente")."""
        if self._auth is not None:
            if (getattr(self._auth, "sin_cliente", False) and not solo_password
                    and not self._auth.es_password):
                return None
            return self._auth
        from atom_core import cloud_config
        from atom_core.google_auth import GoogleAuth

        client = cloud_config.load_client(ROOT)
        if client is None:
            if not self._broker:
                # Escritorio sin `google_client.json`: la UI ofrece el mensaje
                # de "falta el cliente OAuth", salvo para el modo password.
                auth = GoogleAuth("", "", sin_cliente=True,
                                  hosted_domain=cloud_config.HOSTED_DOMAIN)
                self._auth = auth
                if solo_password or auth.es_password:
                    return auth
                return None
            # Raspberry Pi: nunca va a tener `google_client.json` (ese es
            # justo el punto del broker), así que la ausencia de cliente aquí
            # no es un error, es el caso normal. Se construye sin credenciales
            # propias; `login()` en esta instancia falla explicando que hay
            # que emparejar por QR.
            self._auth = GoogleAuth("", "", broker_only=True,
                                    hosted_domain=cloud_config.HOSTED_DOMAIN)
            return self._auth
        self._auth = GoogleAuth(client.client_id, client.client_secret,
                                hosted_domain=cloud_config.HOSTED_DOMAIN)
        return self._auth

    def cloud_status(self) -> dict:
        from atom_core import cloud_config

        auth = self._get_auth()
        if auth is None:
            return {"ok": True, "configured": False, "logged_in": False,
                    "bucket": cloud_config.BUCKET_DATOS,
                    "help": cloud_config.missing_client_help(),
                    "pairing": False,
                    "estado": ESTADO_SIN_CREDENCIAL,
                    "estado_mensaje": self._credencial.actual()["mensaje"],
                    "pendientes": len(cola_subidas.pendientes())}
        ident = auth.identity
        return {"ok": True, "configured": True,
                "logged_in": auth.is_logged_in(),
                "email": ident.email if ident else None,
                "picture": ident.picture if ident else None,
                "nombre": ident.nombre if ident else None,
                # Lo que se sabe SIN preguntar a Google: si la sesión sigue
                # viva se comprueba aparte (`cloud_verify`), porque eso es una
                # llamada de red y el estado inicial no puede esperarla.
                "validada_en": auth.validada_en,
                "aviso": auth.aviso_store,
                "bucket": cloud_config.BUCKET_DATOS,
                "uploading": self._uploading,
                # Le dice a la UI que enseñe la pantalla de QR en vez del botón
                # "Iniciar sesión con Google": este equipo no tiene cliente
                # OAuth propio (ver `_get_auth`), solo puede emparejarse.
                "pairing": bool(getattr(auth, "broker_only", False)),
                # `{organizer, estadillos}` en modo password; `None` = todo
                # visible (Google/broker o sesión sin dato).
                "acceso_modulos": getattr(auth, "acceso_modulos", None),
                "estado": self._credencial.actual()["estado"],
                "estado_mensaje": self._credencial.actual()["mensaje"],
                "pendientes": len(cola_subidas.pendientes())}

    def cloud_comprobar(self, profunda: bool = False) -> dict:
        """Comprueba de verdad si la credencial sirve, y cachea el resultado.

        Síncrona a propósito: la llaman el arranque y el paso previo a cada
        acción, que necesitan la respuesta antes de seguir. `cloud_verify`
        sigue existiendo para la comprobación manual, que va por evento.

        `profunda` está para el latido de 6 h; hoy ambas rutas usan
        `verificar()`, que ya pasa por el broker de la Suite.
        """
        auth = self._get_auth()
        if auth is None or not auth.is_logged_in():
            # Sin token local no hay nada que preguntar: hay que emparejar.
            self._credencial.registrar(ESTADO_SIN_CREDENCIAL, "No hay dispositivo emparejado.")
            return self._credencial.actual()
        try:
            valida, texto = auth.verificar()
            estado = clasificar(valida, texto, hubo_red=True)
            logger.info("cloud_comprobar: estado=%s valida=%s", estado, valida)
            self._credencial.registrar(estado, texto)
        except AuthError as exc:
            # El backend contestó y dijo que no: revocado o token inválido.
            logger.warning("cloud_comprobar: AuthError (%s): %s", type(exc).__name__, exc)
            self._credencial.registrar(ESTADO_SIN_CREDENCIAL, str(exc))
        except OSError as exc:
            # No se llegó a hablar con el backend: no acuses a la credencial.
            logger.warning("cloud_comprobar: OSError (%s): %s", type(exc).__name__, exc)
            self._credencial.registrar(ESTADO_SIN_CONEXION, str(exc))
        except Exception as exc:
            # Organizar es local y NUNCA puede caerse por un fallo inesperado aquí.
            logger.warning("cloud_comprobar: excepción inesperada (%s): %s", type(exc).__name__, exc)
            self._credencial.registrar(ESTADO_SIN_CONEXION, str(exc))
        return self._credencial.actual()

    def cloud_asegurar_estado(self) -> dict:
        """Estado de la credencial, recomprobando solo si toca.

        Se llama antes de cada acción. La Pi está normalmente apagada, así que
        en vez de sondear en bucle se comprueba al arrancar y, si sigue
        encendida, como mucho una vez cada 6 h.
        """
        if self._credencial.necesita_comprobar():
            return self.cloud_comprobar()
        return self._credencial.actual()

    def cloud_verify(self) -> dict:
        """Comprueba contra Google que la sesión guardada sigue sirviendo.

        Va por hilo y contesta con un evento `atom:cloud` (`kind: 'session'`):
        un refresh puede tardar segundos con mala red y bloquear el bridge
        dejaría la ventana congelada en el arranque.
        """
        auth = self._get_auth()
        if auth is None or not auth.is_logged_in():
            return {"started": False, "logged_in": False}
        if self._verifying:
            return {"started": False, "reason": "Ya se está comprobando."}
        self._verifying = True

        def worker() -> None:
            try:
                valida, texto = auth.verificar()
                ident = auth.identity
                self._push_cloud({"kind": "session", "ok": valida, "text": texto,
                                  "email": ident.email if ident else None,
                                  "validada_en": auth.validada_en})
            except Exception as exc:  # noqa: BLE001 - se enseña, no se traga
                self._push_cloud({"kind": "session", "ok": False, "text": str(exc)})
            finally:
                self._verifying = False

        threading.Thread(target=worker, daemon=True).start()
        return {"started": True}

    @staticmethod
    def _prefijo_token(prefix: str) -> str:
        """Prefijo de inspección (`EMPRESA--PLANTA--AÑO--TIPO/`) con el que se
        pide el token de GCS en modo password: primer segmento del destino."""
        return (prefix or "").strip("/").split("/")[0] + "/"

    def _id_inspeccion(self, prefix: str, inspeccion_id: int | None = None,
                       auth=None) -> int | None:
        """Id de la inspección de `prefix`: el explícito o el del último
        catálogo cargado (`cloud_inspecciones`). En modo password, sin id se
        lanza un error explícito (la Suite responde 400 sin él): nunca se deja
        que un fallo acabe como listado vacío."""
        if inspeccion_id is None:
            clave = (prefix or "").strip("/").split("/")[0]
            inspeccion_id = getattr(self, "_ids_inspeccion", {}).get(clave)
        if inspeccion_id is None:
            auth = auth or self._auth
            if getattr(auth, "es_password", False):
                from atom_core.google_auth import AuthError
                raise AuthError(
                    "Falta el id de la inspección para acceder al bucket: "
                    "elige la inspección en la lista.")
        return inspeccion_id

    def _estadillos_acceso(self, folder: str, auth=None) -> tuple[str, dict]:
        """`(raiz, kw_token)` de estadillos de la inspección `folder` (prefijo).

        Google/broker: raíz canónica `<PLANTA>/ESTADILLOS` y token por
        `token_prefix`. Password: la raíz la decide la Suite (token por
        `{planta_id, inspeccion_id, ambito:'estadillos'}`); `planta_id` e
        `inspeccion_id` salen del catálogo y si falta alguno es un error
        visible, sin fallback."""
        from atom_core import estadillo_canonico

        auth = auth or self._get_auth()
        if not getattr(auth, "es_password", False):
            raiz = estadillo_canonico.prefijo_planta(folder)
            return raiz, {"token_prefix": f"{raiz}/"}
        from atom_core.google_auth import AuthError
        clave = (folder or "").strip("/").split("/")[0]
        planta_id = getattr(self, "_plantas_inspeccion", {}).get(clave)
        if planta_id is None:
            raise AuthError(
                "Falta el id de la planta para acceder a los estadillos: "
                "recarga la lista de inspecciones y elige la inspección.")
        # Lanza AuthError explícito en password si la inspección no se conoce.
        inspeccion_id = self._id_inspeccion(folder, auth=auth)
        raiz = auth.prefijo_estadillos(planta_id, inspeccion_id).strip("/")
        return raiz, {"planta_id": planta_id, "inspeccion_id": inspeccion_id,
                      "ambito": "estadillos"}

    def cloud_login_password(self, usuario: str, password: str) -> dict:
        """Entra con usuario y contraseña de ATOM Suite (modo password).

        Síncrono: es un POST corto, sin navegador. Devuelve
        `{"ok": True, "email", "nombre", "domain"}` o `{"ok": False,
        "error": <mensaje legible>}`. La contraseña no se loguea ni se
        devuelve nunca.
        """
        from atom_core import cloud_config
        from atom_core.google_auth import AuthError

        auth = self._get_auth(solo_password=True)
        if auth is None:
            return {"ok": False, "error": cloud_config.missing_client_help()}
        if getattr(auth, "broker_only", False):
            return {"ok": False,
                    "error": "Este equipo se empareja por QR desde ATOM Suite, "
                             "no con usuario y contraseña."}
        try:
            ident = auth.login_password(usuario, password)
        except AuthError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - contrato: nunca reventar el IPC
            return {"ok": False, "error": f"No se pudo iniciar sesión: {exc}"}
        self._push_cloud({"kind": "login", "ok": True, "email": ident.email})
        return {"ok": True, "email": ident.email, "nombre": ident.nombre,
                "domain": ident.domain}

    def cloud_login(self) -> dict:
        """Abre el navegador para el consentimiento. Devuelve al instante; el
        resultado llega como evento `atom:cloud` (el consentimiento puede tardar
        minutos y bloquear el bridge dejaría la ventana congelada)."""
        auth = self._get_auth()
        if auth is None:
            from atom_core import cloud_config

            return {"started": False, "reason": cloud_config.missing_client_help()}
        if getattr(auth, "broker_only", False):
            # Aquí no hay navegador de sistema que sirva de nada (kiosco sin
            # teclado): el único camino es `cloud_pair_start`/`cloud_pair_poll`.
            return {"ok": False,
                    "error": "Este equipo se empareja por QR desde ATOM Suite, "
                             "no con «Iniciar sesión con Google»."}
        if self._logging_in:
            return {"started": False, "reason": "Ya hay un login en curso."}
        self._logging_in = True

        def worker() -> None:
            try:
                ident = auth.login()
                self._push_cloud({"kind": "login", "ok": True,
                                  "email": ident.email if ident else None})
            except Exception as exc:  # noqa: BLE001 - se enseña, no se traga
                self._push_cloud({"kind": "login", "ok": False, "text": str(exc)})
            finally:
                self._logging_in = False

        threading.Thread(target=worker, daemon=True).start()
        return {"started": True}

    def cloud_logout(self) -> dict:
        auth = self._get_auth()
        if auth is None:
            return {"ok": False, "error": "No hay sesión."}
        try:
            auth.logout()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        # El catálogo es de la sesión que se cierra: no reutilizarlo con otro usuario.
        self._catalogo_cache = None
        # Sin esto el estado cacheado se queda en `ok` hasta el siguiente
        # latido (6 h): la UI seguiria sin avisar de que ya no hay sesion.
        self._credencial.invalidar("Se cerro la sesion en este equipo.")
        self._olvidar_pin()
        return {"ok": True}

    # ---- catálogo de perfiles (pantalla de entrada tipo Netflix) -----------
    # No confundir con `_get_auth`/`_credencial`, que hablan de LA sesión
    # activa: esto es la lista de cuentas recordadas en este equipo, para
    # poder cambiar de una a otra sin repetir el consentimiento de Google.
    def _store_perfiles(self):
        """SessionStore propio de los perfiles. Perezoso: los tests lo sustituyen."""
        if self._perfiles_store is None:
            from atom_core.google_auth import STORE_NAME, user_data_dir
            from atom_core.session_store import SessionStore

            self._perfiles_store = SessionStore(user_data_dir() / STORE_NAME)
        return self._perfiles_store

    def listar_perfiles(self) -> list[dict]:
        """Perfiles guardados para la pantalla de entrada, con el avatar ya
        resuelto a `data:` URI.

        La foto no puede ser la URL cruda de Google: esta pantalla se pinta
        ANTES de que exista sesión activa (no hay token con el que reintentar
        un 429) y el WebView de Windows falla contra `googleusercontent.com`
        con cierta frecuencia. Por eso se pasa por `avatar_cache`, que cachea
        en disco y nunca lanza. Todo el método va envuelto: un fallo aquí no
        puede dejar la pantalla de entrada en negro.
        """
        try:
            from atom_core import avatar_cache
            from atom_core.google_auth import user_data_dir

            dir_datos = user_data_dir()
            perfiles = self._store_perfiles().listar_perfiles()
            resultado = []
            for p in perfiles:
                picture = (avatar_cache.obtener(p.picture, p.email, dir_datos)
                           if p.picture else "")
                resultado.append({
                    "email": p.email,
                    "nombre": p.nombre,
                    "picture": picture,
                    "modo": p.modo,
                    "tiene_credencial": p.tiene_credencial,
                })
            # Purga avatares de perfiles ya borrados: sin esto la caché crece
            # sin límite con cada cuenta que alguna vez inició sesión aquí.
            avatar_cache.limpiar(dir_datos, {p.email for p in perfiles})
            return resultado
        except Exception as exc:  # noqa: BLE001 - nunca tumbar la pantalla de entrada
            logger.warning("listar_perfiles: no se pudo leer el catálogo: %s", exc)
            return []

    def activar_perfil(self, email: str) -> dict:
        """Convierte un perfil del catálogo en la sesión activa."""
        try:
            ok = self._store_perfiles().activar_perfil(email)
        except Exception as exc:  # noqa: BLE001
            logger.warning("activar_perfil: fallo al activar %s: %s", email, exc)
            return {"ok": False}
        if ok:
            # `_get_auth` cachea el `GoogleAuth` en `self._auth`: si no se
            # invalida aquí, la app seguiría operando con la credencial de la
            # cuenta anterior hasta reiniciar, aunque la BD ya apunte a otra.
            self._auth = None
        return {"ok": ok}

    def borrar_perfil(self, email: str) -> None:
        """Quita un perfil del catálogo (y su sesión activa, si lo era)."""
        try:
            self._store_perfiles().borrar_perfil(email)
        except Exception as exc:  # noqa: BLE001
            logger.warning("borrar_perfil: fallo al borrar %s: %s", email, exc)
            return
        # `borrar_perfil` del store ya borra la sesión activa si coincide;
        # invalidamos el `GoogleAuth` cacheado por la misma razón que en
        # `activar_perfil`, para no seguir operando con una credencial que
        # el catálogo ya no reconoce.
        self._auth = None

    def _store_pin(self):
        """SessionStore propio del PIN. Perezoso: los tests lo sustituyen."""
        if self._pin_store is None:
            from atom_core.google_auth import STORE_NAME, user_data_dir
            from atom_core.session_store import SessionStore

            self._pin_store = SessionStore(user_data_dir() / STORE_NAME)
        return self._pin_store

    def pin_estado(self) -> dict:
        try:
            hay = pin_kiosco.hay_pin(self._store_pin())
        except Exception as exc:  # noqa: BLE001 - un store roto no bloquea la Pi
            logger.warning("[pin] No se pudo leer el PIN del kiosco: %s", exc)
            hay = False
        return {
            "ok": True,
            "hay_pin": hay,
            "bloqueado": self._pin_intentos.bloqueado(),
            "espera_segundos": self._pin_intentos.espera_segundos(),
        }

    def pin_fijar(self, nuevo: str) -> dict:
        """Alta INICIAL del PIN. Si ya hay uno, hay que pasar por `pin_cambiar`.

        Sin esta guarda, cualquiera con acceso al Chromium del kiosco -- que
        es justo el actor del que protege el PIN -- reescribe el PIN vigente
        sin conocerlo y sin pasar por el bloqueo escalado.
        """
        if self._pin_intentos.bloqueado():
            return {
                "ok": False,
                "error": "Demasiados intentos.",
                "espera_segundos": self._pin_intentos.espera_segundos(),
            }
        try:
            store = self._store_pin()
            if pin_kiosco.hay_pin(store):
                return {"ok": False, "error": "Ya hay un PIN: usa cambiar."}
            pin_kiosco.fijar(store, nuevo)
        except pin_kiosco.PinInvalido as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[pin] No se pudo fijar el PIN del kiosco: %s", exc)
            return {"ok": False, "error": "No se pudo guardar el PIN."}
        self._pin_intentos.acierto()
        return {"ok": True}

    def pin_verificar(self, pin: str) -> dict:
        # Nunca los digitos del PIN: solo longitud recibida y numero de
        # intento consecutivo (0-based en `_fallos`, de ahi el +1), que es
        # lo unico util para depurar el pad del kiosco sin exponer el PIN.
        longitud = len(pin) if isinstance(pin, str) else -1
        n_intento = self._pin_intentos._fallos + 1
        if self._pin_intentos.bloqueado():
            logger.warning(
                "[pin] verificar bloqueado: longitud=%s intento=%s espera=%ss",
                longitud, n_intento, self._pin_intentos.espera_segundos(),
            )
            return {
                "ok": False,
                "error": "Demasiados intentos.",
                "espera_segundos": self._pin_intentos.espera_segundos(),
            }
        try:
            correcto = pin_kiosco.verificar(self._store_pin(), pin)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[pin] verificar error: longitud=%s intento=%s", longitud, n_intento)
            return {"ok": False, "error": str(exc)}
        if correcto:
            logger.info("[pin] verificar OK: longitud=%s intento=%s", longitud, n_intento)
            self._pin_intentos.acierto()
            return {"ok": True}
        self._pin_intentos.fallo()
        logger.warning("[pin] verificar fallo: longitud=%s intento=%s", longitud, n_intento)
        return {
            "ok": False,
            "error": "PIN incorrecto.",
            "espera_segundos": self._pin_intentos.espera_segundos(),
        }

    def pin_cambiar(self, actual: str, nuevo: str) -> dict:
        if self._pin_intentos.bloqueado():
            return {
                "ok": False,
                "error": "Demasiados intentos.",
                "espera_segundos": self._pin_intentos.espera_segundos(),
            }
        try:
            pin_kiosco._validar(nuevo)
        except pin_kiosco.PinInvalido as exc:
            return {"ok": False, "error": str(exc)}
        try:
            cambiado = pin_kiosco.cambiar(self._store_pin(), actual, nuevo)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        if not cambiado:
            self._pin_intentos.fallo()
            return {
                "ok": False,
                "error": "El PIN actual no es correcto.",
                "espera_segundos": self._pin_intentos.espera_segundos(),
            }
        self._pin_intentos.acierto()
        return {"ok": True}

    def pin_telemetria(self, datos: dict) -> dict:
        """Telemetria de un intento COMPLETO de PIN (KioskLock.jsx), para
        saber en campo si reaparece el fallo "PIN correcto falla al primer
        intento". Fire-and-forget desde el front: cualquier fallo aqui se
        traga, nunca debe tumbar el kiosco.

        SEGURIDAD: nunca llegan ni se guardan los digitos del PIN tecleado
        ni su longitud/composicion, solo timing y contadores. Se descarta
        cualquier clave no esperada y cualquier valor con tipo incorrecto.
        """
        if not isinstance(datos, dict):
            return {"ok": False}
        tipos_esperados = {
            "ts": str,
            "ok": bool,
            "n_intento": int,
            "toques_aceptados": int,
            "toques_descartados_debounce": int,
            "toques_descartados_arrastre": int,
            "borrados": int,
            "intervalos_ms": list,
            "duracion_total_ms": (int, float),
        }
        limpio: dict = {}
        for clave, tipo in tipos_esperados.items():
            if clave not in datos:
                continue
            valor = datos[clave]
            # bool es subclase de int: se comprueba aparte para no colar un
            # 0/1 como si fuera el booleano `ok`.
            if tipo is bool and not isinstance(valor, bool):
                continue
            if tipo is not bool and isinstance(valor, bool):
                continue
            if not isinstance(valor, tipo):
                continue
            if clave == "intervalos_ms":
                if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in valor):
                    continue
                valor = [float(v) for v in valor]
            limpio[clave] = valor
        if "ts" not in limpio or "ok" not in limpio:
            return {"ok": False}
        limpio["ts_servidor"] = datetime.datetime.now(_TZ_ESTADILLO).isoformat()
        try:
            from external_tools import user_log_dir
            carpeta = user_log_dir()
            os.makedirs(carpeta, exist_ok=True)
            ruta = os.path.join(carpeta, _LOG_TELEMETRIA_PIN)
            try:
                if os.path.exists(ruta) and os.path.getsize(ruta) > _LIMITE_TELEMETRIA_PIN:
                    rotado = ruta + ".1"
                    if os.path.exists(rotado):
                        os.remove(rotado)
                    os.replace(ruta, rotado)
            except OSError:
                pass  # rotacion best-effort: nunca bloquea el registro
            with open(ruta, "a", encoding="utf-8") as f:
                f.write(json.dumps(limpio, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001 - nunca tumbar el kiosco
            logger.warning("[pin] No se pudo escribir la telemetria: %s", exc)
            return {"ok": False}
        return {"ok": True}

    def _olvidar_pin(self) -> None:
        """Desemparejar resetea el PIN: es la via de recuperacion acordada."""
        logger.warning("_olvidar_pin: se borra el PIN del kiosco")
        try:
            pin_kiosco.borrar(self._store_pin())
        except Exception as exc:  # noqa: BLE001
            logger.warning("[pin] No se pudo borrar el PIN del kiosco: %s", exc)
        self._pin_intentos.acierto()

    # ---- emparejamiento por QR (modo broker, Raspberry Pi) -----------------
    # La Pi no puede abrir el navegador del sistema en su propia pantalla como
    # hace `cloud_login` (o sí puede, pero no tiene sentido: es un kiosco sin
    # teclado). En vez de eso, el responsable escanea un QR con el móvil y consiente
    # ahí; la Pi solo pregunta a la Suite si ya terminó (`cloud_pair_poll`).
    def cloud_pair_start(self) -> dict:
        """Pide a la Suite un `pair_id` nuevo y la URL para el QR.

        Síncrono: es una sola petición HTTP rápida, no hay progreso que
        empujar por eventos (a diferencia de `cloud_login`, que espera minutos
        el consentimiento en el navegador)."""
        from atom_core.google_auth import SUITE_URL
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            f"{SUITE_URL}/api/organizer/pair/start", method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            return {"ok": False, "error": f"La Suite devolvió {exc.code}."}
        except Exception as exc:  # noqa: BLE001 - se enseña, no se traga
            return {"ok": False, "error": str(exc)}

    def cloud_pair_poll(self, pair_id: str) -> dict:
        """Pregunta a la Suite si `pair_id` ya se emparejó.

        La UI repite esta llamada mientras enseña el QR (por eso es síncrono y
        no un hilo con evento). En cuanto la Suite dice "listo" con un
        `device_token`, se completa la sesión local vía `auth.pair()` y se
        emite el mismo evento que `cloud_login` para que el resto de la UI
        reaccione igual sin distinguir de dónde vino el login."""
        from atom_core.google_auth import SUITE_URL
        import urllib.error
        import urllib.parse
        import urllib.request

        url = f"{SUITE_URL}/api/organizer/pair/poll?" + urllib.parse.urlencode(
            {"pair_id": pair_id})
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                datos = json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            return {"ok": False, "error": f"La Suite devolvió {exc.code}."}
        except Exception as exc:  # noqa: BLE001 - se enseña, no se traga
            return {"ok": False, "error": str(exc)}

        if datos.get("estado") == "listo" and datos.get("device_token"):
            auth = self._get_auth()
            if auth is None:
                from atom_core import cloud_config

                return {"ok": False, "error": cloud_config.missing_client_help()}
            try:
                ident = auth.pair(
                    datos["device_token"],
                    datos.get("email", ""),
                    datos.get("picture", ""),
                    datos.get("nombre", ""),
                )
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}
            # Emparejar ES la comprobacion: la Suite acaba de validar el
            # device_token. Sin registrarlo, el estado cacheado se quedaba en
            # `sin-credencial` y la UI seguia bloqueada tras emparejar.
            self._credencial.registrar(ESTADO_OK, "Dispositivo emparejado.")
            self._olvidar_pin()
            # Mismo evento que `cloud_login`: la UI no necesita saber si el
            # login vino del navegador de escritorio o de un QR emparejado.
            self._push_cloud({"kind": "login", "ok": True,
                              "email": ident.email if ident else None})
        return datos

    def cloud_inspecciones(self) -> dict:
        """Catálogo de inspecciones para el desplegable.

        Sale de la BD de Aerotools, vía la API de ATOM Suite y con la sesión
        del operador — la app no habla con la BD directamente (ver
        `atom_core/inspecciones.py`). Esa es la única fuente: sin sesión o sin
        API no hay lista, y se dice. No se sirve una copia local vieja, porque
        de esta lista sale el destino de la subida.
        """
        from atom_core import cloud_config, inspecciones

        auth = self._get_auth()
        if auth is None or not auth.is_logged_in():
            return {"ok": False, "inspecciones": [], "origen": "api",
                    "bajado_en": 0.0,
                    "error": "Inicia sesión para ver las inspecciones."}
        cat = inspecciones.cargar_catalogo(cloud_config.BUCKET_DATOS, auth)
        if not hasattr(self, "_ids_inspeccion"):
            self._ids_inspeccion = {}
        for it in cat.get("inspecciones") or []:
            if it.get("id") is not None and it.get("prefijo"):
                self._ids_inspeccion[it["prefijo"]] = it["id"]
        if not hasattr(self, "_plantas_inspeccion"):
            self._plantas_inspeccion = {}
        for it in cat.get("inspecciones") or []:
            if it.get("planta_id") is not None and it.get("prefijo"):
                self._plantas_inspeccion[it["prefijo"]] = it["planta_id"]
        if cat.get("ok"):
            self._catalogo_cache = cat
        return cat

    def inspeccion_sugerir(self, carpeta: str, estadillo_path: "str | list" = "") -> dict:
        """Sugiere la inspección según carpeta del vuelo y estadillo.

        Devuelve `{estado: unica|varias|ninguna|conflicto, prefijo?, candidatos}`
        o `{estado: 'error', mensaje}`. Reutiliza el catálogo ya descargado
        (`_catalogo_cache`, lo rellena `cloud_inspecciones`); si no hay, lo pide
        igual que `cloud_inspecciones`.
        """
        try:
            from atom_core import inspecciones

            auth = self._get_auth()
            logueado = auth is not None and auth.is_logged_in()
            cat = getattr(self, "_catalogo_cache", None) if logueado else None
            if not cat or not cat.get("ok"):
                cat = self.cloud_inspecciones()
            if not cat.get("ok"):
                return {"estado": "error",
                        "mensaje": cat.get("error") or "No hay catálogo de inspecciones."}
            info = None
            if estadillo_path:
                info = self.read_estadillo_info(estadillo_path)
            return inspecciones.sugerir_inspeccion(
                carpeta, info, cat.get("inspecciones") or [])
        except Exception as exc:  # noqa: BLE001 — se reenvía al front
            return {"estado": "error", "mensaje": f"{type(exc).__name__}: {exc}"}

    def _destino(self, folder: str, prefix: str | None) -> tuple[Path | None, str, str]:
        """Carpeta y prefijo destino ya validados. Devuelve `(root, prefix, error)`.

        El prefijo lo manda la UI: es la inspección elegida. **No se cae al
        nombre de la carpeta si falta.** Ese era el mecanismo anterior y es
        justo el que se quita: dos «Nueva carpeta» de vuelos distintos
        aterrizaban en el mismo prefijo y se pisaban. Sin inspección no hay
        destino, y la app lo dice en vez de inventárselo.
        """
        from atom_core import cloud_config

        root = Path(folder or "")
        if not root.is_dir():
            return None, "", "Esa carpeta no existe."

        limpio = cloud_config.prefijo_desde_carpeta((prefix or "").strip())
        if not limpio:
            return None, "", ("Elige una inspección: sin ella no hay destino "
                              "válido dentro del bucket.")
        return root, limpio, ""

    # ---- inventario del destino, en background ---------------------------
    # Cuánto vale un inventario ya calculado. Diez minutos son de sobra para
    # que el operario lea el estadillo y elija inspección, y lo que se cuele en
    # el bucket mientras tanto no rompe nada: `upload_file` manda la
    # precondición `ifGenerationMatch=0` y GCS corta con 412 sin gastar bytes
    # (ver `cloud_upload.YaExiste`). Esa red de seguridad es lo que permite
    # fiarse de una foto del bucket ligeramente vieja.
    INV_TTL = 600.0

    def _inventario_cacheado(self, prefix: str) -> dict | None:
        """El inventario de `prefix` si lo hay y sigue fresco."""
        with self._inv_lock:
            inv = self._inv.get(prefix)
        if inv is None:
            return None
        if time.monotonic() - inv["t"] > self.INV_TTL:
            return None
        return inv["remotos"]

    def _verificar_lote_completo(self, plan, prefix_lote: str, auth,
                                 inspeccion_id: int | None = None
                                 ) -> tuple[list[tuple[str, str]], bool]:
        """Cruza el plan contra un listado FRESCO del bucket.

        Devuelve `(faltantes, verificado)`. Un `UploadResult.ok` solo dice que
        ningun objeto lanzo una excepcion; esto dice que estan de verdad en el
        bucket y con su tamaño. Es lo que separa "la barra llego al 100 %" de
        "no se quedo nada por el camino", y por eso el manifest depende de
        esto y no solo de `ok`.

        `verificado=False` = no se pudo listar (red). NO es prueba de que
        falte nada, asi que el que llama no lo trata como fallo: solo pierde
        la garantia, y se lo dice a la UI en vez de callarselo.
        """
        from atom_core import cloud_config, cloud_upload

        try:
            remotos = cloud_upload.listar_objetos_remotos(
                cloud_config.BUCKET_DATOS, prefix_lote, auth,
                token_prefix=self._prefijo_token(prefix_lote),
                inspeccion_id=self._id_inspeccion(prefix_lote, inspeccion_id, auth))
        except Exception as exc:  # noqa: BLE001 - informativo, no bloquea
            if getattr(auth, "es_password", False):
                # En modo password el fallo NO es informativo: debe verse.
                raise
            self._log_subida("verificacion de %s: no se pudo listar (%s)",
                             prefix_lote, exc)
            return [], False

        faltantes: list[tuple[str, str]] = []
        for item in plan.items:
            remoto = remotos.get(item.remote)
            if remoto is None:
                faltantes.append((item.remote, "no esta en el bucket"))
            elif item.size >= 0 and remoto.size != item.size:
                faltantes.append((
                    item.remote,
                    f"tamaño remoto {remoto.size} != {item.size} local"))
        return faltantes, True

    def _inventario_precalentar(self, prefix: str) -> None:
        """Lanza (si no está ya) el listado del prefijo en un hilo.

        No devuelve nada: cuando termina empuja `kind: "inventario"` por
        `atom:cloud` para que la UI pinte los pendientes cuando los tenga, en
        vez de hacerla esperar antes de enseñar nada.
        """
        from atom_core import cloud_config, cloud_upload

        auth = self._get_auth()
        if auth is None or not auth.is_logged_in():
            return
        with self._inv_lock:
            if prefix in self._inv_hilos:
                return  # ya hay un hilo con este mismo prefijo
            inv = self._inv.get(prefix)
            if inv is not None and time.monotonic() - inv["t"] <= self.INV_TTL:
                return  # ya está calculado y fresco
            self._inv_hilos.add(prefix)

        def worker() -> None:
            try:
                t0 = time.monotonic()
                remotos = cloud_upload.listar_objetos_remotos(
                    cloud_config.BUCKET_DATOS, prefix, auth,
                    token_prefix=self._prefijo_token(prefix),
                    inspeccion_id=self._id_inspeccion(prefix, auth=auth))
            except Exception as exc:  # noqa: BLE001 - informativo, no bloquea
                self._log_subida("inventario de %s: no se pudo listar (%s)",
                                 prefix, exc)
                with self._inv_lock:
                    self._inv_hilos.discard(prefix)
                self._push_cloud({"kind": "inventario", "prefix": prefix,
                                  "ok": False, "error": str(exc)})
                return
            ahora = time.monotonic()
            with self._inv_lock:
                # Poda de caducados: si no, cada inspección del día deja su
                # listado (decenas de miles de rutas) retenido en memoria.
                self._inv = {p: v for p, v in self._inv.items()
                             if ahora - v["t"] <= self.INV_TTL}
                self._inv[prefix] = {"remotos": remotos, "t": ahora}
                self._inv_hilos.discard(prefix)
            self._push_cloud({"kind": "inventario", "prefix": prefix,
                              "ok": True, "existing": len(remotos),
                              "elapsed": round(time.monotonic() - t0, 1)})

        threading.Thread(target=worker, daemon=True).start()

    def _construir_plan(self, folder: str, prefix: str | None,
                        on_progress=None, should_stop=None) -> dict:
        """Cuerpo compartido de `cloud_prepare`/`cloud_prepare_start`.

        `on_progress`/`should_stop` son `None` en la llamada síncrona; la
        variante en hilo los usa para emitir avance y permitir cancelar.
        """
        from atom_core import cloud_config, cloud_upload

        root, prefix, error = self._destino(folder, prefix)
        if error:
            return {"ok": False, "error": error}
        try:
            plan = cloud_upload.build_plan(root, prefix, on_progress=on_progress,
                                           should_stop=should_stop)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

        out = {"ok": True, "prefix": prefix, "files": len(plan.items),
               "bytes": plan.total_bytes, "bucket": cloud_config.BUCKET_DATOS,
               "existing": None, "pendientes": None, "bytes_pendientes": None,
               "ya_subidos": None}
        if not plan.items:
            out["error"] = ("La carpeta no tiene ningún fichero subible "
                            "(imágenes, vídeos, CSV o estadillos).")
            out["ok"] = False
            return out

        out["inventario"] = "no"
        auth = self._get_auth()
        if auth is not None and auth.is_logged_in():
            remotos = self._inventario_cacheado(prefix)
            if remotos is None:
                out["inventario"] = "calculando"
                self._inventario_precalentar(prefix)
            else:
                out["inventario"] = "listo"
                pendientes, hechos = cloud_upload.reconciliar(plan, remotos)
                out["existing"] = len(remotos)
                out["ya_subidos"] = len(hechos)
                out["pendientes"] = len(pendientes)
                out["bytes_pendientes"] = sum(i.size for i in pendientes)
        return out

    def cloud_prepare(self, folder: str, prefix: str | None = None) -> dict:
        """Qué se subiría de verdad: total, lo que ya está y lo que falta.

        Cruza la carpeta con lo que ya hay en el destino, así que lo que
        enseña es el trabajo REAL pendiente, no el tamaño de la carpeta.

        NUNCA lista el bucket aquí: un prefijo con decenas de miles de objetos
        tarda ~20 s y esto se llama justo al elegir carpeta, con la pantalla
        esperando. El listado se lanza en background (`_inventario_precalentar`)
        y esta llamada devuelve al instante con `inventario: "calculando"`; la
        UI recibe `kind: "inventario"` por `atom:cloud` cuando esté y vuelve a
        preguntar, y entonces sí salen los pendientes de la caché.
        """
        return self._construir_plan(folder, prefix)

    def cloud_prepare_start(self, folder: str, prefix: str | None = None) -> dict:
        """Igual que `cloud_prepare` pero en un hilo: el `rglob` de la carpeta
        entera dejaba la pantalla esperando. El plan llega por `atom:analisis`."""
        if self._analizando:
            return {"started": False, "reason": "Ya hay un análisis en curso."}
        self._analizando = True

        def worker() -> None:
            try:
                data = self._construir_plan(
                    folder, prefix,
                    on_progress=lambda n: self._push_analisis(
                        {"kind": "scan", "scope": "plan", "done": n}),
                    should_stop=lambda: self._cancel_analisis,
                )
                if self._cancel_analisis:
                    self._push_analisis({"kind": "cancelled", "scope": "plan"})
                else:
                    self._push_analisis({"kind": "done", "scope": "plan", "data": data})
            except Exception as exc:  # noqa: BLE001 - llega a la UI como error
                self._push_analisis({"kind": "error", "scope": "plan", "text": str(exc)})
            finally:
                self._analizando = False

        threading.Thread(target=worker, daemon=True).start()
        return {"started": True}

    # Los errores de la Suite viajan como códigos; el usuario del Organizer no
    # tiene por qué descifrarlos, y son justo los dos casos que más se va a
    # encontrar (relanzar encima de una operación viva, o subir sin estadillo).
    _ERRORES_ORGANIZAR = {
        "operacion-en-curso": "Esta inspección ya se está organizando ahora mismo.",
        "estadillo-no-encontrado": "No se encontró el estadillo de la subida, así que no se puede organizar.",
        "estadillo-no-legible": "El estadillo de la subida no se puede leer.",
        "inspeccion-no-encontrada": "La inspección ya no existe en la Suite.",
        "inspeccion_id-requerido": "Falta la inspección.",
        "esquema-desactualizado": "La Suite no está actualizada para organizar en la nube.",
        "unauthorized": "Sin permiso para organizar en la nube: vuelve a iniciar sesión.",
        "no-session": "Sin permiso para organizar en la nube: vuelve a iniciar sesión.",
    }

    def cloud_organizar(self, inspeccion_id: int) -> dict:
        """Pide a la Suite que lance el Cloud Run Job de organización sobre una
        subida ya completada (`POST /api/organizer/lanzar-desde-subida`).

        FAIL-OPEN: la subida ya terminó bien antes de llamar aquí, así que
        cualquier fallo (sin login, red caída, la Suite devuelve error o un
        `None` fail-open de `RunReporter`) se traduce en
        `{"ok": False, "error": ...}`, nunca en una excepción que invalide lo
        ya subido.
        """
        if not inspeccion_id:
            return {"ok": False, "error": "Falta la inspección."}
        try:
            reporter = self._reporter_actual()
            if reporter is None:
                return {"ok": False, "error": "Sin sesión: inicia sesión para organizar en la nube."}
            resp = reporter.lanzar_organizacion(inspeccion_id=inspeccion_id)
            if not isinstance(resp, dict) or not resp.get("ok"):
                bruto = (resp or {}).get("error") if isinstance(resp, dict) else None
                clave = str(bruto or "").split(":", 1)[0].strip()
                mensaje = self._ERRORES_ORGANIZAR.get(clave) or bruto
                return {"ok": False, "error": mensaje or "No se pudo lanzar la organización en la nube."}
            return {
                "ok": True,
                "operacion_id": resp.get("operacion_id"),
                "destino": resp.get("destino"),
            }
        except Exception as exc:  # noqa: BLE001 - fail-open: la subida ya terminó bien
            self._log_subida("cloud_organizar: fallo lanzando la organización (%s)", exc)
            return {"ok": False, "error": str(exc)}

    def cloud_upload(self, folder: str, force: bool = False,
                     prefix: str | None = None,
                     inspeccion_id: int | None = None,
                     confirmar_subida_extra: bool = False) -> dict:
        """Sube la carpeta entera al bucket. El progreso va por `atom:cloud`.

        `force` se mantiene por compatibilidad con llamadas antiguas y se
        ignora: ya no hay nada que forzar, porque subir sobre un destino con
        datos dejó de ser destructivo (ver el comentario en `worker`).

        `inspeccion_id` es el id de la inspección elegida en la UI. Si llega,
        al terminar (éxito o fallo) se avisa a la Suite vía
        `POST /api/organizer/subidas` para que `/organizer` la saque del
        panel "SUBIDAS SIN ORGANIZAR" o la enseñe en rojo. Si es `None`
        (prefijo escrito a mano, inspección no encontrada), no se reporta
        nada: solo queda en el log local.

        `confirmar_subida_extra`: esta carpeta ya tiene un lote COMPLETO
        registrado (su `manifest.json` se escribió) y el operario ha
        confirmado dos veces en la UI que esto es una subida EXTRA con OTRO
        estadillo, no un reintento. Sin esta confirmación, una carpeta con
        lote completo no se sube: se devuelve `requiere_confirmacion` con el
        lote anterior para que la UI pida las dos confirmaciones antes de
        volver a llamar. Un lote INCOMPLETO (sin `manifest.json`, típico de
        un corte de red) se reanuda directo, sin preguntar nada: eso es
        "reintentar", no "subir otra vez" (ver `atom_core/lotes.py`).
        """
        self.cloud_asegurar_estado()
        if self._uploading:
            return {"started": False, "reason": "Ya hay una subida en curso."}

        from datetime import datetime, timezone

        from atom_core import cloud_config, cloud_upload, estadillo as estadillo_mod, lotes, upload_log

        auth = self._get_auth()
        if auth is None:
            return {"started": False, "reason": cloud_config.missing_client_help()}
        if not auth.is_logged_in():
            # En el campo, decir "no se puede subir" es perder el trabajo del
            # día. Se acepta el encargo y se sube cuando vuelva a haber
            # credencial.
            destino, prefijo_norm, error = self._destino(folder, prefix)
            if error:
                return {"started": False, "reason": error}
            job = cola_subidas.encolar(str(folder), prefijo_norm, inspeccion_id)
            self._credencial.registrar(ESTADO_SIN_CREDENCIAL, "No hay dispositivo emparejado.")
            return {
                "started": False,
                "encolado": True,
                "job": job,
                "reason": "Sin sesión: la subida queda en cola y saldrá al volver a emparejar.",
            }

        root, prefix, error = self._destino(folder, prefix)
        if error:
            return {"started": False, "reason": error}

        # Un lote sin estadillo no se puede organizar: se aborta ANTES de
        # subir nada, no a mitad (ver atom_core/lotes.py).
        rutas_estadillos = estadillo_mod.detectar_estadillos(folder)["rutas"]
        if not rutas_estadillos:
            # NUNCA se coge el estadillo del padre en automático (decisión de
            # el responsable): si hay candidatos sueltos ahí se avisa
            # con su ruta completa, pero no se usan ni se aceptan solos.
            candidatos_padre = estadillo_mod.detectar_estadillos_en_padre(folder)["rutas"]
            if candidatos_padre:
                return {"started": False,
                        "reason": ("No se ha encontrado ningún estadillo DENTRO de la "
                                  "carpeta. Hay candidato(s) en la carpeta PADRE, pero no "
                                  "se usan en automático: " + ", ".join(candidatos_padre) +
                                  ". Confírmalo explícitamente en la UI si quieres usarlo.")}
            return {"started": False,
                    "reason": ("No se ha encontrado ningún estadillo en la "
                              "carpeta: sin estadillo el lote no se puede "
                              "organizar.")}

        # El lote es por CARPETA local, no por pulsación de "Subir". Un lote
        # INCOMPLETO (sin manifest.json: se cortó, se canceló) se reanuda
        # directo. Uno COMPLETO exige confirmación explícita: sin ella no se
        # sube nada, con ella se abre un lote NUEVO (otro estadillo sobre la
        # misma carpeta) — ver atom_core/lotes.py.
        usuario = (self._cuenta_actual() or "").split("@")[0]
        estado_previo = lotes.estado_lote_carpeta(root)
        if estado_previo is not None and estado_previo["completo"]:
            if not confirmar_subida_extra:
                return {
                    "started": False,
                    "requiere_confirmacion": True,
                    "lote_anterior": estado_previo["lote"],
                    "reason": (
                        "Esta carpeta ya se subió por completo (lote "
                        f"{estado_previo['lote']}). Si es una subida EXTRA "
                        "con otro estadillo, confírmalo."),
                }
            lote = lotes.nombre_lote(datetime.now(timezone.utc), usuario)
            lotes.registrar_lote(root, lote)
        elif estado_previo is not None:
            lote = estado_previo["lote"]  # incompleto: reanudar sin preguntar
        else:
            lote = lotes.nombre_lote(datetime.now(timezone.utc), usuario)
            lotes.registrar_lote(root, lote)
        prefix_lote = f"{prefix}/{lotes.CARPETA_SUBIDAS}/{lote}"

        self._uploading = True
        self._cancel_upload = False

        def worker() -> None:
            plan = None
            reporter = None
            try:
                plan = cloud_upload.build_plan(root, prefix_lote)
                if not plan.items:
                    raise RuntimeError("La carpeta no tiene ficheros subibles.")
                estadillos_rel = cloud_upload.agregar_estadillos(
                    plan, rutas_estadillos)

                provider = cloud_upload.GcsOAuthProvider(
                    cloud_config.BUCKET_DATOS, auth,
                    prefix=self._prefijo_token(prefix),
                    inspeccion_id=inspeccion_id)

                self._push_cloud({"kind": "start", "files": len(plan.items),
                                  "bytes": plan.total_bytes, "prefix": prefix_lote})

                # Telemetria EN VIVO hacia `/organizer`. Sin esto la Suite solo
                # se enteraba al terminar (`_reportar_subida`), asi que una
                # subida de horas era invisible desde la web: nadie podia saber
                # que una planta se estaba subiendo ni por donde iba.
                # Es un ciclo aparte del de `subida()`: este alimenta
                # `organizer_runs` (progreso), aquel `organizer_subidas`
                # (histórico y panel de "sin organizar"). Los dos hacen falta.
                reporter = self._reporter_actual()
                if reporter is not None:
                    # `inspeccion_id` solo si lo hay: con prefijo escrito a mano
                    # no existe, y mandar None lo grabaria como NULL igualmente
                    # pero ensuciando el body. El run se pinta por `inspeccion`
                    # (el prefijo), el id es lo que deja enlazarlo con la ficha.
                    extra = ({} if inspeccion_id is None
                             else {"inspeccion_id": inspeccion_id})
                    reporter.iniciar(inspeccion=prefix, etapa="subida",
                                     items_total=len(plan.items),
                                     bytes_total=plan.total_bytes, **extra)
                    # `iniciar` es fail-open: si la Suite no contesto, el run no
                    # existe y todo lo demas seria no-op. Soltarlo aqui es la
                    # diferencia entre "no hay run" y "no sabemos que no lo hay".
                    if not reporter.activo:
                        reporter = None
                        self._log_subida(
                            "cloud_upload: la Suite no acepto el alta del run; "
                            "la subida sigue, pero sin progreso en /organizer")

                # Ya no hay guarda anti-pisado ni «continuar subida»: lo que
                # ya está en el destino se identifica objeto a objeto y se
                # descarta (`reconciliar`), en vez de bloquear la subida entera
                # y pedirle al operador que confirme a ciegas. Subir dos veces
                # la misma carpeta es ahora una operación segura y barata.
                # Se reutiliza el inventario que se calculó al elegir carpeta
                # si sigue fresco: volver a listar aquí son otros ~20 s con el
                # operario mirando una barra parada. Lo que se haya colado en
                # el bucket desde entonces lo para la precondición
                # `ifGenerationMatch=0` con un 412, sin gastar bytes.
                # `prefix_lote` ahora SÍ puede ser un lote reanudado (ya no es
                # siempre una carpeta nueva vacía), así que el inventario
                # cacheado de ese prefijo vuelve a tener sentido pasarlo: si
                # está fresco, ahorra el listado; si no (`None`, lo normal:
                # nadie precalienta el prefijo del lote, solo el de la
                # inspección), `upload_plan` lista `prefix_lote` él solo antes
                # de subir nada.
                # Reintento de LOTE: los reintentos de `upload_plan` son por
                # objeto (mismo proceso, misma sesion resumible). Un wifi que
                # se cae de verdad tumba TODOS los objetos en vuelo a la vez,
                # agota esos reintentos y deja el lote a medias sin que nadie
                # lo relance. Aqui se vuelve a llamar entero, con backoff, y
                # cada ronda salvo la primera relista el bucket (`remotos`
                # cacheado ya no vale: puede haber cambiado a mitad de ronda).
                acumulado = cloud_upload.UploadResult()
                espera = ESPERA_RONDA_INICIAL
                sin_avance = 0
                res = None
                verificado = False
                faltantes: list[tuple[str, str]] = []
                for ronda in range(1, RONDAS_SUBIDA_MAX + 1):
                    res = cloud_upload.upload_plan(
                        plan, provider,
                        on_progress=lambda t: self._push_cloud({"kind": "log", "text": t}),
                        on_stats=lambda s: self._on_stats_subida(reporter, s),
                        should_stop=lambda: self._cancel_upload,
                        remotos=(self._inventario_cacheado(prefix_lote)
                                 if ronda == 1 else None),
                    )
                    # AUDITORIA de completitud. `res.ok` solo dice que ningun
                    # objeto lanzo una excepcion; no dice que esten en el
                    # bucket. Aqui se cruza el plan contra un listado FRESCO:
                    # lo que falte entra en `failed` y se lleva otra ronda,
                    # asi que el manifest solo se escribe con el 100 %
                    # comprobado contra GCS, no con "no hubo excepciones".
                    if res.ok and not self._cancel_upload:
                        faltantes, verificado = self._verificar_lote_completo(
                            plan, prefix_lote, auth, inspeccion_id)
                        if faltantes:
                            self._push_cloud({
                                "kind": "log",
                                "text": (f"Verificación: faltan {len(faltantes)} "
                                         f"de {len(plan.items)} objetos en el "
                                         f"bucket, se reintentan."),
                            })
                            res.failed.extend(faltantes)

                    acumulado.uploaded += res.uploaded
                    acumulado.skipped += res.skipped
                    acumulado.skipped_remoto += res.skipped_remoto
                    acumulado.skipped_precondicion += res.skipped_precondicion
                    acumulado.reconciliado = (
                        acumulado.reconciliado or res.reconciliado)
                    acumulado.bytes_sent += res.bytes_sent
                    acumulado.elapsed += res.elapsed
                    acumulado.retries += res.retries
                    acumulado.failed = res.failed

                    if res.ok or self._cancel_upload:
                        break

                    if res.uploaded == 0 and res.bytes_sent == 0:
                        sin_avance += 1
                        if sin_avance >= 2:
                            break
                    else:
                        sin_avance = 0

                    self._push_cloud({
                        "kind": "log",
                        "text": (f"Ronda {ronda}: {len(res.failed)} objetos "
                                 f"fallaron, reintentando en {espera}s…"),
                    })
                    esperado = 0
                    while esperado < espera and not self._cancel_upload:
                        time.sleep(1)
                        esperado += 1
                    if self._cancel_upload:
                        break
                    espera = min(espera * 2, ESPERA_RONDA_MAX)

                rondas_ejecutadas = ronda
                res = acumulado
                mbps_total = (
                    (res.bytes_sent * 8) / res.elapsed / 1_000_000
                    if res.elapsed > 0 else 0.0)

                # `manifest.json` es el marcador de "lote completo": se sube
                # EL ÚLTIMO y SOLO si todo lo demás fue bien. Si falla (o la
                # subida se canceló/falló a medias), NO se escribe: el lote
                # queda invisible para la Suite a propósito (ver
                # atom_core/lotes.py). Un fallo aquí se trata como un fallo
                # más de la subida (entra en `res.failed`), no aparte. Y solo
                # si se escribe con éxito se marca el lote como completo en
                # el estado local: así una siguiente subida de esta carpeta
                # sabe que hace falta confirmación, no lo reanuda a ciegas.
                if res.ok and not self._cancel_upload:
                    try:
                        manifest = lotes.manifest_lote(
                            lote, self._cuenta_actual(), estadillos_rel,
                            len(plan.items))
                        self._subir_objeto_json(
                            f"{prefix_lote}/manifest.json", manifest,
                            prefix=self._prefijo_token(prefix),
                            inspeccion_id=inspeccion_id)
                    except Exception as exc_manifest:  # noqa: BLE001
                        res.failed.append(("manifest.json", str(exc_manifest)))
                    else:
                        # El manifest YA esta en el bucket: para la Suite el
                        # lote esta completo, pase lo que pase aqui. Si no se
                        # puede persistir el estado local (disco lleno,
                        # permisos), NO es un fallo de subida: se avisa y se
                        # sigue. Lo contrario dejaria el estado local en
                        # "incompleto" y una siguiente subida reanudaria un
                        # lote que la Suite ya puede estar organizando.
                        try:
                            lotes.marcar_lote_completo(root, lote)
                        except Exception as exc_estado:  # noqa: BLE001
                            print(f"[lotes] manifest subido pero no se pudo "
                                  f"marcar {lote} como completo: {exc_estado}")

                self._push_cloud({
                    "kind": "done", "ok": res.ok,
                    "uploaded": res.uploaded, "skipped": res.skipped,
                    "skipped_remoto": res.skipped_remoto,
                    "skipped_precondicion": res.skipped_precondicion,
                    "reconciliado": res.reconciliado,
                    "bytes": res.bytes_sent, "elapsed": res.elapsed,
                    "mbps": round(mbps_total, 1), "retries": res.retries,
                    "failed": [{"objeto": o, "error": e} for o, e in res.failed[:20]],
                    "failed_total": len(res.failed),
                    "cancelled": self._cancel_upload,
                    "log": str(upload_log.ruta()),
                    "rondas": rondas_ejecutadas,
                    # Garantia dura para el operario: no "la barra llego al
                    # 100 %", sino "N de M objetos comprobados en el bucket".
                    # `verificado: false` = no se pudo listar para comprobarlo.
                    "verificado": verificado,
                    "verificados": (len(plan.items) - len(faltantes)
                                    if verificado else 0),
                    "items_total": len(plan.items),
                })

                # Aviso a la Suite (`/organizer`), en su PROPIO try: un fallo
                # aquí NUNCA debe pisar el resultado ya entregado a la UI
                # local (`_push_cloud` de arriba). Sin este try anidado, una
                # excepción del reporte caería al `except` de abajo y
                # empujaría un `kind:error` DESPUÉS del `kind:done`, además de
                # reportar a la Suite un fallo sobre una subida que fue bien.
                # Una subida parcial (`res.ok` False) sí cuenta como fallo: si
                # completara `vuelo_subida`, el panel invitaría a organizar
                # datos incompletos.
                try:
                    if self._cancel_upload:
                        motivo = "Subida cancelada por el operador"
                        self._reportar_subida(inspeccion_id, plan, estado="error",
                                              error=motivo)
                        self._cerrar_run(reporter, ok=False, error=motivo)
                    elif res.ok:
                        self._reportar_subida(inspeccion_id, plan, estado="ok")
                        self._cerrar_run(reporter, ok=True)
                    else:
                        primeros = "; ".join(
                            f"{o}: {e}" for o, e in res.failed[:5])
                        motivo = f"{len(res.failed)} objetos fallaron: {primeros}"
                        self._reportar_subida(
                            inspeccion_id, plan, estado="error", error=motivo)
                        self._cerrar_run(reporter, ok=False, error=motivo)
                except Exception as exc_rep:  # noqa: BLE001 - fail-open
                    self._log_subida(
                        "cloud_upload: fallo reportando a la Suite (%s)", exc_rep)
            except Exception as exc:  # noqa: BLE001 - llega a la UI como error
                self._push_cloud({"kind": "error", "text": str(exc)})
                try:
                    self._reportar_subida(inspeccion_id, plan, estado="error",
                                          error=str(exc))
                    self._cerrar_run(reporter, ok=False, error=str(exc))
                except Exception as exc_rep:  # noqa: BLE001 - fail-open
                    self._log_subida(
                        "cloud_upload: fallo reportando a la Suite (%s)", exc_rep)
            finally:
                self._uploading = False

        threading.Thread(target=worker, daemon=True).start()
        return {"started": True}

    # ---- estadillo: ubicación canónica en el bucket ------------------------
    # Acciones propias: no cuelgan de organizar ni de subir la jornada, así
    # que la ubicación canónica sale igual en local→Drive→bucket que en RAW.
    def estadillo_validar(self, rutas: list[str]) -> dict:
        """Valida los estadillos elegidos y devuelve lo que se ha entendido.

        Síncrono a propósito: el operario tiene que ver el resultado antes de
        que se suba nada.
        """
        from atom_core import estadillo as estadillo_mod

        res = estadillo_mod.validar_para_subida(rutas)
        return {k: v for k, v in res.items() if k != "vuelos"}

    def estadillo_subir(self, folder: str, rutas: list[str]) -> dict:
        """Sube los estadillos a la ubicación canónica del bucket.

        Acción propia: no depende de haber organizado ni de haber subido la
        jornada, así que la ruta canónica es la misma en modo local y en RAW.
        """
        from atom_core import cloud_config
        from atom_core import estadillo as estadillo_mod

        # Lock no bloqueante, igual que `cloud_upload`/`self._uploading`: sin
        # esto un doble-click (o dos dispositivos a la vez) arrancaba dos
        # hilos subiendo el mismo estadillo en paralelo.
        if self._estadillo_subiendo:
            return {"started": False, "reason": "Ya hay una subida en curso."}

        # Todo lo previo al arranque del hilo (chequeo de sesión, validación)
        # va envuelto: su contrato con la UI es devolver siempre
        # `{"started": False, "reason": ...}` ante cualquier problema, nunca
        # propagar la excepción por el puente IPC (el JS de arriba solo mira
        # `r.started === false`, no espera un `catch`).
        try:
            # Mismo chequeo síncrono que `cloud_upload`: sin él la llamada
            # devolvía `started: True` y el «no has iniciado sesión» sólo
            # salía después, disfrazado del error genérico de la primera
            # subida.
            auth = self._get_auth()
            if auth is None:
                return {"started": False, "reason": cloud_config.missing_client_help()}
            if not auth.is_logged_in():
                return {"started": False,
                        "reason": "Primero inicia sesión con tu cuenta de Aerotools."}

            validacion = estadillo_mod.validar_para_subida(rutas)
            if not validacion["ok"]:
                return {"started": False, "reason": validacion["error"]}
        except Exception as exc:  # noqa: BLE001 - contrato: nunca reventar el IPC
            return {"started": False, "reason": str(exc)}

        self._estadillo_subiendo = True

        def worker():
            self._subir_estadillo_worker(folder, rutas, validacion)

        threading.Thread(target=worker, daemon=True).start()
        return {"started": True, "reason": None}

    def estadillo_existente(self, prefijo: str) -> dict:
        """¿Ya hay un estadillo subido para este prefijo?

        Fail-open: la UI solo usa esto para pre-marcar un checkbox, así que
        cualquier fallo (sin login, sin red, prefijo vacío) se traduce en
        `existe: False` y nunca revienta la llamada.
        """
        from atom_core import cloud_config, cloud_upload, estadillo_canonico

        try:
            auth = self._get_auth()
            if auth is None:
                return {"existe": False, "error": cloud_config.missing_client_help()}
            if not auth.is_logged_in():
                return {"existe": False,
                        "error": "Primero inicia sesión con tu cuenta de Aerotools."}

            raiz, kw_token = self._estadillos_acceso(prefijo, auth)
            prefix = f"{raiz}/{estadillo_canonico.CARPETA_ACTUAL}/"
            n = cloud_upload.objetos_en_prefijo(
                cloud_config.BUCKET_DATOS, prefix, auth, **kw_token)
            return {"existe": n > 0, "error": None}
        except Exception as exc:  # noqa: BLE001 - fail-open, la UI solo pre-marca un checkbox
            return {"existe": False, "error": str(exc)}

    def estadillo_bajar_nube(self, prefijo: str) -> dict:
        """Baja a disco el/los estadillo(s) ya subidos de esa inspección.

        Solo escritorio: es la contraparte de `estadillo_subir`, síncrona
        también a propósito (son 1-2 ficheros pequeños, no una jornada
        entera). Misma construcción de prefijo que `estadillo_existente`.
        Filtra `manifest.json`/`estadillo.json` (metadatos internos, no lo
        que el operario subió) y solo baja CSV/XLSX. Nunca sobrescribe: si el
        nombre ya existe en destino, añade un sufijo numérico.

        Devuelve `{ok, rutas: [{ruta, nombre}], error}`. `error` puesto y
        `ok: False` cuando no hay login, no hay ningún estadillo en el
        prefijo, o falla la descarga.
        """
        from atom_core import cloud_config, cloud_upload, estadillo_canonico, google_auth

        try:
            auth = self._get_auth()
            if auth is None:
                return {"ok": False, "rutas": [], "error": cloud_config.missing_client_help()}
            if not auth.is_logged_in():
                return {"ok": False, "rutas": [],
                        "error": "Primero inicia sesión con tu cuenta de Aerotools."}

            raiz, kw_token = self._estadillos_acceso(prefijo, auth)
            prefix = f"{raiz}/{estadillo_canonico.CARPETA_ACTUAL}/"
            remotos = cloud_upload.listar_objetos_remotos(
                cloud_config.BUCKET_DATOS, prefix, auth, **kw_token)

            excluidos = {estadillo_canonico.NOMBRE_MANIFEST,
                        estadillo_canonico.NOMBRE_NORMALIZADO}
            candidatos = sorted(
                nombre for nombre in remotos
                if nombre.rsplit("/", 1)[-1] not in excluidos
                and nombre.lower().endswith((".csv", ".xlsx"))
            )
            if not candidatos:
                return {"ok": False, "rutas": [],
                        "error": "No hay ningún estadillo subido para esta inspección."}

            destino_dir = google_auth.estadillos_recibidos_dir() / "nube"
            destino_dir.mkdir(parents=True, exist_ok=True)

            rutas = []
            for nombre_remoto in candidatos:
                base = nombre_remoto.rsplit("/", 1)[-1]
                stem, ext = os.path.splitext(base)
                objetivo = destino_dir / base
                sufijo = 1
                while objetivo.exists():
                    objetivo = destino_dir / f"{stem}_{sufijo}{ext}"
                    sufijo += 1
                cloud_upload.descargar_objeto(
                    cloud_config.BUCKET_DATOS, nombre_remoto, auth, objetivo,
                    **kw_token)
                rutas.append({"ruta": str(objetivo), "nombre": objetivo.name})

            return {"ok": True, "rutas": rutas, "error": None}
        except Exception as exc:  # noqa: BLE001 - contrato: nunca reventar el IPC
            return {"ok": False, "rutas": [], "error": str(exc)}

    # ---- estadillo: modo "esperando estadillo" (LAN, sin token) -----------
    # La app "Estadillo Digital" (Christian) consulta/manda el estadillo por
    # HTTP desde cualquier IP de la LAN (`atom_core/webserver.py`,
    # `_RUTAS_LAN_ABIERTAS`); estos 3 métodos los llama el propio kiosco
    # (loopback, `METODOS_EXPUESTOS`) para abrir/cerrar esa ventana y ver su
    # estado. `_estadillo_recibir` lo llama el handler HTTP directamente, no
    # el kiosco: no está en `METODOS_EXPUESTOS`.
    def estadillo_espera_iniciar(self, carpeta: str, inspeccion: dict | None,
                                  segundos: int = 600) -> dict:
        """Arranca el modo espera: desde este momento las rutas LAN abiertas
        responden, durante `segundos` (10 min por defecto) o hasta que
        llegue un estadillo valido. El recuento de fotos y su rango EXIF se
        calculan en un hilo aparte (puede haber miles de fotos) para no
        bloquear la llamada ni el servidor.

        Volver a llamar a este metodo (el kiosco reinicia la espera)
        resetea el contador de caducidad y sustituye el estado anterior
        entero, aunque no hubiera caducado."""
        ahora = self._estadillo_reloj()
        estado = {
            "carpeta": carpeta,
            "inspeccion": inspeccion or {},
            "fotos": {"total": 0, "primera": None, "ultima": None, "calculando": True},
            "recibido": False,
            "rutas": [],
            "errores": [],
            "caduca_en": ahora + datetime.timedelta(seconds=max(1, int(segundos))),
        }
        with self._estadillo_espera_lock:
            self._estadillo_espera = estado

        def worker():
            from exif import rango_horas_exif

            try:
                total, primera, ultima = rango_horas_exif(carpeta)
                fotos = {"total": total, "primera": primera, "ultima": ultima, "calculando": False}
            except Exception as exc:  # noqa: BLE001 — no debe tumbar el hilo de fondo
                with self._estadillo_espera_lock:
                    if self._estadillo_espera is estado:
                        estado["errores"].append(str(exc))
                        estado["fotos"]["calculando"] = False
                return
            with self._estadillo_espera_lock:
                if self._estadillo_espera is estado:
                    estado["fotos"] = fotos

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    def estadillo_espera_cancelar(self) -> dict:
        with self._estadillo_espera_lock:
            self._estadillo_espera = None
        self._estadillo_registrar_evento(None, "cancelado")
        return {"ok": True}

    def estadillo_espera_carpeta(self, carpeta: str | None) -> dict:
        """Actualiza la carpeta de la espera EN CURSO sin tocar su caducidad
        ni ningun otro campo (ni "reinicia" el contador como haria volver a
        llamar a `estadillo_espera_iniciar`).

        Pensado para el caso en que el kiosco arranca la espera antes de
        elegir carpeta (o la cambia despues): en cuanto React tenga una
        carpeta nueva, llama aqui y la espera "se entera sola". `carpeta`
        puede venir `None`/`""` para quitarla (vuelve a quedar sin carpeta
        seleccionada). Si no hay espera activa es un no-op explicito, no un
        error: el kiosco puede llamar esto en cualquier momento.
        """
        with self._estadillo_espera_lock:
            estado = self._estadillo_espera
            if estado is None:
                return {"ok": False, "motivo": "No hay modo espera activo."}
            estado["carpeta"] = carpeta or None
        return {"ok": True}

    def carpeta_trabajo_fijar(self, path: str | None) -> dict:
        """Fija la "carpeta de trabajo" del panel de control remoto
        (`/api/control/carpeta`, `atom_core/webserver.py`): estado unico en
        `Api`, no una closure por handler HTTP, para que `/api/control/carpeta`,
        `/api/control/estado` y `/api/control/organizar` vean siempre la misma
        carpeta. En `METODOS_EXPUESTOS` (local) para que el kiosco pueda
        llamarla el dia que elija la carpeta desde su propia pantalla.

        Reutiliza `estadillo_espera_carpeta`: si hay una espera de estadillo
        activa, tambien se entera de la carpeta nueva (sin reiniciar su
        caducidad), igual que hacia antes el handler HTTP a mano -asi
        `_mover_estadillo_espera_si_toca` sigue funcionando."""
        self._carpeta_trabajo = path or None
        try:
            self.estadillo_espera_carpeta(self._carpeta_trabajo)
        except Exception:  # noqa: BLE001 — no debe tumbar la fijacion de carpeta
            logger.warning("carpeta_trabajo_fijar: estadillo_espera_carpeta fallo para %s", self._carpeta_trabajo)
        return {"ok": True, "carpeta": self._carpeta_trabajo}

    def _carpeta_trabajo_actual(self) -> str | None:
        """Carpeta de trabajo actual del panel de control remoto (ver
        `carpeta_trabajo_fijar`), o `None` si no se ha elegido ninguna. Solo
        para uso interno (`atom_core/webserver.py`), sin exponer al bridge
        JS: no hay motivo para que el front la LEA por su cuenta, solo la
        fija (`carpeta_trabajo_fijar`) y se entera de los cambios por SSE
        (`atom:control_carpeta`)."""
        return self._carpeta_trabajo

    def _estadillo_en_carpeta(self, carpeta: str | None) -> dict:
        """`{encontrado, nombre, buscando}` para el indicador del selector de
        carpeta del kiosco. Reusa `estadillo.detectar_estadillos` (la misma
        detección de `PasoEstadillo`/`estadillos_detectar`), pero SIN
        bloquear esta llamada: el resultado se cachea por carpeta y, si aún
        no hay caché, se lanza un escaneo en hilo aparte y se responde
        `buscando: True` -el siguiente poll de `estadillo_espera_estado`
        recoge el resultado ya calculado.
        """
        vacio = {"encontrado": False, "nombre": None, "buscando": False}
        if not carpeta or not os.path.isdir(carpeta):
            return vacio
        carpeta_norm = os.path.normpath(carpeta)
        with self._estadillo_carpeta_lock:
            cacheado = self._estadillo_carpeta_cache.get(carpeta_norm)
            if cacheado is not None:
                return dict(cacheado)
            if carpeta_norm in self._estadillo_carpeta_hilos:
                return {"encontrado": False, "nombre": None, "buscando": True}
            self._estadillo_carpeta_hilos.add(carpeta_norm)

        def worker() -> None:
            try:
                # El candado de `precargar_pandas` serializa el primer import
                # de pandas con el resto de hilos (ver atom_core/precarga.py).
                precarga.precargar_pandas()
                from atom_core.estadillo import detectar_estadillos

                rutas = detectar_estadillos(carpeta_norm)["rutas"]
                resultado = {
                    "encontrado": bool(rutas),
                    "nombre": os.path.basename(rutas[0]) if rutas else None,
                    "buscando": False,
                }
            except Exception as exc:  # noqa: BLE001 — indicador informativo, nunca rompe
                logger.warning("estadillo_en_carpeta: fallo detectando en %s: %s",
                               carpeta_norm, exc)
                resultado = {"encontrado": False, "nombre": None, "buscando": False}
            with self._estadillo_carpeta_lock:
                self._estadillo_carpeta_cache[carpeta_norm] = resultado
                self._estadillo_carpeta_hilos.discard(carpeta_norm)

        threading.Thread(target=worker, daemon=True).start()
        return {"encontrado": False, "nombre": None, "buscando": True}

    # Ventanas de recencia para derivar `fase` a partir del log de eventos:
    # cuanto se considera "acaba de pasar" antes de volver a "esperando".
    _ESTADILLO_VENTANA_CONECTADO_S = 30
    _ESTADILLO_VENTANA_RECHAZADO_S = 30
    _ESTADILLO_VENTANA_RECIBIENDO_S = 3

    # Tope de espera de `_estadillo_calcular_validacion` al escaneo EXIF de
    # la carpeta: pasado esto, el POST responde `pendiente: True` y el hilo
    # sigue en segundo plano (ver su docstring).
    _ESTADILLO_VALIDACION_TIMEOUT_S = 5

    # Margen a cada lado de [Hora_de_inicio, Hora_final] al filtrar los
    # vuelos del estadillo por tarjeta (`_estadillo_filtrar_por_tarjeta`):
    # el piloto arranca a grabar unos segundos antes/después de la hora que
    # apunta a mano, así que exigir el intervalo exacto descartaría vuelos
    # reales. 5 min es margen suficiente sin solapar con el vuelo siguiente
    # en una campaña normal (vuelos separados por >=10-15 min).
    _ESTADILLO_FILTRO_TARJETA_MARGEN_S = 300

    # IPs de loopback: peticiones del propio Chromium del kiosco a su
    # servidor local, nunca un portatil real conectado por la LAN/hotspot.
    _ESTADILLO_IPS_LOOPBACK = frozenset({"127.0.0.1", "::1"})

    def _estadillo_registrar_evento(self, ip: str | None, tipo: str, detalle: str = "") -> None:
        """Añade un evento al log de actividad del modo espera (últimos 10) y,
        si es un contacto remoto real (`ping`/`consulta`/`envio`), actualiza
        `ultimo_contacto`. Lo llama el handler HTTP con la IP del cliente en
        cada petición a `/api/estadillo/*`; las acciones puramente locales
        (`cancelado`, `caducado`) se registran con `ip=None`. Un contacto
        desde loopback (127.0.0.1/::1) no es un portatil real -es el propio
        Chromium del kiosco- y por tanto tampoco actualiza `ultimo_contacto`.
        """
        ahora = self._estadillo_reloj()
        evento = {"cuando_dt": ahora, "ip": ip, "tipo": tipo, "detalle": detalle}
        with self._estadillo_espera_lock:
            self._estadillo_eventos.append(evento)
            del self._estadillo_eventos[:-10]
            if tipo in ("ping", "consulta", "envio") and ip not in self._ESTADILLO_IPS_LOOPBACK:
                self._estadillo_ultimo_contacto = {"ip": ip, "cuando_dt": ahora, "accion": tipo}

    @staticmethod
    def _estadillo_evento_pub(evento: dict) -> dict:
        return {
            "cuando": evento["cuando_dt"].isoformat(timespec="seconds"),
            "ip": evento["ip"],
            "tipo": evento["tipo"],
            "detalle": evento.get("detalle") or "",
        }

    def estadillo_espera_estado(self) -> dict:
        """Estado del modo espera. `esperando` es `False` tanto si nunca se
        inicio como si caduco (`caducado` distingue los dos casos) o si ya
        se recibio el estadillo con exito y por tanto ya no admite mas POST
        -en ese caso `esperando` sigue `True` para que el kiosco/pagina LAN
        puedan seguir enseñando "recibido", pero `recibido` es `True`. Este
        `recibido` no caduca a efectos de PANTALLA (el operario puede tardar
        en pulsar OK y no debe perder la tarjeta de "recibido"); el corte
        real de si un nuevo POST puede aceptarse como espera limpia pasado
        `caduca_en` vive en el handler HTTP (`webserver.py`,
        `_estadillo_recibir_post`), que compara `segundos_restantes` antes
        de responder 409 `estadillo_ya_recibido` (bug 2026-09-23).

        Ademas de lo anterior (compatible con lo que ya habia), añade:
        - `fase`: 'inactivo'|'esperando'|'conectado'|'recibiendo'|
          'recibido_ok'|'rechazado'|'caducado'. Derivada, no se guarda.
        - `ultimo_contacto`: `{ip, cuando, accion}` del ultimo `ping`,
          `consulta` o `envio` remoto, o `None` si no ha habido ninguno.
        - `eventos`: los ultimos 10 `{cuando, ip, tipo, detalle}` del log
          (sobrevive a que la espera actual termine o se cancele).
        - `resumen`: solo si `recibido` es `True` (lo que devolvio
          `_estadillo_recibir` al aceptar el POST).
        """
        with self._estadillo_espera_lock:
            estado = self._estadillo_espera
            eventos_snapshot = list(self._estadillo_eventos)
            ultimo_contacto_snapshot = (
                dict(self._estadillo_ultimo_contacto) if self._estadillo_ultimo_contacto else None
            )
            ahora = self._estadillo_reloj()

            if estado is None:
                fase = "inactivo"
                resultado = {"esperando": False, "caducado": False}
            else:
                restante = (estado["caduca_en"] - ahora).total_seconds()
                # Caducar no es un `recibido`: si ya llego el estadillo, la
                # espera "termino con exito" y el cronometro deja de importar
                # PARA LA PANTALLA (el kiosco sigue enseñando "recibido"
                # aunque el operario tarde en pulsar OK). El corte real para
                # aceptar un POST nuevo como espera limpia esta en
                # `webserver.py` (`_estadillo_recibir_post`), no aqui.
                if restante <= 0 and not estado["recibido"]:
                    fase = "caducado"
                    resultado = {"esperando": False, "caducado": True}
                    if not estado.get("_evento_caducado_registrado"):
                        estado["_evento_caducado_registrado"] = True
                        evento = {"cuando_dt": ahora, "ip": None, "tipo": "caducado", "detalle": ""}
                        self._estadillo_eventos.append(evento)
                        del self._estadillo_eventos[:-10]
                        eventos_snapshot = list(self._estadillo_eventos)
                elif estado["recibido"]:
                    fase = "recibido_ok"
                    resultado = {
                        "esperando": True,
                        "caducado": False,
                        "inspeccion": estado["inspeccion"],
                        "fotos": dict(estado["fotos"]),
                        "recibido": True,
                        "rutas": list(estado["rutas"]),
                        "errores": list(estado["errores"]),
                        "caduca_en": estado["caduca_en"].isoformat(timespec="seconds"),
                        "segundos_restantes": max(0, int(restante)),
                    }
                    if estado.get("resumen"):
                        resultado["resumen"] = estado["resumen"]
                    if estado.get("validacion") is not None:
                        resultado["validacion"] = estado["validacion"]
                else:
                    ultimo_evento = eventos_snapshot[-1] if eventos_snapshot else None
                    if (ultimo_evento and ultimo_evento["tipo"] == "envio"
                            and (ahora - ultimo_evento["cuando_dt"]).total_seconds()
                            <= self._ESTADILLO_VENTANA_RECIBIENDO_S):
                        fase = "recibiendo"
                    elif (ultimo_evento and ultimo_evento["tipo"] == "rechazado"
                            and (ahora - ultimo_evento["cuando_dt"]).total_seconds()
                            <= self._ESTADILLO_VENTANA_RECHAZADO_S):
                        fase = "rechazado"
                    elif (ultimo_contacto_snapshot
                            and (ahora - ultimo_contacto_snapshot["cuando_dt"]).total_seconds()
                            <= self._ESTADILLO_VENTANA_CONECTADO_S):
                        fase = "conectado"
                    else:
                        fase = "esperando"
                    resultado = {
                        "esperando": True,
                        "caducado": False,
                        "inspeccion": estado["inspeccion"],
                        "fotos": dict(estado["fotos"]),
                        "recibido": False,
                        "rutas": list(estado["rutas"]),
                        "errores": list(estado["errores"]),
                        "caduca_en": estado["caduca_en"].isoformat(timespec="seconds"),
                        "segundos_restantes": max(0, int(restante)),
                    }

        # La carpeta seleccionada solo se conoce si ya se inicio la espera
        # (llega como argumento a `estadillo_espera_iniciar`); antes de eso
        # el backend no tiene forma de saber que carpeta eligio el kiosco (es
        # estado local de React, `KioskScreen`). `carpeta_seleccionada` exige
        # ademas que la ruta exista de verdad en disco (pudo borrarse tras
        # iniciar la espera).
        carpeta_valor = estado["carpeta"] if estado is not None else None
        carpeta_seleccionada = bool(carpeta_valor) and os.path.isdir(carpeta_valor)
        resultado["carpeta_seleccionada"] = carpeta_seleccionada
        resultado["carpeta"] = carpeta_valor
        if resultado.get("esperando") and not carpeta_seleccionada:
            resultado["aviso"] = "No hay carpeta seleccionada en el Organizer"

        # Indicador para el selector de carpeta del kiosco: ¿hay algún
        # estadillo YA en la carpeta elegida? (no confundir con `recibido`,
        # que es el que llegó por LAN y aún no se ha movido, ver
        # `_mover_estadillo_espera_si_toca`).
        resultado["estadillo_en_carpeta"] = self._estadillo_en_carpeta(carpeta_valor)

        resultado["fase"] = fase
        resultado["ultimo_contacto"] = (
            {"ip": ultimo_contacto_snapshot["ip"],
             "cuando": ultimo_contacto_snapshot["cuando_dt"].isoformat(timespec="seconds"),
             "accion": ultimo_contacto_snapshot["accion"]}
            if ultimo_contacto_snapshot else None
        )
        resultado["eventos"] = [self._estadillo_evento_pub(e) for e in eventos_snapshot]

        from atom_core import red_info

        try:
            resultado["red"] = red_info.info_red()
        except Exception:  # noqa: BLE001 — informativo, nunca debe romper el estado
            resultado["red"] = {"hostname": "", "puerto": red_info.PUERTO_PUBLICO, "ips": [], "url": ""}
        return resultado

    def _estadillo_filtrar_por_tarjeta(self, vuelos: list, carpeta: str | None) -> tuple[list, dict]:
        """Se queda solo con los vuelos del estadillo recibido cuyo horario
        coincide con fotos reales de `carpeta` (la que el kiosco tiene
        elegida, la tarjeta que se está organizando): la app de Christian
        manda TODOS los vuelos de la campaña/día, no solo los de esta
        tarjeta/SD.

        Reutiliza `atom_core.validacion_vuelos.validar` -mismo criterio de
        ventana [Hora_de_inicio, Hora_final] + margen que ya usa el aviso
        previo a subir- para decidir, vuelo a vuelo: "tiene alguna foto en
        su horario" (estado != `sin_fotos`) -> se queda; si no, se descarta.

        Las horas del estadillo y el EXIF `DateTimeOriginal` de las DJI se
        asumen ya en la MISMA hora local (España, Europe/Madrid): ninguna de
        las dos trae zona horaria en origen, así que se comparan tal cual,
        sin convertir (ver `validacion_vuelos._a_madrid_naive`).

        Si `carpeta` no está seleccionada, no existe en disco, o no se puede
        leer NINGÚN EXIF con hora (tarjeta recién insertada, fotos sin el
        tag) no se filtra nada -se devuelven todos los vuelos recibidos tal
        cual- y se avisa: mejor procesar de más que perder vuelos por un
        fallo de lectura."""
        from atom_core import estadillo as estadillo_mod, validacion_vuelos
        import exif as exif_mod

        avisos: list[str] = []
        vuelos_recibidos = len(vuelos)

        tiempos: list = []
        if carpeta and os.path.isdir(carpeta):
            try:
                tiempos = exif_mod.listar_horas_exif(carpeta)
            except Exception as exc:  # noqa: BLE001 — fail-open, nunca debe tumbar la recepcion
                avisos.append(f"No se pudo leer el EXIF de la carpeta para filtrar por tarjeta: {exc}")
                tiempos = []

        if not tiempos:
            avisos.append(
                "No se ha podido leer la hora EXIF de ninguna foto de la carpeta elegida"
                if carpeta else
                "No hay ninguna carpeta seleccionada en el Organizer"
            )
            avisos.append(
                "No se ha filtrado por tarjeta: se han aceptado TODOS los vuelos recibidos."
            )
            return list(vuelos), {
                "vuelos_recibidos": vuelos_recibidos,
                "vuelos_en_tarjeta": vuelos_recibidos,
                "vuelos_descartados": [],
                "avisos": avisos,
            }

        normalizados = [estadillo_mod._fila_json_a_csv(v) for v in vuelos]
        # `Fecha` llega tal cual la manda la app (puede traer `:` estilo
        # EXIF, `/` o `-`) y `validacion_vuelos._combinar_fecha_hora` exige
        # `YYYY-MM-DD`: se normaliza con la MISMA función que usa
        # `filas_para_suite` para el body que se le manda a la Suite, así
        # las dos rutas leen la fecha igual.
        ventanas = [{
            "pb": n.get("PB") or "",
            "num_vuelo": n.get("Vuelo") or "",
            "fecha": estadillo_mod._normalizar_fecha_suite(n.get("Fecha") or "") or "",
            "hora_inicio": n.get("Hora_de_inicio") or "",
            "hora_fin": n.get("Hora_final") or "",
        } for n in normalizados]

        resultado = validacion_vuelos.validar(
            ventanas, tiempos, margen_s=self._ESTADILLO_FILTRO_TARJETA_MARGEN_S)
        # `validar` procesa los vuelos EN EL MISMO ORDEN que se le pasan
        # (`ventanas`, que es paralela a `vuelos`), así que basta con
        # emparejar por posición -no hace falta reconstruir el id, que
        # puede llevar un sufijo `#N` si hay pb+vuelo duplicados.
        estados_por_indice = [v["estado"] for v in resultado["vuelos"]]

        vuelos_filtrados = []
        descartados = []
        for i, vuelo in enumerate(vuelos):
            estado_v = estados_por_indice[i] if i < len(estados_por_indice) else "sin_fotos"
            if estado_v == "sin_fotos":
                n = normalizados[i]
                descartados.append({
                    "pb": n.get("PB"), "vuelo": n.get("Vuelo"),
                    "hora_inicio": n.get("Hora_de_inicio"), "hora_fin": n.get("Hora_final"),
                })
            else:
                vuelos_filtrados.append(vuelo)

        if descartados:
            avisos.append(
                f"{len(descartados)} vuelo{'s' if len(descartados) != 1 else ''} del estadillo "
                "descartado(s) por tarjeta: su horario no coincide con ninguna foto de la "
                "carpeta elegida."
            )

        return vuelos_filtrados, {
            "vuelos_recibidos": vuelos_recibidos,
            "vuelos_en_tarjeta": len(vuelos_filtrados),
            "vuelos_descartados": descartados,
            "avisos": avisos,
        }

    def _estadillo_recibir(self, vuelos: list) -> dict:
        """N `FlightRecord` (JSON de "Estadillo Digital") -> CSV temporal con
        cabeceras ES + mismo gate de validación que un CSV subido a mano
        (`estadillo.validar_para_subida`). Solo lo llama el handler HTTP de
        `/api/estadillo`, que ya ha comprobado que hay modo espera activo.

        Antes de escribir el CSV, filtra los vuelos recibidos a los de ESTA
        tarjeta (`_estadillo_filtrar_por_tarjeta`): la app de Christian
        manda el estadillo de la campaña entera, no solo el de la SD que se
        está organizando."""
        from atom_core import estadillo as estadillo_mod
        from atom_core.google_auth import estadillos_recibidos_dir

        with self._estadillo_espera_lock:
            estado = self._estadillo_espera
        if estado is None:
            return {"ok": False, "errores": ["No hay modo espera activo."]}

        vuelos_filtrados, filtro = self._estadillo_filtrar_por_tarjeta(vuelos, estado.get("carpeta"))

        try:
            ruta = estadillo_mod.escribir_csv_desde_json(vuelos_filtrados, estadillos_recibidos_dir())
        except ValueError as exc:
            with self._estadillo_espera_lock:
                if self._estadillo_espera is estado:
                    estado["errores"] = [str(exc)]
            return {"ok": False, "errores": [str(exc)]}

        validacion = estadillo_mod.validar_para_subida([ruta])
        if not validacion["ok"]:
            with self._estadillo_espera_lock:
                if self._estadillo_espera is estado:
                    estado["errores"] = [validacion["error"]]
            return {"ok": False, "errores": [validacion["error"]]}

        info = estadillo_mod.read_estadillo_info(ruta)
        resumen = {
            "planta": info.get("trabajo", ""),
            "fecha": info.get("fecha", ""),
            # Campaña de varios días: la lista completa, no solo la primera
            # fecha (`fecha` se mantiene por compatibilidad con clientes que
            # ya lo leían así).
            "fechas": info.get("fechas", []),
            "pilotos": info.get("pilotos", []),
            "drones": info.get("drones", []),
            "n_vuelos": info.get("num_vuelos", 0),
            # Lista de vuelos con su hora de inicio/fin (`{pb, vuelo, fecha,
            # inicio, final, cruza_medianoche}`, misma forma que devuelve
            # `read_estadillo_info`): el frontend la usa para pintar el
            # resumen tras recibir el estadillo (`EsperaEstadillo.jsx`) y
            # calcular el tiempo de vuelo total.
            "vuelos": info.get("vuelos", []),
            # Filtro por tarjeta (`_estadillo_filtrar_por_tarjeta`): cuántos
            # vuelos mandó la app de Christian en total, cuántos quedaron
            # tras filtrar por las fotos de la carpeta elegida, cuáles se
            # descartaron y por qué (`avisos`).
            "vuelos_recibidos": filtro["vuelos_recibidos"],
            "vuelos_en_tarjeta": filtro["vuelos_en_tarjeta"],
            "vuelos_descartados": filtro["vuelos_descartados"],
            "avisos": filtro["avisos"],
        }
        val_fotos = self._estadillo_calcular_validacion(estado, validacion["vuelos"])
        with self._estadillo_espera_lock:
            if self._estadillo_espera is estado:
                estado["recibido"] = True
                estado["rutas"] = [ruta]
                estado["errores"] = []
                estado["resumen"] = resumen
                estado["validacion"] = val_fotos

        return {"ok": True, "resumen": resumen, "validacion": val_fotos}

    def _estadillo_calcular_validacion(self, estado: dict, vuelos_estadillo: list) -> dict:
        """Compara `vuelos_estadillo` (forma de `estadillo.filas_para_suite`)
        contra las fotos EXIF de `estado["carpeta"]` (`validacion_vuelos.
        validar`). Nunca rechaza nada -solo avisa-, así que corre EN LA
        MISMA llamada de `_estadillo_recibir`, con un tope de 5 s: carpetas
        con miles de fotos pueden tardar más en leer todo el EXIF, y el
        hilo HTTP no puede quedarse bloqueado indefinidamente. Si no da
        tiempo, el hilo sigue en segundo plano y termina escribiendo
        `estado["validacion"]` igualmente -el polling de
        `estadillo_espera_estado` lo recoge en cuanto termine-, y aquí se
        devuelve `pendiente: True` mientras tanto."""
        from atom_core import validacion_vuelos

        carpeta = estado.get("carpeta")
        resultado_hilo: dict = {}

        def worker():
            import exif

            try:
                tiempos = exif.listar_horas_exif(carpeta) if carpeta else []
                resultado_hilo["valor"] = validacion_vuelos.validar(vuelos_estadillo, tiempos)
            except Exception as exc:  # noqa: BLE001 — fail-open, nunca debe tumbar la recepción
                resultado_hilo["valor"] = {
                    "ok": False, "pendiente": False, "vuelos": [], "fotos_fuera": 0,
                    "avisos": [f"No se pudo comparar el estadillo con las fotos de la carpeta: {exc}"],
                }
            with self._estadillo_espera_lock:
                if self._estadillo_espera is estado:
                    estado["validacion"] = resultado_hilo["valor"]

        hilo = threading.Thread(target=worker, daemon=True)
        hilo.start()
        hilo.join(self._ESTADILLO_VALIDACION_TIMEOUT_S)
        if "valor" in resultado_hilo:
            return resultado_hilo["valor"]
        return {"ok": False, "pendiente": True, "vuelos": [], "fotos_fuera": 0, "avisos": []}

    def _subir_estadillo_worker(self, folder: str, rutas: list[str], validacion: dict):
        from datetime import datetime, timezone

        from atom_core import cloud_upload, estadillo_canonico

        self._push_cloud({"kind": "start", "scope": "estadillo"})
        # `finally` es lo que libera el candado de `estadillo_subir`
        # (`self._estadillo_subiendo`): con dos `return` intermedios (error de
        # subida, `res["ok"]` falso) y el camino feliz al final, cualquier
        # salida que no pasara por aquí dejaba la subida "en curso" para
        # siempre y la UI sin poder reintentar (`Ya hay una subida en curso.`
        # de por vida).
        try:
            try:
                locales = []
                for i, ruta in enumerate(rutas, start=1):
                    locales.append(
                        {
                            "orden": i,
                            "ruta": ruta,
                            "nombre_original": os.path.basename(ruta),
                            "md5_b64": cloud_upload._file_md5_b64(ruta),
                            "bytes": os.path.getsize(ruta),
                            "ext": os.path.splitext(ruta)[1],
                        }
                    )

                raiz, kw_token = self._estadillos_acceso(folder)
                prefix_token = f"{raiz}/"
                plan = estadillo_canonico.plan_subida(
                    planta=folder,
                    ficheros_locales=locales,
                    vuelos=validacion["vuelos"],
                    validacion=validacion,
                    ahora=datetime.now(timezone.utc),
                    subido_por=self._cuenta_actual(),
                    base=raiz,
                )

                kw_prov = {k: v for k, v in kw_token.items()
                           if k in ("planta_id", "inspeccion_id", "ambito")}
                res = estadillo_canonico.ejecutar_plan(
                    plan,
                    subir_fichero=lambda remoto, ruta: self._subir_objeto_fichero(
                        remoto, ruta, prefix=prefix_token, **kw_prov),
                    subir_json=lambda remoto, cont: self._subir_objeto_json(
                        remoto, cont, prefix=prefix_token, **kw_prov),
                )
            except Exception as exc:
                self._push_cloud({"kind": "error", "scope": "estadillo", "error": str(exc)})
                return

            if not res["ok"]:
                self._push_cloud({"kind": "error", "scope": "estadillo", "error": res["error"]})
                return

            # Fail-open de principio a fin, como el `_notificar_estadillo` que
            # esto sustituye: el crudo ya está en el bucket, así que un fallo
            # avisando a la Suite no puede dejar al operario sin el evento
            # `done`.
            try:
                reporter = self._reporter_actual()
                if reporter is not None:
                    reporter.estadillo(
                        validacion["vuelos"],
                        ruta_manifest=res["ruta_manifest"],
                    )
            except Exception:  # noqa: BLE001 - fail-open
                pass

            self._push_cloud(
                {
                    "kind": "done",
                    "scope": "estadillo",
                    "ruta_manifest": res["ruta_manifest"],
                    "vuelos_detectados": validacion["vuelos_detectados"],
                }
            )
        finally:
            self._estadillo_subiendo = False

    def _cuenta_actual(self) -> str | None:
        """El email de la sesión de Google activa, o None sin login."""
        auth = self._get_auth()
        ident = auth.identity if auth is not None else None
        return ident.email if ident else None

    def _reporter_actual(self):
        """`RunReporter` con la sesión activa, o `None` sin login.

        Sin login no hay a quién atribuir la misión ni credencial para
        avisar a la Suite; el crudo ya está en el bucket, así que el
        estadillo queda re-ingestable más tarde en vez de perderse.
        """
        from atom_core.run_reporter import RunReporter

        auth = self._get_auth()
        if auth is None or not getattr(auth, "is_logged_in", lambda: False)():
            return None
        return RunReporter(auth=auth)

    def _log_subida(self, msg: str, *args) -> None:
        """Log local del reporte de subida. Nunca lanza: se usa en rutas
        fail-open donde una excepción del propio log sería absurda."""
        try:
            import logging

            from atom_core import upload_log

            logging.getLogger(upload_log.LOGGER_NAME).info(msg, *args)
        except Exception:  # noqa: BLE001 - fail-open
            pass

    def _on_stats_subida(self, reporter, s: dict) -> None:
        """`on_stats` de la subida: repinta el kiosco y late hacia la Suite.

        Corre en los hilos de subida, por eso el repintado local va PRIMERO:
        es lo que ve el operario y no puede quedar detras de una llamada de
        red. `RunReporter.progreso` ya trae throttle propio y manda el PATCH
        en un hilo aparte, asi que llamarlo en cada snapshot no frena nada.
        """
        self._push_cloud({"kind": "stats", **s})
        if reporter is not None:
            reporter.progreso(s)

    def _cerrar_run(self, reporter, *, ok: bool, error: str | None = None) -> None:
        """Cierra el run de progreso, si lo hubo. Sin `reporter` es no-op.

        Separado de `_reportar_subida` porque son dos destinos distintos
        (`organizer_runs` vs `organizer_subidas`) y uno puede existir sin el
        otro: hay run sin `inspeccion_id`, y hay reporte de subida aunque la
        Suite rechazara el alta del run.
        """
        if reporter is None:
            return
        reporter.fin(ok=ok, error=error)

    def _reportar_subida(self, inspeccion_id: int | None, plan, *,
                          estado: str, error: str | None = None) -> None:
        """Avisa a la Suite del resultado de una subida (`RunReporter.subida`).

        Sin `inspeccion_id` no se reporta nada (solo queda en el log local):
        no hay fallback por `planta/tipo/anio`, ver diseño. Telemetría del
        PLAN, no de lo subido (`plan.items`/`plan.total_bytes`, nunca
        `res.uploaded`/`res.bytes_sent`): en un reintento donde todo ya
        estaba en destino esas cifras serían 0 para una subida completa.
        """
        if inspeccion_id is None:
            self._log_subida(
                "cloud_upload: sin inspeccion_id, no se reporta a la Suite (estado=%s)",
                estado)
            return
        reporter = self._reporter_actual()
        if reporter is None:
            return
        num_objetos = len(plan.items) if plan is not None else None
        bytes_total = plan.total_bytes if plan is not None else None
        reporter.subida(inspeccion_id=inspeccion_id, estado=estado,
                        num_objetos=num_objetos, bytes=bytes_total, error=error)

    def _subir_objeto_fichero(self, remoto: str, ruta_local: str, *,
                              prefix: str | None = None,
                              inspeccion_id: int | None = None,
                              planta_id: int | None = None,
                              ambito: str | None = None) -> None:
        """Puente fino a `cloud_upload.upload_file`: sube un fichero local ya
        existente al objeto `remoto` del bucket. Sin lógica propia."""
        from atom_core import cloud_config, cloud_upload

        provider = cloud_upload.GcsOAuthProvider(
            cloud_config.BUCKET_DATOS, self._get_auth(),
            prefix=prefix, inspeccion_id=inspeccion_id,
            planta_id=planta_id, ambito=ambito)
        item = cloud_upload.UploadItem(
            local=Path(ruta_local), remote=remoto, size=os.path.getsize(ruta_local),
        )
        cloud_upload.upload_file(item, provider)

    def _subir_objeto_json(self, remoto: str, contenido: dict, *,
                           prefix: str | None = None,
                           inspeccion_id: int | None = None,
                           planta_id: int | None = None,
                           ambito: str | None = None) -> None:
        """Puente fino: vuelca `contenido` a un fichero temporal y lo sube
        como si fuera un objeto normal, reutilizando `_subir_objeto_fichero`."""
        import tempfile

        data = json.dumps(contenido, ensure_ascii=False, indent=2).encode("utf-8")
        # El `finally` engloba también la escritura: con `delete=False`, un fallo
        # en `tmp.write` (disco lleno) dejaría el temporal huérfano.
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
                tmp_path = tmp.name
                tmp.write(data)
            self._subir_objeto_fichero(remoto, tmp_path, prefix=prefix,
                                       inspeccion_id=inspeccion_id,
                                       planta_id=planta_id, ambito=ambito)
        finally:
            if tmp_path is not None:
                os.unlink(tmp_path)

    def cloud_pendientes(self) -> dict:
        return {"pendientes": cola_subidas.pendientes()}

    def cloud_drenar(self) -> dict:
        """Lanza las subidas encoladas. Solo tiene sentido con estado `ok`.

        Va de una en una: `cloud_upload` ya rechaza si hay otra subida en curso,
        y el resto de la cola sigue ahí para el siguiente intento.

        Contrato: lanza COMO MUCHO 1 job por llamada (se corta en el primer
        `started`), aunque haya varios pendientes. Quien quiera vaciar la cola
        entera tiene que volver a llamar cuando esa subida termine; no
        encadena drenajes automáticos, porque eso tocaría el camino crítico
        de subida al bucket.
        """
        if self._credencial.actual()["estado"] != ESTADO_OK:
            return {"lanzados": 0, "reason": "Sin credencial válida."}
        lanzados = 0
        for job in cola_subidas.pendientes():
            if job.get("tipo") == cola_subidas.TIPO_RESULTADO:
                r = self.resultado_subir(job["folder"], job.get("inspeccion_id"), "urgencia", True)
                if r.get("started"):
                    lanzados += 1
                    break
                cola_subidas.marcar_intento(job["id"], str(r.get("reason", "")))
                continue
            r = self.cloud_upload(job["folder"], prefix=job["prefix"],
                                  inspeccion_id=job.get("inspeccion_id"))
            if r.get("started"):
                cola_subidas.descartar(job["id"])
                lanzados += 1
                break
            cola_subidas.marcar_intento(job["id"], str(r.get("reason", "")))
        return {"lanzados": lanzados}

    def cloud_cancel(self) -> dict:
        """Pide parar. Los ficheros ya subidos quedan; el manifiesto local deja
        que una subida posterior siga donde se quedó sin repetirlos."""
        self._cancel_upload = True
        return {"ok": True}

    def _push_cloud(self, detail: dict) -> None:
        if not self._sink:
            return
        self._sink.dispatch("atom:cloud", detail)

    def _push_resultado(self, detail: dict) -> None:
        if not self._sink:
            return
        self._sink.dispatch("atom:resultado", detail)

    def resultado_cancelar(self) -> dict:
        """Para la subida del resultado. Lo ya subido queda; la siguiente pasada continúa."""
        self._cancel_resultado = True
        return {"ok": True}

    def resultado_subir(self, folder: str, inspeccion_id: int | None, modo: str = "urgencia",
                        seguir_en_normal: bool = False) -> dict:
        """Sube la salida organizada a `plantas_pv_nl`. Progreso por `atom:resultado`.

        `modo='urgencia'` sube solo lo imprescindible para el análisis; con
        `seguir_en_normal` encadena después el modo normal (completa el resto sin resubir
        nada). Un fallo de red/credencial deja el trabajo en `cola_subidas` (tipo
        `resultado`) para reintentarlo; un error de modo o de datos NO se reintenta solo.
        """
        from atom_core import cola_subidas as cola, subida_resultado as sr
        from atom_core.google_auth import AuthError

        if modo not in sr.MODOS:
            return {"started": False, "reason": f"Modo de subida desconocido: {modo}."}
        if not inspeccion_id:
            return {"started": False, "reason": "Elige una inspección: sin ella no hay destino en el bucket."}
        if not Path(folder or "").is_dir():
            return {"started": False, "reason": "La carpeta de salida no existe."}
        with self._resultado_lock:
            if self._subiendo_resultado:
                return {"started": False, "reason": "Ya hay una subida del resultado en curso."}
            self._subiendo_resultado = True
        self._cancel_resultado = False
        auth = self._get_auth()

        def worker() -> None:
            modos = [sr.MODO_URGENCIA, sr.MODO_NORMAL] if (modo == sr.MODO_URGENCIA and seguir_en_normal) else [modo]
            try:
                self._push_resultado({"kind": "start", "modo": modo})
                for i, m in enumerate(modos):
                    resumen = sr.subir_resultado(
                        folder, int(inspeccion_id), m, auth, on_estado=lambda e: self._push_resultado({"kind": "estado", **e}),
                        should_stop=lambda: self._cancel_resultado)
                    sigue = resumen.ok and i < len(modos) - 1
                    self._push_resultado({"kind": "done", "continua": sigue, **resumen.a_dict()})
                    if not sigue:
                        break
                for j in cola.pendientes():
                    if j.get("tipo") == cola.TIPO_RESULTADO and j["folder"] == str(folder) and j.get("inspeccion_id") == inspeccion_id:
                        if resumen.ok:
                            cola.descartar(j["id"])
            except sr.SubidaResultadoError as exc:
                self._push_resultado({"kind": "error", "text": str(exc)})
            except (AuthError, OSError) as exc:
                cola.encolar(str(folder), "@resultado", inspeccion_id, tipo=cola.TIPO_RESULTADO)
                self._push_resultado({"kind": "error", "text": f"{exc} La subida queda pendiente y se reintentará."})
            except Exception as exc:  # noqa: BLE001 - la UI tiene que enterarse SIEMPRE
                logger.exception("resultado_subir falló")
                self._push_resultado({"kind": "error", "text": f"{type(exc).__name__}: {exc}"})
            finally:
                self._subiendo_resultado = False

        threading.Thread(target=worker, daemon=True).start()
        return {"started": True}

    def _auto_subir_resultado(self, params: dict, done: dict | None) -> None:
        """Disparo automático al terminar un `split_images`: urgencia primero y, sin parar,
        normal en segundo plano. Si procedería pero falta algo, lo dice (evento `aviso`)."""
        from atom_core import subida_resultado as sr

        sube, motivo = sr.decidir_automatica("split_images", params, done)
        if not sube:
            if motivo:
                self._push_resultado({"kind": "aviso", "text": motivo})
            return
        destino = (params.get("destino") or params.get("output_folder") or "").strip()
        self.resultado_subir(destino, params.get("inspeccion_id"), sr.MODO_URGENCIA, True)

    def _push_analisis(self, detail: dict) -> None:
        if not self._sink:
            return
        self._sink.dispatch("atom:analisis", detail)

    # ---- disparo del pipeline ---------------------------------------------
    def run_organize(self, params: dict, advanced: dict | None = None) -> dict:
        """Atajo de la pantalla principal: "Organizar completo". `advanced` son
        overrides de SplitImagesConfig del panel Modo avanzado (o None)."""
        return self.run_task("split_images", params, advanced)

    def run_task(self, task: str, params: dict, advanced: dict | None = None) -> dict:
        """Arranca un task del pipeline en un hilo aparte. Devuelve al instante;
        el progreso llega a React por eventos `atom:progress`."""
        # El refresco del indicador de la nube va en SU PROPIO hilo, nunca aquí:
        # `cloud_asegurar_estado` hace red (refresh del token contra Google, 30 s
        # de plazo, y el lock de GoogleAuth puede estar tomado por la comprobación
        # de arranque). Llamarla en línea bloqueaba el hilo del bridge Qt, así que
        # `run_task` no llegaba a devolver: la promesa del front no resolvía nunca
        # y el modal se quedaba en "Preparando…" para siempre, sin un solo evento.
        # Organizar es 100 % local y no depende de esta respuesta para nada.
        threading.Thread(target=self._cloud_estado_en_segundo_plano, daemon=True).start()
        if self._running:
            return {"started": False, "reason": "Ya hay un proceso en curso."}
        self._running = True
        cancelacion.limpiar()  # un run nuevo no hereda la cancelación del anterior
        # Estado del panel de control remoto (`GET /api/control/estado`): un
        # run nuevo empieza limpio, sin arrastrar la fase/progreso/error del
        # anterior (`_push` los va actualizando mientras corre).
        self._control_fase = None
        self._control_progreso = None
        self._control_ultimo_error = None
        threading.Thread(
            target=self._run_task_worker, args=(task, params, advanced), daemon=True
        ).start()
        return {"started": True}

    def run_cancelar(self) -> dict:
        """Pide parar el run en curso (botón «Cancelar» del modal de progreso).

        Cooperativo: el pipeline mira la bandera en puntos seguros (entre
        ficheros/filas/fases), termina el fichero en curso y emite `done` con
        status `cancelled`. Lo ya organizado se conserva (manifiesto) y se
        retoma al relanzar. No toca el origen."""
        if not self._running:
            return {"ok": False, "reason": "No hay ningún proceso en curso."}
        logger.info("run_cancelar: solicitud de cancelación recibida; se para en el próximo punto seguro")
        cancelacion.solicitar()
        return {"ok": True}

    def _cloud_estado_en_segundo_plano(self) -> None:
        """Refresca el indicador de credencial sin bloquear a quien lo pidió."""
        try:
            self.cloud_asegurar_estado()
        except Exception as exc:  # noqa: BLE001 — el indicador es cortesía, no requisito
            logger.warning("cloud_asegurar_estado en segundo plano falló: %s", exc)

    def _run_task_worker(self, task: str, params: dict, advanced: dict | None) -> None:
        ultimo_done: dict = {}

        def emit(kind: str, payload) -> None:
            if kind == "done" and isinstance(payload, dict):
                ultimo_done.update(payload)
            detail = {"kind": kind}
            if kind == "progress":
                detail["value"] = int(payload)
            elif kind in ("plan", "phase", "stats", "done"):
                detail["data"] = payload  # list / dict estructurado
            elif payload is not None:
                detail["text"] = str(payload)
            self._push(detail)

        try:
            # Import perezoso: atom_core arrastra gui.py/PySide → no lo cargamos
            # al abrir la ventana, solo al primer run. Va DENTRO del try porque
            # estando fuera un fallo aquí (una dependencia que PyInstaller no
            # empaquetó, un DLL del SDK ausente) mataba el hilo en silencio: ni un
            # evento para el front, `_running` clavado en True para siempre y el
            # modal en "Preparando…" sin forma de salir. Ahora se reporta.
            # El candado de `precargar_pandas` serializa el primer import de pandas
            # con el resto de hilos (ver atom_core/precarga.py).
            precarga.precargar_pandas()
            from atom_core.organize import run_task

            if task == "split_images":
                params = self._mover_estadillo_espera_si_toca(params)
            run_task(task, params, emit, advanced or None)
            if task == "split_images":
                try:
                    self._auto_subir_resultado(params, ultimo_done or None)
                except Exception:  # noqa: BLE001 - subir no puede tumbar el cierre del run
                    logger.exception("disparo automático de la subida del resultado")
        except Exception as exc:  # noqa: BLE001 — el front tiene que enterarse SIEMPRE
            logger.exception("El task %s murió antes de poder informar", task)
            emit("error", f"{type(exc).__name__}: {exc}")
        except cancelacion.RunCancelado:
            # Cancelación que no pasó por el manejo de organize.run_task: sin
            # esto la UI se quedaba en «Cancelando…» (BaseException).
            emit("done", {"status": "cancelled", "cancelled": True,
                          "mensaje": "Cancelado por el usuario"})
        finally:
            self._running = False
            cancelacion.limpiar()  # no dejar la bandera armada tras cancelar
            # Sin esto, lo que quedara en el buffer cuando el pipeline deja de
            # emitir no llegaria nunca: el vaciado lo dispara el evento
            # SIGUIENTE, y despues del ultimo no hay ninguno.
            self._flush_push()

    def _mover_estadillo_espera_si_toca(self, params: dict) -> dict:
        """Si hay un estadillo recibido por LAN (modo espera, ver
        `_estadillo_recibir`) pendiente para ESTA carpeta, lo mueve de
        `estadillos_recibidos_dir()` a la carpeta que se va a organizar
        (`params["origen"]`) justo AHORA, al empezar -nunca antes: hasta
        este momento el fichero se queda donde "Estadillo Digital" lo dejó.

        Solo mueve el estadillo asociado a la espera/inspección actual: si
        la carpeta de la espera no coincide con la de este run (otra
        carpeta, u otra espera ya cancelada/sustituida), no toca nada. Si el
        move falla o no aplica, el run sigue con `params` tal cual -nunca
        bloquea- y queda un warning en el log.
        """
        try:
            with self._estadillo_espera_lock:
                estado = self._estadillo_espera
                if estado is None or not estado.get("recibido") or not estado.get("rutas"):
                    return params
                origen = (params.get("origen") or params.get("input_folder") or "").strip()
                carpeta_espera = estado.get("carpeta")
                if not origen or not os.path.isdir(origen):
                    return params
                if not carpeta_espera or os.path.normpath(carpeta_espera) != os.path.normpath(origen):
                    return params  # estadillo de otra carpeta/espera: no es el de este run
                ruta_vieja = estado["rutas"][0]

            from atom_core import estadillo as estadillo_mod

            ruta_nueva = estadillo_mod.mover_estadillo_recibido_a_carpeta(ruta_vieja, origen)
            if not ruta_nueva:
                logger.warning(
                    "No se pudo mover el estadillo recibido por LAN (%s) a %s: "
                    "el run sigue con la ruta original.", ruta_vieja, origen)
                return params

            with self._estadillo_espera_lock:
                if self._estadillo_espera is estado:
                    estado["rutas"] = [ruta_nueva]

            nuevos = dict(params)
            rutas_actuales = estadillo_mod.desempaquetar_rutas(nuevos.get("estadillo") or nuevos.get("estad") or "")
            rutas_actuales = [ruta_nueva if r == ruta_vieja else r for r in rutas_actuales]
            if "estadillo" in nuevos:
                nuevos["estadillo"] = estadillo_mod.empaquetar_rutas(rutas_actuales)
            if "estad" in nuevos:
                nuevos["estad"] = estadillo_mod.empaquetar_rutas(rutas_actuales)
            return nuevos
        except Exception as exc:  # noqa: BLE001 — nunca debe bloquear el run
            logger.warning("Fallo moviendo el estadillo recibido por LAN: %s", exc)
            return params

    # Cada cuanto (segundos) y cada cuantos eventos se vacia el buffer de
    # progreso. 0,15 s es el limite por debajo del cual el ojo ya no distingue
    # que la barra avanza a saltos, y mantiene el coste de UI en ~7 viajes/s
    # pase lo rapido que pase el pipeline.
    _PUSH_INTERVALO = 0.15
    _PUSH_MAX_BUFER = 200

    def _push(self, detail: dict) -> None:
        """Encola un evento para React (Python → JS).

        No lo manda al momento a proposito: `evaluate_js` de pywebview/Qt es
        SINCRONO (crea un `Semaphore(0)`, dispara la señal al hilo de UI y hace
        `acquire()` hasta que Chromium ejecuta el JS), asi que cada evento
        PARABA el hilo del pipeline hasta que React terminaba de renderizar. Con
        dos eventos por imagen eso ataba la velocidad de organizar al ritmo de
        repintado del navegador — y en Windows, donde el compositing va por
        software (`--disable-gpu`, ver `main`), ese ritmo es lento.

        Los eventos estructurados (`plan`/`phase`/`stats`/`done`) fuerzan el
        vaciado: marcan cambios de estado que la UI no puede mostrar con retraso,
        y `done` ademas cierra la corrida.
        """
        # Ultimo progreso/fase/error para `GET /api/control/estado`: se
        # guarda pase o no `self._sink` (el panel de control puede consultar
        # `/estado` aunque nadie este escuchando el SSE ahora mismo).
        kind = detail.get("kind")
        if kind == "progress":
            self._control_progreso = detail.get("value")
        elif kind in ("phase", "plan"):
            self._control_fase = detail.get("data")
        elif kind == "error":
            self._control_ultimo_error = detail.get("text")
        if not self._sink:
            return
        with self._push_lock:
            self._push_buf.append(detail)
            urgente = detail.get("kind") in ("plan", "phase", "stats", "done")
            ahora = time.monotonic()
            if not urgente and len(self._push_buf) < self._PUSH_MAX_BUFER \
                    and (ahora - self._push_last) < self._PUSH_INTERVALO:
                return
        self._flush_push()

    def _flush_push(self) -> None:
        """Suelta el buffer en UNA sola llamada a `evaluate_js`.

        Va todo en un unico script con N `dispatchEvent` seguidos, en vez de N
        llamadas: lo caro no es el `dispatchEvent` (microsegundos) sino el viaje
        con semaforo hasta el hilo de UI. Y como React 19 agrupa por defecto los
        `setState` que ocurren dentro de la misma tarea del bucle de eventos,
        los N eventos producen UN solo re-render en lugar de N.

        Los `progress` intermedios se descartan y solo sobrevive el ultimo: es
        un porcentaje, y pintar el 41 % para pisarlo con el 47 % en el mismo
        fotograma no lo ve nadie. El texto del log NO se toca — cada linea se
        entrega tal cual y en orden, que ahi si se perderia informacion.
        """
        with self._push_lock:
            if not self._push_buf:
                return
            pendientes, self._push_buf = self._push_buf, []
            self._push_last = time.monotonic()

        ultimo_progress = None
        for d in pendientes:
            if d.get("kind") == "progress":
                ultimo_progress = d
        compactados = [
            d for d in pendientes
            if d.get("kind") != "progress" or d is ultimo_progress
        ]

        if self._sink:
            self._sink.dispatch_many("atom:progress", compactados)

    # ---- control del sistema (modo servidor / Raspberry Pi) ---------------
    def sistema_apagar(self, modo: str) -> dict:
        """Apaga o reinicia el equipo desde el modo servidor (kiosco Raspberry Pi).

        Sin `sudo`: en la Pi hay una regla de polkit que autoriza a este
        usuario a ejecutar `systemctl poweroff`/`reboot` sin contraseña, asi
        que invocar `sudo` aqui solo anadiria un paso que pide password y
        rompe el flujo no interactivo del kiosco.
        """
        if modo not in ("poweroff", "reboot"):
            return {"ok": False, "error": "modo no valido"}
        try:
            proc = subprocess.run(
                ["systemctl", modo],
                check=False, capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0:
                return {"ok": True}
            return {"ok": False, "error": proc.stderr.strip() or f"returncode={proc.returncode}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def red_listar(self) -> dict:
        """Lista las redes wifi visibles (modo servidor / Raspberry Pi).

        Usa `nmcli -t` (salida estable en formato tabulado con ':') en vez
        del formato humano, para no depender de columnas alineadas. El SSID
        puede contener ':' escapado como '\\:', asi que el parseo no puede
        ser un split(':') ingenuo.
        """
        try:
            proc = subprocess.run(
                ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY", "device", "wifi", "list"],
                check=False, capture_output=True, text=True, timeout=15,
            )
            if proc.returncode != 0:
                return {"ok": False, "error": proc.stderr.strip() or f"returncode={proc.returncode}"}
            redes, actual = _parse_nmcli_wifi(proc.stdout)
            # `guardada` deja que el kiosco conecte de un toque a una red ya
            # conocida en vez de abrir el teclado a pedir una clave que la Pi
            # ya tiene. El hotspot propio no cuenta: no es una red a la que
            # conectarse.
            guardados = set(self._perfiles_wifi_por_ssid()) - {_AP_SSID}
            for red in redes:
                red["guardada"] = red.get("ssid") in guardados
            return {"ok": True, "actual": actual, "redes": redes}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def red_conexion(self) -> dict:
        """Como esta conectada la Pi ahora mismo (cable / wifi / nada).

        Lo pinta el indicador del home del kiosco, que se refresca cada pocos
        segundos: por eso NO puede provocar un escaneo wifi (`--rescan no`),
        que tarda segundos y ademas tumba el throughput de la propia wifi.
        El cable manda sobre la wifi si ambos estan arriba: es la ruta buena.
        """
        # En Windows no hay `nmcli`: cada llamada (el indicador la repite cada
        # 10s) lanzaria un subprocess condenado a fallar, sin ningun dato util.
        if platform.system() != "Linux":
            return {"ok": True, "tipo": "ninguna", "ssid": "", "senal": None, "ip": ""}
        try:
            proc = subprocess.run(
                ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"],
                check=False, capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0:
                return {"ok": False, "error": proc.stderr.strip() or f"returncode={proc.returncode}"}
            cable = wifi = None
            for linea in proc.stdout.splitlines():
                campos = _split_nmcli_line(linea)
                if len(campos) < 4 or campos[2] != "connected":
                    continue
                # El hotspot propio no es "estar conectado a una red".
                if campos[3] == "atom-ap":
                    continue
                if campos[1] == "ethernet" and cable is None:
                    cable = campos
                elif campos[1] == "wifi" and wifi is None:
                    wifi = campos
            elegido = cable or wifi
            if elegido is None:
                return {"ok": True, "tipo": "ninguna", "ssid": "", "senal": None, "ip": ""}
            tipo = "cable" if elegido is cable else "wifi"
            # CONNECTION es el nombre del PERFIL (`netplan-wlan0-CASA`), no el
            # SSID: para el indicador hace falta el SSID real del AP en uso.
            ssid, senal = self._wifi_en_uso() if tipo == "wifi" else ("", None)
            if tipo == "wifi" and not ssid:
                ssid = elegido[3]
            return {
                "ok": True, "tipo": tipo, "ssid": ssid,
                "senal": senal, "ip": self._ip_dispositivo(elegido[0]),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def disco_estado(self) -> dict:
        """Si hay un disco externo montado ahora mismo.

        Lo pinta el indicador del kiosco, que se refresca por polling cada
        10s: por eso solo mira montajes ya hechos (glob + stat), sin ningun
        escaneo lento ni subprocess.
        """
        try:
            if not sys.platform.startswith("linux"):
                return {"ok": True, "conectado": False}
            return {"ok": True, "conectado": _disco_externo() is not None}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ---- historial de procesos (logs de corrida de organize.run_task) -----
    def logs_listar(self) -> dict:
        """Resumen de los runs anteriores para la pantalla «Historial de
        procesos»: uno por fichero `atom-organizer-run_*.log` de
        `user_log_dir()`, más recientes primero. No lee ningún fichero
        entero (ver `_resumir_log_run`), así que es seguro llamarlo aunque
        haya cientos de corridas acumuladas."""
        try:
            from external_tools import user_log_dir
            carpeta = user_log_dir()
        except Exception as exc:  # noqa: BLE001 — se reenvía al front
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "runs": []}
        if not os.path.isdir(carpeta):
            return {"ok": True, "runs": []}
        try:
            nombres = os.listdir(carpeta)
        except OSError as exc:
            return {"ok": False, "error": str(exc), "runs": []}
        runs = []
        for nombre in nombres:
            m = _NOMBRE_LOG_RUN.match(nombre)
            if not m:
                continue
            try:
                resumen = _resumir_log_run(os.path.join(carpeta, nombre), nombre, m)
            except Exception:
                continue  # log corrupto/ilegible: se salta, no rompe el listado
            if resumen is not None:
                runs.append(resumen)
        runs.sort(key=lambda r: r["fecha"], reverse=True)
        return {"ok": True, "runs": runs[:200]}

    def logs_leer(self, nombre: str) -> dict:
        """Contenido de un log de corrida concreto.

        SEGURIDAD: `nombre` viaja desde el front (y, en modo servidor, desde
        un cliente HTTP), así que se valida como basename puro — nada de
        separadores de ruta ni `..` — y además se comprueba que la ruta
        resuelta cae DENTRO de `user_log_dir()`. Sin esto, un nombre como
        `../../Config.ini` o una ruta absoluta dejaría leer cualquier
        fichero del disco del usuario.
        """
        try:
            from external_tools import user_log_dir
            carpeta = user_log_dir()
        except Exception as exc:  # noqa: BLE001 — se reenvía al front
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        nombre = str(nombre or "")
        if (not nombre or "/" in nombre or "\\" in nombre or nombre in (".", "..")
                or os.path.basename(nombre) != nombre):
            return {"ok": False, "error": "Nombre de log no válido."}

        # `realpath` (no `abspath`): resuelve symlinks ANTES de comprobar la
        # contención, si no un enlace con nombre válido dentro de la carpeta
        # serviría el contenido de cualquier fichero del disco.
        carpeta_abs = os.path.realpath(carpeta)
        ruta = os.path.realpath(os.path.join(carpeta_abs, nombre))
        try:
            dentro = os.path.commonpath([ruta, carpeta_abs]) == carpeta_abs
        except ValueError:  # unidades distintas en Windows
            dentro = False
        if not dentro or not os.path.isfile(ruta):
            return {"ok": False, "error": "No se encontró ese log."}

        # Fichero grande: solo la COLA (1 MB), que es lo que interesa ante un
        # fallo — el principio ya se ve en el resumen de logs_listar.
        _LIMITE = 1024 * 1024
        try:
            tam = os.path.getsize(ruta)
            with open(ruta, "rb") as f:
                if tam > _LIMITE:
                    f.seek(-_LIMITE, os.SEEK_END)
                    f.readline()  # descarta la línea partida por el seek
                    crudo = f.read()
                    truncado = True
                else:
                    crudo = f.read()
                    truncado = False
            return {
                "ok": True,
                "texto": crudo.decode("utf-8", errors="replace"),
                "truncado": truncado,
                "bytes": tam,
            }
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def logs_carpeta(self) -> dict:
        """Ruta de la carpeta de logs (`user_log_dir()`), para que el usuario
        pueda ir a buscarla a mano (p. ej. para adjuntarla a un correo de
        soporte)."""
        try:
            from external_tools import user_log_dir
            carpeta = user_log_dir()
            os.makedirs(carpeta, exist_ok=True)
            return {"ok": True, "ruta": carpeta}
        except Exception as exc:  # noqa: BLE001 — se reenvía al front
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def _wifi_en_uso(self) -> tuple[str, int | None]:
        """(ssid, senal 0-100) de la wifi en uso, sin forzar escaneo."""
        try:
            proc = subprocess.run(
                ["nmcli", "-t", "-f", "IN-USE,SIGNAL,SSID", "device", "wifi", "list", "--rescan", "no"],
                check=False, capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0:
                return "", None
            for linea in proc.stdout.splitlines():
                campos = _split_nmcli_line(linea)
                if len(campos) >= 3 and campos[0].strip() == "*":
                    try:
                        return campos[2], int(campos[1])
                    except ValueError:
                        return campos[2], None
        except Exception:
            return "", None
        return "", None

    def _ip_dispositivo(self, dispositivo: str) -> str:
        """IPv4 (sin prefijo) del dispositivo, o cadena vacia si no tiene."""
        try:
            proc = subprocess.run(
                ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", dispositivo],
                check=False, capture_output=True, text=True, timeout=10,
            )
            for linea in proc.stdout.splitlines():
                if ":" in linea:
                    valor = linea.split(":", 1)[1].strip()
                    if valor:
                        return valor.split("/")[0]
        except Exception:
            return ""
        return ""

    def red_conectar(self, ssid: str, password: str | None = None) -> dict:
        """Conecta a una red wifi por SSID (modo servidor / Raspberry Pi).

        Nunca se debe filtrar la password: ni en el comando (se pasa como
        argumento a subprocess, nunca por shell) ni en el error devuelto,
        que se sanitiza si por lo que sea nmcli la reflejase en stderr.
        """
        if password:
            cmd = ["nmcli", "device", "wifi", "connect", ssid, "password", password]
        else:
            cmd = ["nmcli", "device", "wifi", "connect", ssid]
        # nmcli tarda varios segundos: dejar constancia del intento para que la
        # pantalla de la Pi pueda mostrar "Conectando a X" mientras tanto.
        self._ap_intento = ssid
        try:
            proc = subprocess.run(
                cmd, check=False, capture_output=True, text=True, timeout=60,
            )
            if proc.returncode != 0 and password and _falta_key_mgmt(proc.stderr):
                # La Pi ya trae un perfil guardado de esa wifi (el que crea
                # netplan) sin la seccion de seguridad: nmcli lo reutiliza y
                # aborta con "key-mgmt: property is missing". Se completa el
                # perfil en vez de borrarlo, porque el script de rescate de
                # wifi depende de que ese perfil siga existiendo con su nombre.
                if self._reparar_perfil_wifi(ssid, password):
                    proc = subprocess.run(
                        cmd, check=False, capture_output=True, text=True, timeout=60,
                    )
            if proc.returncode == 0:
                # Conexion wifi lograda: si el hotspot de configuracion seguia
                # activo (usuario completo el flujo desde el movil), se apaga
                # para devolver la Pi a la red normal sin esperar al timeout.
                # El guard es `_ap_token` y no `red_ap_estado()` a proposito:
                # solo lo levantamos nosotros lo apagamos nosotros, y asi la
                # ruta normal (conectar desde la pantalla de la Pi) no paga un
                # nmcli extra por cada conexion.
                if getattr(self, "_ap_token", ""):
                    self.red_ap_desactivar()
                self._ap_intento = ""
                return {"ok": True}
            self._ap_intento = ""
            error = proc.stderr.strip() or f"returncode={proc.returncode}"
            if password:
                error = error.replace(password, "***")
            return {"ok": False, "error": error}
        except Exception as exc:
            self._ap_intento = ""
            error = str(exc)
            if password:
                error = error.replace(password, "***")
            return {"ok": False, "error": error}

    def _perfiles_wifi_por_ssid(self) -> dict[str, list[str]]:
        """Mapa SSID -> perfiles NM guardados para el.

        El nombre del perfil no tiene por que coincidir con el SSID (netplan
        los llama `netplan-wlan0-<SSID>`), asi que hay que preguntarle a cada
        uno por su SSID real. Se hace en una sola pasada porque lo consumen
        tanto el listado de redes como la reparacion del perfil.
        """
        listado = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
            check=False, capture_output=True, text=True, timeout=15,
        )
        if listado.returncode != 0:
            return {}
        mapa: dict[str, list[str]] = {}
        for linea in listado.stdout.splitlines():
            nombre, _, tipo = linea.rpartition(":")
            if tipo != "802-11-wireless" or not nombre:
                continue
            det = subprocess.run(
                ["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", nombre],
                check=False, capture_output=True, text=True, timeout=15,
            )
            ssid = det.stdout.strip() if det.returncode == 0 else ""
            if ssid:
                mapa.setdefault(ssid, []).append(nombre)
        return mapa

    def _perfiles_de_ssid(self, ssid: str) -> list[str]:
        return self._perfiles_wifi_por_ssid().get(ssid, [])

    def _reparar_perfil_wifi(self, ssid: str, password: str) -> bool:
        """Completa `key-mgmt`/`psk` en los perfiles guardados de ese SSID.

        Devuelve True si toco al menos uno, para que la llamada decida si
        merece la pena reintentar la conexion.
        """
        reparado = False
        for perfil in self._perfiles_de_ssid(ssid):
            mod = subprocess.run(
                ["nmcli", "connection", "modify", perfil,
                 "802-11-wireless-security.key-mgmt", "wpa-psk",
                 "802-11-wireless-security.psk", password],
                check=False, capture_output=True, text=True, timeout=20,
            )
            reparado = reparado or mod.returncode == 0
        return reparado

    # ---- hotspot de configuracion (Raspberry Pi sin teclado) ---------------
    def _ap_password(self) -> str:
        """Password estable del hotspot: se genera una vez y se persiste en el
        mismo Config.ini de usuario (seccion "paths", junto a ruta_thermoviewer)
        para que el QR impreso/mostrado no cambie entre arranques."""
        import configparser
        import secrets
        import string
        from external_tools import _user_config_path

        path = _user_config_path()
        cfg = configparser.ConfigParser()
        cfg.optionxform = str
        if os.path.exists(path):
            cfg.read(path)
        pwd = cfg.get("paths", "ap_password", fallback="") if cfg.has_section("paths") else ""
        if pwd:
            return pwd
        alfabeto = string.ascii_letters + string.digits
        pwd = "".join(secrets.choice(alfabeto) for _ in range(10))
        if not cfg.has_section("paths"):
            cfg.add_section("paths")
        cfg.set("paths", "ap_password", pwd)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            cfg.write(f)
        return pwd

    def red_ap_estado(self) -> dict:
        """Estado del hotspot de configuracion (con-name fijo `atom-ap`)."""
        try:
            proc = subprocess.run(
                ["nmcli", "-t", "-f", "NAME", "connection", "show", "--active"],
                check=False, capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0:
                return {"ok": False, "error": proc.stderr.strip() or f"returncode={proc.returncode}"}
            activo = "atom-ap" in proc.stdout.splitlines()
            intento = getattr(self, "_ap_intento", "")
            if not activo:
                return {"ok": True, "activo": False, "ssid": "", "password": "",
                        "ip": "", "token": "", "intento": intento}
            return {
                "ok": True, "activo": True, "ssid": _AP_SSID,
                "password": self._ap_password(), "ip": "10.42.0.1",
                "token": getattr(self, "_ap_token", ""), "intento": intento,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def red_ap_activar(self) -> dict:
        """Levanta el hotspot para que el usuario configure la wifi desde el
        movil (pantalla de la Pi es 480x320, inviable teclear ahi)."""
        import secrets
        try:
            # Guarda la conexion wifi actual para poder restaurarla al apagar
            # el hotspot (nmcli no la conserva automaticamente).
            proc = subprocess.run(
                ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"],
                check=False, capture_output=True, text=True, timeout=10,
            )
            previa = ""
            if proc.returncode == 0:
                for linea in proc.stdout.splitlines():
                    campos = linea.split(":")
                    if len(campos) >= 2 and campos[1] == "802-11-wireless":
                        previa = campos[0]
                        break
            # Si el AP ya estaba levantado (segunda pulsacion, o reabrir la
            # pantalla), la conexion wifi "activa" ES el propio hotspot: guardarla
            # como previa haria que al cerrar el AP intentasemos restaurar
            # `atom-ap` y la Pi se quedase SIN RED. Solo se apunta la previa la
            # primera vez y nunca el propio hotspot.
            if previa and previa != "atom-ap" and not getattr(self, "_ap_conexion_previa", ""):
                self._ap_conexion_previa = previa

            password = self._ap_password()
            hotspot_cmd = [
                "nmcli", "device", "wifi", "hotspot", "ifname", "wlan0",
                "con-name", "atom-ap", "ssid", _AP_SSID, "password", password,
            ]
            proc = subprocess.run(hotspot_cmd, check=False, capture_output=True, text=True, timeout=30)
            if proc.returncode != 0:
                error = (proc.stderr.strip() or f"returncode={proc.returncode}").replace(password, "***")
                return {"ok": False, "error": error}

            # El hotspot NUNCA debe autoarrancar: si algo se tuerce, apagar y
            # encender la Pi tiene que devolverla a la wifi de siempre. Es la
            # unica salvaguarda que sigue valiendo aunque el proceso muera.
            subprocess.run(
                ["nmcli", "connection", "modify", "atom-ap", "connection.autoconnect", "no"],
                check=False, capture_output=True, text=True, timeout=10,
            )

            if not getattr(self, "_ap_token", ""):
                self._ap_token = secrets.token_urlsafe(8)

            # Autoapagado a los 10 min: si nadie completa el flujo desde el
            # movil, no queremos dejar la Pi sin red indefinidamente.
            if self._ap_timer is not None:
                self._ap_timer.cancel()
            self._ap_timer = threading.Timer(600.0, self.red_ap_desactivar)
            self._ap_timer.daemon = True
            self._ap_timer.start()

            return {
                "ok": True, "ssid": _AP_SSID, "password": password,
                "ip": "10.42.0.1", "token": self._ap_token,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def red_ap_desactivar(self) -> dict:
        """Apaga el hotspot y restaura la wifi que hubiera antes, si la hay."""
        try:
            if self._ap_timer is not None:
                self._ap_timer.cancel()
                self._ap_timer = None
            proc = subprocess.run(
                ["nmcli", "connection", "down", "atom-ap"],
                check=False, capture_output=True, text=True, timeout=20,
            )
            if proc.returncode != 0 and "not an active connection" not in (proc.stderr or "").lower():
                self._ap_token = ""
                return {"ok": False, "error": proc.stderr.strip() or f"returncode={proc.returncode}"}

            previa = getattr(self, "_ap_conexion_previa", "")
            self._ap_conexion_previa = ""
            if previa:
                subprocess.run(
                    ["nmcli", "connection", "up", previa],
                    check=False, capture_output=True, text=True, timeout=30,
                )

            self._ap_token = ""
            return {"ok": True}
        except Exception as exc:
            self._ap_token = ""
            return {"ok": False, "error": str(exc)}



def _falta_key_mgmt(stderr: str) -> bool:
    """True si nmcli fallo porque el perfil guardado no declara `key-mgmt`.

    El texto exacto cambia entre versiones y locales de nmcli, asi que se
    busca la propiedad, que es la parte estable del mensaje.
    """
    return "key-mgmt" in (stderr or "")


def _parse_nmcli_wifi(salida: str) -> tuple[list[dict], str | None]:
    """Parsea la salida de `nmcli -t -f ACTIVE,SSID,SIGNAL,SECURITY device wifi list`.

    Devuelve (redes, ssid_activo). Descarta SSID vacios, deduplica por SSID
    quedandose con la senal mas alta, y ordena por senal descendente.
    Tiene en cuenta que el SSID puede traer ':' escapado como '\\:'.
    """
    vistas: dict[str, dict] = {}
    actual = None
    for linea in salida.splitlines():
        if not linea:
            continue
        campos = _split_nmcli_line(linea)
        if len(campos) < 4:
            continue
        active, ssid, signal, security = campos[0], campos[1], campos[2], campos[3]
        if not ssid:
            continue
        try:
            senal = int(signal)
        except ValueError:
            senal = 0
        es_activa = active == "yes"
        if es_activa:
            actual = ssid
        red = {
            "ssid": ssid,
            "senal": senal,
            "segura": bool(security) and security != "--",
            "activa": es_activa,
        }
        existente = vistas.get(ssid)
        if existente is None or red["senal"] > existente["senal"]:
            vistas[ssid] = red
    redes = sorted(vistas.values(), key=lambda r: r["senal"], reverse=True)
    return redes, actual


def _split_nmcli_line(linea: str) -> list[str]:
    """Divide una linea `-t` de nmcli por ':' respetando el escape '\\:'."""
    campos = []
    actual = []
    escapando = False
    for ch in linea:
        if escapando:
            actual.append(ch)
            escapando = False
        elif ch == "\\":
            escapando = True
        elif ch == ":":
            campos.append("".join(actual))
            actual = []
        else:
            actual.append(ch)
    campos.append("".join(actual))
    return campos


def resolve_target(dev: bool) -> str:
    if dev:
        return DEV_URL
    if not DIST_INDEX.exists():
        sys.exit(
            f"[app_webview] Falta el build del front: {DIST_INDEX}\n"
            "Ejecuta:  cd webui && npm run build   (o usa --dev con npm run dev)"
        )
    # El perfil de QtWebEngine persiste entre versiones y, con la misma URL,
    # puede servir el index.html cacheado tras una actualización in-place.
    # El marcador de versión cambia la URL en cada versión sin cambiar el origin
    # (localStorage y sesión se conservan).
    #
    # OJO: tiene que ser un FRAGMENTO (#), no una query (?). El backend
    # WebView2/EdgeChromium (el que usa pywebview en Windows cuando no hay Qt)
    # no resuelve un `?query` sobre `file://`: intenta abrir el fichero literal
    # "index.html?v=3.4.92", no lo encuentra y pinta la pagina de error de Edge
    # (pantalla en blanco / ERR_FILE_NOT_FOUND). El fragmento sí es válido en
    # todos los backends porque no forma parte de la ruta del fichero.
    version = urllib.parse.quote(_app_version_for_title())
    return f"{DIST_INDEX.resolve().as_uri()}#v={version}"


def _app_version_for_title() -> str:
    """Versión para la barra de título. Nunca revienta el arranque por esto."""
    try:
        from atom_core import updater

        return updater.current_version()
    except Exception:
        return "?"


_BACKOFF_ARRANQUE_SEGUNDOS = (5, 10, 20, 40, 80, 160)
_BACKOFF_ARRANQUE_ESTABLE_SEGUNDOS = 300


def _ciclo_comprobacion_arranque(api: Api, dormir=time.sleep, push=None) -> None:
    """Comprueba la credencial al arrancar y reintenta con backoff si falla.

    El kiosco puede arrancar antes de que el wifi resuelva DNS: la primera
    `cloud_comprobar` sale `sin-conexion` aunque la red vaya a funcionar
    segundos después. Esta función reintenta indefinidamente (es un hilo
    daemon; la Pi se apaga sola) con backoff creciente hasta estabilizarse
    en `ESTADO_OK` o `ESTADO_SIN_CREDENCIAL`.

    Extraída de `_comprobar_al_arrancar` como función pura y testeable: sin
    hilos ni sleeps reales, solo llamadas a `api` y a `dormir`/`push`
    inyectables.
    """
    if push is None:
        push = api._push_cloud

    def comprobar() -> dict:
        try:
            return api.cloud_comprobar()
        except Exception as exc:
            # El evento a la UI tiene que llegar siempre, o el aviso de
            # credencial se queda colgado para siempre en el arranque.
            return {"estado": ESTADO_SIN_CONEXION, "mensaje": str(exc)}

    estado = comprobar()
    push({"kind": "session",
          "ok": estado["estado"] == ESTADO_OK,
          "estado": estado["estado"],
          "text": estado["mensaje"]})

    reintentos = 0
    estado_anterior = estado["estado"]
    while estado_anterior == ESTADO_SIN_CONEXION:
        if reintentos < len(_BACKOFF_ARRANQUE_SEGUNDOS):
            espera = _BACKOFF_ARRANQUE_SEGUNDOS[reintentos]
        else:
            espera = _BACKOFF_ARRANQUE_ESTABLE_SEGUNDOS
        dormir(espera)
        reintentos += 1

        estado = comprobar()
        if estado["estado"] != estado_anterior:
            push({"kind": "session",
                  "ok": estado["estado"] == ESTADO_OK,
                  "estado": estado["estado"],
                  "text": estado["mensaje"]})
            logger.info("comprobacion arranque: estado %s -> %s tras %d reintentos",
                        estado_anterior, estado["estado"], reintentos)
        estado_anterior = estado["estado"]

    if estado_anterior == ESTADO_OK:
        api.cloud_drenar()


def _comprobar_al_arrancar(api: Api) -> None:
    """Lanza la primera comprobación de credencial en un hilo aparte.

    Se llama justo después de tener el sink de eventos listo (`bind_sink` /
    `bind_window`). No retrasa el pintado de la UI: `cloud_comprobar` hace red
    y puede tardar. Si sale `sin-conexion`, `_ciclo_comprobacion_arranque`
    reintenta con backoff en el mismo hilo daemon.
    """
    threading.Thread(target=_ciclo_comprobacion_arranque, args=(api,), daemon=True).start()


# Nivel por defecto del log de aplicación, sobreescribible con ATOM_LOG_LEVEL.
NIVEL_LOG_POR_DEFECTO = logging.INFO

# Cuánto se deja crecer el log de aplicación antes de rotar, y cuántas copias se
# guardan. 2 MiB × 3 son suficientes para varias sesiones y no engordan el perfil
# del usuario, que es donde vive (%APPDATA%\ATOM-Organizer\Logs).
LOG_APP_BYTES_MAX = 2 * 1024 * 1024
LOG_APP_COPIAS = 3
LOG_APP_NOMBRE = "atom-organizer-app.log"


def _nivel_log() -> int:
    """Nivel efectivo del log, leído de ATOM_LOG_LEVEL con caída al default."""
    nivel = getattr(logging, os.environ.get("ATOM_LOG_LEVEL", "").upper(), None)
    return nivel if isinstance(nivel, int) else NIVEL_LOG_POR_DEFECTO


def configurar_log_a_fichero(nivel: int | None = None) -> str | None:
    """Engancha un fichero de log al logger raíz. Devuelve la ruta, o None.

    PORQUÉ: hasta ahora `logging.basicConfig` SOLO corría en el camino
    `--server` (el kiosco de la Pi). En el .exe de Windows, que es windowed y
    no tiene consola, el logger raíz no tenía ni un handler: todo
    `logger.exception(...)` se tiraba a la basura. Por eso, cuando organizar
    reventaba, no había traceback que pedirle al usuario — solo el mensaje
    corto del modal, que además viene enmascarado (ver atom_core/precarga.py).

    Va en un try/except amplio a propósito: quedarse sin log es malo, pero no
    arrancar es peor. Si la carpeta no es escribible, la app abre igual.
    """
    from logging.handlers import RotatingFileHandler

    try:
        from external_tools import user_log_dir

        carpeta = user_log_dir()
        os.makedirs(carpeta, exist_ok=True)
        ruta = os.path.join(carpeta, LOG_APP_NOMBRE)
        raiz = logging.getLogger()
        # Idempotente: dos llamadas (o un test que reimporta) no deben apilar
        # handlers y duplicar cada línea del log.
        for h in raiz.handlers:
            if isinstance(h, RotatingFileHandler) and getattr(h, "atom_log_app", False):
                return ruta
        handler = RotatingFileHandler(
            ruta, maxBytes=LOG_APP_BYTES_MAX, backupCount=LOG_APP_COPIAS,
            encoding="utf-8",
        )
        handler.atom_log_app = True
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        raiz.addHandler(handler)
        efectivo = _nivel_log() if nivel is None else nivel
        if raiz.level == logging.NOTSET or raiz.level > efectivo:
            raiz.setLevel(efectivo)
        return ruta
    except Exception:  # noqa: BLE001 — sin log se sigue arrancando
        return None


def instalar_capturas_de_excepcion() -> None:
    """Manda al log lo que muere fuera de un try: hilos y hilo principal.

    Los hilos daemon de la app (`_run_task_worker`, el worker de estadillos,
    los de red) ya capturan lo suyo, pero cualquier excepción que se escape de
    un hilo NUEVO desaparecía sin dejar rastro: `threading` la imprime en
    stderr, y en una app windowed stderr no va a ninguna parte.
    """
    anterior_sys = sys.excepthook

    def _sys_hook(tipo, valor, tb):
        logging.getLogger("atom.excepthook").error(
            "Excepción no capturada en el hilo principal", exc_info=(tipo, valor, tb)
        )
        anterior_sys(tipo, valor, tb)

    sys.excepthook = _sys_hook

    def _hilo_hook(args):
        if args.exc_type is SystemExit:
            return
        logging.getLogger("atom.excepthook").error(
            "Excepción no capturada en el hilo %s",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _hilo_hook


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ATOM Organizer (UI React/pywebview)")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Cargar el dev server de Vite (localhost:5173) con HMR.",
    )
    parser.add_argument(
        "--server",
        action="store_true",
        help="No abrir ventana: servir la UI por HTTP (Raspberry Pi / ARM64).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Interfaz del modo servidor. Por defecto solo local; usa 0.0.0.0 "
             "SOLO si quieres abrirla desde otro equipo de la red.",
    )
    parser.add_argument("--port", type=int, default=8765,
                        help="Puerto del modo servidor (por defecto 8765).")
    return parser


def main() -> None:
    from atom_core import sin_consola
    sin_consola.aplicar()

    parser = _build_parser()
    args = parser.parse_args()

    if args.server:
        from atom_core.event_sink import QueueSink
        from atom_core.webserver import servir

        # En modo servidor (kiosco Pi) no hay ventana ni consola: el servicio
        # redirige stdout/stderr a fichero, así que basta con loguear a stderr
        # para que quede rastro en disco cuando se pierde la sesión/emparejamiento.
        nivel_env = os.environ.get("ATOM_LOG_LEVEL", "INFO")
        nivel = getattr(logging, nivel_env.upper(), None)
        if not isinstance(nivel, int):
            nivel = logging.INFO
        logging.basicConfig(
            stream=sys.stderr,
            level=nivel,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        # Además de stderr, a fichero: en la Pi el servicio puede perder el
        # journal, y el log de la app es lo único que queda de un arranque roto.
        configurar_log_a_fichero(nivel)
        instalar_capturas_de_excepcion()
        from atom_core.google_auth import migrar_estadillos_recibidos_legacy
        migrar_estadillos_recibidos_legacy()
        # En el hilo principal y antes de que `servir` levante nada: ver
        # atom_core/precarga.py.
        precarga.precargar_en_arranque()

        api = Api(broker=True)
        sink = QueueSink()
        api.bind_sink(sink)
        _comprobar_al_arrancar(api)
        servir(api, str(DIST_INDEX.parent), args.host, args.port, sink)
        return

    # LO PRIMERO del camino con ventana: el .exe es windowed y sin esto no hay
    # ningún handler en el logger raíz, o sea que ningún fallo deja rastro.
    _ruta_log = configurar_log_a_fichero()
    instalar_capturas_de_excepcion()
    logger.info("ATOM Organizer v%s arrancando (log: %s)",
                _app_version_for_title(), _ruta_log or "sin fichero")

    # En un hilo daemon, como `precarga`: es I/O de disco (mover ficheros
    # legacy) e idempotente/nunca lanza, no hay motivo para bloquear el hilo
    # principal antes de `webview.start()` (incidente 3.4.102: cualquier
    # trabajo síncrono aquí retrasa la ventana y compite con la carrera del
    # sink de eventos).
    from atom_core.google_auth import migrar_estadillos_recibidos_legacy
    threading.Thread(target=migrar_estadillos_recibidos_legacy, daemon=True).start()

    # Precarga de pandas en un hilo de fondo: importarlo cuesta más de un segundo
    # y hacerlo aquí retrasaba la aparición de la ventana. El `Lock` de
    # `precargar_pandas()` serializa, así que un consumidor que llegue antes de
    # tiempo simplemente espera a que el hilo termine (ver atom_core/precarga.py).
    # La neutralización de pytz sí va aquí, síncrona y antes de que exista ningún
    # hilo: es barata (no importa pandas) y así queda hecha aunque algún módulo
    # con `import pandas` en el cuerpo se adelante al hilo de precarga. Sin ella
    # volvería el `_pandas_datetime_CAPI` de la v3.4.77.
    precarga.neutralizar_pytz()
    threading.Thread(target=precarga.precargar_en_arranque, daemon=True).start()
    # El selector de carpeta de Windows compila su C# la primera vez; se hace ya
    # para que la primera apertura del diálogo no lo pague.
    precalentar_dialogo_carpeta()

    try:
        webview = _import_webview()
    except RuntimeError as exc:
        sys.exit(str(exc))

    target = resolve_target(args.dev)
    api = Api()

    # Geometría persistida del arranque anterior (JSON aparte de Config.ini: ver
    # atom_core/window_state.py). Si no existe (primer arranque, o el fichero se
    # invalidó) la ventana abre MAXIMIZADA, que es lo esperable la primera vez.
    geo_previa = window_state.leer()
    ancho, alto = 1100, 760
    pos_x = pos_y = None
    maximizada_inicial = True
    if geo_previa is not None:
        ancho, alto = geo_previa["ancho"], geo_previa["alto"]
        pos_x, pos_y = geo_previa["x"], geo_previa["y"]
        maximizada_inicial = geo_previa["maximizada"]

    window_kwargs = dict(
        # La versión va en el TÍTULO de la ventana, no solo en el header de la UI:
        # es lo que se ve en la barra de tareas y en una captura de pantalla, que es
        # como el usuario final reporta en qué build está.
        title=f"ATOM Organizer v{_app_version_for_title()}",
        url=target,
        js_api=api,
        width=ancho,
        height=alto,
        min_size=(760, 520),
        background_color="#0a0a0a",
        maximized=maximizada_inicial,
    )
    if pos_x is not None and pos_y is not None:
        window_kwargs["x"] = pos_x
        window_kwargs["y"] = pos_y

    window = webview.create_window(**window_kwargs)
    api.bind_window(window)
    _comprobar_al_arrancar(api)

    # Cableado de persistencia de geometría. En un try/except amplio a propósito:
    # esto es una comodidad, no algo esencial para que la app funcione — si algo
    # falla (versión de pywebview sin alguno de estos eventos, backend distinto,
    # etc.) la ventana debe abrir igual, sin geometría persistida.
    try:
        estado_ventana = window_state.EstadoVentana(
            ancho=ancho, alto=alto, x=pos_x, y=pos_y, maximizada=maximizada_inicial
        )
        window.events.resized += lambda w, h: estado_ventana.on_resized(w, h)
        window.events.moved += lambda x, y: estado_ventana.on_moved(x, y)
        window.events.maximized += lambda: estado_ventana.on_maximized()
        window.events.restored += lambda: estado_ventana.on_restored()

        def _guardar_geometria_al_cerrar():
            window_state.guardar(estado_ventana.snapshot())
            # Sin return: devolver True aquí cancelaría el cierre de la ventana.

        window.events.closing += _guardar_geometria_al_cerrar
    except Exception:
        logger.exception("No se pudo cablear la persistencia de geometría de ventana")

    # Comprobación de actualizaciones 3 s después del arranque. En modo --dev no
    # molesta (se corre desde fuente, la versión instalada no tiene sentido).
    if not args.dev:
        api.start_update_check()

    # Backend Qt (PySide6 + QtWebEngine, Chromium embebido) en AMBOS SO.
    # Windows abandonó WebView2 (v3.8.x): con ese backend la UI renderizaba pero
    # `window.pywebview` NUNCA se inyectaba → el bridge JS↔Python quedaba muerto y
    # ninguna llamada `window.pywebview.api.*` llegaba a Python (pw=N en la sonda,
    # confirmado en VM con http_server/private_mode/storage_path). QtWebEngine usa
    # el mismo motor ya probado en Linux, donde el bridge funciona.
    # Ahora sí: queda constancia de que se intenta pintar con GPU. Si esta ventana
    # no llega a confirmar su primer frame, el arranque siguiente verá el marcador.
    if globals().get("_pendiente_diferido"):
        render_state.guardar(dict(render_state.leer(), pendiente=True))

    webview.start(gui="qt", debug=args.dev)


if __name__ == "__main__":
    # LO PRIMERO, antes de cualquier otro efecto: en Windows el start method es
    # `spawn` y el hijo re-ejecuta el .exe congelado; sin esto no ejecuta el worker,
    # muere, y el ProcessPoolExecutor de utils.run_batch se rompe entero
    # (BrokenProcessPool en TODOS los items: recorte RGB y compresión). gui.py ya lo
    # llamaba en su propio __main__, pero el entry point del build webview es ESTE
    # fichero y gui.py sólo se importa como módulo, así que aquel nunca corría.
    multiprocessing.freeze_support()
    if platform.system() == "Linux":
        # UseOzonePlatform: integración Wayland/X11 del Chromium de QtWebEngine.
        os.environ.setdefault(
            "QTWEBENGINE_CHROMIUM_FLAGS", "--enable-features=UseOzonePlatform"
        )
    elif platform.system() == "Windows":
        # --disable-gpu fuerza el rasterizador software de Chromium. Sin GPU real
        # (máquina virtual, sesión RDP, drivers pobres) el compositing acelerado de
        # QtWebEngine deja la ventana EN NEGRO (confirmado en VM QEMU), pero pintarlo
        # todo por CPU en un portátil normal se nota como lag. Así que se intenta con
        # GPU y se cae a software solo si un arranque no llegó a pintar: la decisión
        # y su porqué están en atom_core/render_state.py. Se respeta un valor previo
        # de la env var si ya existe (override manual).
        try:
            _estado_render = render_state.olvidar_degradacion(
                render_state.leer(), _app_version_for_title())
            _usar_gpu, _estado_render, _motivo = render_state.decidir(_estado_render)
            # El marcador `pendiente` NO se escribe todavía. Entre este punto y la
            # apertura de la ventana aún pueden petar los imports, la precarga o el
            # updater, y un crash así no es culpa de la GPU: contarlo degradaba el
            # render a software durante diez arranques sin motivo (pasó con el fallo
            # de pytz de la v3.4.77). Aquí se persiste el estado YA SIN pendiente —
            # lo que además comprueba que el fichero es escribible — y el marcado
            # real lo hace `main()` justo antes de `webview.start()`.
            _pendiente_diferido = bool(_estado_render.get("pendiente"))
            if not render_state.guardar(dict(_estado_render, pendiente=False)) \
                    and _pendiente_diferido:
                # No se pudo dejar constancia del intento (disco lleno, permisos):
                # si ese intento dejara la ventana en negro, el arranque siguiente
                # no vería el marcador y reintentaría GPU para siempre. Sin poder
                # registrar el intento, no se intenta.
                _usar_gpu = False
                _pendiente_diferido = False
        except Exception:  # noqa: BLE001 — ante cualquier fallo, lo que siempre pintó
            _usar_gpu = False
        if not _usar_gpu:
            os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    main()
