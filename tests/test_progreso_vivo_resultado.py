"""Progreso en vivo de la subida del resultado: callback del listado del bucket y
desglose `por_pb` (agrupación por carpeta PBn)."""
from __future__ import annotations

import io
import json

from atom_core import cloud_upload as cu
from atom_core import subida_resultado as sr
from tests.test_subida_resultado import ProveedorFalso, _arbol, gcs  # noqa: F401


def test_pb_de_agrupa_por_carpeta_pb():
    assert sr.pb_de("TERMICA/PB17/V1/a.tiff") == "PB17"
    assert sr.pb_de("RGB/PB3_V2/x.JPG") == "PB3"
    assert sr.pb_de("TERMICA/pb05/a.tiff") == "PB5"
    assert sr.pb_de("CSVs/V1_meta.csv") == "General"
    assert sr.pb_de("ESTADILLOS/PB9.xlsx") == "General"   # el nombre del fichero no cuenta


def test_progreso_por_pb_cuenta_una_vez_y_ordena_numerico():
    pref = "P/"
    mk = lambda r, n: cu.UploadItem(local=None, remote=pref + r, size=n)  # noqa: E731
    items = [mk("TERMICA/PB10/a", 10), mk("TERMICA/PB2/a", 20), mk("TERMICA/PB2/b", 30), mk("CSVs/x.csv", 5)]
    pp = sr.ProgresoPorPB(items, pref)
    pp.marcar(items[1], True)
    pp.marcar(items[1], True)         # repetido: no duplica
    pp.marcar(items[2], False)        # fallida cuenta como fallida, no como hecha
    lista = pp.lista()
    assert [g["pb"] for g in lista] == ["PB2", "PB10", "General"]
    pb2 = lista[0]
    assert (pb2["ficheros_hechos"], pb2["ficheros_total"], pb2["bytes_done"], pb2["bytes_total"], pb2["fallidas"]) == (1, 2, 20, 50, 1)
    assert pb2["terminado"] is True   # todo resuelto (uno hecho + uno fallido)
    assert lista[1]["terminado"] is False


def test_listar_objetos_remotos_llama_on_pagina(monkeypatch):
    paginas = [
        {"items": [{"name": f"p/{i}", "size": "1"} for i in range(3)], "nextPageToken": "t1"},
        {"items": [{"name": f"p/{i}", "size": "1"} for i in range(3, 5)]},
    ]
    it = iter(paginas)
    monkeypatch.setattr(cu.urllib.request, "urlopen",
                        lambda *a, **k: io.BytesIO(json.dumps(next(it)).encode()))
    monkeypatch.setattr(cu, "token_gcs", lambda *a, **k: "tok")
    vistos = []
    res = cu.listar_objetos_remotos("b", "p", None, on_pagina=lambda n, p: vistos.append((n, p)))
    assert len(res) == 5
    assert vistos == [(3, 1), (5, 2)]


def test_listar_objetos_remotos_callback_roto_no_rompe(monkeypatch):
    monkeypatch.setattr(cu.urllib.request, "urlopen",
                        lambda *a, **k: io.BytesIO(json.dumps({"items": [{"name": "p/a", "size": "1"}]}).encode()))
    monkeypatch.setattr(cu, "token_gcs", lambda *a, **k: "tok")
    def malo(n, p):
        raise RuntimeError("x")
    assert len(cu.listar_objetos_remotos("b", "p", None, on_pagina=malo)) == 1


class _ProvConListado(ProveedorFalso):
    soporta_progreso_listado = True

    def listar_remotos(self, prefix, on_pagina=None):
        if on_pagina:
            on_pagina(1000, 1)
            on_pagina(1500, 2)
        return super().listar_remotos(prefix)


def test_subir_resultado_emite_listado_y_por_pb(tmp_path, gcs):  # noqa: F811
    destino = _arbol(tmp_path, {
        "TERMICA/PB1/V1/A_T.tiff": b"aa", "TERMICA/PB2/V1/B_T.tiff": b"bbb", "CSVs/V1_meta.csv": b"m",
    })
    eventos = []
    r = sr.subir_resultado(destino, 7, sr.MODO_NORMAL, auth=None, proveedor=_ProvConListado(),
                           concurrency=1, on_estado=eventos.append)
    assert r.ok
    lis = [e for e in eventos if e["etapa"] == "listando" and e["paginas"] > 0]
    assert [(e["listados"], e["paginas"]) for e in lis] == [(1000, 1), (1500, 2)]
    ult = eventos[-1]["por_pb"]
    assert {g["pb"] for g in ult} == {"PB1", "PB2", "General"}
    assert all(g["terminado"] and g["ficheros_hechos"] == g["ficheros_total"] for g in ult)
    assert sum(g["bytes_done"] for g in ult) == 6


# --- Subida: progreso global, un solo escaneo, menos ruido --------------------------------

import logging  # noqa: E402

from tests.test_subida_resultado import PREFIJO, SALIDA_PEQUENA  # noqa: E402


class _ProvContando(ProveedorFalso):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.listados = 0

    def listar_remotos(self, prefix, on_pagina=None):
        self.listados += 1
        return super().listar_remotos(prefix)


def _subir_falso(plan, provider, **kw):
    cb = kw.get("on_stats")
    total = sum(i.size for i in plan.items)
    for n in range(1, 201):   # 200 avisos de stats por pasada: el log debe condensarlos
        if cb:
            cb({"files_done": min(n, len(plan.items)), "files_total": len(plan.items),
                "bytes_done": total * n // 200, "bytes_total": total, "final": n == 200})
    for it in plan.items:
        provider.calls.append((it.remote, it.size))
    return cu.UploadResult(uploaded=len(plan.items))


def test_on_progress_se_invoca_con_ficheros_bytes_mbs_y_eta(tmp_path, caplog):
    _arbol(tmp_path, SALIDA_PEQUENA)
    lineas = []
    with caplog.at_level(logging.INFO, logger=sr.logger.name):
        sr.subir_resultado(tmp_path, 7, sr.MODO_NORMAL, auth=None, proveedor=_ProvContando(),
                           subir=_subir_falso, on_progress=lineas.append)
    assert lineas, "on_progress nunca se invocó"
    ult = lineas[-1]
    assert "ficheros" in ult and "MB/s" in ult and "ETA" in ult and "MB" in ult
    # Nunca una línea por fichero: con throttle (~5 s / ~5 %) y 8 ficheros salen muy pocas.
    assert len(lineas) <= 25   # 400 avisos de stats → ≈1 línea cada 5 %
    assert any("subida resultado:" in r.getMessage() and r.levelno == logging.INFO for r in caplog.records)


def test_destino_se_escanea_una_vez_inicial_y_una_de_verificacion(tmp_path):
    _arbol(tmp_path, SALIDA_PEQUENA)
    prov = _ProvContando()
    sr.subir_resultado(tmp_path, 7, sr.MODO_NORMAL, auth=None, proveedor=prov, subir=_subir_falso)
    assert prov.listados == 2  # inicial + verificación final (única, justificada)


def test_cadena_urgentes_normal_reutiliza_el_listado_final(tmp_path):
    _arbol(tmp_path, SALIDA_PEQUENA)
    prov = _ProvContando()
    r1 = sr.subir_resultado(tmp_path, 7, sr.MODO_URGENCIA, auth=None, proveedor=prov, subir=_subir_falso)
    assert prov.listados == 2 and r1.remotos_finales is not None
    r2 = sr.subir_resultado(tmp_path, 7, sr.MODO_NORMAL, auth=None, proveedor=prov, subir=_subir_falso,
                            remotos=r1.remotos_finales)
    assert prov.listados == 3  # la 2.ª pasada solo añade su verificación, no un listado inicial
    assert r2.ok


def test_verificacion_sigue_detectando_faltantes_con_remotos_inyectados(tmp_path):
    _arbol(tmp_path, SALIDA_PEQUENA)
    prov = _ProvContando()
    prov.refleja_subidas = False   # lo «subido» no aparece en el bucket
    r = sr.subir_resultado(tmp_path, 7, sr.MODO_URGENCIA, auth=None, proveedor=prov, subir=_subir_falso,
                           remotos={})
    assert r.faltantes and not r.ok


def test_sin_clasificar_avisa_una_vez_con_recuento(tmp_path, caplog):
    datos = dict(SALIDA_PEQUENA)
    datos["RARO/uno.bin"] = b"1"
    datos["RARO/dos.bin"] = b"2"
    _arbol(tmp_path, datos)
    with caplog.at_level(logging.DEBUG, logger=sr.logger.name):
        sr.subir_resultado(tmp_path, 7, sr.MODO_NORMAL, auth=None, proveedor=_ProvContando(), subir=_subir_falso)
    avisos = [r for r in caplog.records if "sin clasificar" in r.getMessage()]
    assert len(avisos) == 1 and "3 fichero" in avisos[0].getMessage()


def test_reconciliar_no_emite_debug_por_fichero(tmp_path, caplog):
    items = [cu.UploadItem(local=tmp_path / f"{i}", remote=f"p/{i}", size=5) for i in range(50)]
    remotos = {it.remote: cu.RemoteObject(it.remote, 9, "") for it in items}   # tamaño distinto
    plan = cu.UploadPlan(root=tmp_path, items=items, prefix="p")
    with caplog.at_level(logging.DEBUG, logger=cu._log.name):
        pend, _ = cu.reconciliar(plan, remotos)
    assert len(pend) == 50
    assert not [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len([r for r in caplog.records if "50 por tamaño" in r.getMessage()]) == 1
