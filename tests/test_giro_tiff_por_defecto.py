"""
El giro del TIFF térmico tiene que venir puesto de fábrica, igual que el
criterio de rotación (ver test_criterio_rotacion_por_defecto.py).

BUG QUE FIJA ESTO (v3.4.3): `initialState` de TaskBlock.jsx inicializaba TODOS
los selects al índice 0, ignorando el `default` declarado en schema.js. El
select `__tif_rot` declara default 1 (= "Auto"), pero arrancaba en 0
(= "Sin giro") y por tanto el front metía en `advanced`
`{convert_to_tiff_rotate_auto: false, ...}`. Ese objeto PISA los defaults de
`_default_split_config` (organize.py: `cfg = replace(cfg, **coerced)`), así que
el usuario no tenía forma de que el TIFF se girara sin abrir el panel avanzado
y tocar el desplegable: el TIFF salía 640x512 (como el JPG crudo) en vez de
512x640, y tampoco se giraban los JPG térmicos. Mismo patrón que el bug de
emissivity/humidity a 0.0.

Es un bug PREEXISTENTE: no se pudo ver hasta que dejó de fallar el -16 y
empezaron a generarse TIFFs (v3.4.2).
"""
import json
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCHEMA = os.path.join(REPO, "webui", "src", "schema.js")
TASKBLOCK = os.path.join(REPO, "webui", "src", "TaskBlock.jsx")
ORGANIZE = os.path.join(REPO, "atom_core", "organize.py")


def _leer(path):
    return open(path, encoding="utf-8").read()


def _bloque_select(fuente, nombre):
    """Devuelve el texto del literal del select `nombre` en schema.js."""
    i = fuente.index(f"name: '{nombre}', type: 'select'")
    # Hasta el cierre del objeto: la línea `},` a la misma indentación del `{`.
    fin = fuente.index("\n      },", i)
    return fuente[i:fin]


def test_initial_state_respeta_el_default_de_los_selects():
    """
    La regresión concreta: `s[f.name] = 0` a pelo. Si vuelve, cualquier select
    con `default` declarado en schema.js arranca en la opción equivocada y su
    valor se cuela en `advanced`, pisando el backend.
    """
    fuente = _leer(TASKBLOCK)
    asignacion = re.search(r"f\.type === 'select'\)\s*s\[f\.name\]\s*=\s*(.+)", fuente)
    assert asignacion, "No se localizó la inicialización de los selects en TaskBlock.jsx."
    valor = asignacion.group(1).split("//")[0].strip()
    assert "f.default" in valor, (
        f"initialState inicializa los selects a `{valor}`: ignora el `default` de "
        "schema.js y el select manda su opción 0 dentro de `advanced`."
    )


def test_el_select_de_giro_del_tif_arranca_en_auto():
    fuente = _leer(SCHEMA)
    bloque = _bloque_select(fuente, "__tif_rot")

    default = re.search(r"default:\s*(\d+)", bloque)
    assert default, "El select __tif_rot ya no declara `default`: arrancaría en 'Sin giro'."
    idx = int(default.group(1))

    opciones = re.findall(r"\{ label: '([^']+)', params: \{([^}]*)\} \}", bloque)
    assert idx < len(opciones), f"default {idx} fuera de rango ({len(opciones)} opciones)."
    label, params = opciones[idx]
    assert "convert_to_tiff_rotate_auto: true" in params, (
        f"La opción por defecto de __tif_rot es '{label}', que no activa el giro "
        "automático. Ni el TIFF térmico ni su JPG saldrían girados."
    )


def test_el_default_del_front_coincide_con_el_del_backend():
    """
    Los dos lados tienen que decir lo mismo. Si el backend deja de traer
    rotate_auto=True, o el front deja de mandarlo, el TIFF vuelve a no girar sin
    que nadie lo note (el criterio de giro se calcula bien igualmente).
    """
    fuente = _leer(ORGANIZE)
    bloque = fuente[fuente.index("def _default_split_config"):]
    bloque = bloque[:bloque.index("\n\n\n")]
    asignacion = re.search(r"convert_to_tiff_rotate_auto\s*=\s*(\w+)", bloque)
    assert asignacion, "No se encontró convert_to_tiff_rotate_auto en _default_split_config."
    assert asignacion.group(1) == "True", (
        f"_default_split_config trae convert_to_tiff_rotate_auto={asignacion.group(1)}: "
        "el TIFF térmico no se giraría."
    )


# --- v3.4.4: que el "no giro" deje de ser silencioso ------------------------
def test_sin_flags_de_giro_se_dice_en_el_log(tmp_path, logger):
    """
    Con los tres flags a False no se gira nada — correcto — pero
    antes se salía con un `return 0` MUDO. Desde el log era indistinguible de
    "el criterio salió 0" o de "el paso ni corrió", y costó una ronda entera de
    diagnóstico con v3.4.3. Ahora tiene que decirlo.
    """
    import types

    import pipeline as gen_struct_folder

    mensajes = []
    cb = types.SimpleNamespace(emit=lambda payload=None, *a, **k: mensajes.append(payload))

    obj = gen_struct_folder.SplitImages(logger)
    giradas = obj.rotate_thermal_jpgs_in_place(str(tmp_path), cb, cb, False, False, False)

    assert giradas == 0
    assert any(isinstance(m, str) and "Sin giro" in m for m in mensajes), (
        "rotate_thermal_jpgs_in_place vuelve a salir en silencio con los flags a False."
    )
