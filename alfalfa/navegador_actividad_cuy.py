"""Correlación con el cursor de CB-5, sólo en la conexión HTTP ya autorizada."""
from __future__ import annotations

import re
from datetime import datetime

from . import navegador_actividad as actividad
from .cuybrowser_http import ErrorCuyBrowser


def _scope(data, profile):
    scope = data.get('scope')
    if not isinstance(scope, dict) or not isinstance(profile, str) or scope.get('profile') != profile:
        return None
    daemon = scope.get('daemon_session_id')
    return (daemon, profile) if isinstance(daemon, str) and re.fullmatch(actividad.UUID, daemon) else None


def _cursor(data):
    value = data.get('cursor')
    return value if isinstance(value, str) and 1 <= len(value) <= 1024 else None


async def iniciar(http, profile):
    if not actividad.activa():
        return None
    if http.capabilities.get('auditRecent') is not True or 'audit_recent' not in http.tools:
        actividad.cuy('no_disponible')
        return None
    try:
        data = await http.call('audit_recent', {'limit': 1}, timeout=2, approve=False)
    except ValueError:
        actividad.cuy('incompleta')
        return None
    if data.get('enabled') is False:
        actividad.cuy('desactivada')
        return None
    scope, cursor = _scope(data, profile), _cursor(data)
    if scope is None:
        actividad.cuy('ambito_cambiado')
        return None
    if data.get('enabled') is not True or cursor is None or data.get('has_more') is not False:
        actividad.cuy('incompleta')
        return None
    return http, scope, cursor


def _entrada(row, daemon):
    if (not isinstance(row, dict) or not isinstance(row.get('tool'), str)
            or row['tool'] not in actividad.ACCIONES_CUY):
        return None
    seq, stamp, decision = row.get('seq'), row.get('ts'), row.get('permission')
    if (type(seq) is not int or not 0 < seq <= 2**53 - 1 or row.get('id') != f'{daemon}:{seq}'
            or type(row.get('ok')) is not bool or not isinstance(stamp, str)
            or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z', stamp)
            or decision not in ('approved', 'requested', 'denied', 'no_consta')):
        return None
    try:
        hora = datetime.fromisoformat(stamp).timestamp()
    except (ValueError, OverflowError, OSError):
        return None
    return {'id': row['id'], 'tool': row['tool'], 'ok': row['ok'], 'hora': hora,
            'permission': decision, 'origen': actividad.sitio_seguro(row.get('origin'))}


async def terminar(observacion, http, profile):
    if observacion is None:
        return
    anterior, scope, cursor = observacion
    if http is not anterior or profile != scope[1]:
        actividad.cuy('ambito_cambiado')
        return
    registros, ids = [], set()
    estado = 'disponible'
    try:
        for _ in range(2):
            data = await http.call('audit_recent', {'limit': 100, 'cursor': cursor}, timeout=2, approve=False)
            if _scope(data, profile) != scope:
                actividad.cuy('ambito_cambiado')
                return
            if data.get('enabled') is False:
                actividad.cuy('desactivada')
                return
            rows = data.get('entries')
            if data.get('enabled') is not True or not isinstance(rows, list) or len(rows) > 100:
                estado = 'incompleta'
                break
            for row in rows:
                entry = _entrada(row, scope[0])
                if not isinstance(row, dict) or (entry is None and isinstance(row.get('tool'), str)
                                                 and row['tool'] in actividad.ACCIONES_CUY):
                    estado = 'incompleta'
                if entry is not None and entry['id'] not in ids:
                    ids.add(entry['id'])
                    registros.append(entry)
            if data.get('truncated') is not False or data.get('legacy_omitted', 0) != 0:
                estado = 'incompleta'
            if data.get('has_more') is False:
                break
            new_cursor = _cursor(data)
            if data.get('has_more') is not True or new_cursor is None or new_cursor == cursor:
                estado = 'incompleta'
                break
            cursor = new_cursor
        else:
            estado = 'incompleta'
    except ErrorCuyBrowser as exc:
        estado = 'cursor_caducado' if exc.code == 'AUDIT_CURSOR_EXPIRED' else 'incompleta'
    except ValueError:
        estado = 'incompleta'
    actividad.cuy('incompleta' if len(registros) > 20 else estado, registros)
