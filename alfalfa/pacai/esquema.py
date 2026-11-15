"""Validación de mensajes contra el esquema JSON de PACAI 0.2.

Derivado del SDK de Python de PACAI (Apache-2.0); ver NOTICE y LICENSE en esta carpeta.
Usa `jsonschema`, que ya es dependencia de AlfalfaCode.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

__all__ = ['esquema', 'validar', 'es_valido']


@lru_cache(maxsize=1)
def esquema() -> dict:
    return json.loads((Path(__file__).with_name('pacai-0.2.schema.json')).read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def _validador():
    import jsonschema

    return jsonschema.Draft202012Validator(esquema(), format_checker=jsonschema.FormatChecker())


def validar(mensaje: Mapping[str, Any]) -> list[str]:
    """Los errores de esquema de un mensaje; lista vacía si es válido."""
    errores = sorted(_validador().iter_errors(mensaje), key=lambda e: list(e.absolute_path))
    return [e.message for e in errores]


def es_valido(mensaje: Mapping[str, Any]) -> bool:
    return not validar(mensaje)
