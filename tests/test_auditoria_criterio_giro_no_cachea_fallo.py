"""Auditoria CRITICO 5 (parte segura): un fallo al leer el criterio de giro no
se memoiza. Si el CSV aparece despues, el vuelo se gira bien en vez de quedarse
en 0 toda la corrida."""
import pipeline


class _P:
    def emit(self, *a, **k):
        pass


def test_fallo_de_lectura_no_se_cachea(tmp_path):
    from utils import OrganizerLogger as ol
    obj = pipeline.SplitImages(ol("t_giro", log_dir=str(tmp_path / "L"), create_file_handler=False))
    vuelo = tmp_path / "TERMICA" / "PB1" / "PB1_V1"
    vuelo.mkdir(parents=True)
    assert obj.read_auto_rotate_degree(str(vuelo), _P()) == 0  # CSV aun sin escribir
    csvs = tmp_path / "CSVs" / pipeline.utils.CRITERIO_DIRNAME
    csvs.mkdir(parents=True)
    (csvs / "PB1_V1_Videofiles.csv").write_text("Degree\n90\n", encoding="utf-8")
    assert obj.read_auto_rotate_degree(str(vuelo), _P()) == 90
