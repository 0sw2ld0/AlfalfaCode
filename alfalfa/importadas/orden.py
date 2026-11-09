"""`alfalfa --importar`: lo que hay de OpenCode, Codex y Claude Code en esta carpeta y su estado.

    alfalfa --importar                             # la lista, con la definición de cada cosa
    alfalfa --importar --autorizar mcp:docs@HUELLA # autoriza lo que viste (se puede repetir)
    alfalfa --importar --autorizar todo            # en una terminal: todos los pendientes
    alfalfa --importar --autorizar todo --sin-internet
    alfalfa --importar --retirar CLAVE

`CLAVE@HUELLA` sólo autoriza si la definición en disco sigue siendo la que tenía esa huella
(la que enseña la lista); sin huella, o con `todo`, hace falta una terminal de verdad. Dentro de
una sesión de AlfalfaCode (ALFALFA_SESION) no se autoriza nada: un agente no se da permisos.
Con --json emite un evento `importadas` (el que lee Ajustes ▸ Importar de la app).
"""
from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from . import REQUIEREN_AUTORIZACION, Importadas
from .traduccion import visible

ESTADOS = {'autorizado': 'autorizado', 'pendiente': 'sin autorizar', 'cambio': 'cambió: vuelve a autorizarlo',
           'activo': 'activo', 'desactivado': 'desactivado en su configuración', 'duplicado': 'duplicado',
           'invalido': 'no se puede usar'}
TIPOS = {'mcp': 'Servidores MCP', 'agente': 'Agentes', 'comando': 'Órdenes «/»', 'instrucciones': 'Instrucciones'}
EN_SESION = 'ALFALFA_SESION'


def argumentos(parser):
    parser.add_argument('--autorizar', action='append', default=[], metavar='CLAVE@HUELLA',
                        help='Con --importar: autorizar un servidor MCP, un agente o una orden «/» importados, '
                             'si su huella sigue siendo la de la lista («todo»: los pendientes, en una terminal)')
    parser.add_argument('--retirar', action='append', default=[], metavar='CLAVE',
                        help='Con --importar: retirar una autorización')
    parser.add_argument('--sin-internet', action='store_true',
                        help='Con --importar --autorizar: el servidor MCP corre en el sandbox sin internet')


def _terminal():
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _elegir(importadas, claves, todos, exigir_huella):
    elegidos, errores = [], []
    for clave in claves:
        if clave == 'todo':
            if exigir_huella:
                errores.append('«todo» sólo vale en una terminal; aquí autoriza cada uno con CLAVE@HUELLA')
            else:
                elegidos += todos
            continue
        elemento, huella = importadas.buscar_con_huella(clave)
        if elemento is None or elemento.tipo not in REQUIEREN_AUTORIZACION:
            errores.append('No hay nada autorizable con la clave ' + clave)
        elif huella is not None and huella != elemento.huella:
            errores.append(elemento.clave + ' cambió desde que lo viste (huella ' + huella + ', ahora '
                           + elemento.huella + '); vuelve a mirarlo antes de autorizarlo')
        elif huella is None and exigir_huella:
            errores.append('Para autorizar ' + elemento.clave + ' fuera de una terminal indica su huella: '
                           + clave + '@' + elemento.huella)
        else:
            elegidos.append(elemento)
    return list({e.clave: e for e in elegidos}.values()), errores


def orden(args, hogar, ambiente=None):
    from ..stdio import Transport
    ambiente = os.environ if ambiente is None else ambiente
    transporte = Transport(sys.stdout) if args.json else None
    importadas = Importadas(Path.cwd(), hogar)
    errores = []
    if args.autorizar and ambiente.get(EN_SESION):
        errores.append('Dentro de una sesión de AlfalfaCode no se autoriza nada: hazlo tú en una terminal o en '
                       'Ajustes ▸ Importar.')
    elif args.autorizar:
        elegidos, fallos = _elegir(importadas, args.autorizar, importadas.autorizables(),
                                   exigir_huella=not _terminal())
        errores += fallos
        validos = [e for e in elegidos if e.estado in ('pendiente', 'cambio', 'autorizado')]
        errores += ['No se autoriza ' + e.clave + ': está ' + ESTADOS[e.estado] for e in elegidos if e not in validos]
        if validos:
            importadas.autorizar(validos, red=not args.sin_internet)
    if args.retirar:
        autorizados = [e for t in REQUIEREN_AUTORIZACION for e in importadas._autorizados(t)]
        elegidos, fallos = _elegir(importadas, args.retirar, autorizados, exigir_huella=False)
        errores += fallos
        if elegidos:
            importadas.retirar(elegidos)
    if transporte:
        transporte.emit('importadas', **importadas.informe(), errores=errores)
    else:
        imprimir(importadas, errores)
    return 1 if errores else 0


def imprimir(importadas, errores=()):
    informe = importadas.informe()
    print('Configuraciones de otras herramientas en ' + informe['cwd'])
    if not informe['elementos']:
        print('  No hay nada de OpenCode, Codex ni Claude Code que importar aquí.')
    for tipo, titulo in TIPOS.items():
        filas = [f for f in informe['elementos'] if f['tipo'] == tipo]
        if not filas:
            continue
        print('\n' + titulo)
        for fila in filas:
            estado = ESTADOS.get(fila['estado'], fila['estado'])
            if fila.get('red') is False:
                estado += ', sin internet'
            print('  ' + visible(('/' if tipo == 'comando' else '') + fila['nombre']) + '  [' + estado + ']  '
                  + fila['herramienta'] + ' · ' + visible(fila['lugar']))
            textos = [fila.get('detalle'), *(fila.get('definicion') or []), fila.get('problema'),
                      *(fila.get('avisos') or []), 'lo tapa ' + fila['tapado_por'] if fila.get('tapado_por') else None]
            for texto in textos:
                if texto:
                    print('      ' + visible(texto))
            if fila.get('vista'):
                print('      ┊ ' + fila['vista'].replace('\n', '\n      ┊ '))
            if fila['autorizable'] and fila['estado'] != 'autorizado':
                print('      alfalfa --importar --autorizar ' + shlex.quote(fila['corta'] + '@' + fila['huella']))
    for aviso in informe['avisos']:
        print('\nAviso: ' + visible(aviso))
    if importadas.autorizables():
        print('\nLos servidores MCP, los agentes y las órdenes «/» no se activan hasta que los autorizas, una vez.'
              ' Todos (en esta terminal): alfalfa --importar --autorizar todo')
    print('Autorizaciones en ' + informe['archivo'] + ' (sólo huellas; ninguna clave se copia).')
    for error in errores:
        print('Error: ' + visible(error), file=sys.stderr)
