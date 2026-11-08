"""Lectura pública de la evidencia nativa: si una comprobación vale y si sigue vigente.

Antes el estudio llamaba a privados de `RunEvidence` (`_check_sufficient`,
`_check_is_current`, `_classification`). La regla vive aquí, sin depender del
runtime, y esos métodos delegan en estas funciones para que el resultado sea el
mismo por cualquiera de los dos caminos.
"""
from __future__ import annotations

import re

from . import espacio
from .operaciones import classify_operation


def salida_incompleta(registro):
    """¿La salida se cortó sin que el runtime garantice que la orden terminó?

    Un corte antiguo o ambiguo sigue sin valer. Sólo la ejecución completa más
    un recorte explícito al guardar lo distinguen de una salida interrumpida.
    """
    return bool(registro.get('truncated') and not (
        registro.get('execution_complete') is True
        and registro.get('output_storage_truncated') is True))


def clasificacion(operacion):
    """La clasificación guardada de una operación o comprobación, o la que le toca."""
    guardada = operacion.get('classification') if isinstance(operacion, dict) else None
    if isinstance(guardada, dict):
        return guardada
    return classify_operation(operacion.get('argv'), operacion.get('purpose'),
                              operacion.get('cwd'), operacion.get('output'),
                              operacion.get('exit_code'),
                              output_truncated=bool(operacion.get('truncated')))


# Miran o buscan, pero no verifican el trabajo: un `ls` en verde no puede tapar
# unas pruebas en rojo.
_SOLO_MIRAN = frozenset({'ls', 'which', 'cat', 'echo', 'printf', 'pwd', 'true', 'test', '[', 'rg', 'grep',
                         'find', 'wc', 'head', 'tail', 'stat', 'file', 'type', 'command', 'git', 'sleep',
                         'sed', 'awk', 'env', 'date', 'cd'})


def comprobacion_sustantiva(argv):
    """¿La orden ejecuta algo que verifique el trabajo, y no sólo lo mira?"""
    valores = [str(v) for v in argv] if isinstance(argv, list) else []
    if not valores:
        return False
    programa = valores[0].replace('\\', '/').rsplit('/', 1)[-1]
    if programa in ('bash', 'sh', 'zsh') and len(valores) == 3 and valores[1] in ('-c', '-lc'):
        return any(tramo.split() and tramo.split()[0].rsplit('/', 1)[-1] not in _SOLO_MIRAN
                   for tramo in re.split(r'&&|\|\||;|\|', valores[2]))
    return programa not in _SOLO_MIRAN


def comprobacion_suficiente(comprobacion, *, clasificar=clasificacion):
    """¿La comprobación pasó de verdad? Un exit 0 con salida cortada o espacio movido no vale.

    `clasificar` deja que `RunEvidence._check_sufficient` use su propio
    `_classification` (y lo que lo parchee) sin cambiar la regla.
    """
    if comprobacion.get('workspace_stable') is False:
        return False
    clase = clasificar(comprobacion)
    if clase.get('disposition') != 'pass':
        return False
    if type(comprobacion.get('exit_code')) is not int:
        return False
    if (salida_incompleta(comprobacion) or comprobacion.get('timed_out')
            or comprobacion.get('exception') or comprobacion.get('output_limit_exceeded')):
        return False
    if clase.get('status') == 'expected_absence' and not clase.get('context_verified'):
        return False
    return True


def dentro_del_espacio(carpeta, raiz):
    """La comprobación corrió en la raíz o en una subcarpeta suya (campo cwd de run_checks)."""
    if not isinstance(carpeta, str) or not isinstance(raiz, str) or not carpeta or not raiz:
        return False
    if carpeta == raiz:
        return True
    from pathlib import PurePosixPath
    return PurePosixPath(raiz) in PurePosixPath(carpeta).parents


def comprobacion_vigente(comprobacion, *, sesion, proyecto, cwd, revision, huella, desajustes):
    """¿La comprobación se hizo sobre el mismo espacio, revisión y contenido de ahora?

    `huella` y `desajustes` son funciones: leer los archivos cuesta y, como antes,
    sólo se hace si lo barato ya coincide.
    """
    if 'workspace_context' in comprobacion and (
            comprobacion.get('workspace_stable') is not True
            # Se busca en el módulo al llamar: las pruebas parchean
            # `app.workspace_verification.matches`, que es este mismo módulo.
            or not espacio.matches(comprobacion['workspace_context'])):
        return False
    return (comprobacion.get('session_id') == sesion
            and comprobacion.get('project') == proyecto
            and dentro_del_espacio(comprobacion.get('cwd'), cwd)
            and comprobacion.get('revision') == revision
            and isinstance(comprobacion.get('fingerprint'), dict)
            and comprobacion.get('fingerprint') == huella()
            and not desajustes())
