"""Guard por `esperado_shardN.json` (etapas Cloud Run, sin manifiesto.db)."""
import json
import logging
from types import SimpleNamespace

import pytest

from atom_core import esperado
from atom_core.manifiesto import NOMBRE_CARPETA_MANIFIESTO
from atom_core.phases import PipelinePhasesMixin
from tests.test_guard_termicas_sin_girar import _Cb, _fake_phases, _fila_t, _manifiesto


def _raiz(tmp_path):
    raiz = tmp_path / "destino"
    (raiz / "TERMICA").mkdir(parents=True)
    (raiz / "RGB").mkdir()
    return raiz


def _cfg(raiz):
    return SimpleNamespace(output_folder=str(raiz))


def test_esperado_se_escribe_y_se_lee_con_rutas_relativas(tmp_path):
    raiz = _raiz(tmp_path)
    r = str(raiz)
    esperado.escribir_esperado(r, 3, [(f"{r}/TERMICA/a.JPG", f"{r}/PB1/PB1_V1/a.JPG")])
    esperado.escribir_esperado(r, 0, [])
    fotos = esperado.leer_esperados(r)
    assert fotos == [{"origen": "TERMICA/a.JPG", "destino": "PB1/PB1_V1/a.JPG"}]
    assert json.loads((raiz / NOMBRE_CARPETA_MANIFIESTO / "esperado_shard3.json").read_text())["shard"] == 3


def test_foto_esperada_que_sigue_en_raiz_es_error_con_numero_y_ejemplos(tmp_path):
    raiz = _raiz(tmp_path)
    r = str(raiz)
    (raiz / "TERMICA" / "a.JPG").write_bytes(b"x")  # nunca se movio
    (raiz / "PB1" / "PB1_V1").mkdir(parents=True)
    (raiz / "PB1" / "PB1_V1" / "b.JPG").write_bytes(b"x")  # colocada bien
    esperado.escribir_esperado(r, 0, [(f"{r}/TERMICA/a.JPG", f"{r}/PB1/PB1_V1/a.JPG"),
                                      (f"{r}/TERMICA/b.JPG", f"{r}/PB1/PB1_V1/b.JPG")])
    fake, _ = _fake_phases(tmp_path)
    cb = _Cb()
    with pytest.raises(ValueError, match=r"1 de 2.*TERMICA/a\.JPG"):
        fake._exigir_sin_vuelo_asignado_en_raiz(_cfg(raiz), cb)
    assert any(str(x).startswith("ERROR: ") for x in cb.mensajes)


def test_foto_esperada_que_desaparece_sin_llegar_a_destino_es_error(tmp_path):
    raiz = _raiz(tmp_path)
    r = str(raiz)
    esperado.escribir_esperado(r, 1, [(f"{r}/RGB/c.JPG", f"{r}/PB1/PB1_V1/c.JPG")])
    fake, _ = _fake_phases(tmp_path)
    with pytest.raises(ValueError, match="no aparece en destino"):
        fake._exigir_sin_vuelo_asignado_en_raiz(_cfg(raiz), _Cb())


def test_todo_colocado_pasa(tmp_path):
    raiz = _raiz(tmp_path)
    r = str(raiz)
    (raiz / "PB1" / "PB1_V1").mkdir(parents=True)
    (raiz / "PB1" / "PB1_V1" / "a.JPG").write_bytes(b"x")
    (raiz / "TERMICA" / "suelta.JPG").write_bytes(b"x")  # sin vuelo: legitima
    esperado.escribir_esperado(r, 0, [(f"{r}/TERMICA/a.JPG", f"{r}/PB1/PB1_V1/a.JPG")])
    fake, _ = _fake_phases(tmp_path)
    fake._exigir_sin_vuelo_asignado_en_raiz(_cfg(raiz), _Cb())


def test_sin_manifiesto_ni_esperado_lanza_error_explicito(tmp_path, caplog):
    raiz = _raiz(tmp_path)
    fake, _ = _fake_phases(tmp_path)
    cb = _Cb()
    with caplog.at_level(logging.ERROR, logger="t"), \
            pytest.raises(FileNotFoundError, match="NO verificable"):
        fake._exigir_sin_vuelo_asignado_en_raiz(_cfg(raiz), cb)
    assert any("NO verificable" in r.message for r in caplog.records)
    assert any(str(x).startswith("ERROR: ") for x in cb.mensajes)


def test_manifiesto_cruza_por_ruta_no_por_basename(tmp_path):
    # Misma basename en RGB: la fila es TERMICA, no debe acusar al RGB homonimo.
    raiz = _raiz(tmp_path)
    (raiz / "RGB" / "DJI_0001_T.JPG").write_bytes(b"x")
    _manifiesto(raiz / NOMBRE_CARPETA_MANIFIESTO / "manifiesto.db",
                [_fila_t(raiz, "/origen/DJI_0001_T.JPG")]).cerrar()
    fake, _ = _fake_phases(tmp_path)
    fake._exigir_sin_vuelo_asignado_en_raiz(_cfg(raiz), _Cb())


def test_fila_fallida_tiene_mensaje_propio(tmp_path):
    raiz = _raiz(tmp_path)
    (raiz / "TERMICA" / "DJI_0001_T.JPG").write_bytes(b"x")
    m = _manifiesto(raiz / NOMBRE_CARPETA_MANIFIESTO / "manifiesto.db",
                    [_fila_t(raiz, "/origen/DJI_0001_T.JPG")])
    m.marcar_fallida(m.todas()[0]["id"], "boom")
    m.cerrar()
    fake, _ = _fake_phases(tmp_path)
    with pytest.raises(ValueError) as exc:
        fake._exigir_sin_vuelo_asignado_en_raiz(_cfg(raiz), _Cb())
    assert "FALLIDA" in str(exc.value) and "su fase no las procesó" not in str(exc.value)


def test_limpiar_sobrantes_borra_solo_indices_fuera_del_reparto_actual(tmp_path):
    raiz = _raiz(tmp_path)
    r = str(raiz)
    for i in range(4):  # corrida previa con 4 shards
        esperado.escribir_esperado(r, i, [(f"{r}/TERMICA/f{i}.JPG", f"{r}/PB1/PB1_V1/f{i}.JPG")])
    borrados = esperado.limpiar_sobrantes(r, 2)  # corrida actual: 2 shards
    assert sorted(borrados) == ["esperado_shard2.json", "esperado_shard3.json"]
    carpeta = raiz / NOMBRE_CARPETA_MANIFIESTO
    assert sorted(p.name for p in carpeta.glob("esperado_shard*.json")) == [
        "esperado_shard0.json", "esperado_shard1.json"]
    assert [f["origen"] for f in esperado.leer_esperados(r)] == ["TERMICA/f0.JPG", "TERMICA/f1.JPG"]
    assert esperado.limpiar_sobrantes(r, 2) == []  # idempotente (shards concurrentes)


def test_limpiar_sobrantes_sin_carpeta_no_falla(tmp_path):
    assert esperado.limpiar_sobrantes(str(tmp_path / "vacio"), 1) == []


def test_borrar_propio_solo_borra_su_indice(tmp_path):
    raiz = _raiz(tmp_path)
    r = str(raiz)
    for i in range(3):
        esperado.escribir_esperado(r, i, [(f"{r}/TERMICA/f{i}.JPG", f"{r}/PB1/PB1_V1/f{i}.JPG")])
    assert esperado.borrar_propio(r, 1) is True
    assert esperado.borrar_propio(r, 1) is False
    carpeta = raiz / NOMBRE_CARPETA_MANIFIESTO
    assert sorted(p.name for p in carpeta.glob("esperado_shard*.json")) == [
        "esperado_shard0.json", "esperado_shard2.json"]


def test_borrar_propio_sin_carpeta_no_falla(tmp_path):
    assert esperado.borrar_propio(str(tmp_path / "vacio"), 0) is False


def test_gen_folder_struct_borra_su_esperado_previo_antes_de_trabajar(tmp_path):
    from tests.test_estadillo_multiple import FakeSignal, _csv, _make_gsf
    raiz = tmp_path / "destino"
    (raiz / "RGB").mkdir(parents=True)
    (raiz / "TERMICA").mkdir()
    r = str(raiz)
    esperado.escribir_esperado(r, 0, [(f"{r}/TERMICA/viejo.JPG", f"{r}/PB9/PB9_V9/viejo.JPG")])
    esperado.escribir_esperado(r, 1, [(f"{r}/TERMICA/otro.JPG", f"{r}/PB9/PB9_V9/otro.JPG")])
    gsf = _make_gsf(tmp_path)

    def _revienta(*a, **k):
        raise RuntimeError("muere antes de escribir")
    gsf.utils_obj.prepare_output_folder = _revienta
    est = _csv(tmp_path / "e.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    cb = FakeSignal()
    with pytest.raises(RuntimeError):
        gsf.gen_folder_struct(est, r, r, True, 0, 0, 0, cb, cb, shard_index=0, shard_count=2)
    carpeta = raiz / NOMBRE_CARPETA_MANIFIESTO
    assert sorted(p.name for p in carpeta.glob("esperado_shard*.json")) == ["esperado_shard1.json"]


def test_faltantes_gcs_lista_una_vez_por_carpeta_y_da_el_mismo_resultado():
    from atom_core import almacen as almacen_mod
    from tests.test_struct_almacen import _sembrar_almacen_gcs
    bucket = _sembrar_almacen_gcs("b-esperado")
    try:
        bucket.objetos["raiz/PB1/PB1_V1/b.JPG"] = b"x"      # colocada bien
        bucket.objetos["raiz/TERMICA/a.JPG"] = b"x"         # sigue en la raíz
        bucket.objetos["raiz/PB1/PB1_V1/a.JPG"] = b"x"
        bucket.objetos["raiz/TERMICA/PB10/z.JPG"] = b"x"    # no debe confundirse
        fotos = [{"origen": "TERMICA/a.JPG", "destino": "PB1/PB1_V1/a.JPG"},
                 {"origen": "TERMICA/b.JPG", "destino": "PB1/PB1_V1/b.JPG"},
                 {"origen": "RGB/c.JPG", "destino": "PB1/PB1_V1/c.JPG"}]
        listados = []
        original = bucket.list_blobs
        bucket.list_blobs = lambda prefix="": (listados.append(prefix), original(prefix=prefix))[1]
        malas = esperado.faltantes("gs://b-esperado/raiz", fotos)
        assert malas == [
            "TERMICA/a.JPG -> PB1/PB1_V1/a.JPG (sigue en la raíz)",
            "RGB/c.JPG -> PB1/PB1_V1/c.JPG (no aparece en destino)"]
        assert sorted(listados) == ["raiz/PB1/PB1_V1", "raiz/RGB", "raiz/TERMICA"]
    finally:
        almacen_mod._limpiar_cache_almacenes()
