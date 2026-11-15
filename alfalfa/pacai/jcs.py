# Derivado del SDK de Python de PACAI (Apache-2.0); ver NOTICE y LICENSE en esta carpeta.
"""Canonización JSON (JCS, RFC 8785) y digest de aprobaciones (§4.5).

Solo usa la biblioteca estándar. Los números se escriben como en ECMAScript
(``Number.prototype.toString``) y las claves se ordenan por unidades UTF-16.
"""
from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from typing import Any

__all__ = ['canonicalize', 'digest']


def _number(v: float) -> str:
    if math.isnan(v) or math.isinf(v):
        raise ValueError('JCS no admite NaN ni infinitos')
    if v == 0:
        return '0'  # también -0
    signo = '-' if v < 0 else ''
    # repr() da los dígitos más cortos que reproducen el double, como ECMAScript.
    _, tupla, exp = Decimal(repr(abs(v))).as_tuple()
    digitos = ''.join(map(str, tupla)).lstrip('0')
    exp += len(digitos) - len(digitos.rstrip('0'))
    digitos = digitos.rstrip('0')
    k, n = len(digitos), len(digitos) + exp  # valor = 0.d1…dk × 10^n
    if k <= n <= 21:
        cuerpo = digitos + '0' * (n - k)
    elif 0 < n <= 21:
        cuerpo = digitos[:n] + '.' + digitos[n:]
    elif -6 < n <= 0:
        cuerpo = '0.' + '0' * (-n) + digitos
    else:
        e = n - 1
        cuerpo = digitos[0] + ('.' + digitos[1:] if k > 1 else '') + 'e' + ('+' if e > 0 else '-') + str(abs(e))
    return signo + cuerpo


def _clave_utf16(k: str) -> bytes:
    return k.encode('utf-16-be', 'surrogatepass')


def canonicalize(valor: Any) -> str:
    """Devuelve el texto JCS de un valor JSON de Python."""
    if valor is None:
        return 'null'
    if valor is True:
        return 'true'
    if valor is False:
        return 'false'
    if isinstance(valor, int):
        return str(valor) if abs(valor) < 2 ** 53 else _number(float(valor))
    if isinstance(valor, float):
        return _number(valor)
    if isinstance(valor, str):
        return json.dumps(valor, ensure_ascii=False)
    if isinstance(valor, (list, tuple)):
        return '[' + ','.join(canonicalize(x) for x in valor) + ']'
    if isinstance(valor, dict):
        if not all(isinstance(k, str) for k in valor):
            raise TypeError('las claves JSON deben ser cadenas')
        return '{' + ','.join(json.dumps(k, ensure_ascii=False) + ':' + canonicalize(valor[k])
                              for k in sorted(valor, key=_clave_utf16)) + '}'
    raise TypeError(f'tipo no JSON: {type(valor).__name__}')


def digest(arguments: Any) -> str:
    """``sha256:<hex>`` de los argumentos canonizados con JCS (§4.5)."""
    return 'sha256:' + hashlib.sha256(canonicalize(arguments).encode('utf-8')).hexdigest()
