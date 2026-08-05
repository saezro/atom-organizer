"""
El sentinel `__tif_rot_mode`: distinguir «Sin giro» de «front viejo».

BUG QUE FIJA ESTO (v3.4.5). Con v3.4.4 el log de una corrida real de Daniel
mostró `convert_to_tiff_rotate_auto: False` dentro de `[advanced]`, y ese dato
NO cierra el diagnóstico: el dict que manda el front es EXACTAMENTE el mismo en
dos escenarios opuestos —

  1. el usuario eligió «Sin giro» en el desplegable (comportamiento correcto), y
  2. el front es anterior a v3.4.3, donde `initialState` forzaba el índice 0 de
     todos los selects y por tanto mandaba los tres flags a false sin que el
     usuario tocara nada (bug).

Los tres bools a false son indistinguibles, así que el backend no podía elegir
entre respetar la intención o proteger su default. `__tif_rot_mode` rompe el
empate: viaja en `advanced`, solo lo manda el front nuevo, y NO existe en
`SplitImagesConfig` (la coerción de `run_task` lo descarta por `k in hints`).
Si no llega, la elección no es expresable -> se ignoran los flags de giro y
manda `_default_split_config` (auto=True).
"""
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(REPO, "webui", "src", "schema.js")


def _bloque_select(fuente, nombre):
    i = fuente.index(f"name: '{nombre}', type: 'select'")
    return fuente[i:fuente.index("\n      },", i)]


def test_todas_las_opciones_de_giro_mandan_el_sentinel():
    """
    Si una sola opción se queda sin `__tif_rot_mode`, el backend la lee como
    «front viejo» y la sobreescribe con auto: elegir «Sin giro» dejaría de
    funcionar en silencio.
    """
    bloque = _bloque_select(open(SCHEMA, encoding="utf-8").read(), "__tif_rot")
    opciones = re.findall(r"\{ label: '([^']+)', params: \{([^}]*)\} \}", bloque)
    assert len(opciones) == 4, f"Se esperaban 4 opciones de giro, hay {len(opciones)}."
    for label, params in opciones:
        assert "__tif_rot_mode:" in params, (
            f"La opción '{label}' de __tif_rot no manda __tif_rot_mode: el backend "
            "la tratará como build antigua y forzará el giro automático."
        )


def test_sin_sentinel_los_flags_de_giro_del_front_se_ignoran():
    """El escenario del log de v3.4.4: build vieja mandando los tres a false."""
    from utils import resolve_tif_rotation_intent

    limpio, aviso = resolve_tif_rotation_intent({
        "end_thermo_files": "_T",
        "convert_to_tiff_rotate_auto": False,
        "convert_to_tiff_rotate_90": False,
        "convert_to_tiff_rotate_minus_90": False,
    })
    assert not any(k.startswith("convert_to_tiff_rotate") for k in limpio), (
        "Un front sin __tif_rot_mode sigue pisando el default del backend: el TIFF "
        "volvería a salir sin girar, y su JPG térmico también."
    )
    assert limpio["end_thermo_files"] == "_T", "Se han tocado ajustes que no son de giro."
    assert aviso and "__tif_rot_mode" in aviso, "El descarte tiene que quedar en el log."


def test_con_sentinel_se_respeta_la_eleccion_del_usuario():
    """Con sentinel manda el usuario, incluido «Sin giro»."""
    from utils import resolve_tif_rotation_intent

    for modo, flags in (
        ("none", {"convert_to_tiff_rotate_auto": False, "convert_to_tiff_rotate_90": False,
                  "convert_to_tiff_rotate_minus_90": False}),
        ("90", {"convert_to_tiff_rotate_auto": False, "convert_to_tiff_rotate_90": True,
                "convert_to_tiff_rotate_minus_90": False}),
        ("auto", {"convert_to_tiff_rotate_auto": True, "convert_to_tiff_rotate_90": False,
                  "convert_to_tiff_rotate_minus_90": False}),
    ):
        entrada = {"__tif_rot_mode": modo, **flags}
        limpio, aviso = resolve_tif_rotation_intent(entrada)
        assert limpio == entrada, f"El modo {modo!r} no llega intacto al backend."
        assert aviso is None


def test_el_sentinel_no_es_un_campo_del_dataclass():
    """
    No es un ajuste: si alguien lo añade a SplitImagesConfig, `replace()` lo
    escribiría en el cfg y dejaría de ser un canal de metadatos.
    """
    from utils import TIF_ROTATION_INTENT_KEY, SplitImagesConfig

    assert TIF_ROTATION_INTENT_KEY not in SplitImagesConfig.__annotations__


def test_advanced_sin_flags_de_giro_no_se_toca():
    from utils import resolve_tif_rotation_intent

    entrada = {"end_thermo_files": "_T", "compress_level": "40"}
    limpio, aviso = resolve_tif_rotation_intent(entrada)
    assert limpio == entrada and aviso is None
