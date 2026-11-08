"""`$ALFALFA_HOME/proveedores.json`: lo que no es secreto de los proveedores.

Guarda el proveedor por defecto y las URL (la de Ollama y la del servicio
Alfalfa, que es configurable porque el repositorio es público). Las claves y el
código de cortesía van en el Llavero (`llavero.py`), nunca aquí.

    {"por_defecto": "ollama",
     "ollama": {"url": "http://127.0.0.1:11434"},
     "openai": {"modelos": ["gpt-6-luna"]},
     "alfalfa": {"url": "https://alfalfa.example.com"}}
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import PREFIJOS

ARCHIVO = 'proveedores.json'
CLAVES_SECRETAS = ('clave', 'key', 'api_key', 'token', 'codigo', 'secret')


# Las pruebas lo apuntan a una carpeta temporal para no leer la del usuario.
_casa = None


def ruta():
    casa = os.environ.get('ALFALFA_HOME') or _casa or Path.home() / '.alfalfa'
    return Path(casa).expanduser().absolute() / ARCHIVO


def leer():
    """El contenido, o {} si no hay archivo o no se entiende."""
    try:
        valor = json.loads(ruta().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return valor if isinstance(valor, dict) else {}


def _sin_secretos(valor):
    if isinstance(valor, dict):
        for clave, dentro in valor.items():
            if str(clave).lower() in CLAVES_SECRETAS:
                raise ValueError('Las claves van en el Llavero, no en ' + ARCHIVO + '.')
            _sin_secretos(dentro)
    elif isinstance(valor, list):
        for dentro in valor:
            _sin_secretos(dentro)


def escribir(valor):
    if not isinstance(valor, dict):
        raise ValueError(ARCHIVO + ' debe ser un objeto.')
    _sin_secretos(valor)
    destino = ruta()
    destino.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporal = destino.with_suffix('.tmp')
    temporal.write_text(json.dumps(valor, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.chmod(temporal, 0o600)
    os.replace(temporal, destino)


def de(proveedor):
    """La sección de un proveedor (un diccionario, quizá vacío)."""
    valor = leer().get(proveedor)
    return valor if isinstance(valor, dict) else {}


def por_defecto():
    valor = leer().get('por_defecto')
    return valor if valor in PREFIJOS else 'ollama'


def cambiar(proveedor, campo, valor):
    """Fija `proveedor.campo` (o `por_defecto` con proveedor None) y lo guarda."""
    datos = leer()
    if proveedor is None:
        if valor not in PREFIJOS:
            raise ValueError('Proveedor desconocido: ' + str(valor))
        datos[campo] = valor
    else:
        if proveedor not in PREFIJOS:
            raise ValueError('Proveedor desconocido: ' + str(proveedor))
        seccion = datos.get(proveedor) if isinstance(datos.get(proveedor), dict) else {}
        if valor is None:
            seccion.pop(campo, None)
        else:
            seccion[campo] = valor
        datos[proveedor] = seccion
    escribir(datos)
