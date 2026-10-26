"""`Manifest` escribe por lotes (no por fichero) y SIEMPRE hace flush final;
`particionar` calcula los MD5 en paralelo con el mismo resultado que en serie."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from atom_core import cloud_upload as cu
from atom_core import subida_resultado as sr

PREFIJO = "KL05/INSPECCIONES/TERMICA_MODULOS/2026/"


def _item(carpeta: Path, nombre: str, datos: bytes = b"x") -> cu.UploadItem:
    p = carpeta / nombre
    p.write_bytes(datos)
    return cu.UploadItem(local=p, remote=PREFIJO + nombre, size=len(datos))


def _hidratado(tmp_path: Path, n: int) -> cu.Manifest:
    m = cu.Manifest(tmp_path / "m.json")
    m._done = {f"k{i}": {"size": 1, "mtime": 1, "md5": "AAAA=="} for i in range(n)}
    return m


def _contar_replace(monkeypatch) -> list:
    llamadas: list = []
    real = os.replace
    monkeypatch.setattr(cu.os, "replace", lambda a, b: (llamadas.append(b), real(a, b))[1])
    return llamadas


def test_mark_masivo_no_reescribe_por_fichero(tmp_path, monkeypatch):
    m = _hidratado(tmp_path, 50_000)
    items = [_item(tmp_path, f"f{i}.jpg") for i in range(2000)]
    llamadas = _contar_replace(monkeypatch)
    for it in items:
        m.mark(it, "BBBB==")
    assert len(llamadas) <= 2000 // cu.Manifest.FLUSH_CADA_MARCAS + 1
    m.flush()
    datos = json.loads((tmp_path / "m.json").read_text())["done"]
    assert len(datos) == 50_000 + 2000
    assert all(it.key in datos for it in items)


def test_flush_sin_cambios_no_escribe(tmp_path, monkeypatch):
    m = _hidratado(tmp_path, 10)
    llamadas = _contar_replace(monkeypatch)
    m.flush()
    assert llamadas == []


def test_hidratar_hace_un_solo_flush(tmp_path, monkeypatch):
    items = [_item(tmp_path, f"h{i}.jpg") for i in range(300)]
    remotos = {it.remote: cu.RemoteObject(it.remote, it.size, "AA==") for it in items}
    m = cu.Manifest(tmp_path / "m.json")
    llamadas = _contar_replace(monkeypatch)
    assert m.hidratar(items, remotos) == 300
    assert len(llamadas) == 1
    assert len(json.loads((tmp_path / "m.json").read_text())["done"]) == 300


def test_flush_por_tiempo(tmp_path, monkeypatch):
    m = cu.Manifest(tmp_path / "m.json")
    m._ultimo_flush = time.monotonic() - 60
    m.mark(_item(tmp_path, "a.jpg"), "AA==")
    assert (tmp_path / "m.json").exists()


def test_excepcion_a_mitad_deja_lo_marcado_en_disco(tmp_path, monkeypatch):
    items = [_item(tmp_path, f"u{i}.jpg") for i in range(10)]
    plan = cu.UploadPlan(root=tmp_path, items=items, prefix=PREFIJO.strip("/"))
    vistos = []

    def falso(item, *a, **k):
        vistos.append(item)
        if len(vistos) > 4:
            raise KeyboardInterrupt  # BaseException: sale del bucle de upload_plan
        return "AA=="

    monkeypatch.setattr(cu, "upload_file", falso)
    m = cu.Manifest(tmp_path / "m.json")
    with pytest.raises(KeyboardInterrupt):
        cu.upload_plan(plan, cu.UrlProvider(), concurrency=1, manifest=m, remotos={})
    en_disco = json.loads((tmp_path / "m.json").read_text())["done"]
    assert len(en_disco) == 4
    assert set(en_disco) == {i.key for i in vistos[:4]}


def test_subir_resultado_hace_flush_final_aunque_subir_falle(tmp_path):
    marcadas: list = []

    class Prov:
        solo_crear = False
        def prefijo_destino(self): return PREFIJO
        def listar_remotos(self, prefix): return {}

    carpeta = tmp_path / "salida"
    (carpeta / "CSVs").mkdir(parents=True)
    (carpeta / "CSVs" / "V1_meta.csv").write_bytes(b"m")

    def subir(plan, prov, *, manifest, **k):
        for it in plan.items:
            manifest.mark(it, "AA==")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        sr.subir_resultado(carpeta, 1, sr.MODO_URGENCIA, None, proveedor=Prov(), subir=subir)
    f = carpeta / ".organizado" / sr.NOMBRE_MANIFIESTO_SUBIDA
    assert len(json.loads(f.read_text())["done"]) == 1


def _md5(datos: bytes) -> str:
    return base64.b64encode(hashlib.md5(datos).digest()).decode()


def test_particionar_paralelo_igual_que_secuencial(tmp_path):
    items, remotos = [], {}
    for i in range(60):
        datos = f"contenido-{i}".encode()
        nombre = f"TERMICA/PB1/V1/F{i}_T.tiff"
        (tmp_path / "TERMICA/PB1/V1").mkdir(parents=True, exist_ok=True)
        p = tmp_path / nombre
        p.write_bytes(datos)
        it = cu.UploadItem(local=p, remote=PREFIJO + nombre, size=len(datos))
        items.append(it)
        if i % 4 == 1:
            continue                                   # no existe en bucket
        remoto_datos = datos if i % 4 != 2 else b"X" * len(datos)  # 2: mismo tamaño, otro MD5
        remotos[it.remote] = cu.RemoteObject(it.remote, len(remoto_datos), _md5(remoto_datos))
    (tmp_path / ".organizado").mkdir()
    man = cu.Manifest(tmp_path / ".organizado" / "s.json")

    def clave(p):
        return ([i.remote for i in p.pendientes], [i.remote for i in p.hechos], p.conflictos)

    seq = sr.particionar(items, remotos, man, PREFIJO, concurrency=1)
    progreso: list = []
    par = sr.particionar(items, remotos, man, PREFIJO, concurrency=16,
                         on_progreso=lambda h, t: progreso.append((h, t)))
    assert clave(par) == clave(seq)
    assert len(seq.hechos) == 30 and len(seq.conflictos) == 15 and len(seq.pendientes) == 15
    assert progreso and progreso[-1] == (45, 45)


def test_particionar_propaga_error_de_lectura(tmp_path, monkeypatch):
    it = _item(tmp_path, "A_T.tiff", b"abc")
    remotos = {it.remote: cu.RemoteObject(it.remote, 3, _md5(b"abc"))}
    man = cu.Manifest(tmp_path / "s.json")
    monkeypatch.setattr(cu, "_file_md5_b64", lambda _p: (_ for _ in ()).throw(OSError("ilegible")))
    with pytest.raises(OSError):
        sr.particionar([it], remotos, man, PREFIJO)
