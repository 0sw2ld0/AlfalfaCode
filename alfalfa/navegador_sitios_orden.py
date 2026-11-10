"""Ajustes de sitios sin iniciar modelos, sesiones, proveedores ni el navegador.

El canal stdio es exclusivo de Ajustes: muestra la propuesta y sólo guarda tras
una respuesta humana correlacionada. No ofrece una herramienta al modelo.
"""
import json
import sys
from pathlib import Path
from uuid import uuid4

from .navegador_sitios import Sitios, lista


def orden(args, home, *, entrada=None, salida=None, root=None):
    from .stdio import Transport
    entrada = entrada if entrada is not None else sys.stdin
    salida = salida if salida is not None else sys.stdout
    transport = Transport(salida)
    sites = Sitios(home, root or Path.cwd())
    try:
        _estado(transport, sites)
        if not args.stdio:
            return 0
        while line := entrada.readline(65537):
            request = json.loads(line)
            if not isinstance(request, dict) or request.get('tipo') != 'configurar_sitios':
                raise ValueError('Petición de Ajustes de sitios desconocida.')
            proposed = {'permitidos': lista(request.get('permitidos', [])),
                        'bloqueados': lista(request.get('bloqueados', [])),
                        'estricto': request.get('estricto') is True}
            previous = sites.configuracion()
            ident = uuid4().hex
            transport.emit('permiso', id=ident, argv=['navegador', 'configurar', str(sites.root)],
                           proposito='Cambiar los sitios permitidos y bloqueados sólo para este proyecto.',
                           motivo='navegador', motivos=['navegador'],
                           diff='Antes:\n' + json.dumps(previous, ensure_ascii=False, indent=2)
                           + '\nDespués:\n' + json.dumps(proposed, ensure_ascii=False, indent=2))
            reply = json.loads(entrada.readline(65537) or '{}')
            if not isinstance(reply, dict) or reply.get('tipo') != 'permiso' or reply.get('id') != ident:
                raise ValueError('Respuesta de permiso de Ajustes no válida.')
            if reply.get('aprobado') is not True:
                transport.emit('permiso_resuelto', id=ident, aprobado=False, motivo='Rechazado en Ajustes')
                _estado(transport, sites)
                continue
            if previous != sites.configuracion():
                raise ValueError('Las reglas cambiaron durante la aprobación; vuelve a revisarlas.')
            sites.guardar(proposed)
            transport.emit('permiso_resuelto', id=ident, aprobado=True, motivo='Aprobado en Ajustes')
            _estado(transport, sites)
        return 0
    except (OSError, ValueError) as exc:
        transport.emit('error', texto=str(exc))
        return 1


def _estado(transport, sites):
    transport.emit('navegador_sitios', proyecto=str(sites.root), configuracion=sites.configuracion(),
                   politica=sites.politica())
