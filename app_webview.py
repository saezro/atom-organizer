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
import json
import multiprocessing
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

import webview

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

    def __init__(self) -> None:
        self._window = None
        self._running = False
        self._downloading = False
        self._update_path: str | None = None
        # Subida al bucket (ver más abajo). `_auth` se crea perezoso: sin él, el
        # arranque tendría que leer el fichero de credenciales aunque nadie vaya
        # a subir nada en toda la sesión.
        self._auth = None
        self._logging_in = False
        self._verifying = False
        self._uploading = False
        self._cancel_upload = False

    def bind_window(self, window) -> None:
        self._window = window

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
            res = self._window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False)
            return res[0] if res else None
        except Exception as exc:  # noqa: BLE001 — se traza y se avisa al front
            import traceback
            self._log_picker("pick_file ERROR:\n" + traceback.format_exc())
            self._push({"kind": "error",
                        "text": f"No se pudo abrir el diálogo de archivo: {type(exc).__name__}: {exc}"})
            return None

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
        if not self._window:
            return
        js = ("window.dispatchEvent(new CustomEvent('atom:update',"
              f"{{detail:{json.dumps(detail)}}}))")
        try:
            self._window.evaluate_js(js)
        except Exception:
            pass

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
            return None
        self._auth = GoogleAuth(client.client_id, client.client_secret,
                                hosted_domain=cloud_config.HOSTED_DOMAIN)
        return self._auth

    def cloud_status(self) -> dict:
        from atom_core import cloud_config

        auth = self._get_auth()
        if auth is None:
            return {"ok": True, "configured": False, "logged_in": False,
                    "bucket": cloud_config.BUCKET_DATOS,
                    "help": cloud_config.missing_client_help()}
        ident = auth.identity
        return {"ok": True, "configured": True,
                "logged_in": auth.is_logged_in(),
                "email": ident.email if ident else None,
                # Lo que se sabe SIN preguntar a Google: si la sesión sigue
                # viva se comprueba aparte (`cloud_verify`), porque eso es una
                # llamada de red y el estado inicial no puede esperarla.
                "validada_en": auth.validada_en,
                "aviso": auth.aviso_store,
                "bucket": cloud_config.BUCKET_DATOS,
                "uploading": self._uploading}

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
        return {"ok": True}

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

    def cloud_prepare(self, folder: str, prefix: str | None = None) -> dict:
        """Qué se subiría de verdad: total, lo que ya está y lo que falta.

        Lista el prefijo destino y lo cruza con la carpeta, así que lo que
        enseña es el trabajo REAL pendiente, no el tamaño de la carpeta. Sobre
        una inspección ya subida esto responde «0 pendientes» en un par de
        segundos, que es justo lo que el usuario necesita saber antes de darle
        a subir.
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

        auth = self._get_auth()
        if auth is not None and auth.is_logged_in():
            try:
                remotos = cloud_upload.listar_objetos_remotos(
                    cloud_config.BUCKET_DATOS, prefix, auth)
            except Exception:  # noqa: BLE001 - informativo; no bloquea el paso previo
                pass
            else:
                pendientes, hechos = cloud_upload.reconciliar(plan, remotos)
                out["existing"] = len(remotos)
                out["ya_subidos"] = len(hechos)
                out["pendientes"] = len(pendientes)
                out["bytes_pendientes"] = sum(i.size for i in pendientes)
        return out

    def cloud_upload(self, folder: str, force: bool = False,
                     prefix: str | None = None,
                     inspeccion_id: int | None = None) -> dict:
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
        """
        if self._uploading:
            return {"started": False, "reason": "Ya hay una subida en curso."}

        from atom_core import cloud_config, cloud_upload, upload_log

        auth = self._get_auth()
        if auth is None:
            return {"started": False, "reason": cloud_config.missing_client_help()}
        if not auth.is_logged_in():
            return {"started": False,
                    "reason": "Primero inicia sesión con tu cuenta de Aerotools."}

        root, prefix, error = self._destino(folder, prefix)
        if error:
            return {"started": False, "reason": error}

        self._uploading = True
        self._cancel_upload = False

        def worker() -> None:
            plan = None
            try:
                plan = cloud_upload.build_plan(root, prefix)
                if not plan.items:
                    raise RuntimeError("La carpeta no tiene ficheros subibles.")

                provider = cloud_upload.GcsOAuthProvider(
                    cloud_config.BUCKET_DATOS, auth)

                self._push_cloud({"kind": "start", "files": len(plan.items),
                                  "bytes": plan.total_bytes, "prefix": prefix})

                # Ya no hay guarda anti-pisado ni «continuar subida»: lo que
                # ya está en el destino se identifica objeto a objeto y se
                # descarta (`reconciliar`), en vez de bloquear la subida entera
                # y pedirle al operador que confirme a ciegas. Subir dos veces
                # la misma carpeta es ahora una operación segura y barata.
                res = cloud_upload.upload_plan(
                    plan, provider,
                    on_progress=lambda t: self._push_cloud({"kind": "log", "text": t}),
                    on_stats=lambda s: self._push_cloud({"kind": "stats", **s}),
                    should_stop=lambda: self._cancel_upload,
                )
                self._push_cloud({
                    "kind": "done", "ok": res.ok,
                    "uploaded": res.uploaded, "skipped": res.skipped,
                    "skipped_remoto": res.skipped_remoto,
                    "reconciliado": res.reconciliado,
                    "bytes": res.bytes_sent, "elapsed": res.elapsed,
                    "mbps": round(res.mbps, 1), "retries": res.retries,
                    "failed": [{"objeto": o, "error": e} for o, e in res.failed[:20]],
                    "failed_total": len(res.failed),
                    "cancelled": self._cancel_upload,
                    "log": str(upload_log.ruta()),
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
                        self._reportar_subida(inspeccion_id, plan, estado="error",
                                              error="Subida cancelada por el operador")
                    elif res.ok:
                        self._reportar_subida(inspeccion_id, plan, estado="ok")
                    else:
                        primeros = "; ".join(
                            f"{o}: {e}" for o, e in res.failed[:5])
                        self._reportar_subida(
                            inspeccion_id, plan, estado="error",
                            error=f"{len(res.failed)} objetos fallaron: {primeros}")
                except Exception as exc_rep:  # noqa: BLE001 - fail-open
                    self._log_subida(
                        "cloud_upload: fallo reportando a la Suite (%s)", exc_rep)
            except Exception as exc:  # noqa: BLE001 - llega a la UI como error
                self._push_cloud({"kind": "error", "text": str(exc)})
                try:
                    self._reportar_subida(inspeccion_id, plan, estado="error",
                                          error=str(exc))
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

    def cloud_cancel(self) -> dict:
        """Pide parar. Los ficheros ya subidos quedan; el manifiesto local deja
        que una subida posterior siga donde se quedó sin repetirlos."""
        self._cancel_upload = True
        return {"ok": True}

    def _push_cloud(self, detail: dict) -> None:
        if not self._window:
            return
        js = ("window.dispatchEvent(new CustomEvent('atom:cloud',"
              f"{{detail:{json.dumps(detail)}}}))")
        try:
            self._window.evaluate_js(js)
        except Exception:
            pass

    # ---- disparo del pipeline ---------------------------------------------
    def run_organize(self, params: dict, advanced: dict | None = None) -> dict:
        """Atajo de la pantalla principal: "Organizar completo". `advanced` son
        overrides de SplitImagesConfig del panel Modo avanzado (o None)."""
        return self.run_task("split_images", params, advanced)

    def run_task(self, task: str, params: dict, advanced: dict | None = None) -> dict:
        """Arranca un task del pipeline en un hilo aparte. Devuelve al instante;
        el progreso llega a React por eventos `atom:progress`."""
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

    def _push(self, detail: dict) -> None:
        """Empuja un evento a React (Python → JS)."""
        if not self._window:
            return
        js = (
            "window.dispatchEvent(new CustomEvent('atom:progress',"
            f"{{detail:{json.dumps(detail)}}}))"
        )
        try:
            self._window.evaluate_js(js)
        except Exception:
            pass  # ventana cerrada a mitad de proceso


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


def main() -> None:
    parser = argparse.ArgumentParser(description="ATOM Organizer (UI React/pywebview)")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Cargar el dev server de Vite (localhost:5173) con HMR.",
    )
    args = parser.parse_args()

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
