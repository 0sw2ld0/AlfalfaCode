"""`alfalfa --memoria` y `alfalfa --aprendido`: ver y corregir lo que aprende AlfalfaCode.

    alfalfa --memoria                         los tres almacenes de esta carpeta
    alfalfa --memoria ver ALMACEN             usuario, entorno o proyecto
    alfalfa --memoria escribir ALMACEN        el texto entero (--texto, o $EDITOR / la entrada)
    alfalfa --memoria quitar ALMACEN TEXTO    la entrada que contiene TEXTO
    alfalfa --memoria vaciar ALMACEN

    alfalfa --aprendido                       habilidades aprendidas, propuestas y ajustes
    alfalfa --aprendido ver NOMBRE            usuario:NOMBRE, proyecto:NOMBRE o NOMBRE
    alfalfa --aprendido editar NOMBRE         reescribir su SKILL.md (también si está bloqueada)
    alfalfa --aprendido borrar NOMBRE         a la papelera de $ALFALFA_HOME/aprendido
    alfalfa --aprendido bloquear NOMBRE       el agente ya no la cambia (desbloquear la suelta)
    alfalfa --aprendido pendientes            lo que propuso la revisión al cerrar el turno
    alfalfa --aprendido pendientes ver ID     una propuesta con su diff entero
    alfalfa --aprendido aplicar ID / descartar ID
    alfalfa --aprendido ajustes [CLAVE sí|no] memoria, habilidades o revision
    alfalfa --aprendido ajustes modelo_revision PROVEEDOR/MODELO (o «ninguno»)
    alfalfa --aprendido proyecto sí|no        aprender o no en esta carpeta

Con `--json` responde con líneas del protocolo de `--stdio` (`memoria`, `aprendido`,
`habilidad`, `error`).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from . import BOOLEANOS, apagado_en_proyecto, cambiar_proyecto, guardar_ajustes, leer_ajustes, raiz_de_proyecto
from .habilidades import Habilidades
from .memoria import ALMACENES, Memoria, unir
from .pendientes import Pendientes, para_el_cliente

SI = ('si', 'sí', 'true', '1', 'on', 'yes')
NO = ('no', 'false', '0', 'off')


def argumentos(parser, grupo):
    grupo.add_argument('--memoria', nargs='*', metavar='ACCION',
                       help='Ver y corregir la memoria de AlfalfaCode (usuario, entorno y proyecto)')
    grupo.add_argument('--aprendido', nargs='*', metavar='ACCION',
                       help='Habilidades aprendidas, propuestas de la revisión y ajustes de aprendizaje')
    parser.add_argument('--texto', metavar='TEXTO',
                        help='Con --memoria escribir o --aprendido editar: el texto nuevo, entero')


def pedida(args):
    return getattr(args, 'memoria', None) is not None or getattr(args, 'aprendido', None) is not None


def orden(args, home):
    from ..stdio import Transport
    transporte = Transport(sys.stdout) if args.json else None
    cwd = Path.cwd()
    try:
        if args.memoria is not None:
            datos = _memoria(args.memoria, args.texto, home, cwd)
        else:
            datos = _aprendido(args.aprendido, args.texto, home, cwd)
    except (ValueError, OSError) as exc:
        if transporte:
            transporte.emit('error', texto=str(exc))
        else:
            print('AlfalfaCode · Error: ' + str(exc), file=sys.stderr)
        return 1
    tipo, campos, texto = datos
    if transporte:
        transporte.emit(tipo, **campos)
    else:
        print(texto)
    return 0


# --- Memoria ------------------------------------------------------------------------------------

def _estado_memoria(home, cwd):
    memoria = Memoria(home, cwd)
    ajustes = leer_ajustes(home)
    return {'almacenes': memoria.todos(), 'ajustes': ajustes, 'proyecto': str(raiz_de_proyecto(cwd)),
            'aprende_aqui': not apagado_en_proyecto(cwd, home, ajustes)}


def _texto_memoria(estado, solo=None):
    lineas = []
    for almacen in estado['almacenes']:
        if solo and almacen['almacen'] != solo:
            continue
        lineas.append('── ' + almacen['titulo'] + (' · ' + almacen['proyecto'] if almacen.get('proyecto') else '')
                      + ' (' + str(almacen['usados']) + '/' + str(almacen['limite']) + ' caracteres)')
        lineas += ['  · ' + e.replace('\n', '\n    ') for e in almacen['entradas']] or ['  (vacía)']
    if not solo and not estado['aprende_aqui']:
        lineas.append('El aprendizaje está apagado en esta carpeta (alfalfa --aprendido proyecto sí lo enciende).')
    return '\n'.join(lineas)


def _almacen(valor):
    if valor not in ALMACENES:
        raise ValueError('Almacén desconocido: usa usuario, entorno o proyecto.')
    return valor


def _memoria(partes, texto, home, cwd):
    accion = partes[0] if partes else 'listar'
    memoria = Memoria(home, cwd)
    if accion == 'listar' and len(partes) <= 1:
        estado = _estado_memoria(home, cwd)
        return 'memoria', estado, _texto_memoria(estado)
    if accion == 'ver' and len(partes) == 2:
        almacen = _almacen(partes[1])
        estado = _estado_memoria(home, cwd)
        return 'memoria', estado, _texto_memoria(estado, almacen)
    if accion in ('escribir', 'editar') and len(partes) == 2:
        almacen = _almacen(partes[1])
        if texto is None:
            texto = _editar(unir(memoria.entradas(almacen)), '.md')
        memoria.guardar(almacen, memoria.reemplazar(almacen, texto))
    elif accion == 'quitar' and len(partes) == 3:
        memoria.aplicar(_almacen(partes[1]), [{'accion': 'remove', 'buscar': partes[2]}])
    elif accion == 'vaciar' and len(partes) == 2:
        memoria.guardar(_almacen(partes[1]), [])
    else:
        raise ValueError('Uso: alfalfa --memoria [ver ALMACEN | escribir ALMACEN [--texto T] | quitar ALMACEN TEXTO '
                         '| vaciar ALMACEN]')
    estado = _estado_memoria(home, cwd)
    return 'memoria', estado, _texto_memoria(estado, partes[1])


# --- Habilidades aprendidas ---------------------------------------------------------------------------

def _estado_aprendido(home, cwd):
    ajustes = leer_ajustes(home)
    return {'habilidades': Habilidades(home, cwd).listar(),
            'pendientes': [para_el_cliente(p) for p in Pendientes(home).listar()],
            'ajustes': ajustes, 'proyecto': str(raiz_de_proyecto(cwd)),
            'aprende_aqui': not apagado_en_proyecto(cwd, home, ajustes)}


def _texto_aprendido(estado):
    lineas = ['── Habilidades aprendidas']
    for fila in estado['habilidades']:
        origen = ' · sesión ' + fila['sesion'] + (' turno ' + fila['turno'] if fila.get('turno') else '') \
            if fila.get('sesion') else ''
        lineas.append('  · ' + fila['alcance'] + ':' + fila['nombre'] + (' [bloqueada]' if fila['bloqueada'] else '')
                      + ('' if fila['estado'] == 'al_dia' else ' [' + fila['estado'] + ']') + origen
                      + ('' if fila['de_esta_carpeta'] else ' · ' + str(fila.get('proyecto'))))
        if fila.get('descripcion'):
            lineas.append('    ' + fila['descripcion'][:200])
    if not estado['habilidades']:
        lineas.append('  (ninguna)')
    lineas.append('── Propuestas sin revisar')
    for propuesta in estado['pendientes']:
        lineas.append('  · ' + propuesta['id'] + ' · ' + (
            'memoria ' + str(propuesta.get('almacen')) if propuesta.get('clase') == 'memoria'
            else 'habilidad ' + str(propuesta.get('alcance')) + ':' + str(propuesta.get('nombre')))
            + (' — ' + propuesta['motivo'] if propuesta.get('motivo') else ''))
    if not estado['pendientes']:
        lineas.append('  (ninguna)')
    ajustes = estado['ajustes']
    lineas.append('── Ajustes: ' + ', '.join(k + ' ' + ('sí' if ajustes[k] else 'no') for k in BOOLEANOS)
                  + (', modelo_revision ' + ajustes['modelo_revision'] if ajustes.get('modelo_revision') else '')
                  + ('' if estado['aprende_aqui'] else ' · apagado en esta carpeta'))
    return '\n'.join(lineas)


def _booleano(valor):
    valor = str(valor).lower()
    if valor in SI:
        return True
    if valor in NO:
        return False
    raise ValueError('Usa sí o no.')


def _aprendido(partes, texto, home, cwd):
    accion = partes[0] if partes else 'listar'
    habilidades = Habilidades(home, cwd)
    pendientes = Pendientes(home)
    mensaje = None
    if accion == 'listar' and len(partes) <= 1:
        pass
    elif accion == 'ver' and len(partes) == 2:
        datos = habilidades.ver(*habilidades.resolver(partes[1]))
        texto_ver = '\n'.join('── ' + ruta + '\n' + (contenido if contenido is not None
                                                      else '(no es un archivo normal)')
                              for ruta, contenido in datos['archivos'].items())
        return 'habilidad', {'habilidad': datos}, texto_ver
    elif accion == 'editar' and len(partes) == 2:
        alcance, nombre = habilidades.resolver(partes[1])
        if texto is None:
            actual = habilidades.archivos_actuales(alcance, nombre).get('SKILL.md') or ''
            texto = _editar(actual, '.md')
        habilidades.editar(alcance, nombre, texto)
        mensaje = 'Guardada ' + alcance + ':' + nombre + '.'
    elif accion == 'borrar' and len(partes) == 2:
        alcance, nombre = habilidades.resolver(partes[1])
        habilidades.borrar(alcance, nombre)
        mensaje = 'Borrada ' + alcance + ':' + nombre + ' (está en la papelera de $ALFALFA_HOME/aprendido).'
    elif accion in ('bloquear', 'desbloquear') and len(partes) == 2:
        alcance, nombre = habilidades.resolver(partes[1])
        habilidades.bloquear(alcance, nombre, accion == 'bloquear')
        mensaje = ('Bloqueada ' if accion == 'bloquear' else 'Desbloqueada ') + alcance + ':' + nombre + '.'
    elif accion == 'pendientes' and len(partes) == 1:
        pass
    elif accion == 'pendientes' and len(partes) == 3 and partes[1] == 'ver':
        leida = pendientes.leer(partes[2])
        # Lo que se enseña es el diff recalculado; si no coincide con el guardado, la propuesta no vale.
        propuesta = dict(para_el_cliente(leida), diff=pendientes.verificar(leida))
        return 'pendiente', {'pendiente': propuesta}, (
            propuesta['id'] + ' · ' + str(propuesta.get('motivo') or '') + '\n' + (propuesta.get('diff') or ''))
    elif accion == 'aplicar' and len(partes) == 2:
        mensaje = pendientes.aplicar(partes[1])
    elif accion == 'descartar' and len(partes) == 2:
        pendientes.descartar(partes[1])
        mensaje = 'Propuesta descartada.'
    elif accion == 'ajustes' and len(partes) in (1, 3):
        if len(partes) == 3:
            clave, valor = partes[1], partes[2]
            if clave == 'modelo_revision':
                guardar_ajustes({clave: None if valor.lower() in ('ninguno', 'none', '') else valor}, home)
            elif clave in BOOLEANOS:
                guardar_ajustes({clave: _booleano(valor)}, home)
            else:
                raise ValueError('Ajuste desconocido: ' + clave + '. Son: ' + ', '.join(BOOLEANOS)
                                 + ' y modelo_revision.')
            mensaje = 'Ajuste guardado: vale desde la próxima sesión.'
    elif accion == 'proyecto' and len(partes) == 2:
        cambiar_proyecto(cwd, _booleano(partes[1]), home)
        mensaje = 'Aprendizaje ' + ('encendido' if _booleano(partes[1]) else 'apagado') + ' en ' \
            + str(raiz_de_proyecto(cwd)) + '.'
    else:
        raise ValueError('Uso: alfalfa --aprendido [ver|editar|borrar|bloquear|desbloquear NOMBRE | '
                         'pendientes [ver ID] | aplicar|descartar ID | ajustes [CLAVE VALOR] | proyecto sí|no]')
    estado = _estado_aprendido(home, cwd)
    if mensaje:
        estado['texto'] = mensaje
    return 'aprendido', estado, ((mensaje + '\n') if mensaje else '') + _texto_aprendido(estado)


def _editar(actual, sufijo):
    """En una terminal abre $EDITOR; si no, lee el texto nuevo de la entrada estándar (así lo manda
    la app: un texto en los argumentos se vería en la lista de procesos)."""
    if not sys.stdin.isatty():
        texto = sys.stdin.read()
        if not texto.strip():
            raise ValueError('No llegó ningún texto por la entrada estándar (para vaciar la memoria usa vaciar).')
        return texto
    editor = os.environ.get('VISUAL') or os.environ.get('EDITOR') or 'vi'
    with tempfile.TemporaryDirectory(prefix='alfalfa-') as carpeta:
        ruta = Path(carpeta) / ('editar' + sufijo)
        ruta.write_text(actual, encoding='utf-8')
        subprocess.run([*editor.split(), str(ruta)], check=True)
        return ruta.read_text(encoding='utf-8')
