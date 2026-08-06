"""Almacén local de la sesión del operador: SQLite con el token cifrado.

Sustituye a `~/.config/atom-organizer/google_auth.json`, que guardaba el
`refresh_token` **en claro** y confiaba todo al modo 0600. Aquello sobrevivía
mal a lo de siempre: un backup del perfil, una carpeta sincronizada, un `cat`
para depurar, y el secreto acababa donde no debía.

Qué cambia:

* **Formato.** Una BD SQLite (`session.db`) en vez de un JSON. No es capricho de
  formato: da esquema versionado, escritura transaccional (nunca un fichero a
  medio escribir si la app muere en mitad del guardado) y sitio donde apuntar
  metadatos —cuándo se validó la sesión por última vez— sin reinventar nada.
* **Cifrado.** El `refresh_token` lo sella un *protector* que depende del
  sistema. En Windows es DPAPI, que ya es cifrado autenticado del propio SO y
  no pasa por `secret_box`; en el resto es `secret_box` con una clave local.

**Contra qué protege cada protector — sin vender humo:**

* `dpapi` (Windows): la clave la guarda el propio sistema, ligada a la cuenta de
  usuario. Copiar `session.db` a otro equipo, o abrirla desde otra cuenta, no
  sirve de nada. Esta es la protección de verdad, y es el caso que importa:
  Windows es donde corre la app en producción.
* `keyfile` (Linux/macOS): la clave es un fichero 0600 al lado de la BD. Frente a
  alguien que ya lee tu `$HOME` **esto no protege** —tendría las dos piezas—, y
  fingir lo contrario sería mentir. Lo que sí evita es el caso real y frecuente:
  que el token viaje legible en un backup, un `rsync`, un adjunto de soporte o un
  volcado de logs, donde casi nunca va acompañado del keyfile. El permiso de
  verdad sigue viviendo en IAM, igual que antes.

Solo stdlib (`sqlite3` incluido), por la misma razón que el resto de `atom_core`:
no engordar el bundle de PyInstaller.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import secret_box

__all__ = [
    "SessionStore",
    "Sesion",
    "STORE_NAME",
    "LEGACY_STORE_NAME",
    "protector_por_defecto",
]

STORE_NAME = "session.db"
LEGACY_STORE_NAME = "google_auth.json"
KEYFILE_NAME = "session.key"

ESQUEMA = 1

# Ata cada secreto a su sitio: un BLOB de la fila de sesión no se puede recolocar
# en otra columna/tabla sin que el MAC lo delate.
_AAD_REFRESH = b"sesion.refresh_token"


@dataclass
class Sesion:
    """Lo que hay guardado de la sesión del operador."""

    email: str | None
    refresh_token: str
    actualizado_en: float
    validada_en: float | None = None


# --------------------------------------------------------------------------
# Protectores: de dónde sale la clave maestra
# --------------------------------------------------------------------------

class ProtectorError(RuntimeError):
    """No se pudo proteger/desproteger con este backend."""


class KeyfileProtector:
    """Clave maestra en un fichero 0600 junto a la BD.

    Se crea sola la primera vez. Si el fichero desaparece o se corrompe, la
    sesión guardada deja de poder abrirse: es equivalente a haber cerrado
    sesión, y así lo trata `SessionStore` (borra y pide login de nuevo). Perder
    un `refresh_token` es un login más, no una pérdida de datos.
    """

    nombre = "keyfile"

    def __init__(self, key_path: Path):
        self.key_path = Path(key_path)

    def _clave(self) -> bytes:
        try:
            crudo = self.key_path.read_bytes()
            if len(crudo) == secret_box.TAM_CLAVE:
                return crudo
        except OSError:
            pass
        return self._crear_clave()

    def _crear_clave(self) -> bytes:
        clave = secret_box.clave_nueva()
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.key_path.with_suffix(".tmp")
        # Se crea ya con 0600: escribir primero y ajustar después deja una
        # ventana en la que la clave es legible por todo el mundo.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, clave)
        finally:
            os.close(fd)
        os.replace(tmp, self.key_path)
        return clave

    def proteger(self, claro: bytes) -> bytes:
        return secret_box.sellar(self._clave(), claro, aad=_AAD_REFRESH)

    def desproteger(self, sobre: bytes) -> bytes:
        try:
            return secret_box.abrir(self._clave(), sobre, aad=_AAD_REFRESH)
        except (secret_box.SobreInvalido, ValueError) as exc:
            raise ProtectorError(str(exc)) from exc


class DpapiProtector:
    """DPAPI de Windows: la clave la custodia el sistema, atada a la cuenta.

    Se usa `ctypes` contra `crypt32.dll` en vez de `win32crypt` (pywin32) para
    no depender de que PyInstaller acierte a empaquetar el módulo de extensión.
    """

    nombre = "dpapi"

    _ENTROPIA = b"ATOM Organizer / sesion de Google"

    def proteger(self, claro: bytes) -> bytes:
        return self._llamar(claro, cifrar=True)

    def desproteger(self, sobre: bytes) -> bytes:
        return self._llamar(sobre, cifrar=False)

    # -- ctypes -----------------------------------------------------------
    def _llamar(self, datos: bytes, *, cifrar: bool) -> bytes:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        def blob(b: bytes) -> DATA_BLOB:
            buf = ctypes.create_string_buffer(b, len(b))
            return DATA_BLOB(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

        # `use_last_error`: ctypes guarda el GetLastError de ESTA llamada en su
        # propio hueco, en vez de dejar que cualquier cosa que haga CPython al
        # volver lo pise. Sin esto el código de error del mensaje es una lotería.
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)  # type: ignore[attr-defined]
        entrada = blob(datos)
        entropia = blob(self._ENTROPIA)
        salida = DATA_BLOB()

        if cifrar:
            ok = crypt32.CryptProtectData(
                ctypes.byref(entrada), None, ctypes.byref(entropia),
                None, None, 0, ctypes.byref(salida))
        else:
            ok = crypt32.CryptUnprotectData(
                ctypes.byref(entrada), None, ctypes.byref(entropia),
                None, None, 0, ctypes.byref(salida))
        if not ok:
            verbo = "cifrar" if cifrar else "descifrar"
            raise ProtectorError(
                f"DPAPI no pudo {verbo} la sesión (error {ctypes.get_last_error()})")

        try:
            return ctypes.string_at(salida.pbData, salida.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(salida.pbData)  # type: ignore[attr-defined]


def protector_por_defecto(dir_datos: Path):
    """DPAPI en Windows; fichero de clave en el resto.

    Si DPAPI no responde (Windows recortado, cuenta de servicio sin perfil), se
    cae al keyfile en vez de dejar al usuario sin poder guardar sesión: peor
    protección, pero la app sigue funcionando y `backend` en la BD deja escrito
    con qué se cifró realmente.
    """
    if sys.platform.startswith("win"):
        dpapi = DpapiProtector()
        try:
            dpapi.desproteger(dpapi.proteger(b"prueba"))
            return dpapi
        except Exception:  # noqa: BLE001 - cualquier fallo de DPAPI degrada, no rompe
            pass
    return KeyfileProtector(Path(dir_datos) / KEYFILE_NAME)


# --------------------------------------------------------------------------
# El almacén
# --------------------------------------------------------------------------

class SessionStore:
    """Una sola sesión (la del operador de este perfil) en SQLite.

    La tabla lleva `CHECK (id = 1)`: no hay multi-cuenta y el esquema lo dice en
    voz alta, en vez de dejar que se acumulen filas huérfanas si algún día se
    guarda dos veces.
    """

    def __init__(self, db_path: Path, protector=None):
        self.db_path = Path(db_path)
        self.protector = protector or protector_por_defecto(self.db_path.parent)
        self._error_lectura: str | None = None

    @property
    def error_lectura(self) -> str | None:
        """Por qué no se pudo leer la última sesión guardada, si es que la había.

        Lo consulta la UI para distinguir «nunca has entrado» de «tenías sesión
        pero este equipo ya no puede descifrarla».
        """
        return self._error_lectura

    # -- conexión ---------------------------------------------------------
    def _conectar(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        nuevo = not self.db_path.exists()
        # `timeout`: la sesión se toca desde varios hilos (login, verificación,
        # el refresh de mitad de subida). Son escrituras de microsegundos, pero
        # sin margen de espera dos que coincidan darían un «database is locked»
        # que el usuario vería como sesión perdida.
        con = sqlite3.connect(self.db_path, timeout=10)
        if nuevo and not sys.platform.startswith("win"):
            # Aunque el token vaya cifrado, la BD guarda además el email: no es
            # asunto del resto de cuentas de la máquina.
            try:
                os.chmod(self.db_path, 0o600)
            except OSError:
                pass
        con.execute("PRAGMA journal_mode=DELETE")  # nada de -wal sueltos con datos
        con.execute("""
            CREATE TABLE IF NOT EXISTS sesion (
                id              INTEGER PRIMARY KEY CHECK (id = 1),
                email           TEXT,
                refresh_cifrado BLOB NOT NULL,
                backend         TEXT NOT NULL,
                creado_en       REAL NOT NULL,
                actualizado_en  REAL NOT NULL,
                validada_en     REAL
            )""")
        con.execute("CREATE TABLE IF NOT EXISTS meta (clave TEXT PRIMARY KEY, valor TEXT)")
        con.execute("INSERT OR IGNORE INTO meta VALUES ('esquema', ?)", (str(ESQUEMA),))
        con.commit()
        return con

    # -- API --------------------------------------------------------------
    def leer(self) -> Sesion | None:
        """La sesión guardada, o `None` si no hay o no se puede descifrar."""
        self._error_lectura = None
        if not self.db_path.exists():
            # Preguntar no puede crear nada: instanciar la app sin haber entrado
            # nunca no debe dejar sembrados una BD vacía y un fichero de clave.
            return None
        con = None
        try:
            con = self._conectar()
            fila = con.execute(
                "SELECT email, refresh_cifrado, actualizado_en, validada_en "
                "FROM sesion WHERE id = 1").fetchone()
        except sqlite3.OperationalError as exc:
            # Transitorio (BD bloqueada por otro hilo, permiso momentáneo): el
            # fichero puede estar perfectamente bien, así que NO se toca.
            self._error_lectura = f"No se pudo leer el almacén de sesión: {exc}"
            return None
        except sqlite3.DatabaseError as exc:
            # Esto ya no es transitorio: el fichero no es una BD utilizable. Se
            # aparta para que el siguiente guardado cree una limpia — si no, un
            # `session.db` corrupto bloquearía para siempre tanto el login como
            # la migración del JSON heredado, sin forma de arreglarlo desde la
            # app.
            self._error_lectura = (
                f"El almacén de sesión estaba dañado y se ha descartado ({exc}).")
            self._apartar(self.db_path, ".dañado")
            return None
        finally:
            if con is not None:
                con.close()

        if fila is None:
            return None
        email, cifrado, actualizado, validada = fila
        try:
            refresh = self.protector.desproteger(bytes(cifrado)).decode("utf-8")
        except Exception as exc:  # noqa: BLE001 - descifrar es lo que puede fallar aquí
            # La sesión existe pero este equipo/cuenta no puede abrirla (perfil
            # copiado a otra máquina, keyfile perdido). Se retira: dejarla ahí
            # solo produciría un «tienes sesión» que falla en cada uso.
            self._error_lectura = (
                "La sesión guardada no se puede descifrar en este equipo "
                f"({exc}). Vuelve a iniciar sesión.")
            self.borrar()
            return None
        return Sesion(email=email, refresh_token=refresh,
                      actualizado_en=actualizado, validada_en=validada)

    def guardar(self, email: str | None, refresh_token: str) -> None:
        if not refresh_token:
            raise ValueError("no se guarda una sesión sin refresh_token")
        cifrado = self.protector.proteger(refresh_token.encode("utf-8"))
        ahora = time.time()
        con = self._conectar()
        try:
            con.execute("""
                INSERT INTO sesion (id, email, refresh_cifrado, backend,
                                    creado_en, actualizado_en, validada_en)
                VALUES (1, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(id) DO UPDATE SET
                    email = excluded.email,
                    refresh_cifrado = excluded.refresh_cifrado,
                    backend = excluded.backend,
                    actualizado_en = excluded.actualizado_en
            """, (email, cifrado, self.protector.nombre, ahora, ahora))
            con.commit()
        finally:
            con.close()

    def marcar_validada(self, cuando: float | None = None) -> None:
        """Deja constancia de que la sesión se usó contra Google y funcionó."""
        con = self._conectar()
        try:
            con.execute("UPDATE sesion SET validada_en = ? WHERE id = 1",
                        (cuando if cuando is not None else time.time(),))
            con.commit()
        finally:
            con.close()

    def borrar(self) -> None:
        if not self.db_path.exists():
            return
        try:
            con = self._conectar()
        except sqlite3.Error:
            return
        try:
            con.execute("DELETE FROM sesion")
            con.commit()
        except sqlite3.Error:
            pass
        finally:
            con.close()

    # -- migración desde el JSON en claro ---------------------------------
    def importar_legacy(self, json_path: Path) -> bool:
        """Trae la sesión del `google_auth.json` antiguo y **retira el fichero**.

        Transparente a propósito: el operador no tiene que volver a entrar solo
        porque hayamos cambiado de formato. El JSON no se borra sin más —se
        renombra a `.migrado`— para que un fallo del cambio no deje a nadie sin
        sesión y sin forma de recuperarla; deja de ser un secreto vivo en cuanto
        se revoque o se vuelva a entrar.
        """
        json_path = Path(json_path)
        try:
            datos = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        refresh = datos.get("refresh_token")
        if not refresh:
            # Fichero presente pero sin sesión utilizable: no hay nada que
            # migrar, y se aparta igual para no volver a mirarlo en cada arranque.
            self._apartar(json_path)
            return False
        self.guardar(datos.get("email"), refresh)
        self._apartar(json_path)
        return True

    @staticmethod
    def _apartar(ruta: Path, sufijo: str = ".migrado") -> None:
        """Quita un fichero de en medio sin destruirlo, si se puede.

        Se renombra en vez de borrar: si el cambio de formato sale mal, el
        original sigue ahí para recuperarlo a mano. Sólo se borra cuando
        renombrar no es posible, porque dejarlo donde estaba es peor.
        """
        try:
            os.replace(ruta, ruta.with_suffix(ruta.suffix + sufijo))
        except OSError:
            try:
                ruta.unlink()
            except OSError:
                pass
