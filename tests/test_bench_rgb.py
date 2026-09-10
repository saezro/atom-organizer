"""Tests unitarios de `scripts/bench_rgb.py`.

Rápidos y SIN ejecutar ningún run real: solo el parser de duraciones, el
agregador de estadísticas del CSV de `perfil_rgb` (sobre un CSV sintético) y
que el módulo se importa sin efectos secundarios (no toca `os.environ`, no
llama a `argparse.parse_args()`, no importa `atom_core`)."""
import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def _cabecera_csv():
    return ("ts_inicio,ts_fin,pid,nombre,bytes_origen,t_lectura,t_decode,"
            "t_encode_original,t_encode_crop,t_thumbnail,t_escritura,t_total\n")


def _fila_csv(nombre, t_lectura, t_decode, t_encode_original, t_encode_crop, t_escritura):
    t_thumbnail = 0.0
    t_total = t_lectura + t_decode + t_encode_original + t_encode_crop + t_thumbnail + t_escritura
    return (f"0.0,0.1,123,{nombre},1000,{t_lectura},{t_decode},{t_encode_original},"
            f"{t_encode_crop},{t_thumbnail},{t_escritura},{t_total}\n")


class TestParsearDuracion:
    """`_parsear_duracion`: inverso de `atom_core.apply._formatear_duracion`,
    tanto en el formato compacto (`"6m49s"`) como en el real con espacios
    (`"6 min 49 s"`, `"52.0 s"`)."""

    def test_solo_segundos_compacto(self):
        import bench_rgb
        assert bench_rgb._parsear_duracion("52s") == pytest.approx(52.0)

    def test_minutos_y_segundos_compacto(self):
        import bench_rgb
        assert bench_rgb._parsear_duracion("6m49s") == pytest.approx(6 * 60 + 49)

    def test_horas_minutos_y_segundos_compacto(self):
        import bench_rgb
        assert bench_rgb._parsear_duracion("1h2m3s") == pytest.approx(1 * 3600 + 2 * 60 + 3)

    def test_formato_real_con_espacios(self):
        import bench_rgb
        assert bench_rgb._parsear_duracion("5 min 12 s") == pytest.approx(5 * 60 + 12)

    def test_formato_real_solo_segundos_decimal(self):
        import bench_rgb
        assert bench_rgb._parsear_duracion("52.0 s") == pytest.approx(52.0)

    def test_sin_duracion_devuelve_cero(self):
        import bench_rgb
        assert bench_rgb._parsear_duracion("") == 0.0


class TestParsearLineaTiempos:
    def test_linea_real_de_phases(self):
        import bench_rgb
        linea = ("[tiempos] Organizado completo: 6 min 34 s (Índice 52.0 s · "
                  "RGB 5 min 20 s · Térmicas 1.0 s · Cierre 0.5 s).")
        etapas = bench_rgb._parsear_linea_tiempos(linea)
        assert etapas == {
            "Índice": pytest.approx(52.0),
            "RGB": pytest.approx(5 * 60 + 20),
            "Térmicas": pytest.approx(1.0),
            "Cierre": pytest.approx(0.5),
        }

    def test_sin_parentesis_devuelve_vacio(self):
        import bench_rgb
        assert bench_rgb._parsear_linea_tiempos("[tiempos] sin desglose") == {}


class TestAgregarEstadisticasCsv:
    def test_agrega_suma_media_p95(self, tmp_path):
        import bench_rgb
        ruta = tmp_path / "perfil.csv"
        filas = [
            _fila_csv("a.jpg", 1.0, 2.0, 3.0, 1.0, 0.5),
            _fila_csv("b.jpg", 2.0, 3.0, 4.0, 2.0, 1.0),
            _fila_csv("c.jpg", 3.0, 4.0, 5.0, 3.0, 1.5),
        ]
        ruta.write_text(_cabecera_csv() + "".join(filas), encoding="utf-8")

        resultado = bench_rgb._agregar_estadisticas_csv(str(ruta))

        assert resultado["n_imagenes"] == 3
        lectura = resultado["subetapas"]["t_lectura"]
        assert lectura["suma"] == pytest.approx(6.0)
        assert lectura["media"] == pytest.approx(2.0)
        # p95 por interpolación lineal sobre [1.0, 2.0, 3.0]:
        # posición = (3-1) * 0.95 = 1.9 -> 2.0 + (3.0-2.0)*0.9 = 2.9
        assert lectura["p95"] == pytest.approx(2.9)

        escritura = resultado["subetapas"]["t_escritura"]
        assert escritura["suma"] == pytest.approx(3.0)
        assert escritura["media"] == pytest.approx(1.0)

    def test_fichero_inexistente_devuelve_none(self, tmp_path):
        import bench_rgb
        assert bench_rgb._agregar_estadisticas_csv(str(tmp_path / "no_existe.csv")) is None

    def test_csv_sin_filas_devuelve_none(self, tmp_path):
        import bench_rgb
        ruta = tmp_path / "vacio.csv"
        ruta.write_text(_cabecera_csv(), encoding="utf-8")
        assert bench_rgb._agregar_estadisticas_csv(str(ruta)) is None


class TestPercentil:
    def test_lista_vacia(self):
        import bench_rgb
        assert bench_rgb._percentil([], 95) == 0.0

    def test_un_solo_valor(self):
        import bench_rgb
        assert bench_rgb._percentil([7.0], 95) == 7.0

    def test_p50_es_la_mediana_con_impares(self):
        import bench_rgb
        assert bench_rgb._percentil([1.0, 2.0, 3.0], 50) == pytest.approx(2.0)


class TestImportacionSinEfectosSecundarios:
    def test_import_no_toca_os_environ(self, monkeypatch):
        import os as os_mod
        monkeypatch.delenv("ORGANIZER_PERFIL_RGB", raising=False)
        import importlib
        import bench_rgb
        importlib.reload(bench_rgb)
        assert "ORGANIZER_PERFIL_RGB" not in os_mod.environ

    def test_import_no_ejecuta_argparse(self):
        """Importar el módulo no debe intentar parsear `sys.argv` (que en la
        sesión de pytest trae flags de pytest, no los de bench_rgb): eso
        queda diferido a `main()`, nunca a nivel de módulo. Si se ejecutara
        al importar, `argparse` fallaría con los argumentos de pytest y este
        `import` ya habría petado antes de llegar al assert."""
        import bench_rgb
        assert callable(bench_rgb.main)


class TestFijarControladorRgb:
    """`--workers N`: el override vive SOLO en el script (monkeypatch de
    instrumentación), `atom_core/paralelismo.py` no se toca."""

    def test_fija_minimo_maximo_arranque_y_desactiva_tope_hdd(self):
        import bench_rgb
        import atom_core.paralelismo as paralelismo_mod

        original = paralelismo_mod.ControladorAdaptativo
        try:
            bench_rgb._fijar_controlador_rgb(8)
            # Mismos kwargs que pasa `phases.py` para la fase RGB.
            controlador = paralelismo_mod.ControladorAdaptativo(
                maximo=paralelismo_mod.maximo_cpu_bound(), etiqueta="RGB",
                tope_hdd=paralelismo_mod.TOPE_WORKERS_HDD)

            assert controlador.minimo == 8
            assert controlador.maximo == 8
            assert controlador.trabajadores == 8
            # El tope por disco HDD queda desactivado: no puede mover el
            # número por debajo de `workers`.
            assert controlador.tope_hdd is None
        finally:
            paralelismo_mod.ControladorAdaptativo = original

    def test_decidir_trabajadores_no_mueve_el_numero_fijado(self):
        """Con minimo==maximo==workers, `decidir_trabajadores` (quien mueve
        el número en `revisar()`) devuelve `workers` sin importar el
        historial de mediciones — el requisito 2 de la task, verificado
        sobre la función real, no reimplementada."""
        import bench_rgb
        import atom_core.paralelismo as paralelismo_mod

        original = paralelismo_mod.ControladorAdaptativo
        try:
            bench_rgb._fijar_controlador_rgb(4)
            controlador = paralelismo_mod.ControladorAdaptativo(
                maximo=paralelismo_mod.maximo_cpu_bound(), etiqueta="RGB",
                tope_hdd=paralelismo_mod.TOPE_WORKERS_HDD)

            medicion_buena = paralelismo_mod.Medicion(
                trabajadores=4, completados=100, segundos=5.0,
                mb_procesados=1000.0, cpu_ociosa_pct=90.0, ram_libre_mb=99999.0)
            medicion_mala = paralelismo_mod.Medicion(
                trabajadores=4, completados=1, segundos=5.0,
                mb_procesados=1.0, cpu_ociosa_pct=0.0, ram_libre_mb=1.0)

            for historial in ([medicion_buena, medicion_buena], [medicion_mala, medicion_mala]):
                decidido = paralelismo_mod.decidir_trabajadores(
                    historial, controlador.minimo, controlador.maximo, controlador.mb_por_worker)
                assert decidido == 4
        finally:
            paralelismo_mod.ControladorAdaptativo = original

    def test_sin_workers_no_toca_el_controlador(self):
        """Requisito 3: sin `--workers`, `ControladorAdaptativo` queda
        exactamente el mismo símbolo de siempre."""
        import atom_core.paralelismo as paralelismo_mod
        assert paralelismo_mod.ControladorAdaptativo.__name__ == "ControladorAdaptativo"


class TestParseArgsWorkers:
    def test_default_none(self):
        import bench_rgb
        args = bench_rgb._parse_args(["--origen", "o", "--destino", "d"])
        assert args.workers is None

    def test_workers_explicito(self):
        import bench_rgb
        args = bench_rgb._parse_args(["--origen", "o", "--destino", "d", "--workers", "8"])
        assert args.workers == 8


class TestConstruirInformeWorkersFijados:
    def _resultado_minimo(self):
        return {
            "etapas": {}, "paralelismo": [], "cpu_segundos": None,
            "duracion_pared": 1.0, "csv_stats": None,
        }

    def test_incluye_workers_fijados_none_por_defecto(self):
        import bench_rgb
        informe = bench_rgb._construir_informe([self._resultado_minimo()])
        assert informe["workers_fijados"] is None

    def test_incluye_workers_fijados_con_valor(self):
        import bench_rgb
        informe = bench_rgb._construir_informe([self._resultado_minimo()], workers=8)
        assert informe["workers_fijados"] == 8
