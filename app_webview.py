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
import json
import os
import platform
import subprocess
import sys
import threading
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


class Api:
    """Objeto puente expuesto a JS como `window.pywebview.api`."""

    def __init__(self) -> None:
        self._window = None
        self._running = False

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
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden",
                 "-ExecutionPolicy", "Bypass", "-Command", script],
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
        return self._win_dialog(
            "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$d.Description='Selecciona la carpeta';$d.ShowNewFolderButton=$true;"
            "if($d.ShowDialog($o) -eq [System.Windows.Forms.DialogResult]::OK)"
            "{[Console]::Out.Write($d.SelectedPath)}"
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
            elif kind in ("plan", "phase", "done"):
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
        title="ATOM Organizer",
        url=target,
        js_api=api,
        width=1100,
        height=760,
        min_size=(900, 600),
        background_color="#0a0a0a",
    )
    api.bind_window(window)

    # Backend: en Linux forzamos Qt (el venv trae PySide6/QtWebEngine y así no
    # dependemos del GTK del sistema). En Windows pywebview usará WebView2 solo.
    gui = "qt" if platform.system() == "Linux" else None
    webview.start(gui=gui, debug=args.dev)


if __name__ == "__main__":
    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS", "--enable-features=UseOzonePlatform"
    )
    main()
