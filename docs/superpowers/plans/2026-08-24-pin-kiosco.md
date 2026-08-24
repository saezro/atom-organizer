# PIN de kiosco + pantalla de perfil — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el kiosco de la Raspberry Pi pida un PIN de 4 dígitos al arrancar y tras 10 minutos de inactividad, y que la pantalla de cuenta pase a ser un perfil con foto grande, datos del usuario y cambio de PIN.

**Architecture:** Módulo backend nuevo `atom_core/pin_kiosco.py` que deriva y verifica el PIN con `hashlib.scrypt` y lo persiste en la tabla `meta` de `session.db` (SQLite ya existente, permisos `0600`). El hash nunca sale al frontend: `Api` expone `pin_estado/pin_fijar/pin_verificar/pin_cambiar` y la comparación es en Python. En el frontend, un componente nuevo `KioskLock.jsx` (pad numérico sobre `BotonToque`) actúa de guard antes de `KioskScreen` en `App.jsx`.

**Tech Stack:** Python 3 stdlib (`hashlib.scrypt`, `sqlite3`, `hmac.compare_digest`), pytest; React 18 + Vite, vitest + @testing-library/react.

**Spec:** `docs/superpowers/specs/2026-08-24-pin-kiosco-design.md`

## Global Constraints

- Repo: `/home/rodrigo_saez/atom-organizer-work`, rama `raspi/modo-servidor`. **No hacer merge ni push a producción.** El push a `origin/raspi/modo-servidor` sí está permitido.
- **Verificación Python SIEMPRE con `./venv/bin/python`, nunca `python3`** (el `conftest.py` importa `exifread` y revienta con el intérprete del sistema).
- Comando de tests backend: `./venv/bin/python -m pytest tests/ -q`. Hay **un fallo preexistente conocido**: `tests/test_dji_resiliencia_parallel.py::test_raw_truncado_no_tumba_el_lote`. **No arreglarlo, no tocarlo**: el criterio de éxito es "1 failed (ese) + el resto passed".
- Comando de tests frontend: `cd webui && npm test` (equivale a `vitest run --config vitest.config.js`).
- Build del frontend: `cd webui && NODE_OPTIONS=--max-old-space-size=5120 npm run build` (sin el `NODE_OPTIONS` peta por OOM en esta máquina).
- **Sin dependencias nuevas.** Ni pip ni npm. Todo con stdlib y lo ya instalado.
- Idioma del código y de los mensajes de usuario: **español**. Los identificadores del backend en español siguen la convención del repo (`fijar`, `verificar`, `borrar`).
- El PIN son **exactamente 4 dígitos** (`0000`-`9999`, se aceptan ceros a la izquierda).
- El bloqueo tras fallos es **en memoria del proceso**, no persistido.
- Ningún endpoint puede devolver el hash, el salt ni el PIN. Solo `{hay_pin, bloqueado, espera_segundos}`.

---

### Task 1: Guardar y leer claves en la tabla `meta` de `session.db`

`SessionStore` ya crea la tabla `meta (clave TEXT PRIMARY KEY, valor TEXT)` en `_conectar()` pero solo la usa para sembrar la versión de esquema. No hay forma de leer ni escribir una clave. Esta task añade ese par de métodos, que es donde vivirá el hash del PIN.

**Files:**
- Modify: `atom_core/session_store.py` (clase `SessionStore`, tras `guardar()` en la línea ~372)
- Test: `tests/test_session_store_meta.py` (crear)

**Interfaces:**
- Consumes: nada.
- Produces: `SessionStore.meta_get(clave: str) -> str | None` y `SessionStore.meta_set(clave: str, valor: str | None) -> None`. Con `valor=None` se borra la clave. Las usa la Task 2.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_session_store_meta.py`:

```python
"""La tabla `meta` de session.db como almacen de claves sueltas."""

from atom_core.session_store import SessionStore


def _store(tmp_path):
    return SessionStore(tmp_path / "session.db")


def test_meta_get_devuelve_none_si_no_hay_clave(tmp_path):
    store = _store(tmp_path)
    assert store.meta_get("pin_kiosco") is None


def test_meta_set_y_meta_get_hacen_ida_y_vuelta(tmp_path):
    store = _store(tmp_path)
    store.meta_set("pin_kiosco", "scrypt$1$2$3$sal$hash")
    assert store.meta_get("pin_kiosco") == "scrypt$1$2$3$sal$hash"


def test_meta_set_sobrescribe_el_valor_anterior(tmp_path):
    store = _store(tmp_path)
    store.meta_set("pin_kiosco", "viejo")
    store.meta_set("pin_kiosco", "nuevo")
    assert store.meta_get("pin_kiosco") == "nuevo"


def test_meta_set_con_none_borra_la_clave(tmp_path):
    store = _store(tmp_path)
    store.meta_set("pin_kiosco", "algo")
    store.meta_set("pin_kiosco", None)
    assert store.meta_get("pin_kiosco") is None


def test_meta_no_pisa_la_version_de_esquema(tmp_path):
    store = _store(tmp_path)
    store.meta_set("pin_kiosco", "algo")
    assert store.meta_get("esquema") == "1"
```

- [ ] **Step 2: Ejecutar el test y verificar que falla**

Run: `./venv/bin/python -m pytest tests/test_session_store_meta.py -q`
Expected: FAIL con `AttributeError: 'SessionStore' object has no attribute 'meta_get'`.

- [ ] **Step 3: Implementar los dos métodos**

En `atom_core/session_store.py`, dentro de la clase `SessionStore`, justo después del método `guardar()`:

```python
    def meta_get(self, clave: str) -> str | None:
        """Lee una clave suelta de la tabla `meta`. None si no existe."""
        con = self._conectar()
        try:
            fila = con.execute(
                "SELECT valor FROM meta WHERE clave = ?", (clave,)
            ).fetchone()
        finally:
            con.close()
        return fila[0] if fila else None

    def meta_set(self, clave: str, valor: str | None) -> None:
        """Escribe (o borra, con valor None) una clave suelta de `meta`."""
        con = self._conectar()
        try:
            if valor is None:
                con.execute("DELETE FROM meta WHERE clave = ?", (clave,))
            else:
                con.execute(
                    "INSERT INTO meta (clave, valor) VALUES (?, ?) "
                    "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                    (clave, valor),
                )
            con.commit()
        finally:
            con.close()
```

- [ ] **Step 4: Ejecutar el test y verificar que pasa**

Run: `./venv/bin/python -m pytest tests/test_session_store_meta.py -q`
Expected: 5 passed.

- [ ] **Step 5: No romper el resto**

Run: `./venv/bin/python -m pytest tests/ -q`
Expected: `1 failed, N passed` — el único fallo debe ser `tests/test_dji_resiliencia_parallel.py::test_raw_truncado_no_tumba_el_lote` (preexistente). Si aparece cualquier otro fallo, es regresión: arréglalo.

- [ ] **Step 6: Commit**

```bash
cd /home/rodrigo_saez/atom-organizer-work
git add atom_core/session_store.py tests/test_session_store_meta.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(store): claves sueltas en la tabla meta de session.db"
```

---

### Task 2: Módulo `pin_kiosco` — derivar, fijar, verificar, cambiar y borrar

Módulo puro, sin dependencias de `app_webview`. Recibe el store por parámetro, así que los tests trabajan sobre una base de datos temporal.

**Files:**
- Create: `atom_core/pin_kiosco.py`
- Test: `tests/test_pin_kiosco.py` (crear)

**Interfaces:**
- Consumes: `SessionStore.meta_get` / `meta_set` (Task 1).
- Produces:
  - `CLAVE_META = "pin_kiosco"`
  - `PinInvalido(ValueError)` — excepción de formato
  - `hay_pin(store) -> bool`
  - `fijar(store, pin: str) -> None`
  - `verificar(store, pin: str) -> bool`
  - `cambiar(store, actual: str, nuevo: str) -> bool`
  - `borrar(store) -> None`
  Los usa la Task 4.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_pin_kiosco.py`:

```python
"""PIN local del kiosco: derivacion, verificacion y ciclo de vida."""

import pytest

from atom_core import pin_kiosco
from atom_core.session_store import SessionStore


def _store(tmp_path):
    return SessionStore(tmp_path / "session.db")


def test_sin_pin_al_principio(tmp_path):
    store = _store(tmp_path)
    assert pin_kiosco.hay_pin(store) is False


def test_fijar_y_verificar(tmp_path):
    store = _store(tmp_path)
    pin_kiosco.fijar(store, "1234")
    assert pin_kiosco.hay_pin(store) is True
    assert pin_kiosco.verificar(store, "1234") is True


def test_verificar_pin_incorrecto(tmp_path):
    store = _store(tmp_path)
    pin_kiosco.fijar(store, "1234")
    assert pin_kiosco.verificar(store, "9999") is False


def test_verificar_sin_pin_fijado_es_falso(tmp_path):
    assert pin_kiosco.verificar(_store(tmp_path), "1234") is False


def test_el_pin_no_se_guarda_en_claro(tmp_path):
    store = _store(tmp_path)
    pin_kiosco.fijar(store, "1234")
    guardado = store.meta_get(pin_kiosco.CLAVE_META)
    assert guardado.startswith("scrypt$")
    assert "1234" not in guardado


def test_dos_pines_iguales_dan_hashes_distintos(tmp_path):
    a, b = _store(tmp_path / "a"), _store(tmp_path / "b")
    pin_kiosco.fijar(a, "1234")
    pin_kiosco.fijar(b, "1234")
    assert a.meta_get(pin_kiosco.CLAVE_META) != b.meta_get(pin_kiosco.CLAVE_META)


@pytest.mark.parametrize("malo", ["123", "12345", "abcd", "12a4", "", "  12", None])
def test_formato_invalido_se_rechaza(tmp_path, malo):
    with pytest.raises(pin_kiosco.PinInvalido):
        pin_kiosco.fijar(_store(tmp_path), malo)


def test_ceros_a_la_izquierda_son_validos(tmp_path):
    store = _store(tmp_path)
    pin_kiosco.fijar(store, "0007")
    assert pin_kiosco.verificar(store, "0007") is True
    assert pin_kiosco.verificar(store, "7") is False


def test_cambiar_con_el_actual_correcto(tmp_path):
    store = _store(tmp_path)
    pin_kiosco.fijar(store, "1234")
    assert pin_kiosco.cambiar(store, "1234", "5678") is True
    assert pin_kiosco.verificar(store, "5678") is True


def test_cambiar_con_el_actual_incorrecto_no_toca_nada(tmp_path):
    store = _store(tmp_path)
    pin_kiosco.fijar(store, "1234")
    assert pin_kiosco.cambiar(store, "0000", "5678") is False
    assert pin_kiosco.verificar(store, "1234") is True


def test_borrar_deja_el_kiosco_sin_pin(tmp_path):
    store = _store(tmp_path)
    pin_kiosco.fijar(store, "1234")
    pin_kiosco.borrar(store)
    assert pin_kiosco.hay_pin(store) is False


def test_hash_corrupto_se_trata_como_sin_pin(tmp_path):
    store = _store(tmp_path)
    store.meta_set(pin_kiosco.CLAVE_META, "basura-que-no-es-un-hash")
    assert pin_kiosco.hay_pin(store) is False
    assert pin_kiosco.verificar(store, "1234") is False
```

- [ ] **Step 2: Ejecutar el test y verificar que falla**

Run: `./venv/bin/python -m pytest tests/test_pin_kiosco.py -q`
Expected: FAIL con `ModuleNotFoundError` / `ImportError: cannot import name 'pin_kiosco'`.

- [ ] **Step 3: Implementar el módulo**

Crear `atom_core/pin_kiosco.py`:

```python
"""PIN local del kiosco de la Raspberry Pi.

Es un secreto del DISPOSITIVO, no de la persona: la Pi vive en una sala
compartida y esto evita que cualquiera que pase opere con la credencial
emparejada. Se guarda derivado con scrypt en la tabla `meta` de
`session.db`, que ya tiene permisos 0600.

El hash nunca sale de aqui: la verificacion ocurre en Python y el
frontend solo recibe si o no.
"""

from __future__ import annotations

import base64
import hmac
import os
import re

from hashlib import scrypt

CLAVE_META = "pin_kiosco"
LONGITUD = 4

# Parametros scrypt: interactivos. La Pi es un ARM modesto y esto se
# ejecuta una vez por desbloqueo, no en un bucle.
N = 2 ** 14
R = 8
P = 1
SAL_BYTES = 16

_FORMATO = re.compile(r"^\d{4}$")


class PinInvalido(ValueError):
    """El PIN no tiene el formato exigido (4 digitos)."""


def _validar(pin) -> str:
    if not isinstance(pin, str) or not _FORMATO.match(pin):
        raise PinInvalido(f"El PIN son {LONGITUD} digitos.")
    return pin


def _derivar(pin: str, sal: bytes) -> bytes:
    return scrypt(pin.encode("utf-8"), salt=sal, n=N, r=R, p=P, dklen=32)


def _serializar(sal: bytes, hash_: bytes) -> str:
    return "scrypt${}${}${}${}${}".format(
        N, R, P,
        base64.b64encode(sal).decode("ascii"),
        base64.b64encode(hash_).decode("ascii"),
    )


def _deserializar(guardado: str):
    """Devuelve (n, r, p, sal, hash) o None si el valor no es utilizable."""
    try:
        etiqueta, n, r, p, sal_b64, hash_b64 = guardado.split("$")
        if etiqueta != "scrypt":
            return None
        return (
            int(n), int(r), int(p),
            base64.b64decode(sal_b64),
            base64.b64decode(hash_b64),
        )
    except Exception:  # noqa: BLE001 - un meta corrupto no tumba el kiosco
        return None


def hay_pin(store) -> bool:
    guardado = store.meta_get(CLAVE_META)
    return bool(guardado) and _deserializar(guardado) is not None


def fijar(store, pin) -> None:
    pin = _validar(pin)
    sal = os.urandom(SAL_BYTES)
    store.meta_set(CLAVE_META, _serializar(sal, _derivar(pin, sal)))


def verificar(store, pin) -> bool:
    guardado = store.meta_get(CLAVE_META)
    if not guardado:
        return False
    partes = _deserializar(guardado)
    if partes is None:
        return False
    n, r, p, sal, esperado = partes
    if not isinstance(pin, str) or not _FORMATO.match(pin):
        return False
    calculado = scrypt(pin.encode("utf-8"), salt=sal, n=n, r=r, p=p, dklen=len(esperado))
    return hmac.compare_digest(calculado, esperado)


def cambiar(store, actual, nuevo) -> bool:
    if not verificar(store, actual):
        return False
    fijar(store, nuevo)
    return True


def borrar(store) -> None:
    store.meta_set(CLAVE_META, None)
```

- [ ] **Step 4: Ejecutar el test y verificar que pasa**

Run: `./venv/bin/python -m pytest tests/test_pin_kiosco.py -q`
Expected: todos passed (18 casos contando la parametrización).

- [ ] **Step 5: No romper el resto**

Run: `./venv/bin/python -m pytest tests/ -q`
Expected: solo el fallo preexistente de `test_dji_resiliencia_parallel.py`.

- [ ] **Step 6: Commit**

```bash
cd /home/rodrigo_saez/atom-organizer-work
git add atom_core/pin_kiosco.py tests/test_pin_kiosco.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(kiosco): modulo de PIN local con scrypt sobre session.db"
```

---

### Task 3: Bloqueo tras intentos fallidos

Sin límite de intentos, 10.000 combinaciones se prueban a mano en una tarde. El contador vive en memoria del proceso servidor: reiniciar el servidor de la Pi requiere acceso al SO, que ya es un compromiso mayor.

**Files:**
- Modify: `atom_core/pin_kiosco.py` (añadir al final)
- Test: `tests/test_pin_kiosco_intentos.py` (crear)

**Interfaces:**
- Consumes: nada del resto del módulo.
- Produces: clase `ControlIntentos` con `__init__(self, reloj=time.monotonic)`, `bloqueado(self) -> bool`, `espera_segundos(self) -> int`, `fallo(self) -> None`, `acierto(self) -> None`. La usa la Task 4. El parámetro `reloj` existe para que los tests no duerman.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_pin_kiosco_intentos.py`:

```python
"""Bloqueo escalado tras PINs fallidos."""

from atom_core.pin_kiosco import ControlIntentos


class RelojFalso:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def avanzar(self, segundos):
        self.t += segundos


def test_al_principio_no_esta_bloqueado():
    assert ControlIntentos(reloj=RelojFalso()).bloqueado() is False


def test_cuatro_fallos_no_bloquean():
    c = ControlIntentos(reloj=RelojFalso())
    for _ in range(4):
        c.fallo()
    assert c.bloqueado() is False


def test_cinco_fallos_bloquean_treinta_segundos():
    reloj = RelojFalso()
    c = ControlIntentos(reloj=reloj)
    for _ in range(5):
        c.fallo()
    assert c.bloqueado() is True
    assert c.espera_segundos() == 30


def test_la_espera_expira_sola():
    reloj = RelojFalso()
    c = ControlIntentos(reloj=reloj)
    for _ in range(5):
        c.fallo()
    reloj.avanzar(31)
    assert c.bloqueado() is False
    assert c.espera_segundos() == 0


def test_la_espera_escala_en_cada_tanda():
    reloj = RelojFalso()
    c = ControlIntentos(reloj=reloj)
    for _ in range(5):
        c.fallo()
    reloj.avanzar(31)
    for _ in range(5):
        c.fallo()
    assert c.espera_segundos() == 60
    reloj.avanzar(61)
    for _ in range(5):
        c.fallo()
    assert c.espera_segundos() == 120


def test_la_espera_tiene_techo():
    reloj = RelojFalso()
    c = ControlIntentos(reloj=reloj)
    for _ in range(20):
        for _ in range(5):
            c.fallo()
        reloj.avanzar(10000)
    assert c.espera_segundos() <= 600


def test_un_acierto_lo_resetea_todo():
    reloj = RelojFalso()
    c = ControlIntentos(reloj=reloj)
    for _ in range(5):
        c.fallo()
    c.acierto()
    assert c.bloqueado() is False
    assert c.espera_segundos() == 0
    for _ in range(4):
        c.fallo()
    assert c.bloqueado() is False
```

- [ ] **Step 2: Ejecutar el test y verificar que falla**

Run: `./venv/bin/python -m pytest tests/test_pin_kiosco_intentos.py -q`
Expected: FAIL con `ImportError: cannot import name 'ControlIntentos'`.

- [ ] **Step 3: Implementar `ControlIntentos`**

Añadir al final de `atom_core/pin_kiosco.py` (y `import time` arriba, junto a los demás imports):

```python
FALLOS_PARA_BLOQUEAR = 5
ESPERA_INICIAL = 30
ESPERA_MAXIMA = 600


class ControlIntentos:
    """Espera escalada tras varios PINs fallidos seguidos.

    Vive en memoria del proceso: no se persiste a proposito. Reiniciar el
    servidor de la Pi exige acceso al sistema operativo, que ya es un
    compromiso mayor que adivinar el PIN.
    """

    def __init__(self, reloj=time.monotonic) -> None:
        self._reloj = reloj
        self._fallos = 0
        self._tandas = 0
        self._hasta = 0.0

    def bloqueado(self) -> bool:
        return self.espera_segundos() > 0

    def espera_segundos(self) -> int:
        restante = self._hasta - self._reloj()
        return int(restante) + 1 if restante > 0 else 0

    def fallo(self) -> None:
        if self.bloqueado():
            return
        self._fallos += 1
        if self._fallos >= FALLOS_PARA_BLOQUEAR:
            self._fallos = 0
            espera = min(ESPERA_INICIAL * (2 ** self._tandas), ESPERA_MAXIMA)
            self._tandas += 1
            self._hasta = self._reloj() + espera

    def acierto(self) -> None:
        self._fallos = 0
        self._tandas = 0
        self._hasta = 0.0
```

- [ ] **Step 4: Ejecutar el test y verificar que pasa**

Run: `./venv/bin/python -m pytest tests/test_pin_kiosco_intentos.py -q`
Expected: 7 passed.

Nota: `espera_segundos()` redondea hacia arriba (`int(restante) + 1`), así que justo tras bloquear con 30 s devuelve `30`. Si el test falla por un segundo de más o de menos, el fallo está en la implementación, no en el test.

- [ ] **Step 5: No romper el resto**

Run: `./venv/bin/python -m pytest tests/ -q`
Expected: solo el fallo preexistente.

- [ ] **Step 6: Commit**

```bash
cd /home/rodrigo_saez/atom-organizer-work
git add atom_core/pin_kiosco.py tests/test_pin_kiosco_intentos.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(kiosco): espera escalada tras PINs fallidos"
```

---

### Task 4: Endpoints del PIN en `Api` y borrado al desemparejar

**Files:**
- Modify: `app_webview.py` — `Api.__init__` (~línea 139-155), `cloud_logout()` (~733-742), `cloud_pair_poll()` (~771-819), y métodos nuevos
- Test: `tests/test_app_webview_pin.py` (crear)

**Interfaces:**
- Consumes: `pin_kiosco.hay_pin/fijar/verificar/cambiar/borrar` y `pin_kiosco.ControlIntentos` (Tasks 2 y 3).
- Produces, en la clase `Api`:
  - `pin_estado() -> {"ok": True, "hay_pin": bool, "bloqueado": bool, "espera_segundos": int}`
  - `pin_fijar(nuevo) -> {"ok": bool, "error"?: str}`
  - `pin_verificar(pin) -> {"ok": bool, "error"?: str, "espera_segundos"?: int}`
  - `pin_cambiar(actual, nuevo) -> {"ok": bool, "error"?: str, "espera_segundos"?: int}`
  - `Api._pin_store` — atributo perezoso con el `SessionStore`; los tests lo sustituyen.
  Los consume la Task 5 (`bridge.js`).

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_app_webview_pin.py`. Sigue el estilo de `tests/test_app_webview_credencial.py`: `Api()` real y monkeypatch de lo justo.

```python
"""Endpoints del PIN del kiosco en la capa Api."""

from app_webview import Api
from atom_core.session_store import SessionStore


def _api(tmp_path):
    api = Api()
    api._pin_store = SessionStore(tmp_path / "session.db")
    return api


def test_estado_inicial_sin_pin(tmp_path):
    estado = _api(tmp_path).pin_estado()
    assert estado["ok"] is True
    assert estado["hay_pin"] is False
    assert estado["bloqueado"] is False


def test_fijar_deja_hay_pin_en_true(tmp_path):
    api = _api(tmp_path)
    assert api.pin_fijar("1234")["ok"] is True
    assert api.pin_estado()["hay_pin"] is True


def test_fijar_con_formato_malo_devuelve_error(tmp_path):
    api = _api(tmp_path)
    res = api.pin_fijar("12")
    assert res["ok"] is False
    assert "digitos" in res["error"]
    assert api.pin_estado()["hay_pin"] is False


def test_verificar_correcto_e_incorrecto(tmp_path):
    api = _api(tmp_path)
    api.pin_fijar("1234")
    assert api.pin_verificar("1234")["ok"] is True
    assert api.pin_verificar("0000")["ok"] is False


def test_cinco_fallos_bloquean_y_no_admiten_ni_el_pin_bueno(tmp_path):
    api = _api(tmp_path)
    api.pin_fijar("1234")
    for _ in range(5):
        api.pin_verificar("0000")
    estado = api.pin_estado()
    assert estado["bloqueado"] is True
    assert estado["espera_segundos"] > 0
    res = api.pin_verificar("1234")
    assert res["ok"] is False
    assert res["espera_segundos"] > 0


def test_un_acierto_limpia_los_fallos_previos(tmp_path):
    api = _api(tmp_path)
    api.pin_fijar("1234")
    for _ in range(4):
        api.pin_verificar("0000")
    assert api.pin_verificar("1234")["ok"] is True
    for _ in range(4):
        api.pin_verificar("0000")
    assert api.pin_estado()["bloqueado"] is False


def test_cambiar_pide_el_actual(tmp_path):
    api = _api(tmp_path)
    api.pin_fijar("1234")
    assert api.pin_cambiar("0000", "5678")["ok"] is False
    assert api.pin_verificar("1234")["ok"] is True
    assert api.pin_cambiar("1234", "5678")["ok"] is True
    assert api.pin_verificar("5678")["ok"] is True


def test_ningun_endpoint_filtra_el_hash(tmp_path):
    api = _api(tmp_path)
    api.pin_fijar("1234")
    for res in (api.pin_estado(), api.pin_verificar("1234"), api.pin_cambiar("1234", "5678")):
        assert "scrypt" not in str(res)
        assert "1234" not in str(res)


def test_cerrar_sesion_borra_el_pin(tmp_path, monkeypatch):
    api = _api(tmp_path)
    api.pin_fijar("1234")

    class AuthFalso:
        def logout(self):
            return None

    monkeypatch.setattr(api, "_get_auth", lambda: AuthFalso())
    assert api.cloud_logout()["ok"] is True
    assert api.pin_estado()["hay_pin"] is False
```

- [ ] **Step 2: Ejecutar el test y verificar que falla**

Run: `./venv/bin/python -m pytest tests/test_app_webview_pin.py -q`
Expected: FAIL con `AttributeError: 'Api' object has no attribute 'pin_estado'`.

- [ ] **Step 3: Implementar los endpoints**

En `app_webview.py`:

1. Añadir el import junto a los demás de `atom_core` de la cabecera:

```python
from atom_core import pin_kiosco
```

2. En `Api.__init__`, junto a `self._credencial = EstadoCredencial()`:

```python
        # El PIN del kiosco es del dispositivo, no de la sesion: se abre su
        # propio store para no depender de que haya credencial configurada.
        self._pin_store = None
        self._pin_intentos = pin_kiosco.ControlIntentos()
```

3. Añadir estos métodos a `Api` (justo después de `cloud_logout`):

```python
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
            log.warning("No se pudo leer el PIN del kiosco: %s", exc)
            hay = False
        return {
            "ok": True,
            "hay_pin": hay,
            "bloqueado": self._pin_intentos.bloqueado(),
            "espera_segundos": self._pin_intentos.espera_segundos(),
        }

    def pin_fijar(self, nuevo: str) -> dict:
        try:
            pin_kiosco.fijar(self._store_pin(), nuevo)
        except pin_kiosco.PinInvalido as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
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
            log.warning("No se pudo borrar el PIN del kiosco: %s", exc)
        self._pin_intentos.acierto()
```

4. En `cloud_logout()`, justo antes del `return {"ok": True}` final y después de `self._credencial.invalidar(...)`:

```python
        self._olvidar_pin()
```

5. En `cloud_pair_poll()`, justo después de `self._credencial.registrar(ESTADO_OK, "Dispositivo emparejado.")`:

```python
        self._olvidar_pin()
```

Sobre el punto 5: emparejar de nuevo borra el PIN a propósito, para que la pantalla de fijar PIN de la Task 6 salga y el operario cree uno nuevo. Es exactamente el caso "olvidé el PIN".

Si `log` no es el nombre del logger de módulo en `app_webview.py`, usa el que ya exista ahí; no crees uno nuevo.

- [ ] **Step 4: Ejecutar el test y verificar que pasa**

Run: `./venv/bin/python -m pytest tests/test_app_webview_pin.py -q`
Expected: 9 passed.

- [ ] **Step 5: No romper el resto**

Run: `./venv/bin/python -m pytest tests/ -q`
Expected: solo el fallo preexistente. Presta atención a `tests/test_app_webview_credencial.py`, que toca `cloud_logout` y `cloud_pair_poll`.

- [ ] **Step 6: Commit**

```bash
cd /home/rodrigo_saez/atom-organizer-work
git add app_webview.py tests/test_app_webview_pin.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(kiosco): endpoints de PIN y reseteo al desemparejar"
```

---

### Task 5: Puente `bridge.js` y componente `KioskLock`

Pantalla de bloqueo con pad numérico. Se construye sobre `BotonToque` (patrón táctil ya existente) y no sobre `TecladoPantalla`, que es un teclado general con letras y símbolos.

**Files:**
- Modify: `webui/src/bridge.js` (objeto `api`, junto a `cloudPairPoll`, ~línea 245)
- Create: `webui/src/KioskLock.jsx`
- Modify: `webui/src/styles.css` (o el fichero de estilos del kiosco que ya exista; búscalo con `grep -rn "kiosk-avatar" webui/src --include=*.css`)
- Test: `webui/src/KioskLock.test.jsx` (crear)

**Interfaces:**
- Consumes: `Api.pin_estado/pin_fijar/pin_verificar/pin_cambiar` (Task 4).
- Produces:
  - En `bridge.js`: `pinEstado()`, `pinFijar(nuevo)`, `pinVerificar(pin)`, `pinCambiar(actual, nuevo)`
  - `KioskLock` (default export) con props `{ modo, onOk, onCancelar }` donde `modo` es `'verificar' | 'fijar' | 'cambiar'`. `onCancelar` solo se pinta si se pasa (en modo `'verificar'` de arranque no hay salida posible).
  La Task 6 monta `modo='verificar'` y `modo='fijar'`; la Task 7 monta `modo='cambiar'`.

- [ ] **Step 1: Escribir el test que falla**

Crear `webui/src/KioskLock.test.jsx`:

```jsx
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'

const pinVerificar = vi.fn()
const pinFijar = vi.fn()
const pinCambiar = vi.fn()

const puente = {
  isServerMode: () => false,
  api: { pinVerificar, pinFijar, pinCambiar },
}
vi.mock('./bridge.js', () => puente)
vi.mock('./bridge', () => puente)

const { default: KioskLock } = await import('./KioskLock.jsx')

function teclear(digitos) {
  for (const d of digitos) {
    screen.getByRole('button', { name: d }).click()
  }
}

describe('KioskLock', () => {
  beforeEach(() => {
    pinVerificar.mockReset().mockResolvedValue({ ok: true })
    pinFijar.mockReset().mockResolvedValue({ ok: true })
    pinCambiar.mockReset().mockResolvedValue({ ok: true })
  })

  it('pinta los diez digitos y el borrado', () => {
    render(<KioskLock modo="verificar" onOk={() => {}} />)
    for (const d of '0123456789') {
      expect(screen.getByRole('button', { name: d })).toBeTruthy()
    }
    expect(screen.getByRole('button', { name: /borrar/i })).toBeTruthy()
  })

  it('al completar cuatro digitos verifica y avisa al padre', async () => {
    const onOk = vi.fn()
    render(<KioskLock modo="verificar" onOk={onOk} />)
    teclear('1234')
    await vi.waitFor(() => expect(pinVerificar).toHaveBeenCalledWith('1234'))
    await vi.waitFor(() => expect(onOk).toHaveBeenCalled())
  })

  it('no llama al backend con menos de cuatro digitos', () => {
    render(<KioskLock modo="verificar" onOk={() => {}} />)
    teclear('123')
    expect(pinVerificar).not.toHaveBeenCalled()
  })

  it('borrar quita el ultimo digito', async () => {
    render(<KioskLock modo="verificar" onOk={() => {}} />)
    teclear('123')
    screen.getByRole('button', { name: /borrar/i }).click()
    teclear('45')
    await vi.waitFor(() => expect(pinVerificar).toHaveBeenCalledWith('1245'))
  })

  it('un PIN incorrecto muestra el error y no avisa al padre', async () => {
    pinVerificar.mockResolvedValue({ ok: false, error: 'PIN incorrecto.', espera_segundos: 0 })
    const onOk = vi.fn()
    render(<KioskLock modo="verificar" onOk={onOk} />)
    teclear('9999')
    await vi.waitFor(() => expect(screen.getByText(/pin incorrecto/i)).toBeTruthy())
    expect(onOk).not.toHaveBeenCalled()
  })

  it('en modo fijar pide repetir el PIN antes de guardarlo', async () => {
    const onOk = vi.fn()
    render(<KioskLock modo="fijar" onOk={onOk} />)
    teclear('1234')
    await vi.waitFor(() => expect(screen.getByText(/repite/i)).toBeTruthy())
    expect(pinFijar).not.toHaveBeenCalled()
    teclear('1234')
    await vi.waitFor(() => expect(pinFijar).toHaveBeenCalledWith('1234'))
    await vi.waitFor(() => expect(onOk).toHaveBeenCalled())
  })

  it('en modo fijar, si la repeticion no coincide avisa y no guarda', async () => {
    render(<KioskLock modo="fijar" onOk={() => {}} />)
    teclear('1234')
    await vi.waitFor(() => expect(screen.getByText(/repite/i)).toBeTruthy())
    teclear('5678')
    await vi.waitFor(() => expect(screen.getByText(/no coinciden/i)).toBeTruthy())
    expect(pinFijar).not.toHaveBeenCalled()
  })

  it('en modo cambiar pide el actual, luego el nuevo y su repeticion', async () => {
    const onOk = vi.fn()
    render(<KioskLock modo="cambiar" onOk={onOk} onCancelar={() => {}} />)
    teclear('1111')
    teclear('2222')
    teclear('2222')
    await vi.waitFor(() => expect(pinCambiar).toHaveBeenCalledWith('1111', '2222'))
    await vi.waitFor(() => expect(onOk).toHaveBeenCalled())
  })

  it('cuando el backend manda espera, deshabilita el pad', async () => {
    pinVerificar.mockResolvedValue({ ok: false, error: 'Demasiados intentos.', espera_segundos: 30 })
    render(<KioskLock modo="verificar" onOk={() => {}} />)
    teclear('9999')
    await vi.waitFor(() => expect(screen.getByText(/30/)).toBeTruthy())
    expect(screen.getByRole('button', { name: '1' }).disabled).toBe(true)
  })
})
```

- [ ] **Step 2: Ejecutar el test y verificar que falla**

Run: `cd /home/rodrigo_saez/atom-organizer-work/webui && npm test -- KioskLock`
Expected: FAIL al no poder resolver `./KioskLock.jsx`.

- [ ] **Step 3: Añadir los métodos al puente**

En `webui/src/bridge.js`, dentro del objeto `export const api = { ... }`, junto a `cloudPairPoll` (~línea 245):

```js
  // PIN local del kiosco (dispositivo, no usuario). El backend nunca
  // devuelve el hash: solo {ok, hay_pin, bloqueado, espera_segundos}.
  pinEstado: () => call('pin_estado'),
  pinFijar: (nuevo) => call('pin_fijar', nuevo),
  pinVerificar: (pin) => call('pin_verificar', pin),
  pinCambiar: (actual, nuevo) => call('pin_cambiar', actual, nuevo),
```

- [ ] **Step 4: Implementar `KioskLock`**

Crear `webui/src/KioskLock.jsx`:

```jsx
import { useEffect, useState } from 'react'
import BotonToque from './pulsacion.jsx'
import { api, isServerMode } from './bridge.js'

const LONGITUD = 4
const FILAS = [
  ['1', '2', '3'],
  ['4', '5', '6'],
  ['7', '8', '9'],
]

// Cada modo es una secuencia de pasos; cada paso pide un PIN completo.
const PASOS = {
  verificar: ['verificar'],
  fijar: ['nuevo', 'repetir'],
  cambiar: ['actual', 'nuevo', 'repetir'],
}

const TITULOS = {
  verificar: 'Introduce el PIN',
  actual: 'PIN actual',
  nuevo: 'PIN nuevo',
  repetir: 'Repite el PIN nuevo',
}

export default function KioskLock({ modo = 'verificar', onOk, onCancelar }) {
  const tactil = isServerMode()
  const pasos = PASOS[modo] || PASOS.verificar
  const [paso, setPaso] = useState(0)
  const [pin, setPin] = useState('')
  const [previos, setPrevios] = useState({})
  const [error, setError] = useState('')
  const [espera, setEspera] = useState(0)
  const [ocupado, setOcupado] = useState(false)

  // La cuenta atras del bloqueo la lleva el frontend; el backend sigue
  // siendo quien decide, esto solo evita teclear en balde.
  useEffect(() => {
    if (espera <= 0) return undefined
    const t = setTimeout(() => setEspera((s) => s - 1), 1000)
    return () => clearTimeout(t)
  }, [espera])

  const bloqueado = espera > 0 || ocupado

  async function completar(valor) {
    const actual = pasos[paso]
    setPin('')
    if (actual === 'verificar') {
      setOcupado(true)
      const res = await api.pinVerificar(valor).catch(() => ({ ok: false, error: 'No responde.' }))
      setOcupado(false)
      if (res?.ok) return onOk?.()
      setError(res?.error || 'PIN incorrecto.')
      setEspera(res?.espera_segundos || 0)
      return undefined
    }
    if (actual === 'repetir') {
      if (valor !== previos.nuevo) {
        setError('Los PIN no coinciden.')
        setPaso(pasos.indexOf('nuevo'))
        return undefined
      }
      setOcupado(true)
      const res = modo === 'cambiar'
        ? await api.pinCambiar(previos.actual, valor).catch(() => ({ ok: false, error: 'No responde.' }))
        : await api.pinFijar(valor).catch(() => ({ ok: false, error: 'No responde.' }))
      setOcupado(false)
      if (res?.ok) return onOk?.()
      setError(res?.error || 'No se pudo guardar el PIN.')
      setEspera(res?.espera_segundos || 0)
      setPaso(0)
      return undefined
    }
    setPrevios((p) => ({ ...p, [actual]: valor }))
    setError('')
    setPaso(paso + 1)
    return undefined
  }

  function pulsar(digito) {
    if (bloqueado) return
    setError('')
    const valor = (pin + digito).slice(0, LONGITUD)
    setPin(valor)
    if (valor.length === LONGITUD) completar(valor)
  }

  const puntos = Array.from({ length: LONGITUD }, (_, i) => (
    <span key={i} className={i < pin.length ? 'kiosk-pin-punto lleno' : 'kiosk-pin-punto'} />
  ))

  return (
    <div className="kiosk kiosk-pin" data-testid="kiosk-pin">
      <h2 className="kiosk-pin-titulo">{TITULOS[pasos[paso]] || TITULOS.verificar}</h2>
      <div className="kiosk-pin-puntos">{puntos}</div>
      {error && <p className="kiosk-pin-error" data-testid="kiosk-pin-error">{error}</p>}
      {espera > 0 && (
        <p className="kiosk-pin-espera">Demasiados intentos. Espera {espera} s.</p>
      )}
      <div className="kiosk-pin-pad">
        {FILAS.flat().map((d) => (
          <BotonToque
            key={d}
            className="kiosk-pin-tecla"
            tactil={tactil}
            aria-label={d}
            disabled={bloqueado}
            onActivar={() => pulsar(d)}
          >
            {d}
          </BotonToque>
        ))}
        {onCancelar ? (
          <BotonToque className="kiosk-pin-tecla kiosk-pin-aux" tactil={tactil} onActivar={onCancelar}>
            Cancelar
          </BotonToque>
        ) : (
          <span className="kiosk-pin-tecla kiosk-pin-hueco" />
        )}
        <BotonToque
          className="kiosk-pin-tecla"
          tactil={tactil}
          aria-label="0"
          disabled={bloqueado}
          onActivar={() => pulsar('0')}
        >
          0
        </BotonToque>
        <BotonToque
          className="kiosk-pin-tecla kiosk-pin-aux"
          tactil={tactil}
          aria-label="Borrar"
          disabled={bloqueado}
          onActivar={() => { if (!bloqueado) setPin((p) => p.slice(0, -1)) }}
        >
          Borrar
        </BotonToque>
      </div>
    </div>
  )
}
```

Ojo con `BotonToque`: en modo no táctil renderiza un `<button>` normal y en táctil un `<button>` con `onPointerUp`. `disabled` y `aria-label` llegan por `...rest` en ambos casos, así que los tests funcionan con `tactil={false}`.

- [ ] **Step 5: Añadir los estilos**

Localiza el CSS del kiosco: `grep -rn "kiosk-avatar" webui/src --include=*.css`. En ese mismo fichero, siguiendo su convención (dark-first, sin `px` — usa `rem`):

```css
.kiosk-pin { display: flex; flex-direction: column; align-items: center; gap: 1.5rem; padding: 2rem 1rem; }
.kiosk-pin-titulo { font-size: 1.5rem; margin: 0; }
.kiosk-pin-puntos { display: flex; gap: 1rem; }
.kiosk-pin-punto { width: 1rem; height: 1rem; border-radius: 50%; border: 0.125rem solid currentColor; opacity: 0.5; }
.kiosk-pin-punto.lleno { background: currentColor; opacity: 1; }
.kiosk-pin-error, .kiosk-pin-espera { margin: 0; font-size: 1rem; }
.kiosk-pin-pad { display: grid; grid-template-columns: repeat(3, 5.5rem); gap: 1rem; }
.kiosk-pin-tecla { min-height: 4.5rem; font-size: 1.75rem; border-radius: 1rem; }
.kiosk-pin-aux { font-size: 1rem; }
.kiosk-pin-hueco { visibility: hidden; }
```

Si el CSS del kiosco usa variables de color propias (`--fondo`, `--acento`…), úsalas en lugar de `currentColor`.

- [ ] **Step 6: Ejecutar los tests y verificar que pasan**

Run: `cd /home/rodrigo_saez/atom-organizer-work/webui && npm test -- KioskLock`
Expected: 9 passed.

- [ ] **Step 7: No romper el resto**

Run: `cd /home/rodrigo_saez/atom-organizer-work/webui && npm test`
Expected: todos passed (antes de esta task eran 156).

- [ ] **Step 8: Commit**

```bash
cd /home/rodrigo_saez/atom-organizer-work
git add webui/src/bridge.js webui/src/KioskLock.jsx webui/src/KioskLock.test.jsx webui/src/styles.css
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(kiosco): pantalla de PIN con pad tactil"
```

(Ajusta el path del CSS en el `git add` al fichero que hayas tocado de verdad.)

---

### Task 6: Guard del kiosco y bloqueo por inactividad

**Files:**
- Modify: `webui/src/App.jsx` — estado del kiosco (~línea 225-249) y el render de `KioskScreen` (~626-650)
- Test: `webui/src/KioskGuard.test.jsx` (crear)

**Interfaces:**
- Consumes: `KioskLock` (Task 5), `api.pinEstado()` (Task 5).
- Produces: nada para tasks posteriores.

Comportamiento exacto:
- Al arrancar el kiosco se llama a `api.pinEstado()`.
- Si `hay_pin` → `KioskLock modo="verificar"` en lugar de `KioskScreen`, sin salida.
- Si no `hay_pin` **y** hay credencial (`kioskCloudStatus?.logged_in === true`) → `KioskLock modo="fijar"`, obligatorio.
- Si no `hay_pin` y no hay credencial → `KioskScreen` normal (se puede organizar en local sin PIN; el PIN se exige en cuanto se empareja).
- Tras 10 minutos sin ningún `pointerdown` en la ventana, vuelve a bloquear. El temporizador **no corre** mientras `kioskBusy` o `kioskSubiendo` sean `true`.

- [ ] **Step 1: Escribir el test que falla**

Crear `webui/src/KioskGuard.test.jsx`:

```jsx
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'

const pinEstado = vi.fn()
const cloudStatus = vi.fn()

const puente = {
  isServerMode: () => false,
  api: {
    pinEstado,
    pinVerificar: vi.fn().mockResolvedValue({ ok: true }),
    pinFijar: vi.fn().mockResolvedValue({ ok: true }),
    pinCambiar: vi.fn().mockResolvedValue({ ok: true }),
    cloudStatus,
    cloudLogout: vi.fn().mockResolvedValue({}),
    cloudPairStart: () => new Promise(() => {}),
    cloudPairPoll: () => new Promise(() => {}),
    pickFile: () => Promise.resolve(''),
  },
}
vi.mock('./bridge.js', () => puente)
vi.mock('./bridge', () => puente)

const { default: KioskGuard } = await import('./KioskGuard.jsx')

describe('KioskGuard', () => {
  beforeEach(() => {
    pinEstado.mockReset().mockResolvedValue({ ok: true, hay_pin: false, bloqueado: false, espera_segundos: 0 })
  })

  it('con PIN fijado pinta el bloqueo y no los hijos', async () => {
    pinEstado.mockResolvedValue({ ok: true, hay_pin: true, bloqueado: false, espera_segundos: 0 })
    render(<KioskGuard status={{ logged_in: true }} ocupado={false}><p>contenido</p></KioskGuard>)
    await vi.waitFor(() => expect(screen.getByTestId('kiosk-pin')).toBeTruthy())
    expect(screen.queryByText('contenido')).toBeNull()
  })

  it('sin PIN y con sesion obliga a fijarlo', async () => {
    render(<KioskGuard status={{ logged_in: true }} ocupado={false}><p>contenido</p></KioskGuard>)
    await vi.waitFor(() => expect(screen.getByText(/pin nuevo/i)).toBeTruthy())
    expect(screen.queryByText('contenido')).toBeNull()
  })

  it('sin PIN y sin sesion deja usar el kiosco', async () => {
    render(<KioskGuard status={{ logged_in: false }} ocupado={false}><p>contenido</p></KioskGuard>)
    await vi.waitFor(() => expect(screen.getByText('contenido')).toBeTruthy())
  })

  it('tras diez minutos de inactividad vuelve a bloquear', async () => {
    vi.useFakeTimers()
    pinEstado.mockResolvedValue({ ok: true, hay_pin: true, bloqueado: false, espera_segundos: 0 })
    try {
      render(<KioskGuard status={{ logged_in: true }} ocupado={false} desbloqueadoInicial><p>contenido</p></KioskGuard>)
      await act(async () => { await Promise.resolve() })
      expect(screen.getByText('contenido')).toBeTruthy()
      await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000 + 1000) })
      expect(screen.queryByText('contenido')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('no bloquea mientras hay una subida en curso', async () => {
    vi.useFakeTimers()
    pinEstado.mockResolvedValue({ ok: true, hay_pin: true, bloqueado: false, espera_segundos: 0 })
    try {
      render(<KioskGuard status={{ logged_in: true }} ocupado desbloqueadoInicial><p>contenido</p></KioskGuard>)
      await act(async () => { await Promise.resolve() })
      await act(async () => { vi.advanceTimersByTime(20 * 60 * 1000) })
      expect(screen.getByText('contenido')).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })
})
```

- [ ] **Step 2: Ejecutar el test y verificar que falla**

Run: `cd /home/rodrigo_saez/atom-organizer-work/webui && npm test -- KioskGuard`
Expected: FAIL al no poder resolver `./KioskGuard.jsx`.

- [ ] **Step 3: Implementar `KioskGuard`**

El guard va en su propio componente, no inline en `App.jsx`: `App.jsx` ya es enorme y así se puede testear aislado.

Crear `webui/src/KioskGuard.jsx`:

```jsx
import { useEffect, useState } from 'react'
import KioskLock from './KioskLock.jsx'
import { api } from './bridge.js'

export const INACTIVIDAD_MS = 10 * 60 * 1000

/**
 * Puerta del kiosco. Envuelve a `KioskScreen`:
 *
 * - hay PIN            -> pide el PIN, sin salida posible
 * - sin PIN + sesion   -> obliga a crear uno (la Pi no se queda sin PIN)
 * - sin PIN sin sesion -> pasa: organizar es local y no expone nada
 *
 * `ocupado` congela el temporizador de inactividad: bloquear a mitad de
 * una subida dejaria el lote a medias sin nadie mirando.
 */
export default function KioskGuard({ status, ocupado, desbloqueadoInicial = false, children }) {
  const [hayPin, setHayPin] = useState(null)
  const [desbloqueado, setDesbloqueado] = useState(desbloqueadoInicial)
  const [ultimoToque, setUltimoToque] = useState(() => Date.now())

  useEffect(() => {
    let vivo = true
    api.pinEstado()
      .then((res) => { if (vivo) setHayPin(Boolean(res?.hay_pin)) })
      .catch(() => { if (vivo) setHayPin(false) })
    return () => { vivo = false }
  }, [])

  useEffect(() => {
    const toque = () => setUltimoToque(Date.now())
    window.addEventListener('pointerdown', toque)
    window.addEventListener('keydown', toque)
    return () => {
      window.removeEventListener('pointerdown', toque)
      window.removeEventListener('keydown', toque)
    }
  }, [])

  useEffect(() => {
    if (!desbloqueado || ocupado) return undefined
    const t = setTimeout(() => setDesbloqueado(false), INACTIVIDAD_MS)
    return () => clearTimeout(t)
  }, [desbloqueado, ocupado, ultimoToque])

  if (hayPin === null) return null

  if (hayPin && !desbloqueado) {
    return <KioskLock modo="verificar" onOk={() => { setUltimoToque(Date.now()); setDesbloqueado(true) }} />
  }

  if (!hayPin && status?.logged_in === true) {
    return <KioskLock modo="fijar" onOk={() => { setHayPin(true); setUltimoToque(Date.now()); setDesbloqueado(true) }} />
  }

  return children
}
```

- [ ] **Step 4: Ejecutar los tests y verificar que pasan**

Run: `cd /home/rodrigo_saez/atom-organizer-work/webui && npm test -- KioskGuard`
Expected: 5 passed.

- [ ] **Step 5: Montar el guard en `App.jsx`**

En `webui/src/App.jsx`, añadir el import junto a los demás:

```jsx
import KioskGuard from './KioskGuard.jsx'
```

Y envolver el `<KioskScreen ... />` del render (línea ~627). Queda:

```jsx
        {kiosco ? (
          <KioskGuard status={kioskCloudStatus} ocupado={Boolean(kioskBusy || kioskSubiendo)}>
            <KioskScreen
              status={kioskCloudStatus}
              ...resto de props sin tocar...
            />
          </KioskGuard>
        ) : section === 'organizar' ? (
```

No cambies ninguna prop de `KioskScreen`: solo lo envuelves. Verifica el nombre real de la variable de subida en curso (`kioskSubiendo` está declarada en la línea ~239); si no existe, usa la que corresponda y déjalo anotado en el commit.

- [ ] **Step 6: No romper el resto**

Run: `cd /home/rodrigo_saez/atom-organizer-work/webui && npm test`
Expected: todos passed.

- [ ] **Step 7: Commit**

```bash
cd /home/rodrigo_saez/atom-organizer-work
git add webui/src/KioskGuard.jsx webui/src/KioskGuard.test.jsx webui/src/App.jsx
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(kiosco): PIN al entrar y bloqueo por inactividad"
```

---

### Task 7: Pantalla de perfil

La rama `accion === 'cuenta'` de `KioskScreen.jsx` (líneas 469-496) muestra hoy el email suelto y un botón de cerrar sesión. Pasa a ser un perfil.

**Files:**
- Modify: `webui/src/KioskScreen.jsx` (rama `accion === 'cuenta'`, líneas 469-496)
- Modify: el CSS del kiosco (el mismo de la Task 5)
- Test: `webui/src/KioskPerfil.test.jsx` (crear)

**Interfaces:**
- Consumes: `KioskLock` con `modo="cambiar"` (Task 5); campos ya existentes de `status`: `email`, `picture`, `logged_in`, `validada_en`, `pendientes`.
- Produces: nada.

Contenido de la pantalla: foto grande (con la inicial del email de fallback, igual que el avatar de la topbar), email, último acceso formateado desde `status.validada_en` (es un timestamp epoch en segundos; si es `null` se muestra "Sin registrar"), subidas pendientes en cola, botón "Cambiar PIN" y botón "Cerrar sesión".

Nota: **no** se muestra "última subida" — `atom_core/upload_log.py` es un log de texto rotado, no tiene consulta estructurada; añadirla sería otra task.

- [ ] **Step 1: Escribir el test que falla**

Crear `webui/src/KioskPerfil.test.jsx`:

```jsx
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

const puente = {
  isServerMode: () => false,
  api: {
    cloudLogout: vi.fn().mockResolvedValue({}),
    cloudStatus: vi.fn().mockResolvedValue({}),
    cloudPairStart: () => new Promise(() => {}),
    cloudPairPoll: () => new Promise(() => {}),
    pickFile: () => Promise.resolve(''),
    pinEstado: vi.fn().mockResolvedValue({ ok: true, hay_pin: true }),
    pinVerificar: vi.fn().mockResolvedValue({ ok: true }),
    pinFijar: vi.fn().mockResolvedValue({ ok: true }),
    pinCambiar: vi.fn().mockResolvedValue({ ok: true }),
  },
}
vi.mock('./bridge.js', () => puente)
vi.mock('./bridge', () => puente)

const { default: KioskScreen } = await import('./KioskScreen.jsx')

const STATUS = {
  logged_in: true,
  email: 'rebeca@aerotools.es',
  picture: 'https://ejemplo/foto.jpg',
  validada_en: 1756000000,
  pendientes: 3,
}

function pintar(status = STATUS) {
  return render(
    <KioskScreen
      status={status}
      inspecciones={[]}
      busy={false}
      accionInicial="cuenta"
      onRefreshStatus={() => {}}
    />
  )
}

describe('pantalla de perfil del kiosco', () => {
  it('muestra la foto grande y el email', () => {
    pintar()
    const foto = screen.getByTestId('kiosk-perfil-foto')
    expect(foto.getAttribute('src')).toBe('https://ejemplo/foto.jpg')
    expect(screen.getByText('rebeca@aerotools.es')).toBeTruthy()
  })

  it('sin foto cae a la inicial del email', () => {
    pintar({ ...STATUS, picture: null })
    expect(screen.queryByTestId('kiosk-perfil-foto')).toBeNull()
    expect(screen.getByText('R')).toBeTruthy()
  })

  it('muestra las subidas pendientes', () => {
    pintar()
    expect(screen.getByText(/3/)).toBeTruthy()
  })

  it('sin ultimo acceso registrado lo dice', () => {
    pintar({ ...STATUS, validada_en: null })
    expect(screen.getByText(/sin registrar/i)).toBeTruthy()
  })

  it('tiene los botones de cambiar PIN y cerrar sesion', () => {
    pintar()
    expect(screen.getByRole('button', { name: /cambiar pin/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /cerrar sesión/i })).toBeTruthy()
  })

  it('cambiar PIN abre el pad pidiendo el PIN actual', () => {
    pintar()
    screen.getByRole('button', { name: /cambiar pin/i }).click()
    expect(screen.getByText(/pin actual/i)).toBeTruthy()
  })
})
```

- [ ] **Step 2: Ejecutar el test y verificar que falla**

Run: `cd /home/rodrigo_saez/atom-organizer-work/webui && npm test -- KioskPerfil`
Expected: FAIL — no existe `kiosk-perfil-foto` ni el botón "Cambiar PIN".

- [ ] **Step 3: Implementar la pantalla**

En `webui/src/KioskScreen.jsx`, añadir el import:

```jsx
import KioskLock from './KioskLock.jsx'
```

Añadir un estado junto a `const [accion, setAccion] = useState(accionInicial)`:

```jsx
  const [cambiandoPin, setCambiandoPin] = useState(false)
```

Y sustituir la rama completa `if (accion === 'cuenta') { ... }` (líneas 469-496) por:

```jsx
  if (accion === 'cuenta') {
    const logueado = Boolean(status?.logged_in)
    const pendientes = status?.pendientes || 0
    const ultimoAcceso = status?.validada_en
      ? new Date(status.validada_en * 1000).toLocaleString('es-ES', {
          day: '2-digit', month: '2-digit', year: 'numeric',
          hour: '2-digit', minute: '2-digit',
        })
      : 'Sin registrar'
    return (
      <div className="kiosk kiosk-cuenta">
        <div className="kiosk-header kiosk-header-paso">
          <BotonAtras tactil={tactil} onActivar={() => { setCambiandoPin(false); setAccion(null) }} />
          <span className="kiosk-titulo">Cuenta</span>
        </div>
        {!logueado ? (
          <PairScreen onPaired={() => { onRefreshStatus?.(); setAccion(null) }} />
        ) : cambiandoPin ? (
          <KioskLock
            modo="cambiar"
            onOk={() => setCambiandoPin(false)}
            onCancelar={() => setCambiandoPin(false)}
          />
        ) : (
          <div className="kiosk-perfil">
            <div className="kiosk-perfil-foto-marco">
              {status?.picture ? (
                <img
                  src={status.picture}
                  alt={`Foto de ${email}`}
                  className="kiosk-perfil-foto"
                  data-testid="kiosk-perfil-foto"
                />
              ) : (
                <span className="kiosk-perfil-inicial">{inicial}</span>
              )}
            </div>
            <span className="kiosk-perfil-email">{email}</span>
            <dl className="kiosk-perfil-datos">
              <dt>Último acceso</dt>
              <dd>{ultimoAcceso}</dd>
              <dt>Subidas pendientes</dt>
              <dd>{pendientes}</dd>
            </dl>
            <div className="kiosk-perfil-acciones">
              <BotonToque
                className="btn kiosk-btn"
                tactil={tactil}
                onActivar={() => setCambiandoPin(true)}
              >
                Cambiar PIN
              </BotonToque>
              <BotonToque
                className="btn-ghost kiosk-btn"
                tactil={tactil}
                onActivar={async () => {
                  await api.cloudLogout().catch(() => {})
                  onRefreshStatus?.()
                }}
              >
                Cerrar sesión
              </BotonToque>
            </div>
          </div>
        )}
      </div>
    )
  }
```

Cerrar sesión borra el PIN en el backend (Task 4), así que tras hacerlo la Pi vuelve a estar sin PIN y el guard exigirá crear uno nuevo en cuanto se empareje otra vez. Es la vía de recuperación: no hace falta ningún aviso extra en pantalla.

- [ ] **Step 4: Añadir los estilos**

En el mismo CSS del kiosco de la Task 5:

```css
.kiosk-perfil { display: flex; flex-direction: column; align-items: center; gap: 1rem; padding: 1.5rem; }
.kiosk-perfil-foto-marco { width: 9rem; height: 9rem; border-radius: 50%; overflow: hidden; display: flex; align-items: center; justify-content: center; }
.kiosk-perfil-foto { width: 100%; height: 100%; object-fit: cover; }
.kiosk-perfil-inicial { font-size: 4rem; }
.kiosk-perfil-email { font-size: 1.25rem; }
.kiosk-perfil-datos { display: grid; grid-template-columns: auto auto; gap: 0.25rem 1.5rem; margin: 0; }
.kiosk-perfil-datos dt { opacity: 0.7; }
.kiosk-perfil-datos dd { margin: 0; }
.kiosk-perfil-acciones { display: flex; flex-direction: column; gap: 1rem; width: 100%; max-width: 20rem; }
```

- [ ] **Step 5: Ejecutar los tests y verificar que pasan**

Run: `cd /home/rodrigo_saez/atom-organizer-work/webui && npm test -- KioskPerfil`
Expected: 6 passed.

- [ ] **Step 6: No romper el resto**

Run: `cd /home/rodrigo_saez/atom-organizer-work/webui && npm test`
Expected: todos passed. Presta atención a `webui/src/AvisoSesion.test.jsx`, que monta `KioskScreen`.

- [ ] **Step 7: Commit**

```bash
cd /home/rodrigo_saez/atom-organizer-work
git add webui/src/KioskScreen.jsx webui/src/KioskPerfil.test.jsx webui/src/styles.css
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(kiosco): pantalla de perfil con foto grande y cambio de PIN"
```

---

### Task 8: Verificación completa y despliegue en la Pi

**Files:** ninguno (solo build y despliegue).

**Interfaces:** consume todo lo anterior.

- [ ] **Step 1: Suite completa**

```bash
cd /home/rodrigo_saez/atom-organizer-work
./venv/bin/python -m pytest tests/ -q
cd webui && npm test
```
Expected: backend `1 failed` (el preexistente de `test_dji_resiliencia_parallel.py`) y el resto passed; frontend todos passed.

- [ ] **Step 2: Build del frontend**

```bash
cd /home/rodrigo_saez/atom-organizer-work/webui
NODE_OPTIONS=--max-old-space-size=5120 npm run build
```
Expected: build ok; anota el nombre del bundle nuevo (`webui/dist/assets/index-*.js`).

- [ ] **Step 3: Comprobar que el túnel a la Pi está vivo**

```bash
ssh -p 2222 -o BatchMode=yes -o StrictHostKeyChecking=no pi@localhost 'hostname'
```
Si da `Connection refused`, el túnel inverso lo abre el portátil de Rodrigo y está apagado: **para aquí y avísale**. No hay forma de arreglarlo desde la VM.

- [ ] **Step 4: Desplegar**

```bash
cd /home/rodrigo_saez/atom-organizer-work
rsync -av -e "ssh -p 2222 -o BatchMode=yes -o StrictHostKeyChecking=no" --exclude '__pycache__' --exclude '*.pyc' \
  app_webview.py pipeline.py utils.py version.py exif.py external_tools.py organize_cli.py rjpeg_a_tiff.py atom_core pi@localhost:~/organizer/
rsync -av --delete -e "ssh -p 2222 -o BatchMode=yes -o StrictHostKeyChecking=no" webui/dist/ pi@localhost:~/organizer/webui/dist/
```

🚨 **NUNCA `rsync --delete` sobre `~/organizer` entero**: la Pi tiene `pi_login.py` y `programas_externos/x86-runtime/` que dev no tiene. El `--delete` solo dentro de `webui/dist/`.

- [ ] **Step 5: Relanzar el servidor y comprobar**

```bash
ssh -p 2222 pi@localhost 'pkill -f "app_webview.py --server"; cd ~/organizer && setsid ~/venv-test/bin/python app_webview.py --server --host 0.0.0.0 --port 8765 > ~/organizer-server.log 2>&1 < /dev/null &'
sleep 5
ssh -p 2222 pi@localhost 'curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/'
```
Expected: `200`.

- [ ] **Step 6: Recargar Chromium (la Pi no tiene teclado)**

```bash
ssh -p 2222 pi@localhost 'export DISPLAY=:0; W=$(xdotool search --onlyvisible --class chromium | head -1); xdotool windowactivate $W; sleep 1; xdotool key --clearmodifiers ctrl+shift+r'
```

- [ ] **Step 7: Prueba en hardware (la hace Rodrigo, requiere la pantalla)**

```
1. Estando emparejado, recargar        -> debe pedir crear el PIN (pantalla "PIN nuevo" + "Repite").
2. Crear el PIN                        -> entra al kiosco.
3. Recargar                            -> debe pedir el PIN.
4. Meter un PIN malo 5 veces           -> pad deshabilitado con cuenta atrás.
5. Esperar 10 min sin tocar            -> vuelve a bloquear (no durante una subida).
6. Avatar -> Cuenta                    -> foto grande, email, último acceso, pendientes.
7. Cambiar PIN                         -> pide actual, nuevo y repetición.
8. Cerrar sesión y volver a emparejar  -> el PIN se resetea y pide crear uno nuevo.
```

- [ ] **Step 8: Push y documentación**

```bash
cd /home/rodrigo_saez/atom-organizer-work
git push origin raspi/modo-servidor
```
Después, invocar la skill `documentar-sesion` para dejarlo en `30_Gestion/Proyectos/ATOM/ATOM Organizer.md` y en el Diario del día.

---

### Task 9 (OPCIONAL — requiere OK expreso de Rodrigo, toca la Suite en producción): mostrar el nombre del usuario

El perfil enseña email y foto porque son los únicos datos que llegan hoy. En la Pi la sesión entra por **emparejado**, no por login de Google: `GET /api/organizer/pair/poll` de Atom-suite devuelve `estado`, `device_token`, `email` y `picture`, y nada más. El `name` del id_token de Google no se lee ni siquiera en el flujo de escritorio (`_identidad_de_id_token` en `atom_core/google_auth.py:659-685` solo extrae `email`, `hd` y `picture`).

**No arranques esta task sin que Rodrigo confirme que puede tocarse la Suite**: es un cambio en producción, con su propio deploy.

**Files:**
- Modify (Atom-suite, otro repo): el handler de `/api/organizer/pair/poll` para incluir `name`
- Modify: `atom_core/google_auth.py` — `Identity` (~95-102), `_identidad_de_id_token` (~659-685), `pair()`, y la llamada a `store.guardar()` (~288-290)
- Modify: `atom_core/session_store.py` — columna `nombre` en la tabla `sesion` + subir `ESQUEMA` a 2 con su migración
- Modify: `app_webview.py` — `cloud_status()` devuelve `"name": ident.name if ident else None`
- Modify: `webui/src/KioskScreen.jsx` — pintar el nombre sobre el email en el perfil
- Test: ampliar `tests/test_session_store_meta.py` y `webui/src/KioskPerfil.test.jsx`

Orden: primero la Suite (sin ella el resto no tiene dato que mostrar), luego el cliente. El perfil debe seguir funcionando si `name` viene vacío: el email es el fallback.

---

## Notas de diseño que el implementador debe respetar

- **El PIN es del dispositivo, no del usuario.** No lo valides nunca contra la Suite ni lo hagas depender de la red: la Pi tiene que poder desbloquearse con el router caído.
- **El hash no sale del backend.** Ningún endpoint, log ni evento SSE puede contener el hash, el salt ni el PIN en claro. Los tests lo comprueban; no los relajes.
- **Un `session.db` corrupto no puede dejar la Pi inoperable.** Si el `meta` no se puede leer o el valor no parsea, se trata como "no hay PIN" y se sigue. Mejor un kiosco usable que un ladrillo.
- **La inactividad se congela durante las subidas.** Bloquear a mitad de un lote de 3.000 fotos deja el trabajo a medias sin nadie mirando la pantalla.
- **Nada de `px` en el CSS**: `rem`/`vh`/`vw`, para que escale en la pantalla de la Pi.
- **No hay teclado físico en la Pi.** Todo lo que se teclee tiene que poder teclearse con el dedo.
