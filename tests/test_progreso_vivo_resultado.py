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
