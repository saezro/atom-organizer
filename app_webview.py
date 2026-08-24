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
import base64
import importlib
import json
import multiprocessing
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

from atom_core import cola_subidas
from atom_core.credencial import (
    ESTADO_OK, ESTADO_SIN_CREDENCIAL, ESTADO_SIN_CONEXION,
    EstadoCredencial, clasificar,
)
from atom_core.event_sink import WebviewSink
from atom_core.google_auth import AuthError
from atom_core import pin_kiosco

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
    ``sys._MEIPASS``; en ejecución normal, el dir de este script. (Espeja
    ``external_tools.app_base_dir`` para que la UI buildeada se encuentre en el exe.)"""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else Path(__file__).resolve().parent


ROOT = _base_dir()
DIST_INDEX = ROOT / "webui" / "dist" / "index.html"
DEV_URL = "http://localhost:5173"


# Diálogo de carpeta MODERNO en Windows (IFileOpenDialog + FOS_PICKFOLDERS): el del
# Explorador — barra de direcciones, árbol lateral y recuerda la última ubicación.
# Sustituye a System.Windows.Forms.FolderBrowserDialog (el árbol legacy feo que no
# recordaba carpeta). Se declara vía Add-Type C#; sólo se usan SetOptions/Show/GetResult
# y IShellItem.GetDisplayName — el resto de la vtable son stubs para preservar el orden
# de slots COM. Verificado en Win10 (PICKED=[C:\Users\...]). El here-string @"…"@ exige
# que "@ vaya a inicio de línea → el cuerpo va a columna 0 a propósito.
_MODERN_FOLDER_CS = '''Add-Type @"
using System;
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
"@;
'''


class Api:
    """Objeto puente expuesto a JS como `window.pywebview.api`."""

    def __init__(self, *, broker: bool = False) -> None:
        self._window = None
        self._sink = None
        self._running = False
        self._downloading = False
        self._update_path: str | None = None
        # Subida al bucket (ver más abajo). `_auth` se crea perezoso: sin él, el
        # arranque tendría que leer el fichero de credenciales aunque nadie vaya
        # a subir nada en toda la sesión.
        self._auth = None
        self._credencial = EstadoCredencial()
        # El PIN del kiosco es del dispositivo, no de la sesion: se abre su
        # propio store para no depender de que haya credencial configurada.
        self._pin_store = None
        self._pin_intentos = pin_kiosco.ControlIntentos()
        # `broker`: modo Raspberry Pi (`main()`, rama `--server`). Sin cliente
        # OAuth propio, `_get_auth` construye un `GoogleAuth` broker_only en
        # vez de devolver `None`. El escritorio (Windows) nunca pasa esto:
        # `broker=False` por defecto deja el comportamiento intacto.
        self._broker = bool(broker)
        self._logging_in = False
        self._verifying = False
        self._uploading = False
        self._cancel_upload = False
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
            if proc.returncode != 0:
                self._log_picker(f"_win_dialog rc={proc.returncode} err={proc.stderr!r}")
            out = (proc.stdout or "").strip()
            return out or None
        except Exception:
            import traceback
            self._log_picker("_win_dialog EXC:\n" + traceback.format_exc())
            return None

    def _win_pick_folder(self) -> str | None:
        # Diálogo MODERNO del Explorador (IFileOpenDialog + FOS_PICKFOLDERS), con el
        # owner TopMost de _WIN_OWNER para quedar al frente desde el hilo no-foreground.
        return self._win_dialog(
            _MODERN_FOLDER_CS +
            "$p=[ModernFolder]::Pick($o.Handle,'Selecciona la carpeta');"
            "if($p){[Console]::Out.Write($p)}"
        )

    def _win_pick_file(self) -> str | None:
        return self._win_dialog(
            "$d=New-Object System.Windows.Forms.OpenFileDialog;"
            "$d.Title='Selecciona el archivo';$d.Filter='Todos los archivos (*.*)|*.*';"
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

    def pick_file(self) -> str | None:
        try:
            if platform.system() == "Windows":
                return self._win_pick_file()
            webview = _import_webview()
            res = self._window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False)
            return res[0] if res else None
        except Exception as exc:  # noqa: BLE001 — se traza y se avisa al front
            import traceback
            self._log_picker("pick_file ERROR:\n" + traceback.format_exc())
            self._push({"kind": "error",
                        "text": f"No se pudo abrir el diálogo de archivo: {type(exc).__name__}: {exc}"})
            return None

    def list_dir(self, path: str | None = None) -> dict:
        """Lista un directorio para el explorador de la UI.

        En modo servidor no hay dialogo nativo de ficheros (eso lo daba Qt), y
        en una pantalla de 480x320 manejada con el dedo tampoco seria usable.
        El explorador vive en la webui y esto es lo que lo alimenta.
        """
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
        quedar EXACTAMENTE igual que antes: arranca en el home. Esto solo
        cambia en Linux (Raspberry Pi), donde las inspecciones llegan por
        disco USB externo y forzar al operador a navegar desde el home cada
        vez es friccion innecesaria.
        """
        home = os.path.expanduser("~")
        if not sys.platform.startswith("linux"):
            return {"ok": True, "path": home}

        try:
            # Candidatos tipicos de montaje automatico en Linux: udisks2/gvfs
            # montan en /media/<usuario>/<etiqueta>, algunos gestores en
            # /media/<etiqueta> a secas, y /mnt/<lo-que-sea> es el sitio
            # habitual para montajes manuales (fstab, script de arranque).
            import glob

            candidatos = set()
            for patron in ("/media/*/*", "/media/*", "/mnt/*"):
                candidatos.update(glob.glob(patron))

            raiz_dev = os.stat("/").st_dev
            validos = []
            for cand in candidatos:
                try:
                    if not os.path.isdir(cand):
                        continue
                    # Un disco "extra" es, por definicion, uno en un
                    # dispositivo distinto al de la raiz del sistema. Este
                    # criterio no depende de nombres ni de convenciones de
                    # montaje, asi que es robusto ante cualquier gestor de
                    # discos (udisks2, gvfs, montaje manual...).
                    if os.stat(cand).st_dev == raiz_dev:
                        continue
                    if not os.access(cand, os.R_OK):
                        continue
                    # Basta con la primera entrada para saber que no esta
                    # vacio: os.scandir es perezoso, a diferencia de
                    # os.listdir (que en un disco USB con miles de fotos de
                    # inspeccion lee el directorio entero solo para tirarlo).
                    with os.scandir(cand) as it:
                        if next(iter(it), None) is None:
                            continue  # disco montado pero vacio: no sirve de nada
                except OSError:
                    continue  # disco a medio montar, desconectado, etc.
                validos.append(cand)

            if validos:
                destino = sorted(validos)[0]
                resultado = self.list_dir(destino)
                if resultado.get("ok"):
                    return resultado
        except Exception:  # noqa: BLE001 — un disco raro no puede tumbar el selector
            pass

        return self.list_dir(home)

    def folder_is_empty(self, path: str) -> dict:
        """¿Está vacía la carpeta de salida? El front avisa al elegirla (una
        corrida sobre residuos genera duplicados `_1/_2` y errores de recorte).
        El backend igualmente la rechaza al arrancar; esto es feedback previo.
        Devuelve {exists, empty, count}. Carpeta inexistente = válida (vacía)."""
        try:
            if not path or not os.path.isdir(path):
                return {"exists": False, "empty": True, "count": 0}
            entries = os.listdir(path)
            return {"exists": True, "empty": len(entries) == 0, "count": len(entries)}
        except Exception as exc:  # noqa: BLE001 — se reenvía al front
            return {"exists": True, "empty": True, "count": 0,
                    "error": f"{type(exc).__name__}: {exc}"}

    # ---- lectura del estadillo (modal previo al procesado) ----------------
    def read_estadillo_info(self, path: str) -> dict:
        """Info básica de vuelo del estadillo para el modal previo: pilotos,
        dron(es), nº de vuelos y franjas horarias. Sincrónico (no arranca hilo);
        `atom_core.estadillo` solo usa pandas + utils (no arrastra gui/PySide)."""
        try:
            from atom_core.estadillo import read_estadillo_info
            return read_estadillo_info(path)
        except Exception as exc:  # noqa: BLE001 — se reenvía al front
            return {"error": f"{type(exc).__name__}: {exc}"}

    def estadillos_detectar(self, carpeta: str) -> dict:
        """Escanea `carpeta` buscando estadillos sin que el operario tenga que
        elegirlos a mano: base de "detectados N estadillos, M días de vuelo..."
        antes de subir. Sincrónico, como `read_estadillo_info` (mismo módulo,
        no arrastra gui/PySide).

        No encontrar ninguno NO es un error (el operario aún puede elegir a
        mano): `{"rutas": [], "n_estadillos": 0, "info": None, "error": None}`.
        """
        try:
            from atom_core.estadillo import detectar_estadillos, read_estadillo_info
            detectado = detectar_estadillos(carpeta)
            rutas = detectado["rutas"]
            info = read_estadillo_info(rutas) if rutas else None
            return {"rutas": rutas, "n_estadillos": len(rutas), "info": info, "error": None}
        except Exception as exc:  # noqa: BLE001 — se reenvía al front
            return {"error": f"{type(exc).__name__}: {exc}"}

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

        return updater.check()

    def download_update(self, url: str, size: int = 0) -> dict:
        """Descarga en un hilo; el progreso llega a JS como `atom:update`."""
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

        return updater.install(path or self._update_path or "", on_failure=on_failure)

    def _push_update(self, detail: dict) -> None:
        if not self._sink:
            return
        self._sink.dispatch("atom:update", detail)

    def start_update_check(self, delay: float = 3.0) -> None:
        """Chequeo automático diferido tras el arranque (como el migrador: 3 s),
        para no competir con la carga de la UI. Silencioso si no hay novedad o
        si no hay red."""
        def worker() -> None:
            time.sleep(delay)
            try:
                res = self.check_update()
            except Exception as exc:  # noqa: BLE001 — nunca romper el arranque
                res = {"ok": False, "error": str(exc)}
            if res.get("ok") and res.get("update_available"):
                self._push_update({"kind": "available", "data": res})

        threading.Thread(target=worker, daemon=True).start()

    # ---- subida al bucket «datos para organizar» ---------------------------
    # Cuenta de Google del operador + IAM del bucket. La app no lleva ninguna
    # credencial de servicio: quién puede subir se decide fuera, en el IAM.
    def _get_auth(self):
        """`GoogleAuth` cacheado, o None si no hay cliente OAuth configurado."""
        if self._auth is not None:
            return self._auth
        from atom_core import cloud_config
        from atom_core.google_auth import GoogleAuth

        client = cloud_config.load_client(ROOT)
        if client is None:
            if not self._broker:
                # Escritorio sin `google_client.json`: comportamiento de
                # siempre, la UI ofrece el mensaje de "falta el cliente OAuth".
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
            self._credencial.registrar(estado, texto)
        except AuthError as exc:
            # El backend contestó y dijo que no: revocado o token inválido.
            self._credencial.registrar(ESTADO_SIN_CREDENCIAL, str(exc))
        except OSError as exc:
            # No se llegó a hablar con el backend: no acuses a la credencial.
            self._credencial.registrar(ESTADO_SIN_CONEXION, str(exc))
        except Exception as exc:
            # Organizar es local y NUNCA puede caerse por un fallo inesperado aquí.
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
        # Sin esto el estado cacheado se queda en `ok` hasta el siguiente
        # latido (6 h): la UI seguiria sin avisar de que ya no hay sesion.
        self._credencial.invalidar("Se cerro la sesion en este equipo.")
        self._olvidar_pin()
        return {"ok": True}

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
            print(f"[pin] No se pudo leer el PIN del kiosco: {exc}")
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
            print(f"[pin] No se pudo fijar el PIN del kiosco: {exc}")
            return {"ok": False, "error": "No se pudo guardar el PIN."}
        self._pin_intentos.acierto()
        return {"ok": True}

    def pin_verificar(self, pin: str) -> dict:
        if self._pin_intentos.bloqueado():
            return {
                "ok": False,
                "error": "Demasiados intentos.",
                "espera_segundos": self._pin_intentos.espera_segundos(),
            }
        try:
            correcto = pin_kiosco.verificar(self._store_pin(), pin)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        if correcto:
            self._pin_intentos.acierto()
            return {"ok": True}
        self._pin_intentos.fallo()
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

    def _olvidar_pin(self) -> None:
        """Desemparejar resetea el PIN: es la via de recuperacion acordada."""
        try:
            pin_kiosco.borrar(self._store_pin())
        except Exception as exc:  # noqa: BLE001
            print(f"[pin] No se pudo borrar el PIN del kiosco: {exc}")
        self._pin_intentos.acierto()

    # ---- emparejamiento por QR (modo broker, Raspberry Pi) -----------------
    # La Pi no puede abrir el navegador del sistema en su propia pantalla como
    # hace `cloud_login` (o sí puede, pero no tiene sentido: es un kiosco sin
    # teclado). En vez de eso, Rodrigo escanea un QR con el móvil y consiente
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
        return inspecciones.cargar_catalogo(cloud_config.BUCKET_DATOS, auth)

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

    def _verificar_lote_completo(self, plan, prefix_lote: str, auth
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
                cloud_config.BUCKET_DATOS, prefix_lote, auth)
        except Exception as exc:  # noqa: BLE001 - informativo, no bloquea
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
                    cloud_config.BUCKET_DATOS, prefix, auth)
            except Exception as exc:  # noqa: BLE001 - informativo, no bloquea
                self._log_subida("inventario de %s: no se pudo listar (%s)",
                                 prefix, exc)
                with self._inv_lock:
                    self._inv_hilos.discard(prefix)
                self._push_cloud({"kind": "inventario", "prefix": prefix,
                                  "ok": False})
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
        from atom_core import cloud_config, cloud_upload

        root, prefix, error = self._destino(folder, prefix)
        if error:
            return {"ok": False, "error": error}
        try:
            plan = cloud_upload.build_plan(root, prefix)
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
                    cloud_config.BUCKET_DATOS, auth)

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
                            plan, prefix_lote, auth)
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
                            f"{prefix_lote}/manifest.json", manifest)
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

            prefix = (f"{estadillo_canonico.prefijo_planta(prefijo)}/"
                     f"{estadillo_canonico.CARPETA_ACTUAL}/")
            n = cloud_upload.objetos_en_prefijo(
                cloud_config.BUCKET_DATOS, prefix, auth)
            return {"existe": n > 0, "error": None}
        except Exception as exc:  # noqa: BLE001 - fail-open, la UI solo pre-marca un checkbox
            return {"existe": False, "error": str(exc)}

    def _subir_estadillo_worker(self, folder: str, rutas: list[str], validacion: dict):
        from datetime import datetime, timezone

        from atom_core import cloud_upload, estadillo_canonico

        self._push_cloud({"kind": "start", "scope": "estadillo"})
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

            plan = estadillo_canonico.plan_subida(
                planta=folder,
                ficheros_locales=locales,
                vuelos=validacion["vuelos"],
                validacion=validacion,
                ahora=datetime.now(timezone.utc),
                subido_por=self._cuenta_actual(),
            )

            res = estadillo_canonico.ejecutar_plan(
                plan,
                subir_fichero=self._subir_objeto_fichero,
                subir_json=self._subir_objeto_json,
            )
        except Exception as exc:
            self._push_cloud({"kind": "error", "scope": "estadillo", "error": str(exc)})
            return

        if not res["ok"]:
            self._push_cloud({"kind": "error", "scope": "estadillo", "error": res["error"]})
            return

        # Fail-open de principio a fin, como el `_notificar_estadillo` que esto
        # sustituye: el crudo ya está en el bucket, así que un fallo avisando a
        # la Suite no puede dejar al operario sin el evento `done`.
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

    def _subir_objeto_fichero(self, remoto: str, ruta_local: str) -> None:
        """Puente fino a `cloud_upload.upload_file`: sube un fichero local ya
        existente al objeto `remoto` del bucket. Sin lógica propia."""
        from atom_core import cloud_config, cloud_upload

        provider = cloud_upload.GcsOAuthProvider(cloud_config.BUCKET_DATOS, self._get_auth())
        item = cloud_upload.UploadItem(
            local=Path(ruta_local), remote=remoto, size=os.path.getsize(ruta_local),
        )
        cloud_upload.upload_file(item, provider)

    def _subir_objeto_json(self, remoto: str, contenido: dict) -> None:
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
            self._subir_objeto_fichero(remoto, tmp_path)
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

    # ---- disparo del pipeline ---------------------------------------------
    def run_organize(self, params: dict, advanced: dict | None = None) -> dict:
        """Atajo de la pantalla principal: "Organizar completo". `advanced` son
        overrides de SplitImagesConfig del panel Modo avanzado (o None)."""
        return self.run_task("split_images", params, advanced)

    def run_task(self, task: str, params: dict, advanced: dict | None = None) -> dict:
        """Arranca un task del pipeline en un hilo aparte. Devuelve al instante;
        el progreso llega a React por eventos `atom:progress`."""
        # Solo para refrescar el indicador de estado de la nube: organizar es
        # 100 % local y debe funcionar siempre, con o sin credencial.
        self.cloud_asegurar_estado()
        if self._running:
            return {"started": False, "reason": "Ya hay un proceso en curso."}
        self._running = True
        threading.Thread(
            target=self._run_task_worker, args=(task, params, advanced), daemon=True
        ).start()
        return {"started": True}

    def _run_task_worker(self, task: str, params: dict, advanced: dict | None) -> None:
        # Import perezoso: atom_core arrastra gui.py/PySide → no lo cargamos al
        # abrir la ventana, solo al primer run.
        from atom_core.organize import run_task

        def emit(kind: str, payload) -> None:
            detail = {"kind": kind}
            if kind == "progress":
                detail["value"] = int(payload)
            elif kind in ("plan", "phase", "stats", "done"):
                detail["data"] = payload  # list / dict estructurado
            elif payload is not None:
                detail["text"] = str(payload)
            self._push(detail)

        try:
            run_task(task, params, emit, advanced or None)
        finally:
            self._running = False
            # Sin esto, lo que quedara en el buffer cuando el pipeline deja de
            # emitir no llegaria nunca: el vaciado lo dispara el evento
            # SIGUIENTE, y despues del ultimo no hay ninguno.
            self._flush_push()

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
    return str(DIST_INDEX)


def _app_version_for_title() -> str:
    """Versión para la barra de título. Nunca revienta el arranque por esto."""
    try:
        from atom_core import updater

        return updater.current_version()
    except Exception:
        return "?"


def _comprobar_al_arrancar(api: Api) -> None:
    """Lanza la primera comprobación de credencial en un hilo aparte.

    Se llama justo después de tener el sink de eventos listo (`bind_sink` /
    `bind_window`). No retrasa el pintado de la UI: `cloud_comprobar` hace red
    y puede tardar. Si sale OK, aprovecha para drenar un job de la cola.
    """
    def worker() -> None:
        try:
            estado = api.cloud_comprobar()
        except Exception as exc:
            # El evento a la UI tiene que llegar siempre, o el aviso de
            # credencial se queda colgado para siempre en el arranque.
            estado = {"estado": ESTADO_SIN_CONEXION, "mensaje": str(exc)}
        api._push_cloud({"kind": "session",
                         "ok": estado["estado"] == ESTADO_OK,
                         "estado": estado["estado"],
                         "text": estado["mensaje"]})
        if estado["estado"] == ESTADO_OK:
            api.cloud_drenar()
    threading.Thread(target=worker, daemon=True).start()


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
    parser = _build_parser()
    args = parser.parse_args()

    if args.server:
        from atom_core.event_sink import QueueSink
        from atom_core.webserver import servir

        api = Api(broker=True)
        sink = QueueSink()
        api.bind_sink(sink)
        _comprobar_al_arrancar(api)
        servir(api, str(DIST_INDEX.parent), args.host, args.port, sink)
        return

    try:
        webview = _import_webview()
    except RuntimeError as exc:
        sys.exit(str(exc))

    target = resolve_target(args.dev)
    api = Api()

    window = webview.create_window(
        # La versión va en el TÍTULO de la ventana, no solo en el header de la UI:
        # es lo que se ve en la barra de tareas y en una captura de pantalla, que es
        # como el usuario final reporta en qué build está.
        title=f"ATOM Organizer v{_app_version_for_title()}",
        url=target,
        js_api=api,
        width=1100,
        height=760,
        min_size=(900, 600),
        background_color="#0a0a0a",
    )
    api.bind_window(window)
    _comprobar_al_arrancar(api)

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
        # --disable-gpu: fuerza el rasterizador software de Chromium. Sin GPU real
        # (máquina virtual, sesión RDP, drivers pobres) el compositing acelerado de
        # QtWebEngine deja la ventana EN NEGRO (confirmado en VM QEMU). En una UI de
        # formularios el coste de no usar GPU es imperceptible, y así renderiza en
        # cualquier máquina. Se respeta un valor previo de la env var si ya existe.
        os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    main()
