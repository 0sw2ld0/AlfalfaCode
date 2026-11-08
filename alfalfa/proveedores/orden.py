"""Las órdenes de terminal para configurar proveedores.

    alfalfa --proveedores [--json]            estado, sin secretos
    alfalfa --guardar-clave openai            lee la clave por la entrada estándar
    alfalfa --borrar-clave openai
    alfalfa --proveedor-por-defecto openai
    alfalfa --fijar-url alfalfa https://…     (o «-» para quitarla)
"""
from __future__ import annotations

import getpass
import json
import sys

from . import PREFIJOS, disponibles, llavero, configuracion
from .ollama import validar_url

# Qué proveedor guarda qué en el Llavero; cada uno su cuenta.
CON_CLAVE = ('ollama', 'openai', 'alfalfa')
CON_URL = ('ollama', 'alfalfa')


def estado():
    filas = []
    for proveedor in disponibles():
        try:
            filas.append(proveedor.estado())
        except Exception as exc:
            filas.append({'id': proveedor.id, 'nombre': proveedor.nombre, 'configurado': False,
                          'error': type(exc).__name__})
    return {'por_defecto': configuracion.por_defecto(), 'proveedores': filas}


def _leer_secreto(proveedor, entrada):
    if entrada.isatty():
        return getpass.getpass('Clave de ' + proveedor + ' (no se ve al escribir): ')
    return entrada.readline()


def _salida(resultado, como_json, texto):
    if como_json:
        print(json.dumps(dict(version=2, **resultado), ensure_ascii=False))
    else:
        print(texto)


def orden(args, entrada=None):
    entrada = entrada or sys.stdin
    como_json = bool(getattr(args, 'json', False))
    try:
        if args.guardar_clave:
            proveedor = args.guardar_clave
            if proveedor not in CON_CLAVE:
                raise ValueError('Proveedor sin clave: ' + proveedor)
            llavero.guardar(proveedor, _leer_secreto(proveedor, entrada))
            _salida({'tipo': 'proveedores', **estado()}, como_json,
                    'Clave de ' + proveedor + ' guardada en el Llavero.')
        elif args.borrar_clave:
            llavero.borrar(args.borrar_clave)
            _salida({'tipo': 'proveedores', **estado()}, como_json,
                    'Clave de ' + args.borrar_clave + ' borrada del Llavero.')
        elif args.proveedor_por_defecto:
            configuracion.cambiar(None, 'por_defecto', args.proveedor_por_defecto)
            _salida({'tipo': 'proveedores', **estado()}, como_json,
                    'Proveedor por defecto: ' + args.proveedor_por_defecto + '.')
        elif args.fijar_url:
            proveedor, url = args.fijar_url
            if proveedor not in CON_URL:
                raise ValueError('Proveedor sin URL configurable: ' + proveedor)
            valor = None if url == '-' else validar_url(url, 'proveedores.json', proveedor)
            configuracion.cambiar(proveedor, 'url', valor)
            _salida({'tipo': 'proveedores', **estado()}, como_json,
                    'URL de ' + proveedor + ': ' + (valor or 'la de siempre') + '.')
        else:
            datos = estado()
            lineas = ['Proveedor por defecto: ' + datos['por_defecto']]
            for fila in datos['proveedores']:
                lineas.append(('✓ ' if fila.get('configurado') else '· ') + fila['nombre']
                              + (' · ' + fila['url'] if fila.get('url') else ''))
            _salida({'tipo': 'proveedores', **datos}, como_json, '\n'.join(lineas))
        return 0
    except (ValueError, OSError, llavero.LlaveroNoDisponible) as exc:
        if como_json:
            print(json.dumps({'version': 2, 'tipo': 'error', 'texto': str(exc)}, ensure_ascii=False))
        else:
            print('AlfalfaCode · Error: ' + str(exc), file=sys.stderr)
        return 1


def argumentos(grupo):
    grupo.add_argument('--proveedores', action='store_true', help='Estado de los proveedores (sin secretos)')
    grupo.add_argument('--guardar-clave', choices=CON_CLAVE, metavar='PROVEEDOR',
                       help='Guardar en el Llavero la clave que llega por la entrada estándar')
    grupo.add_argument('--borrar-clave', choices=CON_CLAVE, metavar='PROVEEDOR')
    grupo.add_argument('--proveedor-por-defecto', choices=PREFIJOS, metavar='PROVEEDOR')
    grupo.add_argument('--fijar-url', nargs=2, metavar=('PROVEEDOR', 'URL'),
                       help='URL base de Ollama o del servicio Alfalfa («-» la quita)')


def pedida(args):
    return bool(args.proveedores or args.guardar_clave or args.borrar_clave
                or args.proveedor_por_defecto or args.fijar_url)
