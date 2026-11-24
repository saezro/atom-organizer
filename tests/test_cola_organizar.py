"""Persistencia y reglas de la cola de «Organizar» (`atom_core/cola_organizar.py`)."""
from __future__ import annotations

import json
import os

import pytest

from atom_core import cola_organizar as co


@pytest.fixture
def ruta(tmp_path):
    return tmp_path / "datos" / "cola_organizar.json"


def _p(tmp_path, origen="vuelo1", destino="salida"):
    return {"origen": str(tmp_path / origen), "destino": str(tmp_path / destino), "estadillo": "e.csv"}


def test_encolar_persiste_con_indent_2_y_hora_madrid(tmp_path, ruta):
    r = co.encolar(_p(tmp_path), {"x": 1}, ruta=ruta)
    assert r["ok"] and r["avisos"] == []
    texto = ruta.read_text(encoding="utf-8")
    assert texto.startswith("[\n  {")  # indent 2, nunca minificado
    job = json.loads(texto)[0]
    assert job["estado"] == "pendiente" and job["advanced"] == {"x": 1}
    assert job["creado"].endswith(("+01:00", "+02:00"))  # Europe/Madrid, no UTC
    assert job["iniciado"] is None and job["fin"] is None


def test_escritura_atomica_un_fallo_no_corrompe(tmp_path, ruta, monkeypatch):
    co.encolar(_p(tmp_path), ruta=ruta)
    antes = ruta.read_text(encoding="utf-8")

    def falla(*a, **k):
        raise OSError("disco lleno")
    monkeypatch.setattr(os, "replace", falla)
    with pytest.raises(OSError):
        co.encolar(_p(tmp_path, "vuelo2", "otro"), ruta=ruta)
    monkeypatch.undo()
    assert ruta.read_text(encoding="utf-8") == antes
    assert len(co.listar(ruta=ruta)) == 1


def test_fichero_corrupto_es_cola_vacia(tmp_path, ruta):
    ruta.parent.mkdir(parents=True)
    ruta.write_text("[{no es json", encoding="utf-8")
    assert co.listar(ruta=ruta) == []
    assert co.encolar(_p(tmp_path), ruta=ruta)["ok"]


def test_origen_duplicado_pendiente_o_en_curso_se_rechaza(tmp_path, ruta):
    a = co.encolar(_p(tmp_path), ruta=ruta)["job"]
    r = co.encolar(_p(tmp_path, destino="otro"), ruta=ruta)
    assert not r["ok"] and "ya está en la cola" in r["error"]
    co.marcar(a["id"], co.EN_CURSO, ruta=ruta)
    assert not co.encolar(_p(tmp_path, destino="otro"), ruta=ruta)["ok"]
    # Ya terminado: se puede volver a encolar el mismo origen.
    co.marcar(a["id"], co.HECHO, ruta=ruta)
    assert co.encolar(_p(tmp_path, destino="otro"), ruta=ruta)["ok"]


def test_destino_duplicado_se_permite_con_aviso(tmp_path, ruta):
    co.encolar(_p(tmp_path, "v1", "mismo"), ruta=ruta)
    r = co.encolar(_p(tmp_path, "v2", "mismo"), ruta=ruta)
    assert r["ok"] and len(r["avisos"]) == 1 and "acumulan" in r["avisos"][0]
    assert len(co.listar(ruta=ruta)) == 2


def test_origen_igual_destino_o_vacios_se_rechazan(tmp_path, ruta):
    assert not co.encolar({"origen": str(tmp_path), "destino": str(tmp_path)}, ruta=ruta)["ok"]
    assert not co.encolar({"origen": "", "destino": "x"}, ruta=ruta)["ok"]
    assert not co.encolar({"origen": "x", "destino": ""}, ruta=ruta)["ok"]


def test_quitar_nunca_un_en_curso(tmp_path, ruta):
    a = co.encolar(_p(tmp_path, "a", "da"), ruta=ruta)["job"]["id"]
    b = co.encolar(_p(tmp_path, "b", "db"), ruta=ruta)["job"]["id"]
    co.marcar(a, co.EN_CURSO, ruta=ruta)
    r = co.quitar(a, ruta=ruta)
    assert not r["ok"] and "en curso" in r["error"]
    assert co.quitar(b, ruta=ruta)["ok"]
    assert [j["id"] for j in co.listar(ruta=ruta)] == [a]
    assert not co.quitar("nope", ruta=ruta)["ok"]


@pytest.mark.parametrize("estado", [co.HECHO, co.ERROR, co.CANCELADO, co.INTERRUMPIDO])
def test_quitar_estados_terminales(tmp_path, ruta, estado):
    a = co.encolar(_p(tmp_path), ruta=ruta)["job"]["id"]
    co.marcar(a, estado, ruta=ruta)
    assert co.quitar(a, ruta=ruta)["ok"]


def test_siguiente_respeta_el_orden(tmp_path, ruta):
    a = co.encolar(_p(tmp_path, "a", "da"), ruta=ruta)["job"]["id"]
    b = co.encolar(_p(tmp_path, "b", "db"), ruta=ruta)["job"]["id"]
    assert co.siguiente(ruta=ruta)["id"] == a
    co.marcar(a, co.HECHO, resumen="ok", ruta=ruta)
    assert co.siguiente(ruta=ruta)["id"] == b
    co.marcar(b, co.ERROR, error="x", ruta=ruta)
    assert co.siguiente(ruta=ruta) is None


def test_marcar_sella_horas_y_limpia_al_arrancar(tmp_path, ruta):
    a = co.encolar(_p(tmp_path), ruta=ruta)["job"]["id"]
    j = co.marcar(a, co.EN_CURSO, ruta=ruta)
    assert j["iniciado"] and j["fin"] is None
    j = co.marcar(a, co.ERROR, error="boom", ruta=ruta)
    assert j["fin"] and j["error"] == "boom"
    assert co.marcar("nope", co.HECHO, ruta=ruta) is None
    with pytest.raises(ValueError):
        co.marcar(a, "raro", ruta=ruta)


def test_en_curso_pasa_a_interrumpido_al_reabrir_y_no_se_reanuda(tmp_path, ruta):
    a = co.encolar(_p(tmp_path, "a", "da"), ruta=ruta)["job"]["id"]
    b = co.encolar(_p(tmp_path, "b", "db"), ruta=ruta)["job"]["id"]
    co.marcar(a, co.EN_CURSO, ruta=ruta)
    assert co.marcar_interrumpidos(ruta=ruta) == 1
    jobs = {j["id"]: j for j in co.listar(ruta=ruta)}
    assert jobs[a]["estado"] == "interrumpido" and "Reintenta" in jobs[a]["error"]
    assert jobs[b]["estado"] == "pendiente"
    assert co.marcar_interrumpidos(ruta=ruta) == 0  # idempotente


def test_reintentar(tmp_path, ruta):
    a = co.encolar(_p(tmp_path), ruta=ruta)["job"]["id"]
    assert not co.reintentar(a, ruta=ruta)["ok"]  # pendiente: nada que reintentar
    co.marcar(a, co.INTERRUMPIDO, error="cierre", ruta=ruta)
    r = co.reintentar(a, ruta=ruta)
    assert r["ok"] and r["job"]["estado"] == "pendiente" and r["job"]["error"] == ""
    assert not co.reintentar("nope", ruta=ruta)["ok"]


def test_reintentar_rechaza_si_el_origen_ya_esta_en_cola(tmp_path, ruta):
    a = co.encolar(_p(tmp_path), ruta=ruta)["job"]["id"]
    co.marcar(a, co.ERROR, error="x", ruta=ruta)
    co.encolar(_p(tmp_path, destino="otro"), ruta=ruta)
    assert not co.reintentar(a, ruta=ruta)["ok"]


def test_espacio_suficiente(tmp_path):
    gb = 1024 ** 3

    class U:  # noqa: D401
        def __init__(self, libre):
            self.free = libre

    ok, _ = co.espacio_suficiente("o", str(tmp_path / "nuevo" / "d"), uso_disco=lambda p: U(50 * gb),
                                  tamano=lambda o: 10 * gb)
    assert ok
    ok, msg = co.espacio_suficiente("o", str(tmp_path / "d"), uso_disco=lambda p: U(5 * gb),
                                    tamano=lambda o: 10 * gb)
    assert not ok and "Sin espacio" in msg
    # Origen diminuto: manda el margen mínimo de 1 GiB.
    ok, _ = co.espacio_suficiente("o", str(tmp_path), uso_disco=lambda p: U(gb // 2), tamano=lambda o: 10)
    assert not ok
    # Disco no medible: no bloquea.
    def roto(p):
        raise OSError("x")
    assert co.espacio_suficiente("o", str(tmp_path), uso_disco=roto)[0]


def test_lectura_ausente_es_cola_vacia(tmp_path, ruta):
    assert co.listar(ruta=ruta) == []


def test_oserror_transitorio_se_reintenta(tmp_path, ruta, monkeypatch):
    co.encolar(_p(tmp_path), ruta=ruta)
    from pathlib import Path
    real = Path.read_text
    n = {"i": 0}

    def flaky(self, *a, **k):
        if self.name == "cola_organizar.json" and n["i"] < 2:
            n["i"] += 1
            raise PermissionError("antivirus")
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", flaky)
    monkeypatch.setattr("atom_core.cola_subidas._LECTURA_ESPERA_S", 0)
    assert len(co.listar(ruta=ruta)) == 1 and n["i"] == 2


def test_oserror_persistente_lanza_y_no_escribe(tmp_path, ruta, monkeypatch):
    co.encolar(_p(tmp_path), ruta=ruta)
    antes = ruta.read_text(encoding="utf-8")
    from pathlib import Path

    def siempre(self, *a, **k):
        raise PermissionError("bloqueado")
    monkeypatch.setattr(Path, "read_text", siempre)
    monkeypatch.setattr("atom_core.cola_subidas._LECTURA_ESPERA_S", 0)
    with pytest.raises(co.ColaIlegible):
        co.encolar(_p(tmp_path, "vuelo2", "otro"), ruta=ruta)
    monkeypatch.undo()
    assert ruta.read_text(encoding="utf-8") == antes  # la cola buena sigue intacta


def test_json_corrupto_se_aparta_y_se_avisa(tmp_path, ruta, caplog):
    ruta.parent.mkdir(parents=True)
    ruta.write_text("[{\"id\": ", encoding="utf-8")
    co.tomar_avisos()
    assert co.listar(ruta=ruta) == []
    aparte = list(ruta.parent.glob("cola_organizar.json.corrupto-*"))
    assert len(aparte) == 1 and aparte[0].read_text(encoding="utf-8") == "[{\"id\": "
    assert not ruta.exists()
    avisos = co.tomar_avisos()
    assert avisos and "corrupta" in avisos[0] and co.tomar_avisos() == []
    assert co.encolar(_p(tmp_path), ruta=ruta)["ok"]


def test_escritura_hace_fsync_antes_del_replace(tmp_path, ruta, monkeypatch):
    orden = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: (orden.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(os, "replace", lambda a, b: (orden.append("replace"), real_replace(a, b))[1])
    co.encolar(_p(tmp_path), ruta=ruta)
    assert orden == ["fsync", "replace"]


def test_marcar_en_curso_solo_si_sigue_pendiente(tmp_path, ruta):
    j = co.encolar(_p(tmp_path), ruta=ruta)["job"]
    co.marcar(j["id"], co.CANCELADO, ruta=ruta)
    assert co.marcar(j["id"], co.EN_CURSO, solo_si=co.PENDIENTE, ruta=ruta) is None
    assert co.obtener(j["id"], ruta=ruta)["estado"] == co.CANCELADO


def test_subir_al_terminar_se_persiste_y_sale_de_params(tmp_path, ruta):
    r = co.encolar({**_p(tmp_path), "subir_al_terminar": True}, ruta=ruta)
    job = json.loads(ruta.read_text(encoding="utf-8"))[0]
    assert r["job"]["subir_al_terminar"] is True and job["subir_al_terminar"] is True
    assert "subir_al_terminar" not in job["params"]  # es del trabajo, no del pipeline
    assert job["subida"] is None
    assert co.subir_al_terminar(co.obtener(job["id"], ruta=ruta)) is True


def test_subir_al_terminar_ausente_o_no_true_es_false_explicito(tmp_path, ruta):
    co.encolar(_p(tmp_path, "a", "sa"), ruta=ruta)
    co.encolar({**_p(tmp_path, "b", "sb"), "subir_al_terminar": "si"}, ruta=ruta)
    assert [j["subir_al_terminar"] for j in co.listar(ruta=ruta)] == [False, False]


def test_trabajo_viejo_sin_el_campo_no_sube(tmp_path, ruta):
    ruta.parent.mkdir(parents=True)
    ruta.write_text(json.dumps([{"id": "v1", "params": _p(tmp_path), "estado": "hecho"}]), encoding="utf-8")
    assert co.subir_al_terminar(co.obtener("v1", ruta=ruta)) is False


def test_marcar_subida_y_reintentar_la_limpia(tmp_path, ruta):
    jid = co.encolar({**_p(tmp_path), "subir_al_terminar": True}, ruta=ruta)["job"]["id"]
    co.marcar(jid, co.HECHO, ruta=ruta)
    assert co.marcar_subida(jid, "encolada", "ok", ruta=ruta)["subida"] == {"estado": "encolada", "detalle": "ok"}
    with pytest.raises(ValueError):
        co.marcar_subida(jid, "rara", ruta=ruta)
    co.marcar(jid, co.ERROR, error="x", ruta=ruta)
    assert co.reintentar(jid, ruta=ruta)["job"]["subida"] is None
