"""Las órdenes de terminal para configurar proveedores.

    alfalfa --proveedores [--json]            estado, sin secretos
    alfalfa --guardar-clave openai            lee la clave por la entrada estándar
    alfalfa --guardar-clave anthropic         (la de Claude)
    alfalfa --guardar-clave openrouter        (o cualquier compatible con OpenAI del catálogo)
    alfalfa --borrar-clave openai
    alfalfa --catalogo [--json]               estado del catálogo de models.dev y los gratis ahora
    alfalfa --proveedor-por-defecto openai
    alfalfa --fijar-url alfalfa https://…     (o «-» para quitarla)
    alfalfa --activar-codigo                  lee el código CUY-…@servicio por la entrada y lo ata a este Mac
    alfalfa --cuota [--json]                  % usado este mes del servicio Alfalfa
    alfalfa --buscador [MODO] [--json]        buscador web: auto, ollama, searxng o apagado
    alfalfa --fijar-searxng URL               SearXNG del modo searxng («-» vuelve a la de CuySearch Manager)
    alfalfa --instalar-buscador               instala la librería cuysearch si falta (tras un `git pull`)
    alfalfa --reportar [--json]               manda un reporte de error; lee por la entrada una línea
                                              JSON {texto, contacto?, registro?, diagnostico?} o, en la
                                              terminal, pregunta qué pasó
"""
from __future__ import annotations

import getpass
import json
import sys

from . import PREFIJOS, configuracion, disponibles, llavero
from .compatible import es_compatible
from .ollama import validar_url

# Qué proveedor guarda qué en el Llavero; cada uno su cuenta.
CON_CLAVE = ('ollama', 'openai', 'anthropic')
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
        if getattr(args, 'activar_codigo', False):
            from .alfalfa import Alfalfa
            if entrada.isatty():
                codigo = input('Código de cortesía (CUY-…@servicio): ')
            else:
                codigo = entrada.readline()
            hecho = Alfalfa().activar(codigo)
            _salida({'tipo': 'activado', 'servicio': 'alfalfa', **hecho}, como_json,
                    'Código activado en este Mac' + (' para ' + hecho['nombre'] if hecho.get('nombre') else '')
                    + '. Modelos: ' + ', '.join(hecho['modelos']) + '.')
            return 0
        if getattr(args, 'cuota', False):
            from .alfalfa import Alfalfa
            filas = Alfalfa().cuota()
            _salida({'tipo': 'cuota', 'servicio': 'alfalfa', 'cuotas': filas}, como_json,
                    '\n'.join(f"{f['nombre']}: {f['usado_pct']:g} % usado (se renueva el {f['renueva']})"
                              for f in filas) or 'Sin cuota.')
            return 0
        if (getattr(args, 'buscador', None) is not None or getattr(args, 'fijar_searxng', None)
                or getattr(args, 'instalar_buscador', False)):
            from .nativo import web
            modo = args.buscador or None
            if args.instalar_buscador:
                datos = web.instalar()
            elif modo or args.fijar_searxng:
                datos = web.configurar(modo, args.fijar_searxng)
            else:
                datos = web.estado()
            _salida({'tipo': 'buscador', **datos}, como_json,
                    'Buscador web: ' + datos['modo']
                    + (' · ' + datos['searxng_url'] if datos['modo'] == 'searxng' else '')
                    + ('' if datos['libreria'] or datos['remoto'] else ' (falta la librería cuysearch)') + '.')
            return 0
        if getattr(args, 'reportar', False):
            from .alfalfa import Alfalfa
            pedido = _leer_reporte(entrada)
            registro = pedido.get('registro') or ''
            if pedido.get('diagnostico'):
                registro = _diagnostico() + ('\n\n' + registro if registro else '')
            hecho = Alfalfa().reportar(pedido.get('texto'), pedido.get('contacto'), registro)
            _salida({'tipo': 'reporte', **hecho}, como_json,
                    'Reporte enviado' + (f" (#{hecho['id']})" if hecho.get('id') else '') + '. Gracias.'
                    if hecho['enviado'] else
                    'No se pudo enviar: ' + hecho['motivo'] + ' Escríbenos a ' + hecho['soporte'] + '.')
            return 0
        if getattr(args, 'catalogo', False):
            datos = _catalogo()
            _salida({'tipo': 'catalogo', **datos}, como_json, _texto_catalogo(datos))
            return 0
        if args.guardar_clave:
            proveedor = args.guardar_clave
            compatible = proveedor not in CON_CLAVE and es_compatible(proveedor)
            if proveedor not in CON_CLAVE and not compatible:
                raise ValueError('Proveedor sin clave: ' + proveedor)
            if compatible:
                from .compatible import Compatible
                # La clave queda atada a la URL de ahora: si el catálogo la cambia, no la sigue.
                url = Compatible(proveedor).url()
            llavero.guardar(proveedor, _leer_secreto(proveedor, entrada))
            if compatible:
                configuracion.cambiar(proveedor, 'url', url)
                configuracion.cambiar(proveedor, 'guardada', True)
            _salida({'tipo': 'proveedores', **estado()}, como_json,
                    'Clave de ' + proveedor + ' guardada en el Llavero.')
        elif args.borrar_clave:
            proveedor = args.borrar_clave
            if proveedor not in CON_CLAVE + ('alfalfa',) and not es_compatible(proveedor):
                raise ValueError('Proveedor sin clave: ' + proveedor)
            llavero.borrar(proveedor)
            if proveedor not in CON_CLAVE + ('alfalfa',):
                configuracion.cambiar(proveedor, 'guardada', None)
            _salida({'tipo': 'proveedores', **estado()}, como_json,
                    'Clave de ' + proveedor + ' borrada del Llavero.')
        elif args.proveedor_por_defecto:
            configuracion.cambiar(None, 'por_defecto', args.proveedor_por_defecto)
            _salida({'tipo': 'proveedores', **estado()}, como_json,
                    'Proveedor por defecto: ' + args.proveedor_por_defecto + '.')
        elif args.fijar_url:
            proveedor, url = args.fijar_url
            if proveedor not in CON_URL and not es_compatible(proveedor):
                raise ValueError('Proveedor sin URL configurable: ' + proveedor)
            if url == '-':
                valor = None
            elif proveedor in CON_URL:
                valor = validar_url(url, 'proveedores.json', proveedor)
            else:
                from urllib.parse import urlsplit
                partes = urlsplit(url.strip())
                validar_url(partes.scheme + '://' + partes.netloc, 'proveedores.json', proveedor)
                if partes.query or partes.fragment or partes.username or partes.password:
                    raise ValueError('La URL de ' + proveedor + ' no lleva credenciales, consulta ni fragmento.')
                valor = url.strip().rstrip('/')
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


def _catalogo():
    """Fecha y origen del catálogo y los modelos gratis ahora, por proveedor."""
    from .. import catalogo
    datos = catalogo.cargar(red=True)
    por_proveedor = {}
    for proveedor, ficha in catalogo.gratis_ahora():
        por_proveedor.setdefault(proveedor['id'], {'nombre': proveedor['nombre'], 'modelos': []})
        por_proveedor[proveedor['id']]['modelos'].append(ficha['id'])
    return {'fuente': catalogo.URL, 'fecha': datos.get('fecha') or 0, 'origen': datos.get('origen'),
            'proveedores': len(datos['proveedores']),
            'modelos': sum(len(p['modelos']) for p in datos['proveedores'].values()),
            'gratis': sum(len(p['modelos']) for p in por_proveedor.values()),
            'gratis_por_proveedor': por_proveedor, 'aviso': catalogo.AVISO_GRATIS}


def _texto_catalogo(datos):
    import time
    origen = {'red': 'recién bajado', 'cache': 'copia guardada', 'paquete': 'copia mínima del paquete'}
    fecha = time.strftime('%Y-%m-%d %H:%M', time.localtime(datos['fecha'])) if datos['fecha'] else '—'
    lineas = [f"Catálogo de models.dev ({origen.get(datos['origen'], datos['origen'])}, {fecha}): "
              f"{datos['proveedores']} proveedores, {datos['modelos']} modelos.",
              f"Gratis ahora: {datos['gratis']} modelos. {datos['aviso']}."]
    for ident, fila in sorted(datos['gratis_por_proveedor'].items(), key=lambda f: -len(f[1]['modelos'])):
        lineas.append(f"  {fila['nombre']} ({ident}): {len(fila['modelos'])}")
    return '\n'.join(lineas)


def _leer_reporte(entrada):
    """{texto, contacto?, registro?, diagnostico?}: una línea JSON, o preguntando en la terminal."""
    if entrada.isatty():
        return {'texto': input('¿Qué pasó? '), 'contacto': input('Tu correo, si quieres respuesta (opcional): '),
                'diagnostico': True}
    linea = entrada.readline().strip()
    try:
        pedido = json.loads(linea)
    except ValueError:
        return {'texto': linea}
    if not isinstance(pedido, dict):
        raise ValueError('El reporte tiene que ser un objeto JSON.')
    return pedido


def _diagnostico():
    """Versión, sistema y estado de los proveedores, sin secretos. Nunca conversaciones."""
    import platform

    from .. import __version__
    lineas = ['AlfalfaCode ' + __version__ + ' · Python ' + platform.python_version()]
    datos = estado()
    lineas.append('Proveedor por defecto: ' + str(datos['por_defecto']))
    for fila in datos['proveedores']:
        lineas.append(('✓ ' if fila.get('configurado') else '· ') + str(fila.get('nombre'))
                      + (' · error ' + fila['error'] if fila.get('error') else ''))
    return '\n'.join(lineas)


def argumentos(grupo):
    grupo.add_argument('--proveedores', action='store_true', help='Estado de los proveedores (sin secretos)')
    grupo.add_argument('--guardar-clave', metavar='PROVEEDOR',
                       help='Guardar en el Llavero la clave que llega por la entrada estándar (ollama, openai, '
                            'anthropic o un compatible con OpenAI del catálogo, como openrouter u opencode)')
    grupo.add_argument('--borrar-clave', metavar='PROVEEDOR',
                       help='Borrar del Llavero una clave o el código de cortesía')
    grupo.add_argument('--proveedor-por-defecto', metavar='PROVEEDOR',
                       help='Proveedor por defecto: ' + ', '.join(PREFIJOS) + ' o un compatible del catálogo')
    grupo.add_argument('--fijar-url', nargs=2, metavar=('PROVEEDOR', 'URL'),
                       help='URL base de Ollama, del servicio Alfalfa o de un compatible («-» la quita)')
    grupo.add_argument('--catalogo', action='store_true',
                       help='Catálogo de models.dev: fecha, origen y modelos gratis ahora')
    grupo.add_argument('--activar-codigo', action='store_true',
                       help='Atar a este Mac el código de cortesía que llega por la entrada estándar')
    grupo.add_argument('--cuota', action='store_true', help='Cuota del mes usada en el servicio Alfalfa')
    from .nativo.web import MODOS
    grupo.add_argument('--buscador', nargs='?', const='', choices=('',) + MODOS, metavar='MODO',
                       help='Buscador web de los agentes: ' + ', '.join(MODOS) + ' (sin MODO, el actual)')
    grupo.add_argument('--fijar-searxng', metavar='URL',
                       help='URL del SearXNG del modo searxng («-» vuelve a http://127.0.0.1:8888)')
    grupo.add_argument('--instalar-buscador', action='store_true',
                       help='Instalar la librería de búsqueda web si falta (por ejemplo, tras un git pull)')
    grupo.add_argument('--reportar', action='store_true',
                       help='Mandar un reporte de error (al servicio Alfalfa o, si no se puede, por correo)')


def pedida(args):
    return bool(args.proveedores or getattr(args, 'catalogo', False) or args.guardar_clave or args.borrar_clave
                or args.proveedor_por_defecto or args.fijar_url or args.activar_codigo or args.cuota
                or getattr(args, 'reportar', False) or getattr(args, 'buscador', None) is not None
                or getattr(args, 'fijar_searxng', None) or getattr(args, 'instalar_buscador', False))
