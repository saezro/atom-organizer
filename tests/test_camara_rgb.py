"""Ajuste `camara_rgb` (Z|W): qué RGB se recorta cuando la tanda trae `_W` y `_Z`.

Cubre: modo Z (solo `_Z_CROP`), modo W (solo `_W_CROP`), tanda sin `_Z` (sin
cambios), validación del % por clave (`MODELO` / `MODELO_Z`), decimales (48.5),
CSV location en modo Z y coherencia del cierre. Mismos dobles que
`tests/test_indice_organizado.py` / `tests/test_cierre_meta_location_fechas.py`.
"""
import datetime as dt
import os

import pytest
from PIL import Image as PILImage

import pipeline as pipeline_real
from atom_core import apply, camara_rgb, cierre
from atom_core.indice import ErrorModeloSinRecorte, construir_indice
from atom_core.pares import clave_pareja
from external_tools import load_config_or_default
from tests.test_cierre_coherencia import _f
from tests.test_cierre_meta_location_fechas import _Cb, _cfg as _cfg_cierre, _csvs, _fotos, _montar
from tests.test_indice_organizado import (
    _ExifDePrueba, _PipelineDePrueba, _Signal, _cfg, _escribir_estadillo, _manifiesto,
)

TS = dt.datetime(2024, 6, 1, 10, 5, 0)


def _imagen_real(carpeta, nombre, tam=(400, 300)):
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, nombre)
    PILImage.new("RGB", tam, (120, 130, 140)).save(ruta, format="JPEG")
    return ruta


def _indice(tmp_path, nombres, pct, *, camara=None, modelo="H30T", auto=True):
    """Construye el índice sobre `nombres` (imágenes reales) y devuelve (filas por nombre, manifiesto)."""
    _escribir_estadillo(tmp_path / "estadillo.csv", [("1", "1", "2024:06:01", "10:00:00", "10:10:00")])
    ventanas = {("2024:06:01", "10:00:00", "10:10:00"): (dt.datetime(2024, 6, 1, 10, 0, 0),
                                                         dt.datetime(2024, 6, 1, 10, 10, 0))}
    extra = {} if camara is None else {"camara_rgb": camara}
    cfg = _cfg(tmp_path, cropping_rgb=True, cropping_mode_auto=auto, crop_percentage="50", **extra)
    rutas = [_imagen_real(cfg.input_folder, n) for n in nombres]
    pipeline = _PipelineDePrueba(ventanas, pct_por_modelo=pct)
    exif = _ExifDePrueba(timestamps={r: TS for r in rutas}, modelos={r: modelo for r in rutas})
    manifiesto = _manifiesto(tmp_path)
    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())
    return {os.path.basename(f["ruta_origen"]): dict(f) for f in manifiesto.todas()}, manifiesto, cfg


def _aplicar(filas, cfg):
    """Escribe original + `_CROP` de cada fila (apply real, un decode por imagen)."""
    for fila in filas.values():
        apply._escribir_salidas_de_fila(fila, cfg, pipeline_real)


def _salidas(filas):
    return sorted(os.path.basename(f["ruta_salida_original"]) for f in filas.values()), sorted(
        os.path.basename(f["ruta_salida_crop"]) for f in filas.values() if f["ruta_salida_crop"])


# ---- modo Z / W / sin _Z -------------------------------------------------------

def test_modo_z_crea_z_crop_y_no_w_crop(tmp_path):
    filas, m, cfg = _indice(tmp_path, ["DJI_0001_W.JPG", "DJI_0001_Z.JPG"], {"H30T_Z": 48.5}, camara="Z")
    assert filas["DJI_0001_W.JPG"]["ruta_salida_crop"] is None
    assert filas["DJI_0001_Z.JPG"]["ruta_salida_crop"].endswith("DJI_0001_Z_CROP.JPG")
    _aplicar(filas, cfg)
    originales, crops = _salidas(filas)
    assert crops == ["DJI_0001_Z_CROP.JPG"]
    assert all(os.path.exists(f["ruta_salida_original"]) for f in filas.values())  # la W se copia
    assert os.path.exists(filas["DJI_0001_Z.JPG"]["ruta_salida_crop"])
    assert not any(n.endswith("_W_CROP.JPG") for n in os.listdir(os.path.dirname(
        filas["DJI_0001_W.JPG"]["ruta_salida_original"])))
    m.cerrar()


def test_camara_por_defecto_es_z(tmp_path):
    filas, m, _ = _indice(tmp_path, ["DJI_0001_W.JPG", "DJI_0001_Z.JPG"], {"H30T_Z": 48.5})  # sin camara_rgb
    assert filas["DJI_0001_Z.JPG"]["ruta_salida_crop"] and filas["DJI_0001_W.JPG"]["ruta_salida_crop"] is None
    m.cerrar()


def test_modo_w_recorta_solo_la_w_y_copia_la_z(tmp_path):
    filas, m, cfg = _indice(tmp_path, ["DJI_0001_W.JPG", "DJI_0001_Z.JPG"], {"H30T": 70}, camara="W")
    assert filas["DJI_0001_W.JPG"]["ruta_salida_crop"].endswith("DJI_0001_W_CROP.JPG")
    assert filas["DJI_0001_Z.JPG"]["ruta_salida_crop"] is None
    _aplicar(filas, cfg)
    assert _salidas(filas)[1] == ["DJI_0001_W_CROP.JPG"]
    assert os.path.exists(filas["DJI_0001_Z.JPG"]["ruta_salida_original"])
    m.cerrar()


@pytest.mark.parametrize("camara", ["Z", "W"])
def test_tanda_sin_z_no_cambia(tmp_path, camara):
    """Sin `_Z` se recorta toda la RGB con la clave `MODELO`, sea cual sea la cámara."""
    filas, m, _ = _indice(tmp_path, ["DJI_0001_W.JPG", "DJI_0002_W.JPG"], {"H30T": 70}, camara=camara)
    assert all(f["ruta_salida_crop"] for f in filas.values())
    assert all(f["pct_recorte"] == 0.7 for f in filas.values())
    m.cerrar()


# ---- validación del % por clave ------------------------------------------------

def test_modo_z_con_h30t_z_no_exige_h30t_wide(tmp_path):
    filas, m, _ = _indice(tmp_path, ["DJI_0001_W.JPG", "DJI_0001_Z.JPG"], {"H30T_Z": 48.5}, camara="Z")
    assert len(filas) == 2
    m.cerrar()


def test_modo_z_sin_h30t_z_falla_y_nombra_la_clave(tmp_path):
    with pytest.raises(ErrorModeloSinRecorte) as exc:
        _indice(tmp_path, ["DJI_0001_W.JPG", "DJI_0001_Z.JPG"], {"H30T": 70}, camara="Z")
    assert "'H30T_Z'" in str(exc.value) and "DJI_0001_Z.JPG" in str(exc.value)
    assert "DJI_0001_W.JPG" not in str(exc.value)


def test_modo_w_sin_h30t_falla_y_nombra_la_clave(tmp_path):
    with pytest.raises(ErrorModeloSinRecorte) as exc:
        _indice(tmp_path, ["DJI_0001_W.JPG", "DJI_0001_Z.JPG"], {"H30T_Z": 48.5}, camara="W")
    assert "'H30T'" in str(exc.value) and "H30T_Z" not in str(exc.value)


def test_camara_invalida_es_error(tmp_path):
    with pytest.raises(ValueError):
        _indice(tmp_path, ["DJI_0001_W.JPG", "DJI_0001_Z.JPG"], {"H30T_Z": 48.5}, camara="X")


# ---- decimales -----------------------------------------------------------------

def test_decimal_48_5_en_config_y_en_el_recorte(tmp_path):
    ini = tmp_path / "Config.ini"
    ini.write_text("[percentage_by_models]\nH30T_Z = 48.5\nM3T = 71\nM30T = 6,5\n\n[cropping]\ncamara_rgb = w\n")
    cfg = load_config_or_default(str(ini))
    assert cfg["percentage_by_models"] == {"H30T_Z": 48.5, "M3T": 71, "M30T": 6.5}
    assert isinstance(cfg["percentage_by_models"]["M3T"], int)
    assert cfg["camara_rgb"] == "W"

    filas, m, cfg_i = _indice(tmp_path, ["DJI_0001_W.JPG", "DJI_0001_Z.JPG"],
                              {"H30T_Z": cfg["percentage_by_models"]["H30T_Z"]}, camara="Z")
    fila = filas["DJI_0001_Z.JPG"]
    assert fila["pct_recorte"] == pytest.approx(0.485)
    _aplicar(filas, cfg_i)
    with PILImage.open(fila["ruta_salida_crop"]) as im:
        # 400 * 0.485 = 194 (par), alto = 194 / 1.25 = 155.2 -> 155 -> 156 (par)
        assert im.size == (194, 156)
    m.cerrar()


def test_config_no_numerica_o_camara_invalida_falla(tmp_path):
    ini = tmp_path / "Config.ini"
    ini.write_text("[percentage_by_models]\nH30T_Z = abc\n")
    with pytest.raises(ValueError):
        load_config_or_default(str(ini))
    ini.write_text("[cropping]\ncamara_rgb = Q\n")
    with pytest.raises(ValueError):
        load_config_or_default(str(ini))


def test_config_empaquetado_sin_porcentajes_y_camara_z():
    ini = os.path.join(os.path.dirname(__file__), "..", "config", "Config.ini")
    cfg = load_config_or_default(ini)
    assert cfg["percentage_by_models"] == {}      # una sola fuente: la tabla
    assert cfg["camara_rgb"] == "Z"


# ---- CSV location y cierre -----------------------------------------------------

def _tanda_t_w_z(n=2):
    base = dt.datetime(2026, 1, 15, 10, 0, 0)
    imgs = []
    for i in range(1, n + 1):
        t = base + dt.timedelta(seconds=5 * i)
        imgs += [("TERMICA", f"DJI_{i:04d}_T.JPG", t), ("RGB", f"DJI_{i:04d}_W.JPG", t),
                 ("RGB", f"DJI_{i:04d}_Z.JPG", t)]
    return imgs


def test_csv_modo_z_lista_la_z(tmp_path, make_dji_jpeg):
    m = _montar(tmp_path, make_dji_jpeg, _tanda_t_w_z())
    cierre._emitir_meta_location(m, _cfg_cierre(tmp_path), _Cb())  # sin camara_rgb en cfg -> Z
    assert _fotos(tmp_path / "CSVs" / "PB1_V1_location.csv") == ["DJI_0001_Z.JPG", "DJI_0002_Z.JPG"]
    assert _fotos(tmp_path / "CSVs" / "PB1_V1_meta.csv") == ["DJI_0001_T.JPG", "DJI_0002_T.JPG"]
    m.cerrar()


def test_csv_modo_w_como_antes(tmp_path, make_dji_jpeg):
    m = _montar(tmp_path, make_dji_jpeg, _tanda_t_w_z())
    cfg = _cfg_cierre(tmp_path)
    cfg.camara_rgb = "W"
    cierre._emitir_meta_location(m, cfg, _Cb())
    assert _fotos(tmp_path / "CSVs" / "PB1_V1_location.csv") == ["DJI_0001_W.JPG", "DJI_0002_W.JPG"]
    m.cerrar()


def test_csv_sin_z_no_cambia_en_modo_z(tmp_path, make_dji_jpeg):
    imgs = [i for i in _tanda_t_w_z() if not i[1].endswith("_Z.JPG")]
    m = _montar(tmp_path, make_dji_jpeg, imgs)
    cierre._emitir_meta_location(m, _cfg_cierre(tmp_path), _Cb())
    assert _fotos(tmp_path / "CSVs" / "PB1_V1_location.csv") == ["DJI_0001_W.JPG", "DJI_0002_W.JPG"]
    m.cerrar()


def test_clave_pareja_z_solo_en_modo_z():
    assert clave_pareja("DJI_0001_Z.JPG") is None            # modo W / histórico: la _Z no tiene clave
    assert clave_pareja("DJI_0001_Z.JPG", "Z") == (1, "W")
    assert clave_pareja("DJI_0001_W.JPG", "Z") is None
    assert clave_pareja("DJI_0001_W.JPG") == (1, "W")
    assert clave_pareja("DJI_0001_T.JPG", "Z") == (1, "T")


def _fila(tipo, nombre, crop):
    f = _f(tipo, crop=crop)
    f["ruta_salida_original"] = f"/d/RGB/{nombre}"
    return f


def test_cierre_modo_z_compara_n_z_con_n_crop():
    filas = [_f("TERMICA", tiff=False)] * 0 + [
        _fila("RGB", "DJI_0001_W.JPG", False), _fila("RGB", "DJI_0001_Z.JPG", True),
        _fila("RGB", "DJI_0002_W.JPG", False), _fila("RGB", "DJI_0002_Z.JPG", True)]
    r = cierre.recuentos_por_vuelo(filas, None, cropping_rgb=True, camara_rgb="Z")[("1", "1")]
    assert r["estado"] == "OK" and r["n_W"] == 2 and r["n_crop"] == 2
    # Z sin recortar -> falso fallo evitado, pero un fallo real se detecta:
    filas[1] = _fila("RGB", "DJI_0001_Z.JPG", False)
    r = cierre.recuentos_por_vuelo(filas, None, cropping_rgb=True, camara_rgb="Z")[("1", "1")]
    assert r["estado"] == "ERROR" and "crop != Z" in r["motivo"]


def test_cierre_modo_w_no_espera_crop_en_la_z():
    filas = [_fila("RGB", "DJI_0001_W.JPG", True), _fila("RGB", "DJI_0001_Z.JPG", False)]
    assert cierre.recuentos_por_vuelo(filas, None, cropping_rgb=True, camara_rgb="W")[("1", "1")]["estado"] == "OK"


def test_cierre_sin_z_igual_que_siempre():
    filas = [_fila("RGB", "DJI_0001_W.JPG", True), _fila("RGB", "DJI_0002_W.JPG", False)]
    for cam in ("Z", "W", None):
        r = cierre.recuentos_por_vuelo(filas, None, cropping_rgb=True, camara_rgb=cam)[("1", "1")]
        assert r["estado"] == "ERROR" and "crop != W" in r["motivo"]


def test_se_recorta_reglas():
    z, w = "DJI_0001_Z.JPG", "DJI_0001_W.JPG"
    assert camara_rgb.se_recorta(z, "Z", True) and not camara_rgb.se_recorta(w, "Z", True)
    assert camara_rgb.se_recorta(w, "W", True) and not camara_rgb.se_recorta(z, "W", True)
    assert camara_rgb.se_recorta(w, "Z", False) and camara_rgb.se_recorta("DJI_0001_Z_point1.JPG", "Z", True)
    assert camara_rgb.clave_porcentaje("h30t\x00", "Z", True) == "H30T_Z"
    assert camara_rgb.clave_porcentaje("H30T", "W", True) == "H30T"
    assert camara_rgb.clave_porcentaje("H30T", "Z", False) == "H30T"


# ---- coherencia entre fases: camara_rgb en el manifiesto ----------------------

def test_indice_guarda_camara_y_las_fases_siguientes_la_leen(tmp_path):
    from dataclasses import replace
    m = _manifiesto(tmp_path)
    cfg_w = _cfg(tmp_path, camara_rgb="W")
    camara_rgb.fijar_camara_en_manifiesto(m, cfg_w, True)
    assert m.leer_ajuste("camara_rgb") == "W"
    # cierre sin arg explícito (Config.ini dice Z): manda lo guardado y avisa
    avisos = []
    cfg_z = _cfg(tmp_path, camara_rgb="Z")
    assert camara_rgb.fijar_camara_en_manifiesto(m, cfg_z, False, avisos.append).camara_rgb == "W"
    assert avisos and "manda sobre" in avisos[0]
    # arg explícito que no coincide -> error visible
    with pytest.raises(ValueError, match="no coincide"):
        camara_rgb.fijar_camara_en_manifiesto(m, replace(cfg_z, camara_rgb_explicita=True), False)
    # explícito y coincidente -> ok
    assert camara_rgb.fijar_camara_en_manifiesto(
        m, replace(cfg_w, camara_rgb_explicita=True), False).camara_rgb == "W"
    m.cerrar()


def test_manifiesto_sin_ajuste_avisa_y_usa_cfg(tmp_path):
    m = _manifiesto(tmp_path)
    avisos = []
    cfg = _cfg(tmp_path, camara_rgb="Z")
    assert camara_rgb.fijar_camara_en_manifiesto(m, cfg, False, avisos.append) is cfg
    assert avisos and "no guarda camara_rgb" in avisos[0]
    m.cerrar()


def test_construir_indice_via_params_marca_explicita(monkeypatch):
    from atom_core.organize import _default_split_config
    c = _default_split_config({"origen": "/i", "destino": "/o", "camara_rgb": "w"})
    assert (c.camara_rgb, c.camara_rgb_explicita) == ("W", True)
    c = _default_split_config({"origen": "/i", "destino": "/o"})
    assert c.camara_rgb_explicita is False


# ---- tabla versionada de recortes + override ----------------------------------

def test_tabla_empaquetada_valores_actuales():
    from atom_core import recortes
    t = recortes.cargar_tabla()
    assert {(m, c): d["porcentaje"] for (m, c), d in t.items()} == {
        ("MAVIC2-ENTERPRISE-ADVANCED", "W"): 70, ("M4T", "W"): 48, ("M30T", "W"): 65,
        ("M3T", "W"): 71, ("XT2", "W"): 50, ("H30T", "Z"): 48.5,
        ("H30T", "W"): 48.5}


def test_override_gana_a_la_tabla_y_se_sabe_el_origen(tmp_path):
    from atom_core import recortes
    pct, origen = recortes.porcentajes_efectivos({"M4T": 50, "H30T_Z": 47.5, "NUEVO": 60})
    assert pct["M4T"] == 50 and origen["M4T"] == "override"
    assert pct["H30T_Z"] == 47.5 and origen["H30T_Z"] == "override"
    assert pct["NUEVO"] == 60 and origen["NUEVO"] == "override"
    assert pct["M3T"] == 71 and origen["M3T"] == "tabla"
    filas = {(r["modelo"], r["camara"]): r for r in recortes.tabla_efectiva({"M4T": 50})}
    assert filas[("M4T", "W")]["origen"] == "override" and filas[("H30T", "Z")]["origen"] == "tabla"


def test_tabla_invalida_es_error(tmp_path):
    from atom_core import recortes
    malos = ["modelo,camara,porcentaje,nota\nA,X,50,\n", "modelo,camara,porcentaje,nota\nA,W,abc,\n",
             "modelo,camara,porcentaje,nota\nA,W,50,\nA,W,60,\n", "m,c,p\nA,W,50\n"]
    for contenido in malos:
        f = tmp_path / "t.csv"
        f.write_text(contenido)
        with pytest.raises(ValueError):
            recortes.cargar_tabla(str(f))
    with pytest.raises(FileNotFoundError):
        recortes.cargar_tabla(str(tmp_path / "no.csv"))


def test_read_load_config_une_tabla_y_override(tmp_path):
    from external_tools import ReadLoadConfig
    ini = tmp_path / "Config.ini"
    ini.write_text("[percentage_by_models]\nM4T = 52\n")
    c = ReadLoadConfig()
    c.load_new_config(str(ini))
    assert c.percentage_by_models["M4T"] == 52 and c.origen_porcentajes["M4T"] == "override"
    assert c.percentage_by_models["H30T_Z"] == 48.5 and c.origen_porcentajes["H30T_Z"] == "tabla"
    assert c.percentage_overrides == {"M4T": 52}


def test_indice_loguea_origen_y_lo_devuelve_en_el_resumen(tmp_path):
    from atom_core import recortes
    pct, origen = recortes.porcentajes_efectivos({"H30T_Z": 47})
    _escribir_estadillo(tmp_path / "estadillo.csv", [("1", "1", "2024:06:01", "10:00:00", "10:10:00")])
    ventanas = {("2024:06:01", "10:00:00", "10:10:00"): (dt.datetime(2024, 6, 1, 10, 0, 0),
                                                         dt.datetime(2024, 6, 1, 10, 10, 0))}
    cfg = _cfg(tmp_path, cropping_rgb=True, cropping_mode_auto=True, camara_rgb="Z")
    rutas = [_imagen_real(cfg.input_folder, n) for n in ("DJI_0001_W.JPG", "DJI_0001_Z.JPG")]
    pipeline = _PipelineDePrueba(ventanas, pct_por_modelo=pct)
    pipeline.origen_porcentajes = origen
    exif = _ExifDePrueba(timestamps={r: TS for r in rutas}, modelos={r: "H30T" for r in rutas})
    m, cb = _manifiesto(tmp_path), _Signal()
    res = construir_indice(cfg, pipeline, exif, m, cb, _Signal(), _Signal())
    assert res["recortes_usados"] == [{"clave": "H30T_Z", "porcentaje": 47, "origen": "override", "imagenes": 1}]
    assert any("Recorte H30T_Z = 47 % (origen: override" in str(x) for x in cb.mensajes)
    m.cerrar()


def test_falta_en_tabla_y_override_nombra_la_clave(tmp_path):
    from atom_core import recortes
    pct, _ = recortes.porcentajes_efectivos({})
    with pytest.raises(ErrorModeloSinRecorte, match="'DJI-NUEVO_Z'"):
        _indice(tmp_path, ["DJI_0001_W.JPG", "DJI_0001_Z.JPG"], pct, camara="Z", modelo="DJI-NUEVO")


# ---- migración de overrides heredados -----------------------------------------

def test_migracion_quita_defaults_sembrados_y_conserva_el_resto(tmp_path):
    import glob
    from external_tools import migrar_overrides_heredados
    ini = tmp_path / "Config.ini"
    ini.write_bytes(b"[paths]\r\nruta_thermoviewer = X\r\n\r\n[percentage_by_models]\r\n# mio\r\n"
                    b"MAVIC2-ENTERPRISE-ADVANCED = 70\r\nM4T = 48\r\nM30T = 60\r\nM3T = 71.0\r\nXT2 = 50\r\n"
                    b"H30T_Z = 47\r\n\r\n[cropping]\r\ncamara_rgb = W\r\n")
    quitadas = migrar_overrides_heredados(str(ini))
    assert sorted(quitadas) == ["M3T", "M4T", "MAVIC2-ENTERPRISE-ADVANCED", "XT2"]
    cfg = load_config_or_default(str(ini))
    assert cfg["percentage_by_models"] == {"M30T": 60, "H30T_Z": 47}   # distintos = override
    assert cfg["camara_rgb"] == "W" and cfg["ruta_thermoviewer"] == "X"
    assert b"# mio" in ini.read_bytes()
    assert len(glob.glob(str(ini) + ".bak-*")) == 1
    despues = ini.read_bytes()
    assert migrar_overrides_heredados(str(ini)) == []              # idempotente
    assert ini.read_bytes() == despues and len(glob.glob(str(ini) + ".bak-*")) == 1


def test_migracion_sin_cambios_no_hace_backup(tmp_path):
    import glob
    from external_tools import migrar_overrides_heredados
    ini = tmp_path / "Config.ini"
    ini.write_text("[percentage_by_models]\nM4T = 52\n")
    assert migrar_overrides_heredados(str(ini)) == []
    assert glob.glob(str(ini) + ".bak-*") == []


def test_user_config_path_migra_al_arrancar(tmp_path, monkeypatch):
    import external_tools
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(external_tools, "_CONFIGS_MIGRADOS", set())
    base = tmp_path / ("ATOM-Organizer" if external_tools.sys.platform.startswith("win")
                       else ".config/atom-organizer")
    base.mkdir(parents=True)
    (base / "Config.ini").write_text("[percentage_by_models]\nM4T = 48\nXT2 = 55\n")
    ruta = external_tools._user_config_path()
    assert load_config_or_default(ruta)["percentage_by_models"] == {"XT2": 55}


# ---- auditoría: zoom por tanda, FOTO_Z, pareja con Z ---------------------------

def test_foto_z_no_es_zoom_pero_el_contador_dji_si():
    assert not camara_rgb.es_zoom("FOTO_Z.jpg")
    assert not camara_rgb.es_zoom("IMG_Z.JPG")
    assert camara_rgb.es_zoom("DJI_20260101100000_0001_Z.JPG")
    assert camara_rgb.es_zoom("DJI_0001_Z_point2.JPG")
    # una tanda con FOTO_Z no activa el modo zoom: se recorta toda la RGB como siempre
    assert camara_rgb.se_recorta("FOTO_W.jpg", "Z", camara_rgb.tanda_trae_zoom(["FOTO_Z.jpg", "FOTO_W.jpg"]))


def _fila_v(vuelo, tipo, nombre, crop):
    f = _f(tipo, vuelo=vuelo, crop=crop)
    f["ruta_salida_original"] = f"/d/{tipo}/{nombre}"
    return f


def _tanda_mixta():
    """Vuelo 1 con W+Z (crop solo en Z); vuelo 2 SOLO W sin crop (modo Z de la tanda)."""
    return [_fila_v("1", "RGB", "DJI_0001_W.JPG", False), _fila_v("1", "RGB", "DJI_0001_Z.JPG", True),
            _fila_v("2", "RGB", "DJI_0001_W.JPG", False), _fila_v("2", "RGB", "DJI_0002_W.JPG", False)]


def test_cierre_tanda_mixta_sin_falsos_fallos():
    rec = cierre.recuentos_por_vuelo(_tanda_mixta(), None, cropping_rgb=True, camara_rgb="Z")
    assert rec[("1", "1")]["estado"] == "OK"
    assert rec[("1", "2")]["estado"] == "OK"          # vuelo sin Z: la tanda manda, no se espera crop
    # hay_zoom explícito (el que guardó el índice) manda sobre la deducción
    rec = cierre.recuentos_por_vuelo(_tanda_mixta(), None, cropping_rgb=True, camara_rgb="Z", hay_zoom=False)
    assert rec[("1", "2")]["estado"] == "ERROR"


def test_cierre_lee_hay_zoom_guardado_en_el_manifiesto(tmp_path):
    m = _manifiesto(tmp_path)
    assert cierre._hay_zoom_tanda(m, []) is False
    m.guardar_ajuste(camara_rgb.AJUSTE_HAY_ZOOM, "1")
    assert cierre._hay_zoom_tanda(m, []) is True
    m.cerrar()


def test_indice_guarda_hay_zoom_de_la_tanda(tmp_path):
    _, m, _ = _indice(tmp_path, ["DJI_0001_W.JPG", "DJI_0001_Z.JPG"], {"H30T_Z": 48.5}, camara="Z")
    assert m.leer_ajuste(camara_rgb.AJUSTE_HAY_ZOOM) == "1"
    m.cerrar()
    (tmp_path / "otra").mkdir()
    filas, m2, _ = _indice(tmp_path / "otra", ["DJI_0001_W.JPG"], {"H30T": 70}, camara="Z")
    assert m2.leer_ajuste(camara_rgb.AJUSTE_HAY_ZOOM) == "0"
    m2.cerrar()


def _md(nombre, ts):
    from atom_core.indice import _MetadatosImagen
    return _MetadatosImagen(ruta=f"/sd/100MEDIA/{nombre}", nombre=nombre, timestamp=ts,
                            modelo=None, yaw=None, gps=None)


def test_z_hereda_el_vuelo_de_su_t_en_el_borde_en_modo_z():
    from atom_core.indice import _heredar_vuelo_por_pareja
    v1 = {"pb": "1", "vuelo": "1"}
    t = _md("DJI_20260301100459_0007_T.JPG", dt.datetime(2026, 3, 1, 10, 4, 59))
    w = _md("DJI_20260301100500_0007_W.JPG", dt.datetime(2026, 3, 1, 10, 5, 0))
    z = _md("DJI_20260301100500_0007_Z.JPG", dt.datetime(2026, 3, 1, 10, 5, 0))
    res = _heredar_vuelo_por_pareja([(t, v1), (w, None), (z, None)], None, "Z")
    assert res[1][1] is v1 and res[2][1] is v1          # la W conserva su herencia y la Z la gana
    # modo W / histórico: la Z no tiene clave y no hereda (todo igual que antes)
    res = _heredar_vuelo_por_pareja([(t, v1), (w, None), (z, None)])
    assert res[1][1] is v1 and res[2][1] is None


def test_paridad_cuenta_la_z_en_modo_z_y_sin_z_es_identica():
    from atom_core.pares import paridad_tw
    rutas = ["DJI_0001_T.JPG", "DJI_0001_W.JPG", "DJI_0001_Z.JPG", "DJI_0002_T.JPG", "DJI_0002_W.JPG"]
    assert paridad_tw(rutas) == ([], [])                       # histórico: Z ignorada
    assert paridad_tw(rutas, "Z") == (["0002"], [])            # Z: falta la Z del idx 2
    sin_z = ["DJI_0001_T.JPG", "DJI_0001_W.JPG", "DJI_0002_T.JPG"]
    assert paridad_tw(sin_z) == paridad_tw(sin_z, "W") == (["0002"], [])


def test_tanda_sin_z_identica_en_modo_z_y_w(tmp_path):
    from atom_core.indice import _heredar_vuelo_por_pareja, _rgb_pareja
    assert _rgb_pareja(_cfg(tmp_path, camara_rgb="Z"), False) == "W"   # sin Z nunca cambia la pareja
    v1 = {"pb": "1", "vuelo": "1"}
    t = _md("DJI_20260301100459_0007_T.JPG", dt.datetime(2026, 3, 1, 10, 4, 59))
    w = _md("DJI_20260301100500_0007_W.JPG", dt.datetime(2026, 3, 1, 10, 5, 0))
    a = _heredar_vuelo_por_pareja([(t, v1), (w, None)], None, "W")
    assert a == _heredar_vuelo_por_pareja([(t, v1), (w, None)]) and a[1][1] is v1


def test_webserver_expone_sin_colocar_y_config_de_recortes():
    from atom_core.webserver import METODOS_EXPUESTOS, METODOS_REMOTOS
    assert {"sin_colocar_listar", "sin_colocar_asignar", "read_config", "write_config"} <= METODOS_EXPUESTOS
    assert not {"sin_colocar_listar", "sin_colocar_asignar", "write_config"} & METODOS_REMOTOS  # solo local
