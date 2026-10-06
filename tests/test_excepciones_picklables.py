"""Las excepciones propias deben sobrevivir al pickle: si no, un worker del
ProcessPoolExecutor que las lanza rompe el pool (BrokenProcessPool)."""
import concurrent.futures as cf
import importlib
import inspect
import multiprocessing
import pathlib
import pickle

import pytest

from atom_core import lectura_segura as ls

_SRC = pathlib.Path(ls.__file__).resolve().parents[1]


def _clases():
    out = []
    for f in sorted(_SRC.rglob("*.py")):
        rel = f.relative_to(_SRC).with_suffix("")
        nombre = ".".join(rel.parts)
        if rel.parts[-1] == "__init__" or nombre.endswith("__main__"):
            continue
        try:
            mod = importlib.import_module(nombre)
        except Exception:
            continue
        for n, c in vars(mod).items():
            if (inspect.isclass(c) and issubclass(c, BaseException)
                    and c.__module__ == mod.__name__):
                out.append(c)
    return sorted(set(out), key=lambda c: (c.__module__, c.__name__))


def _construir(cls):
    if not any("__init__" in vars(c) for c in cls.__mro__ if c.__module__ != "builtins"):
        return cls("mensaje")
    params = [p for p in list(inspect.signature(cls).parameters.values())
              if p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    return cls(*[1 if p.name in ("leidos", "esperados", "actual", "indexado", "intentos")
                 else "x" for p in params])


@pytest.mark.parametrize("cls", _clases(), ids=lambda c: f"{c.__module__}.{c.__name__}")
def test_excepcion_propia_sobrevive_al_pickle(cls):
    exc = _construir(cls)
    copia = pickle.loads(pickle.dumps(exc))
    assert type(copia) is cls and str(copia) == str(exc)


def test_roundtrip_lectura_incompleta_y_cambiado():
    a = pickle.loads(pickle.dumps(ls.LecturaIncompleta("r.jpg", 3, 9, 5)))
    assert (a.ruta, a.leidos, a.esperados, a.intentos) == ("r.jpg", 3, 9, 5)
    b = pickle.loads(pickle.dumps(ls.FicheroCambiado("r.jpg", 7, 8)))
    assert (b.ruta, b.actual, b.indexado) == ("r.jpg", 7, 8)


def _lanza(tipo):
    if tipo == "inc":
        raise ls.LecturaIncompleta("r.jpg", 1, 2, 5)
    raise ls.FicheroCambiado("r.jpg", 1, 2)


@pytest.mark.parametrize("tipo,cls", [("inc", ls.LecturaIncompleta), ("cam", ls.FicheroCambiado)])
def test_pool_spawn_propaga_la_excepcion_original(tipo, cls):
    ctx = multiprocessing.get_context("spawn")
    with cf.ProcessPoolExecutor(max_workers=1, mp_context=ctx) as ex:
        with pytest.raises(cls):
            ex.submit(_lanza, tipo).result(timeout=120)
