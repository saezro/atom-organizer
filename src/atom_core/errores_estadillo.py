"""Error de datos de un estadillo, con todo lo que el operador necesita para
corregirlo: qué falla y de qué fichero viene. Sin dependencias (lo importan
`estadillo`, `indice` y `pipeline` sin riesgo de ciclo)."""
from __future__ import annotations

import datetime
import os
import re

FORMATO_FECHA = "AAAA:MM:DD"
FORMATO_HORA = "HH:MM:SS"
_RE_HORA = re.compile(r"^\d{1,2}:\d{2}:\d{2}$")
_RE_FECHA = re.compile(r"^\d{4}:\d{2}:\d{2}$")


class ErrorEstadillo(ValueError):
    """Dato inválido de un estadillo. `__str__` es legible tal cual para la UI."""

    def __init__(self, detalle: str, *, ruta: str | None = None, fila=None,
                 pb=None, vuelo=None, columna: str | None = None,
                 valor=None, esperado: str | None = None):
        self.detalle = detalle
        self.ruta = ruta
        self.fichero = os.path.basename(ruta) if ruta else None
        self.fila = fila
        self.pb = pb
        self.vuelo = vuelo
        self.columna = columna
        self.valor = valor
        self.esperado = esperado
        super().__init__(self._texto())

    def __reduce__(self):
        return (_reconstruir, (self.detalle, self.ruta, self.fila, self.pb, self.vuelo,
                               self.columna, self.valor, self.esperado))

    def _texto(self) -> str:
        partes = [f"Estadillo '{self.fichero or 'desconocido'}': {self.detalle}."]
        if self.fila is not None:
            partes.append(f"Fila {self.fila}.")
        if self.pb is not None or self.vuelo is not None:
            partes.append(f"PB {self.pb}, vuelo {self.vuelo}.")
        if self.columna:
            partes.append(f"Columna '{self.columna}'.")
        if self.valor is not None:
            partes.append(f"Valor recibido: '{self.valor}'.")
        if self.esperado:
            partes.append(f"Formato esperado: {self.esperado}.")
        if self.ruta:
            partes.append(f"Ruta: {self.ruta}.")
        partes.append("Corrígelo en el estadillo.")
        return " ".join(partes)


def _reconstruir(detalle, ruta, fila, pb, vuelo, columna, valor, esperado):
    return ErrorEstadillo(detalle, ruta=ruta, fila=fila, pb=pb, vuelo=vuelo,
                          columna=columna, valor=valor, esperado=esperado)


def contexto_fila(df, indice: int, nombres_columnas: dict) -> dict:
    """Contexto (fichero, fila, PB, vuelo) de la fila `indice` de un estadillo
    combinado. `fila` = nº de fila de datos en su fichero (1 = primera)."""
    ctx: dict = {}
    try:
        ruta = df["_estadillo_origen_ruta"].iloc[indice] if "_estadillo_origen_ruta" in df.columns else None
        if ruta is None and "_estadillo_origen" in df.columns:
            ruta = df["_estadillo_origen"].iloc[indice]
        if ruta is not None:
            ctx["ruta"] = str(ruta)
            col = "_estadillo_origen_ruta" if "_estadillo_origen_ruta" in df.columns else "_estadillo_origen"
            ctx["fila"] = int((df[col].iloc[:indice + 1] == df[col].iloc[indice]).sum())
        ctx["pb"] = str(df[nombres_columnas["PB"]].iloc[indice]).strip()
        ctx["vuelo"] = str(df[nombres_columnas["Vuelo"]].iloc[indice]).strip()
    except Exception:  # contexto best-effort: nunca debe tapar el error real
        pass
    return ctx


def validar_fecha(valor, *, columna="Fecha", **ctx) -> str:
    v = str(valor).strip()
    if not _RE_FECHA.match(v):
        raise ErrorEstadillo("fecha con formato inválido", columna=columna, valor=valor,
                             esperado=FORMATO_FECHA, **ctx)
    try:
        datetime.datetime.strptime(v, "%Y:%m:%d")
    except ValueError:
        raise ErrorEstadillo("fecha inexistente", columna=columna, valor=valor,
                             esperado=FORMATO_FECHA, **ctx) from None
    return v


def validar_hora(valor, *, columna, **ctx) -> str:
    v = str(valor).strip()
    ok = bool(_RE_HORA.match(v))
    if ok:
        try:
            datetime.datetime.strptime(v, "%H:%M:%S")
        except ValueError:
            ok = False
    if not ok:
        raise ErrorEstadillo("hora con formato inválido", columna=columna, valor=valor,
                             esperado=FORMATO_HORA, **ctx)
    return v
