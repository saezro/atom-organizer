"""Pipeline unificado de `apply` (`atom_core.apply_pipeline`): RGB + térmica
solapadas en una task, destino nunca sobrescrito, `_T.JPG` por rewrite server-side.

Todo contra el bucket falso en memoria de `test_giro_manifiesto_gcs`; el RGB corre
en un ejecutor de hilos (el de procesos `spawn` es el de producción y no cambia la
lógica que se sujeta aquí).
"""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from atom_core import almacen as almacen_mod
from atom_core import apply, apply_pipeline
from atom_core.manifiesto import Manifiesto
from tests.test_apply_cierre_gcs import (  # noqa: F401 (fixtures)
    RAIZ, _clave, _poner, _tmp_del_fixture_bucket,
)
from tests.test_apply_rgb import _SignalFalsa, _cfg, _fila_manifiesto, _pipeline_doble
from tests.test_apply_termicas import (
    _PipelineDePrueba, _PoolExiftoolFalso, _fila_termica,
)
from tests.test_giro_manifiesto_gcs import bucket  # noqa: F401 (fixture)


@pytest.fixture(autouse=True)
def _exiftool_falso(monkeypatch):
    monkeypatch.setattr(apply, "_PoolExiftool", _PoolExiftoolFalso)


def _manifiesto(tmp_path, filas, nombre="m.db"):
    m = Manifiesto(tmp_path / nombre)
    m.crear_esquema()
    m.insertar_muchas(filas)
    return m


def _escenario(bucket, make_dji_jpeg, tmp_path, destino):
    """2 RGB (una con crop y giro) + 2 térmicas, intercaladas, origen gs://."""
    local = tmp_path / "base.JPG"
    make_dji_jpeg(str(local))
    datos = local.read_bytes()
    filas = []
    for i in (1, 2):
        t_origen = f"{RAIZ}/SD/DJI_000{i}_T.JPG"
        r_origen = f"{RAIZ}/SD/DJI_000{i}_W.JPG"
        _poner(bucket, t_origen, datos + bytes([i]))
        _poner(bucket, r_origen, datos)
        filas.append(_fila_termica(
            t_origen, f"{destino}/T/DJI_000{i}_T.JPG", f"{destino}/T/DJI_000{i}_T.tiff",
            angulo_giro=90 if i == 1 else 0))
        filas.append(_fila_manifiesto(
            r_origen, f"{destino}/R/DJI_000{i}_W.JPG",
            f"{destino}/R/DJI_000{i}_W_CROP.JPG" if i == 1 else None,
            angulo_giro=90 if i == 1 else 0, pct_recorte=0.8 if i == 1 else None))
    return filas


def _correr(m, **kw):
    kw.setdefault("plazas", 2)
    kw.setdefault("fabrica_ejecutor_rgb", lambda n: ThreadPoolExecutor(n))
    return apply_pipeline.aplicar_pipeline(
        m, _cfg(), _PipelineDePrueba(), _SignalFalsa(), _SignalFalsa(), _SignalFalsa(), **kw)


def _objetos(bucket, prefijo):
    return {k[len(_clave(prefijo)):]: v for k, v in bucket.objetos.items()
            if k.startswith(_clave(prefijo))}


def test_destino_existente_es_choque_y_no_se_sobrescribe(bucket, tmp_path, make_dji_jpeg):
    destino = RAIZ + "/OUT"
    filas = _escenario(bucket, make_dji_jpeg, tmp_path, destino)
    ocupado = f"{destino}/R/DJI_0002_W.JPG"
    _poner(bucket, ocupado, b"contenido ajeno")
    ocupado_tiff = f"{destino}/T/DJI_0002_T.tiff"
    _poner(bucket, ocupado_tiff, b"tiff ajeno")
    m = _manifiesto(tmp_path, filas)

    r = _correr(m)

    assert r["hecho"] == 2 and r["fallido"] == 2
    assert bucket.objetos[_clave(ocupado)] == b"contenido ajeno"
    assert bucket.objetos[_clave(ocupado_tiff)] == b"tiff ajeno"
    fallidas = [f for f in m.todas() if f["estado"] == "fallido"]
    assert len(fallidas) == 2 and all("CHOQUE" in f["motivo_fallo"] for f in fallidas)
    # lo que no chocó se escribió
    assert _clave(f"{destino}/R/DJI_0001_W.JPG") in bucket.objetos
    assert _clave(f"{destino}/T/DJI_0001_T.tiff") in bucket.objetos
    m.cerrar()


def test_reintento_idempotente_mismo_contenido(bucket, tmp_path, make_dji_jpeg):
    destino = RAIZ + "/OUT"
    filas = _escenario(bucket, make_dji_jpeg, tmp_path, destino)
    assert _correr(_manifiesto(tmp_path, filas, "a.db")) == {"hecho": 4, "fallido": 0}
    antes = dict(bucket.objetos)
    # Misma salida otra vez (task muerta antes de volcar el delta): idéntico = bien.
    assert _correr(_manifiesto(tmp_path, filas, "b.db")) == {"hecho": 4, "fallido": 0}
    assert bucket.objetos == antes


def test_rgb_y_termica_se_solapan(bucket, tmp_path, make_dji_jpeg):
    destino = RAIZ + "/OUT"
    filas = _escenario(bucket, make_dji_jpeg, tmp_path, destino)
    termica_en_proceso = threading.Event()
    rgb_descargando = threading.Event()
    visto = {}

    class AlmacenEspia(apply_pipeline.AlmacenApply):
        def descargar(self, origen, local):
            if origen.endswith("_W.JPG") and "DJI_0001" in origen:
                # RGB descargando MIENTRAS una térmica está en proceso (aforo CPU)
                visto["termica_ya_en_proceso"] = termica_en_proceso.wait(5)
                rgb_descargando.set()
            return super().descargar(origen, local)

    def convertir_espia(*a, **kw):
        termica_en_proceso.set()
        visto["rgb_descargo_durante"] = rgb_descargando.wait(5)
        return apply._convertir_una_termica(*a, **kw)

    r = _correr(_manifiesto(tmp_path, filas), almacen=AlmacenEspia(),
                convertir_termica=convertir_espia, hilos_descarga=4)

    assert r == {"hecho": 4, "fallido": 0}
    assert visto == {"termica_ya_en_proceso": True, "rgb_descargo_durante": True}


def test_salida_identica_a_la_ruta_actual(bucket, tmp_path, make_dji_jpeg):
    legado = RAIZ + "/LEGADO"
    nuevo = RAIZ + "/NUEVO"
    filas_l = _escenario(bucket, make_dji_jpeg, tmp_path, legado)
    filas_n = _escenario(bucket, make_dji_jpeg, tmp_path, nuevo)
    ml, mn = _manifiesto(tmp_path, filas_l, "l.db"), _manifiesto(tmp_path, filas_n, "n.db")

    rgb = apply._aplicar_rgb_impl(ml, _cfg(), _pipeline_doble(), _SignalFalsa(), _SignalFalsa(),
                                  _SignalFalsa())
    term = apply._aplicar_termicas_impl(ml, _cfg(), _PipelineDePrueba(), _SignalFalsa(),
                                        _SignalFalsa(), _SignalFalsa())
    assert rgb == {"hecho": 2, "fallido": 0} and term == {"hecho": 2, "fallido": 0}
    assert _correr(mn) == {"hecho": 4, "fallido": 0}

    obj_l, obj_n = _objetos(bucket, legado), _objetos(bucket, nuevo)
    assert obj_l.keys() == obj_n.keys() and len(obj_l) == 7  # 2 RGB + CROP + 2 _T.JPG + 2 TIFF
    assert obj_l == obj_n  # mismos objetos y mismos bytes (TIFF, _T.JPG, RGB, CROP)
    assert any(k.endswith(".tiff") for k in obj_n) and any(k.endswith("_T.JPG") for k in obj_n)
    ver_l = {f["ruta_origen"]: f["verificacion"].replace(legado, "X") for f in ml.todas()}
    ver_n = {f["ruta_origen"]: f["verificacion"].replace(nuevo, "X") for f in mn.todas()}
    assert ver_l == ver_n
    for f in mn.todas():
        assert f["estado"] == "hecho"
    ml.cerrar()
    mn.cerrar()


def test_cola_de_bytes_respeta_su_limite(bucket, tmp_path, make_dji_jpeg):
    destino = RAIZ + "/OUT"
    filas = _escenario(bucket, make_dji_jpeg, tmp_path, destino)
    tam = max(len(v) for v in bucket.objetos.values())
    limite = tam * 2 + 10
    m = _manifiesto(tmp_path, filas)
    pipe = apply_pipeline.PipelineApply(
        m, _cfg(), _PipelineDePrueba(), _SignalFalsa(), _SignalFalsa(), _SignalFalsa(),
        limite_bytes=limite, plazas=2, fabrica_ejecutor_rgb=lambda n: ThreadPoolExecutor(n),
        hilos_descarga=4)
    assert pipe.ejecutar() == {"hecho": 4, "fallido": 0}
    assert 0 < pipe.presupuesto.pico_entrada <= limite


def test_presupuesto_bloquea_y_admite_elemento_mayor_que_el_limite():
    p = apply_pipeline.PresupuestoBytes(250)
    assert p.reservar(100) and p.reservar(100)
    hecho = threading.Event()

    def _tercero():
        p.reservar(100)
        hecho.set()

    h = threading.Thread(target=_tercero)
    h.start()
    assert not hecho.wait(0.3)  # 300 > 250: espera
    p.liberar_entrada(100)
    assert hecho.wait(5)
    h.join()
    assert p.pico_entrada <= 250
    # Un único elemento mayor que el límite entra con la cola vacía (no se bloquea)
    q = apply_pipeline.PresupuestoBytes(50)
    assert q.reservar(500)


def test_cola_y_plazas_por_env(monkeypatch):
    monkeypatch.setenv("ATOM_APPLY_COLA_MIB", "10")
    assert apply_pipeline.limite_cola_bytes() == 10 * 1024 * 1024
    monkeypatch.setenv("ATOM_APPLY_COLA_MIB", "x")
    with pytest.raises(ValueError):
        apply_pipeline.limite_cola_bytes()
    monkeypatch.undo()


def test_plazas_cpu_apply_env(monkeypatch):
    from atom_core import paralelismo
    monkeypatch.delenv("ATOM_APPLY_PLAZAS_CPU", raising=False)
    monkeypatch.setenv("ATOM_RGB_WORKERS", "3")
    assert paralelismo.plazas_cpu_apply() == 3
    monkeypatch.setenv("ATOM_APPLY_PLAZAS_CPU", "5")
    assert paralelismo.plazas_cpu_apply() == 5
    monkeypatch.setenv("ATOM_APPLY_PLAZAS_CPU", "0")
    with pytest.raises(ValueError):
        paralelismo.plazas_cpu_apply()


# ---- _T.JPG por rewrite server-side ----------------------------------------

def test_rewrite_jpg_termico_bytes_identicos_y_412_si_existe(bucket):
    datos = b"R-JPEG radiometrico " * 50
    origen, destino = RAIZ + "/SD/A_T.JPG", RAIZ + "/OUT/A_T.JPG"
    _poner(bucket, origen, datos)

    assert apply._copiar_jpg_termico(origen, "no-se-usa", destino) == "copiado"
    assert bucket.objetos[_clave(destino)] == datos
    assert bucket.rewrites == 1

    # Ya existe con el mismo md5: idéntico, sin error ni segunda escritura
    assert apply._copiar_jpg_termico(origen, "no-se-usa", destino) == "identico"
    assert bucket.objetos[_clave(destino)] == datos

    # Existe con otro contenido: 412 -> choque, sin sobrescribir
    otro = RAIZ + "/OUT/B_T.JPG"
    _poner(bucket, otro, b"otro")
    with pytest.raises(almacen_mod.ChoqueDestino):
        apply._copiar_jpg_termico(origen, "no-se-usa", otro)
    assert bucket.objetos[_clave(otro)] == b"otro"
    assert bucket.rechazos >= 2


def test_rewrite_origen_sin_md5_es_error(bucket, monkeypatch):
    origen = RAIZ + "/SD/A_T.JPG"
    _poner(bucket, origen, b"x")
    monkeypatch.setattr(type(bucket.blob("a")), "md5_hash", property(lambda s: None))
    with pytest.raises(OSError, match="md5"):
        almacen_mod.copiar_servidor(origen, RAIZ + "/OUT/A_T.JPG")
    assert _clave(RAIZ + "/OUT/A_T.JPG") not in bucket.objetos
