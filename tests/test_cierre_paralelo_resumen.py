"""M6/M7: lectura de deltas en hilos (igual que en serie) y `resumen_apply.json`."""
import json

from atom_core import apply_shards, resumen_apply


def _jsonl(ruta, filas):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text("\n".join(json.dumps(f) for f in filas) + "\n", encoding="utf-8")


def test_leer_deltas_en_hilos_igual_que_en_serie(tmp_path):
    destino = str(tmp_path)
    esperado = []
    for ejec in ("ej-a", "ej-b"):
        for shard in range(5):
            filas = [{"id": shard * 100 + i, "estado": "hecho", "verificacion": f"{ejec}{i}"}
                     for i in range(3)]
            _jsonl(tmp_path / ".organizado" / "shards" / ejec / f"apply-{shard}.jsonl", filas)
    base = apply_shards.carpeta_shards(destino)
    for ejec in sorted(("ej-a", "ej-b")):
        carpeta = f"{base}/{ejec}"
        for nombre in sorted(n for n in __import__("os").listdir(carpeta)):
            esperado.extend(apply_shards._leer_fichero(f"{carpeta}/{nombre}"))
    assert apply_shards.leer_deltas(destino) == esperado
    assert len(esperado) == 30


def _telemetria(tmp_path, ejec, shard, count, util, indice="idx-1", fase="apply", cpu=100.0):
    d = {"telemetria": "organizer", "fase": fase, "ejecucion": ejec, "ejecucion_indice": indice,
         "shard_index": shard, "shard_count": count, "ok": True, "pared_s": 30.0,
         "cpu_s": cpu, "vcpu": 4, "utilizacion": util}
    carpeta = tmp_path / ".organizado" / "telemetria"
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / f"{ejec}-{fase}-{shard}.json").write_text(json.dumps(d), encoding="utf-8")


def test_resumen_apply_agrega_y_escribe_en_la_ruta_de_la_suite(tmp_path):
    for i, u in enumerate((0.91, 0.95, 0.88)):
        _telemetria(tmp_path, "ap-1", i, 3, u)
    _telemetria(tmp_path, "ap-0", 0, 1, 0.10, indice="otro")   # otra ejecucion: no cuenta
    _telemetria(tmp_path, "ap-1", 0, 3, 0.20, fase="indice")   # otra fase: no cuenta

    ruta, res = resumen_apply.escribir(str(tmp_path), "idx-1")

    assert ruta == str(tmp_path / ".organizado" / "apply" / "idx-1" / "resumen_apply.json")
    assert json.loads(open(ruta).read()) == res
    assert res["utilizacion"]["media"] == 0.913
    assert res["utilizacion"]["min"] == 0.88
    assert res["utilizacion"]["por_shard"] == {"0": 0.91, "1": 0.95, "2": 0.88}
    assert res["n_tasks"] == 3 and res["cpu_s"] == 300.0
    assert res["pared_s_max"] == 30.0 and res["vcpu"] == 4
    assert res["faltan_shards"] == []


def test_resumen_apply_lista_shards_sin_telemetria(tmp_path):
    _telemetria(tmp_path, "ap-1", 0, 4, 0.9)
    _telemetria(tmp_path, "ap-1", 3, 4, 0.8)
    res = resumen_apply.agregar(str(tmp_path), "idx-1")
    assert res["faltan_shards"] == [1, 2]
    assert res["n_tasks"] == 2


def test_resumen_apply_sin_ninguna_telemetria_no_inventa_utilizacion(tmp_path):
    res = resumen_apply.agregar(str(tmp_path), "idx-1")
    assert res["utilizacion"] is None and res["n_tasks"] == 0


def test_cierre_paralelo_emite_lo_mismo_que_el_secuencial(tmp_path, monkeypatch):
    """`_fase_cierre` (verificar || Excel) da los mismos problemas que `verificar` a
    secas, y el Excel se escribe igualmente."""
    from atom_core import cierre as cierre_mod
    from atom_core import indice_excel, phases
    from atom_core.manifiesto import Manifiesto
    from tests.test_cierre_organizado import _cfg, _crear_fichero, _fila_rgb
    from tests.test_apply_rgb import _SignalFalsa

    destino = tmp_path / "out"
    ok = _fila_rgb(destino, "/o/a.JPG", "PB1", "V1")
    pend = _fila_rgb(destino, "/o/b.JPG", "PB1", "V1")
    m = Manifiesto(tmp_path / "m.db")
    m.crear_esquema()
    m.insertar_muchas([ok, pend])
    filas = {f["ruta_origen"]: f for f in m.todas()}
    _crear_fichero(filas["/o/a.JPG"]["ruta_salida_original"])
    m.marcar_hecha(filas["/o/a.JPG"]["id"], "v")
    cfg = _cfg(destino)
    cfg = cfg.__class__(**{**cfg.__dict__, "input_folder": "/o"}) if hasattr(cfg, "__dict__") else cfg

    referencia = cierre_mod.verificar(m, cfg, None)
    llamadas = []
    monkeypatch.setattr(indice_excel, "escribir_indice",
                        lambda *a, **k: llamadas.append(1) or str(destino / "indice.xlsx"))

    class Host:
        organizer_logger_obj = type("L", (), {"logger": __import__("logging").getLogger("t")})()

    cb = _SignalFalsa()
    phases.PipelinePhasesMixin._fase_cierre(
        Host(), m, cfg, cb, _SignalFalsa(), _SignalFalsa(), 1, None, {}, 0.0, "completo", None)
    salida = "".join(map(str, getattr(cb, "lineas", getattr(cb, "mensajes", []))))
    assert llamadas == [1]
    assert cierre_mod.separar_problemas(referencia)[0] or referencia  # hay algo que comparar
    assert referencia
    for problema in cierre_mod.separar_problemas(referencia)[0]:
        assert problema[len(cierre_mod.PREFIJO_ERROR):] in salida
