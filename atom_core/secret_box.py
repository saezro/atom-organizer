"""Cifrado autenticado de secretos pequeños, solo con la stdlib.

Existe porque el `refresh_token` de Google pasa a guardarse en la BD local de
sesión (`session_store.py`) y ahí ya no vale con dejarlo en claro.

**Por qué no `cryptography`.** Es la respuesta obvia y aquí sería la equivocada:
`atom_core` es el core headless y se empaqueta con PyInstaller; meter
`cryptography` arrastra `cffi` y un binario de OpenSSL al bundle, por un secreto
de 100 bytes. La misma razón por la que `updater.py`, `cloud_upload.py` y
`google_auth.py` hablan HTTP con `urllib` en vez de `requests`.

**Qué se construye.** No hay cifrador de bloque en la stdlib, pero sí HMAC-SHA256,
que es un PRF perfectamente respetable. Con él se monta el esquema clásico:

    keystream = HMAC(k_enc, contador)  ‖  HMAC(k_enc, contador+1)  ‖ …
    ciphertext = claro XOR keystream                       (stream cipher en modo contador)
    tag = HMAC(k_mac, cabecera ‖ nonce ‖ ciphertext ‖ aad)  (encrypt-then-MAC)

`k_enc` y `k_mac` salen de HKDF-SHA256 sobre la clave maestra, con el nonce como
sal: son distintas en cada sobre, así que reutilizar una clave maestra para
muchos secretos no acerca dos keystreams. Encrypt-then-MAC (no MAC-then-encrypt)
es el orden que la literatura da por seguro, y el tag se compara con
`hmac.compare_digest` para no filtrar por tiempo cuánto prefijo cuadraba.

**El punto débil está fuera de aquí.** Esto cifra bien; lo que decide la
seguridad real es dónde vive la clave maestra, y eso lo elige `session_store`
(DPAPI del usuario en Windows; fichero 0600 en Linux). Ver allí la nota honesta
sobre contra qué protege cada uno.
"""
from __future__ import annotations

import hmac
import os
import secrets
from hashlib import sha256

__all__ = ["sellar", "abrir", "SobreInvalido", "clave_nueva", "TAM_CLAVE"]

# Marca de formato. Va dentro del MAC: cambiar de esquema en el futuro no puede
# ser algo que un atacante decida por nosotros recortando el prefijo.
CABECERA = b"ATOMBOX1"

TAM_CLAVE = 32
TAM_NONCE = 16
TAM_TAG = 32
_BLOQUE = sha256().digest_size  # 32


class SobreInvalido(ValueError):
    """El sobre no se puede abrir: truncado, manipulado, o clave que no es."""


def clave_nueva() -> bytes:
    """Clave maestra aleatoria, del tamaño nativo de HMAC-SHA256."""
    return secrets.token_bytes(TAM_CLAVE)


def _hkdf(clave: bytes, sal: bytes, info: bytes, largo: int = TAM_CLAVE) -> bytes:
    """HKDF-SHA256 (RFC 5869), extract + expand, en su versión corta.

    Solo se piden 32 bytes, así que basta una vuelta del expand; se deja el
    bucle igual porque un `largo` mayor en el futuro no debe silenciosamente
    devolver una clave más corta de lo pedido.
    """
    prk = hmac.new(sal, clave, sha256).digest()
    salida = b""
    bloque = b""
    contador = 1
    while len(salida) < largo:
        bloque = hmac.new(prk, bloque + info + bytes([contador]), sha256).digest()
        salida += bloque
        contador += 1
    return salida[:largo]


def _keystream(k_enc: bytes, largo: int) -> bytes:
    trozos = []
    generado = 0
    contador = 0
    while generado < largo:
        trozos.append(hmac.new(k_enc, contador.to_bytes(4, "big"), sha256).digest())
        generado += _BLOQUE
        contador += 1
    return b"".join(trozos)[:largo]


def _subclaves(clave: bytes, nonce: bytes) -> tuple[bytes, bytes]:
    return (_hkdf(clave, nonce, b"atom-organizer/enc"),
            _hkdf(clave, nonce, b"atom-organizer/mac"))


def sellar(clave: bytes, claro: bytes, *, aad: bytes = b"") -> bytes:
    """Cifra y autentica `claro`. Devuelve el sobre completo, listo para el BLOB.

    `aad` se autentica pero no se cifra: sirve para atar el sobre a su contexto
    (aquí, el nombre de la fila) y que no se pueda mover un valor cifrado de un
    sitio a otro de la BD sin que el MAC lo note.
    """
    if len(clave) != TAM_CLAVE:
        raise ValueError(f"la clave maestra debe medir {TAM_CLAVE} bytes")
    nonce = os.urandom(TAM_NONCE)
    k_enc, k_mac = _subclaves(clave, nonce)
    cifrado = bytes(a ^ b for a, b in zip(claro, _keystream(k_enc, len(claro))))
    tag = hmac.new(k_mac, CABECERA + nonce + cifrado + aad, sha256).digest()
    return CABECERA + nonce + cifrado + tag


def abrir(clave: bytes, sobre: bytes, *, aad: bytes = b"") -> bytes:
    """Comprueba el MAC y descifra. Lanza `SobreInvalido` si algo no cuadra.

    Se verifica **antes** de descifrar: nunca se devuelve texto que no haya
    pasado el MAC, ni siquiera para inspeccionarlo.
    """
    if len(clave) != TAM_CLAVE:
        raise ValueError(f"la clave maestra debe medir {TAM_CLAVE} bytes")
    minimo = len(CABECERA) + TAM_NONCE + TAM_TAG
    if len(sobre) < minimo or not sobre.startswith(CABECERA):
        raise SobreInvalido("el sobre no tiene el formato esperado")

    nonce = sobre[len(CABECERA):len(CABECERA) + TAM_NONCE]
    cifrado = sobre[len(CABECERA) + TAM_NONCE:-TAM_TAG]
    tag = sobre[-TAM_TAG:]

    k_enc, k_mac = _subclaves(clave, nonce)
    esperado = hmac.new(k_mac, CABECERA + nonce + cifrado + aad, sha256).digest()
    if not hmac.compare_digest(tag, esperado):
        raise SobreInvalido("el contenido cifrado no supera la comprobación de integridad")

    return bytes(a ^ b for a, b in zip(cifrado, _keystream(k_enc, len(cifrado))))
