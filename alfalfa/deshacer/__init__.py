"""Deshacer y rehacer por turno: el código de la carpeta vuelve a como estaba antes de un mensaje.

Cada turno guarda una instantánea de la carpeta al empezar y otra al terminar
(`instantaneas.py`, en un git paralelo fuera del proyecto) y las apunta en su fila de
`task.json` (`turnos[].instantanea` = {repo, antes, despues}).

Deshacer hasta el turno T devuelve a su instantánea «antes» cada archivo que cambió
desde entonces, pero sólo lo que dejó el agente:

- Un archivo que cambió en T o en un turno posterior (lo dicen las instantáneas de
  cada turno, así que cuenta también lo que cambiaron sus órdenes) se restaura si
  sigue como lo dejó el agente: igual que en la instantánea del final de su último
  turno o, si ésta falta (el proceso murió), igual que la huella `after` de `changes`.
- Si no coincide, lo cambiaste tú después (`editado_despues`); si ningún turno lo
  cambió, el cambio no es del agente (`ajeno`). Ninguno se toca sin que se pida por
  su ruta (`forzar`).

Antes de restaurar se guarda el estado de ahora: rehacer lo devuelve, con la misma
regla (lo que cambiaste después de deshacer no se pisa). Cada deshacer y rehacer
queda en `task.json` (`deshechos`) y en la evidencia: los archivos restaurados que
el agente había tocado reciben una fila `changes` con `via: 'deshacer'` y la revisión
del espacio sube, así que ninguna comprobación anterior sigue valiendo.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from . import irreversibles
from .instantaneas import Almacen, SinInstantanea, sha256_de

__all__ = ['Almacen', 'SinInstantanea', 'activas', 'al_empezar_turno', 'al_terminar_turno', 'vista_deshacer',
           'deshacer', 'vista_rehacer', 'rehacer', 'turno_de', 'turno_anterior']

EDITADO = 'editado_despues'
AJENO = 'ajeno'
GRANDE = 'grande'
LIMPIEZA_CADA = 86400


def activas():
    """`ALFALFA_INSTANTANEAS=0` las apaga."""
    return os.environ.get('ALFALFA_INSTANTANEAS', '1').strip().lower() not in ('0', 'no', 'false')


def _turnos(task):
    return [t for t in task.get('turnos', []) if isinstance(t, dict) and isinstance(t.get('id'), str)]


def turno_de(task, ident):
    turno = next((t for t in _turnos(task) if t['id'] == ident), None)
    if turno is None:
        raise ValueError('Esa sesión no tiene un turno con ese id: ' + str(ident)[:40])
    return turno


def turno_anterior(task, ident):
    """El turno terminado justo antes de `ident` (donde se bifurca la conversación) o None si es el primero."""
    previos = []
    for turno in _turnos(task):
        if turno['id'] == ident:
            cerrados = [t for t in previos if isinstance(t.get('hasta'), int)]
            return cerrados[-1] if cerrados else None
        previos.append(turno)
    raise ValueError('Esa sesión no tiene un turno con ese id: ' + str(ident)[:40])


# MARK: - Instantáneas de cada turno

def _apuntar(task, ident, momento, fila):
    for turno in _turnos(task):
        if turno['id'] == ident:
            datos = turno.setdefault('instantanea', {})
            if 'commit' in fila:
                datos['repo'] = fila['repo']
                datos[momento] = fila['commit']
                if fila.get('grandes'):
                    datos.setdefault('grandes', [])
                    datos['grandes'] = list(dict.fromkeys(datos['grandes'] + fila['grandes']))[:20]
            else:
                datos.setdefault('errores', {})[momento] = fila.get('error', '')[:300]
            return


def _instantanea(hogar, task, ident, momento):
    if not activas() or not task.get('cwd') or not task.get('id'):
        return
    almacen = Almacen(hogar, task['cwd'])
    try:
        fila = almacen.guardar(task['id'], ident + '-' + momento,
                               'Turno ' + ident + ' · ' + ('al empezar' if momento == 'antes' else 'al terminar'))
    except (SinInstantanea, OSError, subprocess.SubprocessError, ValueError) as exc:
        # Sin instantánea el turno sigue: sólo no se podrá deshacer su código.
        fila = {'error': str(exc) or type(exc).__name__}
    _apuntar(task, ident, momento, fila)
    if momento == 'despues' and 'commit' in fila:
        _limpiar_de_vez_en_cuando(almacen)


def al_empezar_turno(hogar, task, ident):
    """La instantánea de antes del turno `ident`; un fallo queda apuntado y el turno sigue."""
    _instantanea(hogar, task, ident, 'antes')


def al_terminar_turno(hogar, task, ident):
    _instantanea(hogar, task, ident, 'despues')


def _limpiar_de_vez_en_cuando(almacen):
    marca = almacen.dir / 'limpieza'
    try:
        if marca.is_file() and time.time() - marca.stat().st_mtime < LIMPIEZA_CADA:
            return
        marca.touch()
        almacen.limpiar()
    except (OSError, SinInstantanea, subprocess.SubprocessError):
        pass


# MARK: - Qué dejó el agente

def _pasos(task, desde):
    """[(fecha, antes, despues)] de los turnos y de los deshacer/rehacer desde `desde`, en orden."""
    pasos = []
    for turno in _turnos(task):
        if (turno.get('inicio') or 0) >= desde:
            datos = turno.get('instantanea') or {}
            pasos.append((turno.get('inicio') or 0, datos.get('antes'), datos.get('despues')))
    for paso in task.get('deshechos') or []:
        if isinstance(paso, dict) and (paso.get('fecha') or 0) >= desde:
            pasos.append((paso['fecha'], paso.get('antes'), paso.get('despues')))
    return sorted(pasos, key=lambda paso: paso[0])


def _dejado(almacen, pasos):
    """({ruta: estado al final del último paso que la cambió}, ¿falta alguno?)."""
    dejado, incompleto = {}, False
    for _, antes, despues in pasos:
        if not antes or not despues or not almacen.existe(antes) or not almacen.existe(despues):
            incompleto = True
            continue
        dejado.update(almacen.cambios(antes, despues))
    return dejado, incompleto


def huella(estado):
    """La última huella `after` de cada archivo de `changes`, como `RunEvidence.fingerprint`."""
    ultimas = {}
    for cambio in (estado or {}).get('changes') or []:
        if isinstance(cambio, dict) and isinstance(cambio.get('path'), str) and isinstance(cambio.get('after'), str):
            ultimas[cambio['path']] = cambio['after']
    return ultimas


def _accion(objetivo, actual):
    if objetivo is None:
        return 'borrar'
    return 'recrear' if actual is None else 'restaurar'


# MARK: - Deshacer

def vista_deshacer(hogar, task, estado, ident):
    """Lo que haría deshacer hasta antes del turno `ident`, sin tocar nada.

    {disponible, motivo?, turno, restaurables: [{ruta, accion}], conflictos: [{ruta, accion,
    motivo}], grandes, irreversibles}; las claves con `_` son internas (para `deshacer`)."""
    turno = turno_de(task, ident)
    datos = turno.get('instantanea') or {}
    vista = {'turno': ident, 'disponible': False, 'restaurables': [], 'conflictos': [],
             'grandes': list(datos.get('grandes') or []),
             'irreversibles': irreversibles.de_mensajes(
                 (estado or {}).get('messages', [])[turno.get('desde') or 0:], task.get('cwd') or '.'),
             'primer_turno': turno_anterior(task, ident) is None}
    if not datos.get('antes'):
        error = (datos.get('errores') or {}).get('antes')
        vista['motivo'] = ('No se guardó la instantánea de ese turno: ' + error if error else
                           'Ese turno no tiene instantánea del código (es de antes de que existieran o '
                           'estaban apagadas).')
        return vista
    almacen = Almacen.de_clave(hogar, datos.get('repo'), task['cwd'])
    if not almacen.existe(datos['antes']):
        vista['motivo'] = 'La instantánea de ese turno ya se limpió por antigua.'
        return vista
    try:
        ahora = almacen.guardar(task['id'], 'ahora', 'Antes de deshacer')
    except SinInstantanea as exc:
        vista['motivo'] = 'No se pudo guardar el estado de ahora para poder rehacer: ' + str(exc)
        return vista
    objetivo = almacen.cambios(ahora['commit'], datos['antes'])
    actuales = almacen.cambios(datos['antes'], ahora['commit'])
    dejado, incompleto = _dejado(almacen, _pasos(task, turno.get('inicio') or 0))
    ultimas = huella(estado)
    for ruta in sorted(objetivo):
        destino, actual = objetivo[ruta], actuales.get(ruta)
        if '160000' in (destino or ('',))[0] + (actual or ('',))[0]:
            continue  # Un submódulo no es contenido de esta carpeta.
        fila = {'ruta': ruta, 'accion': _accion(destino, actual)}
        en_disco = almacen.cwd / ruta
        if actual is None and (en_disco.exists() or en_disco.is_symlink()):
            # Está, pero no en la instantánea de ahora: es demasiado grande para compararlo.
            vista['conflictos'].append(dict(fila, motivo=GRANDE))
        elif ruta in dejado and dejado[ruta] == actual:
            vista['restaurables'].append(fila)
        elif ruta in ultimas and (incompleto or ruta not in dejado) and sha256_de(en_disco) == ultimas[ruta]:
            # Sin la instantánea final de algún turno, vale la huella que dejó el agente.
            vista['restaurables'].append(fila)
        else:
            vista['conflictos'].append(dict(fila, motivo=EDITADO if ruta in dejado else AJENO))
    vista.update(disponible=True, _repo=almacen.clave, _ahora=ahora['commit'], _objetivo=datos['antes'],
                 _destino={r: v for r, v in objetivo.items() if v is not None})
    return vista


def _aplicar(hogar, task, estado, vista, forzar, tipo, extra):
    almacen = Almacen.de_clave(hogar, vista['_repo'], task['cwd'])
    forzar = set(forzar or ())
    rutas = [f['ruta'] for f in vista['restaurables']] + [c['ruta'] for c in vista['conflictos']
                                                          if c['ruta'] in forzar]
    ident = uuid4().hex[:12]
    almacen.guardar_ref(vista['_ahora'], task['id'], tipo + '-' + ident + '-antes')
    hechas, fallos = almacen.restaurar(rutas, vista['_destino'])
    try:
        despues = almacen.guardar(task['id'], tipo + '-' + ident + '-despues', tipo.capitalize())['commit']
    except SinInstantanea:
        despues = None
    paso = {'id': ident, 'tipo': tipo, 'fecha': time.time(), 'repo': almacen.clave, 'antes': vista['_ahora'],
            'despues': despues, 'objetivo': vista['_objetivo'], 'restaurados': hechas,
            'conservados': [c['ruta'] for c in vista['conflictos'] if c['ruta'] not in forzar],
            'fallos': fallos, **extra}
    task.setdefault('deshechos', []).append(paso)
    _a_la_evidencia(estado, almacen.cwd, hechas)
    return paso


def _a_la_evidencia(estado, cwd, rutas):
    """Los restaurados que el agente había tocado cambian de huella y ninguna comprobación sigue valiendo."""
    if estado is None or not rutas:
        return
    ultimas = huella(estado)
    cambios = estado.setdefault('changes', [])
    for ruta in rutas:
        if ruta in ultimas:
            despues = sha256_de(Path(cwd) / ruta)
            if despues is not None and despues != ultimas[ruta]:
                cambios.append({'path': ruta, 'before': ultimas[ruta], 'after': despues, 'via': 'deshacer'})
    revision = estado.get('workspace_revision')
    estado['workspace_revision'] = (revision if isinstance(revision, int) else 0) + 1


def deshacer(hogar, task, estado, ident, forzar=(), vista=None):
    """Devuelve el código a antes del turno `ident`. Apunta el paso en `task` y en `estado` (evidencia)."""
    vista = vista or vista_deshacer(hogar, task, estado, ident)
    if not vista['disponible']:
        raise ValueError(vista.get('motivo') or 'No se puede deshacer ese turno.')
    return _aplicar(hogar, task, estado, vista, forzar, 'deshacer', {'turno': ident})


# MARK: - Rehacer

def _deshecho(task, ident=None):
    pasos = [p for p in task.get('deshechos') or [] if isinstance(p, dict) and p.get('tipo') == 'deshacer']
    if ident:
        paso = next((p for p in pasos if p.get('id') == ident), None)
        if paso is None:
            raise ValueError('Esa sesión no tiene ese deshacer: ' + str(ident)[:40])
        return paso
    paso = next((p for p in reversed(pasos) if not p.get('rehecho')), None)
    if paso is None:
        raise ValueError('No hay nada que rehacer en esta sesión.')
    return paso


def vista_rehacer(hogar, task, ident=None):
    """Lo que haría rehacer el último deshacer (o `ident`): la misma forma que `vista_deshacer`."""
    paso = _deshecho(task, ident)
    vista = {'deshecho': paso['id'], 'turno': paso.get('turno'), 'disponible': False, 'restaurables': [],
             'conflictos': [], 'grandes': [], 'irreversibles': []}
    if paso.get('rehecho'):
        vista['motivo'] = 'Ese deshacer ya se rehízo.'
        return vista
    almacen = Almacen.de_clave(hogar, paso.get('repo'), task['cwd'])
    if not paso.get('despues') or not almacen.existe(paso['antes']) or not almacen.existe(paso['despues']):
        vista['motivo'] = 'Ya no está la instantánea de antes de deshacer.'
        return vista
    try:
        ahora = almacen.guardar(task['id'], 'ahora', 'Antes de rehacer')
    except SinInstantanea as exc:
        vista['motivo'] = 'No se pudo guardar el estado de ahora: ' + str(exc)
        return vista
    desde_deshacer = almacen.cambios(paso['despues'], ahora['commit'])
    objetivo = almacen.cambios(ahora['commit'], paso['antes'])
    restaurados = set(paso.get('restaurados') or ())
    for ruta in sorted(restaurados & set(objetivo)):
        en_disco = (almacen.cwd / ruta).exists() or (almacen.cwd / ruta).is_symlink()
        fila = {'ruta': ruta, 'accion': _accion(objetivo[ruta], True if en_disco else None)}
        if ruta in desde_deshacer:
            vista['conflictos'].append(dict(fila, motivo=EDITADO))
        else:
            vista['restaurables'].append(fila)
    vista.update(disponible=True, _repo=almacen.clave, _ahora=ahora['commit'], _objetivo=paso['antes'],
                 _destino={r: v for r, v in objetivo.items() if v is not None})
    return vista


def rehacer(hogar, task, estado, ident=None, forzar=(), vista=None):
    """Devuelve lo que quitó un deshacer. Lo que cambiaste después sólo se pisa si se pide por su ruta."""
    vista = vista or vista_rehacer(hogar, task, ident)
    if not vista['disponible']:
        raise ValueError(vista.get('motivo') or 'No se puede rehacer.')
    paso = _aplicar(hogar, task, estado, vista, forzar, 'rehacer', {'de': vista['deshecho'],
                                                                   'turno': vista.get('turno')})
    for previo in task.get('deshechos') or []:
        if isinstance(previo, dict) and previo.get('id') == vista['deshecho']:
            previo['rehecho'] = paso['id']
    return paso


def publica(vista):
    """La vista sin sus claves internas, para la app y la terminal."""
    return {k: v for k, v in vista.items() if not k.startswith('_')}
