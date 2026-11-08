"""Validación JSON Schema sin red para esquemas que llegan de un servidor MCP.

jsonschema (>= 4.18) resuelve por defecto un $ref desconocido descargándolo con
urllib.request.urlopen (validators._REMOTE_WARNING_REGISTRY), y
jsonschema.validate, que el SDK usa para outputSchema, también. Un servidor
stdio con red: false podría así hacer que este proceso, fuera del sandbox,
abra una conexión a la URL que quiera. Aquí el registro sólo conoce los
metaesquemas incluidos en jsonschema y sus $ref internos; un $ref externo es
irresoluble y la validación falla en vez de descargar nada.
"""
from __future__ import annotations


def _sin_red(uri):
    from referencing.exceptions import NoSuchResource
    raise NoSuchResource(ref=uri)


def validador(esquema):
    """Validador comprobado contra su metaesquema; nunca descarga un $ref."""
    from jsonschema import Draft202012Validator, validators
    from referencing import Registry
    clase = validators.validator_for(esquema, default=Draft202012Validator)
    clase.check_schema(esquema)
    return clase(esquema, registry=Registry(retrieve=_sin_red))
