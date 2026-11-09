"""`alfalfa --importar`: lo que hay de OpenCode, Codex y Claude Code en esta carpeta y su estado.

    alfalfa --importar                        # la lista
    alfalfa --importar --autorizar CLAVE      # autoriza uno (se puede repetir); «todo» = los pendientes
    alfalfa --importar --autorizar todo --sin-internet
    alfalfa --importar --retirar CLAVE

Con --json emite un evento `importadas` (el que lee Ajustes ▸ Importar de la app).
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import REQUIEREN_AUTORIZACION, Importadas

ESTADOS = {'autorizado': 'autorizado', 'pendiente': 'sin autorizar', 'cambio': 'cambió: vuelve a autorizarlo',
           'activo': 'activo', 'desactivado': 'desactivado en su configuración', 'duplicado': 'duplicado',
           'invalido': 'no se puede usar'}
TIPOS = {'mcp': 'Servidores MCP', 'agente': 'Agentes', 'comando': 'Órdenes «/»', 'instrucciones': 'Instrucciones'}


def argumentos(parser):
    parser.add_argument('--autorizar', action='append', default=[], metavar='CLAVE',
                        help='Con --importar: autorizar un servidor MCP o un agente importado («todo»: los pendientes)')
    parser.add_argument('--retirar', action='append', default=[], metavar='CLAVE',
                        help='Con --importar: retirar una autorización')
    parser.add_argument('--sin-internet', action='store_true',
                        help='Con --importar --autorizar: el servidor MCP corre en el sandbox sin internet')


def _elegir(importadas, claves, todos):
    elegidos, desconocidas = [], []
    for clave in claves:
        if clave == 'todo':
            elegidos += todos
            continue
        elemento = importadas.buscar(clave)
        if elemento is None or elemento.tipo not in REQUIEREN_AUTORIZACION:
            desconocidas.append(clave)
        else:
            elegidos.append(elemento)
    return list({e.clave: e for e in elegidos}.values()), desconocidas


def orden(args, hogar):
    from ..stdio import Transport
    transporte = Transport(sys.stdout) if args.json else None
    importadas = Importadas(Path.cwd(), hogar)
    errores = []
    if args.autorizar:
        elegidos, desconocidas = _elegir(importadas, args.autorizar, importadas.autorizables())
        errores += ['No hay nada autorizable con la clave ' + c for c in desconocidas]
        validos = [e for e in elegidos if e.estado in ('pendiente', 'cambio', 'autorizado')]
        errores += ['No se autoriza ' + e.clave + ': está ' + ESTADOS[e.estado] for e in elegidos if e not in validos]
        if validos:
            importadas.autorizar(validos, red=not args.sin_internet)
    if args.retirar:
        elegidos, desconocidas = _elegir(importadas, args.retirar, importadas._autorizados('mcp')
                                         + importadas._autorizados('agente'))
        errores += ['No hay nada autorizable con la clave ' + c for c in desconocidas]
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
                estado += ', sin red'
            print('  ' + ('/' if tipo == 'comando' else '') + fila['nombre'] + '  [' + estado + ']  '
                  + fila['herramienta'] + ' · ' + fila['lugar'])
            for texto in [fila.get('detalle'), fila.get('problema'), *(fila.get('avisos') or []),
                          'lo tapa ' + fila['tapado_por'] if fila.get('tapado_por') else None]:
                if texto:
                    print('      ' + texto)
            if fila['autorizable'] and fila['estado'] != 'autorizado':
                print('      alfalfa --importar --autorizar ' + _citada(fila['corta']))
    for aviso in informe['avisos']:
        print('\nAviso: ' + aviso)
    pendientes = importadas.autorizables()
    if pendientes:
        print('\nLos servidores MCP y los agentes no se activan hasta que los autorizas, una vez.'
              ' Todos: alfalfa --importar --autorizar todo')
    print('Autorizaciones en ' + informe['archivo'] + ' (sólo huellas; ninguna clave se copia).')
    for error in errores:
        print('Error: ' + error, file=sys.stderr)


def _citada(texto):
    import shlex
    return shlex.quote(texto)
