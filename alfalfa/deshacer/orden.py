"""`alfalfa --volver ID --turno T` y `alfalfa --rehacer ID`: deshacer y rehacer desde la terminal o la app.

- `--alcance todo` (por defecto): la conversación y el código vuelven a antes del
  mensaje del turno T. La conversación sigue en una sesión bifurcada hasta el turno
  anterior (si T es el primero, en una nueva: la app la abre con el mensaje en el cuadro).
- `--alcance conversacion`: sólo la conversación, como hasta ahora; los archivos no cambian.
- `--alcance codigo`: sólo el código, en la misma sesión; la conversación sigue igual.
- `--vista-previa`: dice qué se restauraría, qué no (y por qué) y qué no se puede
  deshacer, sin tocar nada.
- `--forzar RUTA` (se repite): restaurar también ese archivo aunque lo cambiaras después.
  En una terminal interactiva se pregunta archivo por archivo.

Con `--json` responde con líneas del protocolo de `--stdio` (`deshacer_vista`,
`deshecho`, `sesion_cambiada`, `error`).
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from pathlib import Path

from . import SinInstantanea, deshacer, publica, rehacer, turno_anterior, turno_de, vista_deshacer, vista_rehacer

ALCANCES = ('todo', 'conversacion', 'codigo')
MOTIVOS = {'editado_despues': 'lo cambiaste después del turno', 'ajeno': 'no lo cambió el agente',
           'orden': 'lo cambió algo durante un turno con órdenes, sin huella del agente',
           'grande': 'es demasiado grande para compararlo', 'ignorado': 'ahora está ignorado',
           'repo_anidado': 'está en un repositorio anidado', 'ilegible': 'no se puede leer',
           'sin_registro': 'no hay registro de cómo estaba'}
# Los que se proponen restaurar: probablemente los cambió una orden del agente.
PROPUESTOS = {'orden'}
ACCIONES = {'restaurar': 'se restaura', 'recrear': 'se recrea', 'borrar': 'se borra',
            'permisos': 'vuelven sus permisos'}


def argumentos(parser, grupo):
    grupo.add_argument('--volver', metavar='ID', help='Volver a antes del mensaje del turno --turno (ver --alcance)')
    grupo.add_argument('--rehacer', metavar='ID', help='Devolver el código que quitó el último deshacer de la sesión')
    parser.add_argument('--alcance', choices=ALCANCES, default='todo',
                        help='Con --volver: «todo» (conversación y código), «conversacion» o «codigo»')
    parser.add_argument('--forzar', action='append', default=[], metavar='RUTA',
                        help='Con --volver o --rehacer: restaurar también este archivo aunque cambiara después')
    parser.add_argument('--vista-previa', action='store_true',
                        help='Con --volver o --rehacer: decir qué cambiaría sin tocar nada')
    parser.add_argument('--deshecho', metavar='ID', help='Con --rehacer: qué deshacer (por defecto, el último)')


def pedida(args):
    return bool(getattr(args, 'volver', None) or getattr(args, 'rehacer', None))


# MARK: - Sesión guardada

class Sesion:
    """task.json y estado nativo de una sesión que no está en ejecución (se toma su cerrojo)."""

    def __init__(self, hogar, ident, cerrar=True):
        if not isinstance(ident, str) or not ident or '/' in ident or ident.startswith('.'):
            raise ValueError('Id de sesión no válido.')
        self.ident = ident
        self.path = Path(hogar) / 'sesiones' / ident
        if not self.path.is_dir() or self.path.is_symlink():
            raise ValueError('No existe esa sesión en el hogar de AlfalfaCode.')
        self.cerrojo = None
        if cerrar:
            self.cerrojo = os.open(self.path / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(self.cerrojo, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(self.cerrojo)
                raise ValueError('La sesión está en ejecución; espera a que termine el turno.') from None
        self.task = json.loads((self.path / 'task.json').read_text())
        self.nativo = self.path / 'native' / ident
        self.historial = None
        self.estado = None
        if (self.nativo / 'session.json').is_file():
            from ..proveedores.nativo import historial
            self.historial = historial.Historial(self.nativo)
            self.estado, _ = self.historial.cargar(json.loads((self.nativo / 'session.json').read_text()))

    def guardar(self):
        from ..sesiones import _escribir
        self.task['updated_at'] = time.time()
        _escribir(self.path / 'task.json', json.dumps(self.task, ensure_ascii=False))
        if self.estado is not None:
            from ..proveedores.nativo.herramientas import atomic_json
            atomic_json(self.nativo / 'session.json', self.historial.guardar(self.estado))

    def anotar(self, texto, datos):
        """Una fila de sistema en la transcripción, tras el último turno, con los datos del paso."""
        turnos = [t for t in self.task.get('turnos', []) if isinstance(t, dict) and t.get('id')]
        fila = {'role': 'system', 'time': time.time(), 'text': texto, 'deshacer': datos}
        if turnos:
            fila['turno'] = turnos[-1]['id']
        with open(self.path / (self.ident + '.jsonl'), 'a', encoding='utf-8') as salida:
            salida.write(json.dumps(fila, ensure_ascii=False) + '\n')

    def cerrar(self):
        if self.cerrojo is not None:
            os.close(self.cerrojo)
            self.cerrojo = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.cerrar()


# MARK: - Textos

def resumen(paso, vista):
    """«Deshecho: 3 archivos vuelven…»: la línea de la conversación y de la terminal."""
    n = len(paso['restaurados'])
    if paso['tipo'] == 'deshacer':
        cuantos = '1 archivo vuelve' if n == 1 else f'{n} archivos vuelven'
        partes = ['Deshecho: ' + cuantos + ' a antes del mensaje.']
    else:
        cuantos = '1 archivo vuelve a como estaba' if n == 1 else f'{n} archivos vuelven a como estaban'
        partes = ['Rehecho: ' + cuantos + ' antes de deshacer.']
    if paso['conservados']:
        lista = ', '.join(paso['conservados'][:5]) + ('…' if len(paso['conservados']) > 5 else '')
        partes.append(('Se queda como está ' if len(paso['conservados']) == 1 else 'Se quedan como están ')
                      + lista + ': cambió después o no era del agente.')
    if paso['fallos']:
        partes.append('No se pudieron restaurar: ' + ', '.join(f['ruta'] for f in paso['fallos'][:5]) + '.')
    if vista.get('irreversibles'):
        partes.append('No se deshace: ' + '; '.join(_minuscula(i['titulo']) for i in vista['irreversibles']) + '.')
    return ' '.join(partes)


def _minuscula(texto):
    return texto[:1].lower() + texto[1:]


def datos_del_paso(paso, vista):
    return {'id': paso['id'], 'paso': paso['tipo'], 'turno': paso.get('turno'), 'de': paso.get('de'),
            'restaurados': paso['restaurados'][:200], 'conservados': paso['conservados'][:200],
            'fallos': paso['fallos'][:50], 'irreversibles': vista.get('irreversibles') or [],
            'grandes': vista.get('grandes') or [], 'no_cubiertos': (vista.get('no_cubiertos') or [])[:50]}


def _texto_de_vista(vista, rehacer_=False):
    lineas = []
    if not vista['disponible']:
        return 'No se puede ' + ('rehacer' if rehacer_ else 'deshacer el código') + ': ' + vista.get('motivo', '')
    if not vista['restaurables'] and not vista['conflictos']:
        lineas.append('El código ya está como antes de ese mensaje: no hay archivos que restaurar.')
    for fila in vista['restaurables']:
        lineas.append('  ' + fila['ruta'] + ' — ' + ACCIONES[fila['accion']])
    for fila in vista['conflictos']:
        lineas.append('  ' + fila['ruta'] + ' — se queda: ' + MOTIVOS.get(fila['motivo'], fila['motivo'])
                      + ' (--forzar ' + fila['ruta'] + ' para restaurarlo)')
    for fila in vista.get('no_cubiertos') or []:
        lineas.append('  ' + fila['ruta'] + ' — no se toca: ' + MOTIVOS.get(fila['motivo'], fila['motivo']))
    for aviso in vista.get('irreversibles') or []:
        lineas.append('No se deshace · ' + aviso['titulo'] + ': ' + '; '.join(aviso['ordenes'][:3]))
    return '\n'.join(lineas)


def _preguntar(conflictos):
    """En una terminal: ¿restaurar también cada archivo que cambió después? Devuelve las rutas elegidas."""
    elegidas = []
    for fila in conflictos:
        propuesto = fila['motivo'] in PROPUESTOS
        try:
            respuesta = input(fila['ruta'] + ': ' + MOTIVOS.get(fila['motivo'], '') + '. ¿Restaurarlo? '
                              + ('[S/n] ' if propuesto else '[s/N] '))
        except EOFError:
            break
        respuesta = respuesta.strip().lower()
        if respuesta in ('s', 'si', 'sí', 'y', 'yes') or (propuesto and not respuesta):
            elegidas.append(fila['ruta'])
    return elegidas


# MARK: - Orden

def orden(args):
    from ..stdio import Transport
    from ..terminal import home_dir
    hogar = home_dir()
    transporte = Transport(sys.stdout) if args.json else None

    def decir(tipo, linea, **campos):
        if transporte:
            transporte.emit(tipo, **campos)
        elif linea:
            print(linea, flush=True)

    try:
        if args.rehacer:
            _rehacer(hogar, args, decir)
        else:
            if not args.turno:
                raise ValueError('Indica con --turno el turno del mensaje al que quieres volver.')
            _volver(hogar, args, decir)
        return 0
    except (OSError, ValueError, SinInstantanea) as exc:
        decir('error', 'AlfalfaCode · Error: ' + str(exc), texto=str(exc) or 'No se pudo deshacer.')
        return 1


def _interactiva(args):
    return not args.json and sys.stdin.isatty()


def _rehacer(hogar, args, decir):
    if args.vista_previa:
        sesion = Sesion(hogar, args.rehacer, cerrar=False)
        vista = vista_rehacer(hogar, sesion.task, args.deshecho)
        decir('rehacer_vista', _texto_de_vista(vista, True), sesion=args.rehacer, **publica(vista))
        return
    with Sesion(hogar, args.rehacer) as sesion:
        vista = vista_rehacer(hogar, sesion.task, args.deshecho)
        forzar = list(args.forzar)
        if _interactiva(args) and vista['disponible']:
            print(_texto_de_vista(vista, True))
            forzar += _preguntar(vista['conflictos'])
            # Mientras contestabas pudo cambiar algo: se vuelve a comparar con el disco.
            vista = vista_rehacer(hogar, sesion.task, args.deshecho)
        paso = rehacer(hogar, sesion.task, sesion.estado, args.deshecho, forzar, vista=vista)
        texto = resumen(paso, vista)
        sesion.anotar(texto, datos_del_paso(paso, vista))
        sesion.guardar()
    decir('deshecho', texto, sesion=args.rehacer, texto=texto, **datos_del_paso(paso, vista))


def _volver(hogar, args, decir):
    from .. import sesiones as registro
    ident, alcance = args.volver, args.alcance
    if args.vista_previa:
        sesion = Sesion(hogar, ident, cerrar=False)
        vista = vista_deshacer(hogar, sesion.task, sesion.estado, args.turno)
        decir('deshacer_vista', _texto_de_vista(vista), sesion=ident, alcance=alcance, **publica(vista))
        return
    with Sesion(hogar, ident) as origen:
        turno_de(origen.task, args.turno)
        previo = turno_anterior(origen.task, args.turno)
        vista = None
        if alcance != 'conversacion':
            vista = vista_deshacer(hogar, origen.task, origen.estado, args.turno)
            if not vista['disponible']:
                raise ValueError(vista.get('motivo') or 'No se puede deshacer el código de ese turno.')
            if _interactiva(args):
                print(_texto_de_vista(vista))
                args.forzar = list(args.forzar) + _preguntar(vista['conflictos'])
                # Mientras contestabas pudo cambiar algo: se vuelve a comparar con el disco.
                vista = vista_deshacer(hogar, origen.task, origen.estado, args.turno)
                if not vista['disponible']:
                    raise ValueError(vista.get('motivo') or 'No se puede deshacer el código de ese turno.')
        if alcance == 'codigo' or (alcance == 'todo' and previo is None):
            # En la misma sesión: sólo el código, o el primer mensaje (la conversación empieza de cero).
            paso = deshacer(hogar, origen.task, origen.estado, args.turno, args.forzar, vista=vista)
            texto = resumen(paso, vista)
            origen.anotar(texto, datos_del_paso(paso, vista))
            origen.guardar()
    nueva = None
    if alcance != 'codigo' and previo is not None:
        nueva = registro.bifurcar(hogar / 'sesiones', ident, previo['id'])
        if alcance == 'todo':
            # La conversación sigue en la bifurcada: el paso y la evidencia son suyos, para rehacer desde ella.
            with Sesion(hogar, nueva) as destino:
                paso = deshacer(hogar, destino.task, destino.estado, args.turno, args.forzar, vista=vista)
                texto = resumen(paso, vista)
                destino.anotar(texto, datos_del_paso(paso, vista))
                destino.guardar()
    if alcance != 'conversacion':
        decir('deshecho', texto, sesion=nueva or ident, texto=texto, **datos_del_paso(paso, vista),
              **({'nueva_conversacion': True} if alcance == 'todo' and previo is None else {}))
    if nueva:
        decir('sesion_cambiada', 'Sigue en la sesión ' + nueva + ': alfalfa --reanudar ' + nueva + ' "…"',
              id=nueva, bifurcada_de=ident)
    elif alcance != 'codigo' and previo is None:
        decir('volver', 'Era el primer mensaje: empieza una sesión nueva en la misma carpeta.', sesion=ident,
              nueva_conversacion=True)
