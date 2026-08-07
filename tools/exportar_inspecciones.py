#!/usr/bin/env python3
"""Vuelca las inspecciones de la BD de Aerotools al bucket, para el organizer.

**Esto NO va dentro del `.exe`.** La app no habla con la BD: lee el objeto
`_inspecciones.json` que deja aquí este script (ver `atom_core/inspecciones.py`
para el porqué — el binario es público y las credenciales de una BD de
producción no pueden viajar dentro). Se ejecuta desde un sitio con `gcloud` y
`psql` autenticados: el PC de Cas, o un cron/Cloud Run el día que se automatice.

    python tools/exportar_inspecciones.py            # sube al bucket
    python tools/exportar_inspecciones.py --dry-run  # sólo enseña el JSON

Las credenciales salen de Secret Manager (`DB_USER`/`DB_PASSWORD`/`DB_NAME` del
proyecto `aerotools-484814`); nunca se escriben en disco ni en la línea de
comandos.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROYECTO = "aerotools-484814"
INSTANCIA = "aerotools-db"
BUCKET = "datos_para_organizar"
OBJETO = "_inspecciones.json"

# Los nombres de tabla salen de las claves ajenas reales de `inspecciones_pv`
# (comprobado 2026-08-06): `plantas_id` apunta a `plantas_pv`, NO a `plantas`
# — esa tabla no existe y darla por hecha hacía reventar el JOIN entero.
TABLA_PLANTAS = "plantas_pv"
TABLA_EMPRESAS = "empresas"

# Ninguna de las dos tiene un nombre de columna garantizado, y la tabla de
# inspecciones tampoco trae el nombre legible. En vez de fijar un nombre a
# ciegas, se busca el primero que exista de esta lista: si mañana la columna se
# llama de otra forma, el arreglo es añadirla aquí y no depurar un SQL roto.
CANDIDATAS_NOMBRE = ["nombre", "name", "denominacion", "razon_social",
                     "descripcion", "titulo", "planta", "empresa"]


def _secreto(nombre: str) -> str:
    return subprocess.run(
        ["gcloud", "secrets", "versions", "access", "latest",
         f"--secret={nombre}", f"--project={PROYECTO}"],
        check=True, capture_output=True, text=True).stdout.strip()


def _host() -> str:
    return subprocess.run(
        ["gcloud", "sql", "instances", "describe", INSTANCIA,
         f"--project={PROYECTO}", "--format=value(ipAddresses[0].ipAddress)"],
        check=True, capture_output=True, text=True).stdout.strip()


class Db:
    def __init__(self) -> None:
        self.user = _secreto("DB_USER")
        self.name = _secreto("DB_NAME")
        self._env = dict(os.environ, PGPASSWORD=_secreto("DB_PASSWORD"))
        self.host = _host()

    def consulta(self, sql: str) -> str:
        """Ejecuta y devuelve la salida cruda (`-A -t`: sin adornos)."""
        return subprocess.run(
            ["psql", "-h", self.host, "-U", self.user, "-d", self.name,
             "-w", "-A", "-t", "-c", sql],
            check=True, capture_output=True, text=True, env=self._env).stdout.strip()

    def columna_nombre(self, tabla: str) -> str | None:
        existentes = set(self.consulta(
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_schema='public' AND table_name='{tabla}'").splitlines())
        for c in CANDIDATAS_NOMBRE:
            if c in existentes:
                return c
        return None


def construir_sql(col_planta: str | None, col_empresa: str | None) -> str:
    """SQL del catálogo. Si falta la columna de nombre, se cae al id.

    Preferimos un catálogo con `planta 41` a no tener catálogo: el operador al
    menos distingue inspecciones, y el prefijo sigue siendo único.
    """
    planta = f"p.{col_planta}" if col_planta else "'planta_' || i.plantas_id"
    empresa = f"e.{col_empresa}" if col_empresa else "'empresa_' || i.empresas_id"
    return f"""
SELECT coalesce(json_agg(row_to_json(t) ORDER BY t.anio DESC, t.empresa, t.planta), '[]')
FROM (
  SELECT i.id                     AS id,
         coalesce({empresa}::text, '') AS empresa,
         coalesce({planta}::text, '')  AS planta,
         coalesce(i."a_o"::text, '')   AS anio,
         coalesce(i.tipo::text, '')    AS tipo,
         coalesce(i.fase::text, '')    AS fase
  FROM public.inspecciones_pv i
  LEFT JOIN public.{TABLA_PLANTAS}  p ON p.id = i.plantas_id
  LEFT JOIN public.{TABLA_EMPRESAS} e ON e.id = i.empresas_id
) t;
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="enseña el JSON y no sube nada")
    ap.add_argument("--bucket", default=BUCKET)
    args = ap.parse_args()

    db = Db()
    col_p = db.columna_nombre(TABLA_PLANTAS)
    col_e = db.columna_nombre(TABLA_EMPRESAS)
    if not col_p or not col_e:
        print(f"aviso: sin columna de nombre en "
              f"{TABLA_PLANTAS + ' ' if not col_p else ''}"
              f"{TABLA_EMPRESAS if not col_e else ''}"
              f" → se usará el id como nombre.", file=sys.stderr)

    filas = json.loads(db.consulta(construir_sql(col_p, col_e)) or "[]")
    payload = {"generado_por": "tools/exportar_inspecciones.py",
               "inspecciones": filas}
    texto = json.dumps(payload, ensure_ascii=False, indent=1)

    if args.dry_run:
        print(texto)
        print(f"\n{len(filas)} inspecciones (no subidas: --dry-run)", file=sys.stderr)
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / OBJETO
        local.write_text(texto, encoding="utf-8")
        subprocess.run(
            ["gcloud", "storage", "cp", str(local), f"gs://{args.bucket}/{OBJETO}"],
            check=True)
    print(f"{len(filas)} inspecciones → gs://{args.bucket}/{OBJETO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
