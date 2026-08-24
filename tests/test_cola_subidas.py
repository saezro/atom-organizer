import time

from atom_core import cola_subidas


def test_encolar_y_leer(tmp_path):
    ruta = tmp_path / "cola.json"
    job = cola_subidas.encolar("/datos/vuelo1", "PLANTA/2026", 42, ruta=ruta)
    assert job["folder"] == "/datos/vuelo1"
    assert job["prefix"] == "PLANTA/2026"
    assert job["inspeccion_id"] == 42
    assert job["intentos"] == 0
    assert cola_subidas.pendientes(ruta=ruta) == [job]


def test_cola_vacia_si_no_hay_fichero(tmp_path):
    assert cola_subidas.pendientes(ruta=tmp_path / "no-existe.json") == []


def test_fichero_corrupto_se_trata_como_vacia(tmp_path):
    # Un corte de corriente a media escritura no debe impedir arrancar.
    ruta = tmp_path / "cola.json"
    ruta.write_text("{esto no es json", encoding="utf-8")
    assert cola_subidas.pendientes(ruta=ruta) == []


def test_encolar_la_misma_carpeta_no_duplica(tmp_path):
    # El operario pulsa "subir" dos veces sin credencial: es un trabajo, no dos.
    ruta = tmp_path / "cola.json"
    a = cola_subidas.encolar("/datos/vuelo1", "PLANTA/2026", 42, ruta=ruta)
    b = cola_subidas.encolar("/datos/vuelo1", "PLANTA/2026", 42, ruta=ruta)
    assert a["id"] == b["id"]
    assert len(cola_subidas.pendientes(ruta=ruta)) == 1


def test_misma_carpeta_distinto_prefijo_son_trabajos_distintos(tmp_path):
    ruta = tmp_path / "cola.json"
    cola_subidas.encolar("/datos/vuelo1", "PLANTA_A/2026", None, ruta=ruta)
    cola_subidas.encolar("/datos/vuelo1", "PLANTA_B/2026", None, ruta=ruta)
    assert len(cola_subidas.pendientes(ruta=ruta)) == 2


def test_descartar(tmp_path):
    ruta = tmp_path / "cola.json"
    job = cola_subidas.encolar("/datos/vuelo1", "PLANTA/2026", None, ruta=ruta)
    assert cola_subidas.descartar(job["id"], ruta=ruta) is True
    assert cola_subidas.pendientes(ruta=ruta) == []
    assert cola_subidas.descartar(job["id"], ruta=ruta) is False


def test_marcar_intento_suma_y_guarda_el_error(tmp_path):
    ruta = tmp_path / "cola.json"
    job = cola_subidas.encolar("/datos/vuelo1", "PLANTA/2026", None, ruta=ruta)
    cola_subidas.marcar_intento(job["id"], "sin red", ruta=ruta)
    cola_subidas.marcar_intento(job["id"], "sin red", ruta=ruta)
    p = cola_subidas.pendientes(ruta=ruta)[0]
    assert p["intentos"] == 2
    assert p["ultimo_error"] == "sin red"


def test_orden_de_llegada(tmp_path):
    ruta = tmp_path / "cola.json"
    cola_subidas.encolar("/a", "P/2026", None, ruta=ruta)
    cola_subidas.encolar("/b", "P/2026", None, ruta=ruta)
    assert [j["folder"] for j in cola_subidas.pendientes(ruta=ruta)] == ["/a", "/b"]


def test_encolar_concurrente_no_pierde_jobs(tmp_path, monkeypatch):
    """8 hilos encolando carpetas distintas a la vez sobre la misma cola:
    al final deben estar los 8 jobs, no menos. Sin lock, el read-modify-write
    de _escribir pisa jobs de otros hilos (lost update) y el test falla."""
    import threading

    ruta = tmp_path / "cola.json"
    n_hilos = 8
    barrera = threading.Barrier(n_hilos)

    escribir_original = cola_subidas._escribir

    def escribir_lento(ruta_arg, jobs):
        # Ensancha la ventana de carrera entre leer y escribir de forma
        # determinista, para no depender de la suerte del scheduler.
        time.sleep(0.02)
        escribir_original(ruta_arg, jobs)

    monkeypatch.setattr(cola_subidas, "_escribir", escribir_lento)

    errores = []

    def trabajo(i):
        try:
            barrera.wait()
            cola_subidas.encolar(f"/datos/vuelo{i}", "PLANTA/2026", i, ruta=ruta)
        except Exception as exc:  # pragma: no cover - solo para diagnóstico
            errores.append(exc)

    hilos = [threading.Thread(target=trabajo, args=(i,)) for i in range(n_hilos)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert not errores
    assert len(cola_subidas.pendientes(ruta=ruta)) == n_hilos
