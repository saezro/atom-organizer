"""Mete las credenciales del cliente OAuth en `cloud_config.py` al construir.

El repo es PÚBLICO, así que el `client_secret` no puede estar en el código: el
secret-scanning de GitHub avisa a Google y Google revoca el cliente — la app se
quedaría sin login sin que nadie hubiera tocado nada. Vive en los *secrets* del
repo y sólo aterriza en el árbol de trabajo del runner, ya en el build.

Lo llama el workflow de release con
`ATOM_GOOGLE_CLIENT_ID` / `ATOM_GOOGLE_CLIENT_SECRET` en el entorno. Si no están
no falla: sale avisando y el `.exe` queda sin credenciales embebidas (la app
seguirá aceptando `google_client.json` junto al ejecutable).

Uso:  python scripts/inject_oauth_client.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

DESTINO = Path(__file__).resolve().parent.parent / "atom_core" / "cloud_config.py"


def main() -> int:
    cid = os.environ.get("ATOM_GOOGLE_CLIENT_ID", "").strip()
    sec = os.environ.get("ATOM_GOOGLE_CLIENT_SECRET", "").strip()
    if not cid or not sec:
        print("[oauth] sin secrets en el entorno: no se inyecta nada "
              "(la app pedirá google_client.json).")
        return 0

    if '"' in cid or '"' in sec or "\\" in cid or "\\" in sec:
        print("[oauth] ERROR: la credencial trae comillas o barras; "
              "no se inyecta para no romper el fuente.", file=sys.stderr)
        return 1

    src = DESTINO.read_text(encoding="utf-8")
    for nombre, valor in (("_BUILD_CLIENT_ID", cid), ("_BUILD_CLIENT_SECRET", sec)):
        viejo = f'{nombre} = ""'
        if viejo not in src:
            print(f"[oauth] ERROR: no encuentro «{viejo}» en {DESTINO.name}.",
                  file=sys.stderr)
            return 1
        src = src.replace(viejo, f'{nombre} = "{valor}"', 1)

    DESTINO.write_text(src, encoding="utf-8")
    print(f"[oauth] credenciales inyectadas en {DESTINO.name} "
          f"(client_id …{cid[-12:]}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
