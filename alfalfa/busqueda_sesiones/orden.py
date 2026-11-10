"""`alfalfa --buscar-sesiones "consulta"` y compañía, desde la terminal o la app.

- `--buscar-sesiones CONSULTA`: busca en las sesiones de esta carpeta; con
  `--todas`, en todas. `--max-resultados N` cambia cuántos (8 por defecto) y
  `--tiempo S`, cuánto puede tardar en poner al día el índice. Una consulta que
  empieza por «-» va pegada: `--buscar-sesiones=-x`. Con `--todas`, de otras
  carpetas no salen salidas de herramientas salvo con `--incluir-externas` (la app:
  lo lee una persona, no un modelo); la herramienta del agente nunca lo usa.
- `--ver-resultado REF`: los mensajes de alrededor de un resultado (`--mensajes N`).
- `--reindexar`: borra el índice y lo rehace desde las sesiones.
- `--borrar-indice`: lo borra sin rehacerlo (las sesiones no cambian; la próxima
  búsqueda lo vuelve a crear).
- `--alcance-busqueda carpeta|todas`: dónde busca el agente con `buscar_sesiones`.

Con `--json`, una línea del protocolo de `--stdio`: `busqueda_sesiones`,
`sesiones_alrededor`, `indice_sesiones` o `error`.
"""
from __future__ import annotations

import sys


def argumentos(parser, grupo):
    grupo.add_argument('--buscar-sesiones', metavar='CONSULTA',
                       help='Buscar en las conversaciones de las sesiones de esta carpeta (con --todas, de todas)')
    # Ojo: ninguna palabra de la ayuda lleva «red» (test_cli_offers_no_network_switch).
    grupo.add_argument('--ver-resultado', metavar='REF',
                       help='Los mensajes de antes y después de un resultado de --buscar-sesiones (su «ref»)')
    grupo.add_argument('--reindexar', action='store_true',
                       help='Rehacer desde cero el índice de búsqueda de las sesiones')
    grupo.add_argument('--borrar-indice', action='store_true',
                       help='Borrar el índice de búsqueda (las sesiones no cambian; se rehace al buscar)')
    grupo.add_argument('--alcance-busqueda', choices=('carpeta', 'todas'),
                       help='Dónde busca el agente con buscar_sesiones: esta carpeta (por defecto) o todas')
    parser.add_argument('--todas', action='store_true',
                        help='Con --buscar-sesiones o --ver-resultado: en las sesiones de todas las carpetas')
    parser.add_argument('--max-resultados', type=int, metavar='N', help='Con --buscar-sesiones: cuántos resultados')
    parser.add_argument('--mensajes', type=int, metavar='N', help='Con --ver-resultado: mensajes a cada lado')
    parser.add_argument('--tiempo', type=_segundos, metavar='SEGUNDOS',
                        help='Con --buscar-sesiones: segundos como mucho para poner al día el índice antes de buscar '
                             '(más de 0)')
    parser.add_argument('--incluir-externas', action='store_true',
                        help='Con --todas: también las salidas de herramientas de otras carpetas (web, MCP, archivos '
                             'leídos), marcadas con su origen. Para lo que lee una persona (la app), no un modelo')


def _segundos(valor):
    import argparse
    try:
        numero = float(valor)
    except ValueError:
        raise argparse.ArgumentTypeError('se esperaba un número de segundos') from None
    if not numero > 0 or numero != numero or numero == float('inf'):
        raise argparse.ArgumentTypeError('tiene que ser mayor que 0 (sin --tiempo no hay tope)')
    return numero


def pedida(args):
    return bool(getattr(args, 'buscar_sesiones', None) is not None or getattr(args, 'ver_resultado', None)
                or getattr(args, 'reindexar', False) or getattr(args, 'borrar_indice', False)
                or getattr(args, 'alcance_busqueda', None))


def orden(args, salida=None):
    from .. import busqueda_sesiones as busqueda
    from ..stdio import Transport
    salida = salida or sys.stdout
    transporte = Transport(salida) if args.json else None
    todas = True if args.todas else None
    try:
        if args.alcance_busqueda:
            valor = busqueda.fijar_alcance(args.alcance_busqueda)
            if transporte:
                transporte.emit('indice_sesiones', alcance=valor)
            else:
                print('El agente buscará en las sesiones de ' + ('todas las carpetas.' if valor == 'todas'
                                                                 else 'la misma carpeta.'), file=salida)
            return 0
        if args.borrar_indice:
            borrado = busqueda.borrar()
            if transporte:
                transporte.emit('indice_sesiones', borrado=borrado, ruta=str(busqueda.ruta_indice()))
            else:
                print('Índice borrado. Las sesiones no cambian; se rehace en la próxima búsqueda.' if borrado
                      else 'No había índice que borrar.', file=salida)
            return 0
        if args.reindexar:
            resumen = busqueda.reindexar()
            if transporte:
                transporte.emit('indice_sesiones', **resumen)
            else:
                print('Índice rehecho: ' + str(resumen['mensajes']) + ' mensajes de ' + str(resumen['sesiones'])
                      + ' sesiones (' + _tamano(resumen['bytes']) + ').', file=salida)
            return 0
        if args.ver_resultado:
            informe = busqueda.alrededor(args.ver_resultado, todas=todas, externas=args.incluir_externas,
                                         cuantos=args.mensajes or busqueda.ALREDEDOR)
            if transporte:
                transporte.emit('sesiones_alrededor', **informe)
            else:
                _imprimir_alrededor(informe, salida)
            return 0
        informe = busqueda.buscar(args.buscar_sesiones, todas=todas, externas=args.incluir_externas,
                                  maximo=args.max_resultados or busqueda.RESULTADOS, tiempo=args.tiempo)
        if transporte:
            transporte.emit('busqueda_sesiones', **informe)
        else:
            _imprimir(informe, salida)
        return 0
    except (OSError, ValueError) as exc:
        if transporte:
            transporte.emit('error', texto=str(exc) or 'No se pudo buscar en las sesiones.')
        else:
            print('AlfalfaCode · Error: ' + str(exc), file=sys.stderr)
        return 1


def _tamano(numero):
    return (str(round(numero / 1024 / 1024, 1)) + ' MB') if numero >= 1024 * 1024 else (str(numero // 1024) + ' KB')


ROLES = {'user': 'tú', 'assistant': 'agente', 'tool': 'herramienta'}


def _imprimir(informe, salida):
    resultados = informe['resultados']
    donde = 'todas las carpetas' if informe['alcance'] == 'todas' else informe.get('carpeta', 'esta carpeta')
    if not resultados:
        print('Sin resultados para «' + informe['consulta'] + '» en ' + donde + '.', file=salida)
        return
    print(str(len(resultados)) + ' resultado' + ('s' if len(resultados) != 1 else '') + ' en ' + donde + ':',
          file=salida)
    for fila in resultados:
        rol = ROLES.get(fila['rol'], fila['rol']) + (' · ' + fila['herramienta'] if fila.get('herramienta') else '')
        print('', file=salida)
        print('● ' + (fila.get('titulo') or fila['sesion']) + '  [' + fila['ref'] + ']', file=salida)
        print('  ' + (fila.get('fecha') or '') + ' · ' + rol, file=salida)
        for linea in fila['fragmento'].splitlines()[:8]:
            print('  │ ' + linea, file=salida)
    if not informe.get('indice_al_dia', True):
        print('\n(El índice aún no está al día: vuelve a buscar en un momento.)', file=salida)
    print('\nMás contexto: alfalfa --ver-resultado REF', file=salida)


def _imprimir_alrededor(informe, salida):
    print('● ' + (informe.get('titulo') or informe['sesion']) + '  (' + informe['sesion'] + ')', file=salida)
    for mensaje in informe['mensajes']:
        rol = ROLES.get(mensaje['rol'], mensaje['rol'])
        marca = '▶' if mensaje.get('buscado') else ' '
        print('', file=salida)
        print(marca + ' ' + rol + ' · ' + (mensaje.get('fecha') or '') + '  [' + mensaje['ref'] + ']', file=salida)
        for linea in mensaje['texto'].splitlines()[:40]:
            print('  │ ' + linea, file=salida)
