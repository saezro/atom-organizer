"""Organizar en varias tandas al MISMO destino (día 1, día 2...) sin pisar nada.

Cubre: (A) foto `hecho` sin asignar que en la tanda nueva sí tiene vuelo se
reabre y se limpia SIN_ORDENAR solo si la escritura nueva fue bien; (B) los
ficheros a medio subir no entran al índice; (C) nunca dos claves en la misma
ruta de salida, entre tandas ni dentro de una; y el caso feliz no cambia.
"""
import datetime as dt
import os
import time

from atom_core import apply as apply_mod
from atom_core import indice
from atom_core.indice import _desambiguar_colisiones_generales, construir_indice
from atom_core.manifiesto import FilaManifiesto, Manifiesto, clave_imagen

from .test_indice_organizado import (
    _cfg, _crear_imagen, _escribir_estadillo, _ExifDePrueba, _manifiesto,
    _PipelineDePrueba, _Signal)

V1 = ("2024:06:01", "10:00:00", "10:10:00")
V2 = ("2024:06:01", "12:00:00", "12:10:00")
VENTANAS = {
    V1: (dt.datetime(2024, 6, 1, 10, 0, 0), dt.datetime(2024, 6, 1, 10, 10, 0)),
    V2: (dt.datetime(2024, 6, 1, 12, 0, 0), dt.datetime(2024, 6, 1, 12, 10, 0)),
}


def _fila(origen="/o/DJI_0001.JPG", salida="/d/PB1/V1/DJI_0001.JPG", **cambios):
    base = dict(
        ruta_origen=origen, tipo="RGB", timestamp_exif="2024-06-01T10:05:00",
        modelo="M3T", pb="1", vuelo="1", nombre_nuevo="", angulo_giro=0,
        pct_recorte=None, comprime=False, ruta_salida_original=salida,
        ruta_salida_crop=None, ruta_salida_tiff=None, unassigned=False)
    base.update(cambios)
    return FilaManifiesto(**base)


def _manifiesto_simple(tmp_path):
    m = Manifiesto(tmp_path / "m.db")
    m.crear_esquema()
    return m


def _escribir(ruta, contenido=b"x"):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "wb") as fh:
        fh.write(contenido)


# --- A: reasignar fotos sin asignar -------------------------------------

def test_hecha_sin_asignar_se_reabre_si_ahora_tiene_vuelo(tmp_path):
    m = _manifiesto_simple(tmp_path)
    viejo = str(tmp_path / "SIN_ORDENAR" / "RGB" / "DJI_0001.JPG")
    m.insertar_o_reabrir([_fila(salida=viejo, pb=None, vuelo=None, unassigned=True)])
    m.marcar_hecha(m.todas()[0]["id"], f"{viejo}:1")

    nuevo = str(tmp_path / "RGB" / "PB1" / "PB1_V1" / "DJI_0001.JPG")
    r = m.insertar_o_reabrir([_fila(salida=nuevo)])

    assert (r.reasignadas, r.saltadas, r.siguen_sin_asignar) == (1, 0, 0)
    fila = m.todas()[0]
    assert fila["estado"] == "pendiente"
    assert fila["ruta_salida_original"] == nuevo
    assert fila["pb"] == "1"


def test_el_fichero_viejo_se_borra_solo_si_la_escritura_nueva_va_bien(tmp_path):
    m = _manifiesto_simple(tmp_path)
    viejo = str(tmp_path / "SIN_ORDENAR" / "RGB" / "DJI_0001.JPG")
    viejo_crop = str(tmp_path / "SIN_ORDENAR" / "RGB" / "DJI_0001_CROP.JPG")
    _escribir(viejo)
    _escribir(viejo_crop)
    m.insertar_o_reabrir([_fila(salida=viejo, ruta_salida_crop=viejo_crop,
                                pb=None, vuelo=None, unassigned=True)])
    m.marcar_hecha(m.todas()[0]["id"], "ok")
    nuevo = str(tmp_path / "RGB" / "PB1" / "DJI_0001.JPG")
    m.insertar_o_reabrir([_fila(salida=nuevo)])
    id_fila = m.todas()[0]["id"]

    # La escritura nueva falla: el viejo se queda.
    m.marcar_fallida(id_fila, "disco lleno")
    assert os.path.exists(viejo) and os.path.exists(viejo_crop)

    # Se reintenta y va bien: ahora sí se borran (original y derivado).
    m.insertar_o_reabrir([_fila(salida=nuevo)])
    m.marcar_hecha(id_fila, "ok")
    assert not os.path.exists(viejo)
    assert not os.path.exists(viejo_crop)


def test_hecha_sin_asignar_que_sigue_sin_asignar_se_salta_y_se_cuenta(tmp_path):
    m = _manifiesto_simple(tmp_path)
    viejo = str(tmp_path / "SIN_ORDENAR" / "RGB" / "DJI_0001.JPG")
    m.insertar_o_reabrir([_fila(salida=viejo, pb=None, vuelo=None, unassigned=True)])
    m.marcar_hecha(m.todas()[0]["id"], "ok")

    r = m.insertar_o_reabrir([_fila(salida=viejo, pb=None, vuelo=None, unassigned=True)])

    assert (r.saltadas, r.reasignadas, r.siguen_sin_asignar) == (1, 0, 1)
    assert m.todas()[0]["estado"] == "hecho"


def test_indice_dia2_reasigna_y_loguea(tmp_path):
    _escribir_estadillo(tmp_path / "estadillo.csv", [("1", "1", *V1)])
    cfg = _cfg(tmp_path)
    ruta = _crear_imagen(cfg.input_folder, "DJI_0001_D.JPG")
    exif = _ExifDePrueba(timestamps={ruta: dt.datetime(2024, 6, 1, 12, 5, 0)})
    m = _manifiesto(tmp_path)
    construir_indice(cfg, _PipelineDePrueba(VENTANAS), exif, m, _Signal(), _Signal(), _Signal())
    assert m.todas()[0]["unassigned"] == 1
    m.marcar_hecha(m.todas()[0]["id"], "ok")

    # Día 2: el estadillo ya incluye el vuelo de las 12:00.
    _escribir_estadillo(tmp_path / "estadillo.csv", [("1", "1", *V1), ("1", "2", *V2)])
    log = _Signal()
    resumen = construir_indice(cfg, _PipelineDePrueba(VENTANAS), exif, m, log, _Signal(), _Signal())

    fila = m.todas()[0]
    assert resumen["reasignadas"] == 1
    assert fila["estado"] == "pendiente" and fila["unassigned"] == 0
    assert "SIN_ORDENAR" not in fila["ruta_salida_original"]
    assert any("1 foto(s) sin asignar" in str(x) and "0 siguen sin asignar" in str(x)
               for x in log.mensajes)
    m.cerrar()


# --- B: ficheros a medio subir ------------------------------------------

def test_indice_omite_ficheros_vacios_y_cola_de_ceros_y_avisa(tmp_path, monkeypatch):
    monkeypatch.setattr(indice, "_FILTRO_ARCHIVO_A_MEDIAS", True)
    _escribir_estadillo(tmp_path / "estadillo.csv", [("1", "1", *V1)])
    cfg = _cfg(tmp_path)
    carpeta = cfg.input_folder
    estable = os.path.join(carpeta, "estable_D.JPG")
    _escribir(estable)                                             # recién escrita: entra
    _escribir(os.path.join(carpeta, "vacia_D.JPG"), b"")           # tamaño 0
    for i in range(8):                                             # cola de ceros
        _escribir(os.path.join(carpeta, f"ceros_{i}_D.JPG"), b"\xff\xd8" + b"x" * 20 + bytes(16))
    exif = _ExifDePrueba(timestamps={estable: dt.datetime(2024, 6, 1, 10, 5, 0)})
    m = _manifiesto(tmp_path)
    log = _Signal()

    resumen = construir_indice(cfg, _PipelineDePrueba(VENTANAS), exif, m, log, _Signal(), _Signal())

    assert [f["ruta_origen"] for f in m.todas()] == [estable]
    assert resumen["omitidas_copiandose"] == 9
    avisos = [x for x in log.mensajes if "omitidas por estar aún copiándose/subiendo" in str(x)]
    assert len(avisos) == 1 and "9 imágenes omitidas" in avisos[0]
    assert avisos[0].count(".JPG") == 5          # hasta 5 ejemplos
    m.cerrar()


def test_cola_de_ceros_solo_jpeg_y_sin_exigir_ffd9(tmp_path, monkeypatch):
    monkeypatch.setattr(indice, "_FILTRO_ARCHIVO_A_MEDIAS", True)
    # R-JPEG DJI: bytes tras el EOI que no son ceros -> válido; sin FFD9 al final.
    rjpeg = tmp_path / "r.JPG"
    _escribir(str(rjpeg), b"\xff\xd8abc\xff\xd9" + bytes(range(1, 40)))
    truncada = tmp_path / "t.jpeg"
    _escribir(str(truncada), b"\xff\xd8abc" + bytes(16))
    tiff = tmp_path / "x.tif"
    _escribir(str(tiff), b"abc" + bytes(16))
    assert indice._a_medio_copiar(str(rjpeg)) is False
    assert indice._a_medio_copiar(str(truncada)) is True
    assert indice._a_medio_copiar(str(tiff)) is False


def test_archivo_en_uso_windows_se_omite_solo_con_error_32(tmp_path, monkeypatch):
    monkeypatch.setattr(indice, "_FILTRO_ARCHIVO_A_MEDIAS", True)
    ruta = tmp_path / "a.JPG"
    _escribir(str(ruta))
    # En Linux es no-op.
    assert indice._a_medio_copiar(str(ruta)) is False
    monkeypatch.setattr(indice, "_es_windows", lambda: True)
    monkeypatch.setattr(indice, "_error_apertura_compartida", lambda r: 32)
    assert indice._a_medio_copiar(str(ruta)) is True
    monkeypatch.setattr(indice, "_error_apertura_compartida", lambda r: 5)
    assert indice._a_medio_copiar(str(ruta)) is False
    monkeypatch.setattr(indice, "_error_apertura_compartida", lambda r: 0)
    assert indice._a_medio_copiar(str(ruta)) is False


def test_filtro_se_puede_desactivar(tmp_path, monkeypatch):
    monkeypatch.setattr(indice, "_FILTRO_ARCHIVO_A_MEDIAS", False)
    ruta = tmp_path / "o" / "a.JPG"
    _escribir(str(ruta), b"")
    omitidas = []
    assert indice._listar_imagenes(str(tmp_path / "o"), omitidas) == [str(ruta)]
    assert omitidas == []


# --- C: nunca pisar otra clave ------------------------------------------

def test_ruta_ocupada_por_otra_clave_del_manifiesto_recibe_sufijo_estable(tmp_path):
    m = _manifiesto_simple(tmp_path)
    ruta = "/d/PB1/V1/DJI_0001.JPG"
    m.insertar_o_reabrir([_fila("/o/a/DJI_0001.JPG", ruta)])   # tanda 1
    m.marcar_hecha(m.todas()[0]["id"], "ok")

    nueva = _fila("/o/b/DJI_0001.JPG", ruta, timestamp_exif="2024-06-02T09:00:00")
    out = _desambiguar_colisiones_generales([nueva], m.rutas_salida_por_clave())
    assert out[0].ruta_salida_original == "/d/PB1/V1/DJI_0001_2.JPG"

    m.insertar_o_reabrir(out)                                  # queda registrada
    # Tercera tanda: ambas claves vuelven; cada una conserva SU ruta.
    entrada = [_fila("/o/a/DJI_0001.JPG", ruta), nueva]
    out2 = _desambiguar_colisiones_generales(entrada, m.rutas_salida_por_clave())
    assert [f.ruta_salida_original for f in out2] == [
        "/d/PB1/V1/DJI_0001.JPG", "/d/PB1/V1/DJI_0001_2.JPG"]
    # Y en orden inverso tampoco se mueven.
    out3 = _desambiguar_colisiones_generales(entrada[::-1], m.rutas_salida_por_clave())
    assert sorted(f.ruta_salida_original for f in out3) == sorted(
        f.ruta_salida_original for f in out2)
    m.cerrar()


def test_sufijo_aplica_a_crop_y_tiff(tmp_path):
    m = _manifiesto_simple(tmp_path)
    m.insertar_o_reabrir([_fila("/o/a/X.JPG", "/d/X.JPG")])
    otra = _fila("/o/b/X.JPG", "/d/X.JPG", ruta_salida_crop="/d/X_CROP.JPG",
                 ruta_salida_tiff="/d/X.tiff", timestamp_exif="2024-06-02T09:00:00")
    out = _desambiguar_colisiones_generales([otra], m.rutas_salida_por_clave())[0]
    assert (out.ruta_salida_original, out.ruta_salida_crop, out.ruta_salida_tiff) == (
        "/d/X_2.JPG", "/d/X_CROP_2.JPG", "/d/X_2.tiff")
    m.cerrar()


def test_sin_ordenar_y_generales_dentro_de_la_tanda_no_se_pisan():
    a = _fila("/o/a/D.JPG", "/d/SIN_ORDENAR/RGB/D.JPG", pb=None, vuelo=None, unassigned=True)
    b = _fila("/o/b/D.JPG", "/d/SIN_ORDENAR/RGB/D.JPG", pb=None, vuelo=None, unassigned=True,
              timestamp_exif="2024-06-02T09:00:00")
    out = _desambiguar_colisiones_generales([a, b], {})
    assert [f.ruta_salida_original for f in out] == [
        "/d/SIN_ORDENAR/RGB/D.JPG", "/d/SIN_ORDENAR/RGB/D_2.JPG"]


def test_carpeta_pb_renombrada_no_se_toca():
    a = _fila("/o/a/D.JPG", "/d/PB1/V1/20240601_100500.JPG", nombre_nuevo="20240601_100500.JPG")
    out = _desambiguar_colisiones_generales([a], {clave_imagen("/o/x.JPG", None, 0):
                                                  {"otra|clave|0"}})
    assert out[0] is a


class _Cb:
    def __init__(self):
        self.mensajes = []

    def emit(self, valor=None, *a, **k):
        self.mensajes.append(valor)


def test_apply_no_sobrescribe_fichero_de_otra_clave_pero_si_el_de_la_misma(tmp_path):
    m = _manifiesto_simple(tmp_path)
    destino_a = str(tmp_path / "d" / "A.JPG")
    destino_b = str(tmp_path / "d" / "B.JPG")
    _escribir(destino_a, b"organizado")
    _escribir(destino_b, b"organizado")
    # Dos filas que (por un manifiesto viejo/corrupto) apuntan al mismo destino que A.
    m.insertar_o_reabrir([_fila("/o/a/A.JPG", destino_a),
                          _fila("/o/b/A.JPG", destino_a, timestamp_exif="2024-06-02T09:00:00"),
                          _fila("/o/c/B.JPG", destino_b)])
    filas = [dict(f) for f in m.pendientes()]
    cb = _Cb()

    libres = apply_mod._vetar_sobrescrituras(m, filas, cb)

    # A y B comparten destino con otra clave -> ambas vetadas; la de B se reescribe.
    assert [f["ruta_origen"] for f in libres] == ["/o/c/B.JPG"]
    estados = {f["ruta_origen"]: f for f in m.todas()}
    assert estados["/o/a/A.JPG"]["estado"] == "fallido"
    assert "No se sobrescribe" in estados["/o/a/A.JPG"]["motivo_fallo"]
    assert estados["/o/c/B.JPG"]["estado"] == "pendiente"
    assert open(destino_a, "rb").read() == b"organizado"
    m.cerrar()


# --- Caso feliz: mismas rutas que hoy -----------------------------------

def test_caso_feliz_dos_tandas_dan_las_mismas_rutas_que_una_sola(tmp_path):
    estad = [("1", "1", *V1), ("1", "2", *V2)]

    def _correr(base, tandas):
        os.makedirs(base, exist_ok=True)
        _escribir_estadillo(base / "estadillo.csv", estad)
        cfg = _cfg(base)
        exif_ts = {}
        m = _manifiesto(base)
        for nombres in tandas:
            for nombre, hora in nombres:
                ruta = _crear_imagen(cfg.input_folder, nombre)
                exif_ts[ruta] = dt.datetime(2024, 6, 1, *hora)
            construir_indice(cfg, _PipelineDePrueba(VENTANAS), _ExifDePrueba(timestamps=exif_ts),
                             m, _Signal(), _Signal(), _Signal())
            for fila in m.pendientes():
                m.marcar_hecha(fila["id"], "ok")
        rutas = {os.path.basename(f["ruta_origen"]):
                 os.path.relpath(f["ruta_salida_original"], cfg.output_folder) for f in m.todas()}
        m.cerrar()
        return rutas

    d1 = [("DJI_0001_D.JPG", (10, 5, 0)), ("DJI_0002_D.JPG", (10, 6, 0))]
    d2 = [("DJI_0003_D.JPG", (12, 5, 0)), ("DJI_0004_D.JPG", (12, 6, 0))]
    una = _correr(tmp_path / "una", [d1 + d2])
    dos = _correr(tmp_path / "dos", [d1, d2])

    assert una == dos
    assert all("_2" not in ruta for ruta in dos.values())


# --- Auditoría: reabrir, contador fallido, borrado casefold/log ----------

def test_previas_sin_ordenar_cuentan_como_ocupadas_por_su_clave(tmp_path):
    m = _manifiesto_simple(tmp_path)
    m.insertar_o_reabrir([_fila("/o/a/DJI_0001.JPG", "/d/SIN_ORDENAR/DJI_0001.JPG")])
    fila = m.todas()[0]
    m._actualizar(fila["id"], previas_sin_ordenar="/d/SIN_ORDENAR/DJI_0001.JPG\n\n/d/SIN_ORDENAR/DJI_0001_CROP.JPG")
    mapa = m.rutas_salida_por_clave()
    assert mapa["/d/sin_ordenar/dji_0001_crop.jpg"] == {fila["clave"]}
    assert "" not in mapa
    m.cerrar()


def test_borrado_de_previa_casefold_y_loguea_error(tmp_path, monkeypatch, caplog):
    import logging
    m = _manifiesto_simple(tmp_path)
    previa = tmp_path / "SIN_ORDENAR" / "A.JPG"
    _escribir(str(previa))
    nueva = str(tmp_path / "sin_ordenar" / "a.jpg")      # misma ruta salvo mayúsculas
    m.insertar_o_reabrir([_fila("/o/a/A.JPG", nueva)])
    fila = m.todas()[0]
    m._actualizar(fila["id"], previas_sin_ordenar=str(previa))
    m.marcar_hecha(fila["id"], "ok")
    assert previa.exists()                                # no se borra: es la misma ruta
    # Fallo al borrar: se loguea, no se propaga.
    otra = tmp_path / "otra.JPG"
    _escribir(str(otra))
    monkeypatch.setattr(os, "remove", lambda r: (_ for _ in ()).throw(OSError("bloqueado")))
    with caplog.at_level(logging.WARNING):
        m._borrar_ruta_previa(str(otra), fila["id"])
    assert any("bloqueado" in r.getMessage() for r in caplog.records)
    m.cerrar()


def test_aplicar_rgb_cuenta_vetadas_como_fallidas(tmp_path, monkeypatch):
    m = _manifiesto_simple(tmp_path)
    destino = str(tmp_path / "d" / "A.JPG")
    _escribir(destino, b"organizado")
    m.insertar_o_reabrir([_fila("/o/a/A.JPG", destino),
                          _fila("/o/b/A.JPG", destino, timestamp_exif="2024-06-02T09:00:00")])
    res = apply_mod.aplicar_rgb(m, None, None, _Cb(), _Cb(), _Cb())
    assert res == {"hecho": 0, "fallido": 2}
    assert open(destino, "rb").read() == b"organizado"
    m.cerrar()


# --- Duplicados en SIN_ORDENAR al cambiar la ruta de origen entre tandas ----

def test_cambio_de_ruta_origen_reabre_la_misma_fila_y_no_duplica(tmp_path):
    m = _manifiesto_simple(tmp_path)
    viejo = str(tmp_path / "SIN_ORDENAR" / "RGB" / "DJI_0001.JPG")
    _escribir(viejo)
    m.insertar_o_reabrir([_fila("gdrive:/x/DJI_0001.JPG", viejo, pb=None, vuelo=None,
                                unassigned=True, bytes_origen=10)], ejecucion_id=1)
    m.marcar_hecha(m.todas()[0]["id"], "ok")
    # Clave guardada con la ruta de origen dentro (formato antiguo/otro origen):
    # la clave exacta de la tanda 2 ya no casa.
    m._conexion().execute("UPDATE imagenes SET clave = 'gdrive:/x/DJI_0001.JPG|t|10'")
    m._conexion().commit()

    nuevo = str(tmp_path / "RGB" / "PB1" / "DJI_0001.JPG")
    r = m.insertar_o_reabrir([_fila("gs://b/DJI_0001.JPG", nuevo, bytes_origen=10)],
                             ejecucion_id=2)

    assert (r.nuevas, r.reasignadas, r.reabiertas_por_nombre) == (0, 1, 1)
    filas = m.todas()
    assert len(filas) == 1
    assert filas[0]["ruta_origen"] == "gs://b/DJI_0001.JPG"
    assert filas[0]["estado"] == "pendiente"
    m.marcar_hecha(filas[0]["id"], "ok")
    assert not os.path.exists(viejo)                      # sin duplicado en SIN_ORDENAR
    # La tanda siguiente ya casa por clave exacta.
    r2 = m.insertar_o_reabrir([_fila("gs://b/DJI_0001.JPG", nuevo, bytes_origen=10)],
                              ejecucion_id=3)
    assert (r2.nuevas, r2.saltadas, r2.reabiertas_por_nombre) == (0, 1, 0)
    m.cerrar()


def test_candidato_ambiguo_no_empareja_y_avisa(tmp_path, caplog):
    import logging
    m = _manifiesto_simple(tmp_path)
    for i, origen in enumerate(("/a/DJI_0001.JPG", "/b/DJI_0001.JPG")):
        m.insertar_o_reabrir([_fila(origen, f"/s{origen}", pb=None, vuelo=None,
                                    unassigned=True, bytes_origen=10)], ejecucion_id=1)
        m._conexion().execute("UPDATE imagenes SET clave = ? WHERE clave NOT LIKE 'viejo%'",
                              (f"viejo{i}",))
        m._conexion().commit()
    with caplog.at_level(logging.WARNING):
        r = m.insertar_o_reabrir([_fila("gs://b/DJI_0001.JPG", "/d/x.JPG", bytes_origen=10)],
                                 ejecucion_id=2)
    assert (r.nuevas, r.reabiertas_por_nombre) == (1, 0)
    assert len(r.ambiguas) == 1
    assert any("ambiguo" in x.getMessage() for x in caplog.records)
    assert len(m.todas()) == 3
    m.cerrar()


def test_borrado_que_falla_avisa_y_se_reintenta_en_la_tanda_siguiente(tmp_path, monkeypatch, caplog):
    import logging
    m = _manifiesto_simple(tmp_path)
    viejo = str(tmp_path / "SIN_ORDENAR" / "DJI_0001.JPG")
    _escribir(viejo)
    m.insertar_o_reabrir([_fila("/o/DJI_0001.JPG", viejo, pb=None, vuelo=None, unassigned=True)],
                         ejecucion_id=1)
    m.marcar_hecha(m.todas()[0]["id"], "ok")
    nuevo = str(tmp_path / "RGB" / "DJI_0001.JPG")
    m.insertar_o_reabrir([_fila("/o/DJI_0001.JPG", nuevo)], ejecucion_id=2)
    id_fila = m.todas()[0]["id"]

    real_remove = os.remove
    monkeypatch.setattr(os, "remove", lambda r: (_ for _ in ()).throw(OSError("bloqueado")))
    with caplog.at_level(logging.ERROR):
        m.marcar_hecha(id_fila, "ok")
    assert any("bloqueado" in x.getMessage() for x in caplog.records)
    assert os.path.exists(viejo)
    assert m.borrados_sin_ordenar_pendientes() == [viejo]

    monkeypatch.setattr(os, "remove", real_remove)        # el bloqueo desaparece
    r = m.insertar_o_reabrir([_fila("/o/DJI_0001.JPG", nuevo)], ejecucion_id=3)
    assert r.borrados_reintentados == 1 and r.borrados_pendientes == 0
    assert not os.path.exists(viejo)
    assert m.borrados_sin_ordenar_pendientes() == []
    m.cerrar()


def test_borrado_que_no_surte_efecto_se_detecta_por_verificacion(tmp_path, monkeypatch, caplog):
    import logging
    m = _manifiesto_simple(tmp_path)
    viejo = str(tmp_path / "SIN_ORDENAR" / "A.JPG")
    _escribir(viejo)
    m.insertar_o_reabrir([_fila("/o/A.JPG", str(tmp_path / "RGB" / "A.JPG"))])
    monkeypatch.setattr(os, "remove", lambda r: None)     # "borra" pero sigue ahí
    with caplog.at_level(logging.ERROR):
        assert m._borrar_ruta_previa(viejo, m.todas()[0]["id"]) is False
    assert any("SIGUE existiendo" in x.getMessage() for x in caplog.records)
    monkeypatch.undo()
    assert m._borrar_ruta_previa(viejo, m.todas()[0]["id"]) is True
    assert not os.path.exists(viejo)
    m.cerrar()


def test_cierre_reporta_duplicado_en_sin_ordenar(tmp_path):
    from atom_core import cierre
    from .test_cierre_organizado import _cfg as _cfg_cierre
    m = _manifiesto_simple(tmp_path)
    viejo = str(tmp_path / "SIN_ORDENAR" / "DJI_0001.JPG")
    _escribir(viejo)
    ordenada = str(tmp_path / "RGB" / "DJI_0001.JPG")
    _escribir(ordenada)
    # Dos filas (el caso real: la clave difería por la ruta de origen).
    m.insertar_o_reabrir([_fila("/o/DJI_0001.JPG", viejo, pb=None, vuelo=None, unassigned=True)],
                         ejecucion_id=1)
    m._conexion().execute("UPDATE imagenes SET clave = 'viejo'")
    m._conexion().commit()
    m.insertar_o_reabrir([_fila("/o/DJI_0001.JPG", ordenada)], ejecucion_id=1)
    for f in m.todas():
        m.marcar_hecha(f["id"], "ok")
    assert m.duplicados_en_sin_ordenar() == [(viejo, ordenada)]
    problemas = cierre.verificar(m, _cfg_cierre(tmp_path))
    errores, _ = cierre.separar_problemas(problemas)
    assert any("SIN_ORDENAR duplican" in e and viejo in e for e in errores)
    m.cerrar()


def test_fila_reabierta_que_vuelve_a_la_misma_ruta_no_borra_la_foto(tmp_path):
    for variante in (lambda r: r, lambda r: r.swapcase()):
        m = Manifiesto(tmp_path / f"m{id(variante)}.db")
        m.crear_esquema()
        ruta = str(tmp_path / f"v{id(variante)}" / "SIN_ORDENAR" / "A.JPG")
        _escribir(ruta)
        fila = m.todas()
        m.insertar_o_reabrir([_fila("/o/A.JPG", ruta, pb=None, vuelo=None, unassigned=True)])
        idf = m.todas()[0]["id"]
        m._actualizar(idf, previas_sin_ordenar=variante(ruta))
        m.marcar_hecha(idf, "ok")
        # Camino de reintento (tanda siguiente) y camino directo.
        m._actualizar(idf, previas_sin_ordenar=variante(ruta))
        ok, pend = m.reintentar_borrados_pendientes()
        assert os.path.exists(ruta) and pend == 0
        assert m._borrar_ruta_previa(variante(ruta), idf) is True
        assert os.path.exists(ruta)
        m.cerrar()



def test_rendimiento_borrado_previas_con_miles_de_filas(tmp_path):
    """~5000 filas, ~500 reasignadas: marcar_hecha y el reintento no son O(filas) por ruta."""
    m = Manifiesto(tmp_path / "perf.db")
    m.crear_esquema()
    n, reasig = 5000, 500
    m.insertar_o_reabrir([
        _fila(f"/o/D{i}.JPG", f"/d/PB1/V1/D{i}.JPG", timestamp_exif=f"2024-06-01T10:{i % 60:02d}:{i // 60:02d}")
        for i in range(n)])
    filas = m.todas()
    viejos = []
    for k, fila in enumerate(filas[:reasig]):
        viejo = str(tmp_path / "SIN_ORDENAR" / f"V{k}.JPG")
        _escribir(viejo)
        viejos.append(viejo)
        m._actualizar(fila["id"], previas_sin_ordenar=viejo)
    # Una protegida: salida actual de otra fila, en otra capitalización.
    protegida = str(tmp_path / "SIN_ORDENAR" / "KEEP.JPG")
    _escribir(protegida)
    m._actualizar(filas[reasig]["id"], ruta_salida_original=protegida)
    m._actualizar(filas[0]["id"], previas_sin_ordenar=viejos[0] + "\n" + protegida.swapcase())
    t0 = time.perf_counter()
    for fila in filas[:reasig // 2]:
        m.marcar_hecha(fila["id"], "ok")
    for fila in filas[reasig // 2:reasig]:
        m._actualizar(fila["id"], estado="hecho")
    ok, pend = m.reintentar_borrados_pendientes()
    dt_s = time.perf_counter() - t0
    assert dt_s < 5, dt_s
    assert pend == 0
    assert all(not os.path.exists(v) for v in viejos)
    assert os.path.exists(protegida)
    m.cerrar()
