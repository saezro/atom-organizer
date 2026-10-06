"""Sin estadillo, `run_task("split_images", ...)` debe pintar el checklist de
fases (`plan`) ANTES de cortar, y marcar la fase "Índice" como la que falla
(`phase` con index 1) en vez de dejar solo un banner de `error` suelto.
Ningún método real del pipeline debe llegar a arrancar (ver comentario del
gate en `atom_core.organize.run_task`)."""
from atom_core import organize


def _emisor():
    eventos = []

    def emit(kind, payload=None):
        eventos.append((kind, payload))
    return eventos, emit


def _de_tipo(eventos, kind):
    return [p for k, p in eventos if k == kind]


def test_sin_estadillo_pinta_plan_y_falla_en_fase_indice(monkeypatch, tmp_path):
    origen = tmp_path / "fotos"
    origen.mkdir()
    destino = tmp_path / "salida"
    destino.mkdir()

    class _HostNuncaLlamado:
        def __getattr__(self, name):
            raise AssertionError(
                f"el pipeline real no debe arrancar sin estadillo (se pidió {name!r})")

    monkeypatch.setattr(organize, "HeadlessHost", _HostNuncaLlamado)

    eventos, emit = _emisor()
    organize.run_task(
        "split_images",
        {"origen": str(origen), "destino": str(destino)},
        emit,
    )

    tipos = [k for k, _ in eventos]
    assert "plan" in tipos, f"el checklist de fases debe pintarse, eventos={tipos}"
    assert "error" in tipos
    assert tipos.index("plan") < tipos.index("error"), (
        "el 'plan' tiene que emitirse ANTES del 'error', para que el modal "
        f"no se quede en un banner suelto sin checklist. eventos={tipos}")

    fases = _de_tipo(eventos, "phase")
    assert fases, "sin estadillo debe emitirse un 'phase' que marque dónde corta"
    assert fases[0]["index"] == 1
    assert fases[0]["name"] == "Índice", (
        "el corte debe marcar la fase 'Índice' (donde el modal enseña los "
        f"datos del estadillo), no otra: {fases[0]}")

    errores = _de_tipo(eventos, "error")
    assert any("estadillo" in str(e).lower() for e in errores)

    assert not _de_tipo(eventos, "done"), "sin estadillo el run no debe llegar a 'done'"


def _csv_valido(path):
    path.write_text(
        "PB;Vuelo;Fecha;Hora_de_inicio;Hora_final\n1;1;2026:03:17;10:00:00;10:05:00\n",
        encoding="utf-8")
    return str(path)


class _HostCorta:
    def __getattr__(self, name):
        raise RuntimeError("fin del test: el gate de estadillo ya pasó")


def test_error_sin_estadillo_incluye_ruta_buscada_y_descartados(monkeypatch, tmp_path):
    origen = tmp_path / "fotos"
    origen.mkdir()
    (origen / "notas.csv").write_text("Columna_A;Columna_B\nfoo;bar\n", encoding="utf-8")
    destino = tmp_path / "salida"
    destino.mkdir()
    monkeypatch.setattr(organize, "HeadlessHost", _HostCorta)

    eventos, emit = _emisor()
    organize.run_task("split_images", {"origen": str(origen), "destino": str(destino)}, emit)

    msg = " ".join(str(e) for e in _de_tipo(eventos, "error"))
    assert str(origen) in msg
    assert "Descartados" in msg and "notas.csv" in msg


def test_error_origen_inexistente_lo_dice(monkeypatch, tmp_path):
    destino = tmp_path / "salida"
    destino.mkdir()
    monkeypatch.setattr(organize, "HeadlessHost", _HostCorta)

    eventos, emit = _emisor()
    organize.run_task(
        "split_images", {"origen": str(tmp_path / "montaje_caido"), "destino": str(destino)}, emit)

    msg = " ".join(str(e) for e in _de_tipo(eventos, "error"))
    assert "no existe" in msg


def test_solo_fallidas_reutiliza_estadillo_del_destino(monkeypatch, tmp_path):
    origen = tmp_path / "fotos"
    origen.mkdir()
    destino = tmp_path / "salida"
    (destino / "ESTADILLOS").mkdir(parents=True)
    est = _csv_valido(destino / "ESTADILLOS" / "2026_10_04_estadillo.csv")
    monkeypatch.setattr(organize, "HeadlessHost", _HostCorta)
    monkeypatch.setattr(organize, "fallidas_reintentables",
                        lambda *_a, **_k: {"mismo_origen": True, "fallidas": 3})

    eventos, emit = _emisor()
    organize.run_task(
        "split_images",
        {"origen": str(origen), "destino": str(destino), "solo_fallidas": True}, emit)

    logs = " ".join(str(e) for e in _de_tipo(eventos, "log"))
    assert "[estadillo] reutilizo el de" in logs and est in logs
    assert not any("No hay estadillo" in str(e) for e in _de_tipo(eventos, "error"))


def test_solo_fallidas_sin_estadillo_en_ningun_sitio_cita_ambas_rutas(monkeypatch, tmp_path):
    origen = tmp_path / "fotos"
    origen.mkdir()
    destino = tmp_path / "salida"
    destino.mkdir()
    monkeypatch.setattr(organize, "HeadlessHost", _HostCorta)
    monkeypatch.setattr(organize, "fallidas_reintentables",
                        lambda *_a, **_k: {"mismo_origen": True, "fallidas": 3})

    eventos, emit = _emisor()
    organize.run_task(
        "split_images",
        {"origen": str(origen), "destino": str(destino), "solo_fallidas": True}, emit)

    msg = " ".join(str(e) for e in _de_tipo(eventos, "error"))
    assert "No hay estadillo" in msg and "ESTADILLOS" in msg
