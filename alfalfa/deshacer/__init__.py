"""Deshacer y rehacer por turno: el código de la carpeta vuelve a como estaba antes de un mensaje.

Cada turno guarda una instantánea de la carpeta al empezar y otra al terminar
(`instantaneas.py`, en un git paralelo fuera del proyecto) y las apunta en su fila de
`task.json` (`turnos[].instantanea` = {repo, antes, despues, cambios_desde, cambios_hasta}).
`cambios_desde`/`cambios_hasta` marcan qué filas de `changes` (la evidencia) escribió
el agente durante ese turno.

Deshacer hasta el turno T devuelve a su instantánea «antes» cada archivo que cambió
desde entonces, pero sólo lo que dejó el agente:

- Lo que cambió en un turno y el agente dejó apuntado en `changes` durante ese turno
  se restaura si sigue igual que al final de su último turno.
- Lo que cambió en un turno con órdenes, subagentes o herramientas externas, pero sin
  fila en `changes`, lo hizo probablemente una orden (`orden`): se restaura sólo si lo
  confirmas (la app lo trae marcado; la terminal pregunta).
- Lo que cambió en un turno sin nada de eso (tú u otro programa mientras trabajaba),
  lo que cambiaste después (`editado_despues`) y lo que ningún turno cambió (`ajeno`)
  se queda, salvo que lo pidas por su ruta.
- Nunca se borra algo que ya existía al empezar el turno sólo porque no estaba en la
  instantánea (ignorado, demasiado grande, en un repositorio anidado o ilegible): va a
  `no_cubiertos` con su motivo y al aviso de lo que no se deshace.

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
from .instantaneas import CARPETA, Almacen, SinInstantanea, cubre, limpiar_todo, sha256_de

__all__ = ['Almacen', 'SinInstantanea', 'activas', 'al_empezar_turno', 'al_terminar_turno', 'vista_deshacer',
           'deshacer', 'vista_rehacer', 'rehacer', 'turno_de', 'turno_anterior']

EDITADO = 'editado_despues'
AJENO = 'ajeno'
ORDEN = 'orden'
# Un archivo del agente cuya huella final tomó una orden al acabar: puede llevar lo que tú cambiaste
# mientras corría. No se propone restaurarlo.
ADOPTADO = 'adoptado'
GRANDE = 'grande'
AGENTE = 'agente'
LIMPIEZA_CADA = 86400
# Herramientas que cambian archivos sin dejar fila en `changes`.
ACTUAN = {'run_command', 'run_checks', 'spawn_agent', 'send_input', 'browser_download', 'app_action'}


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

def _apuntar(task, ident, momento, fila, cambios=None):
    for turno in _turnos(task):
        if turno['id'] == ident:
            datos = turno.setdefault('instantanea', {})
            if isinstance(cambios, int):
                datos['cambios_desde' if momento == 'antes' else 'cambios_hasta'] = cambios
            if 'commit' in fila:
                datos['repo'] = fila['repo']
                datos[momento] = fila['commit']
                if fila.get('grandes'):
                    datos.setdefault('grandes', [])
                    datos['grandes'] = list(dict.fromkeys(datos['grandes'] + fila['grandes']))[:20]
            else:
                datos.setdefault('errores', {})[momento] = fila.get('error', '')[:300]
            return


def _instantanea(hogar, task, ident, momento, estado):
    if not activas() or not task.get('cwd') or not task.get('id'):
        return
    cambios = (estado or {}).get('changes')
    cambios = len(cambios) if isinstance(cambios, list) else None
    almacen = Almacen(hogar, task['cwd'])
    try:
        fila = almacen.guardar(task['id'], ident + '-' + momento,
                               'Turno ' + ident + ' · ' + ('al empezar' if momento == 'antes' else 'al terminar'))
    except (SinInstantanea, OSError, subprocess.SubprocessError, ValueError) as exc:
        # Sin instantánea el turno sigue: sólo no se podrá deshacer su código.
        fila = {'error': str(exc) or type(exc).__name__}
    _apuntar(task, ident, momento, fila, cambios)
    if momento == 'despues' and 'commit' in fila:
        _limpiar_de_vez_en_cuando(hogar)


def al_empezar_turno(hogar, task, ident, estado=None):
    """La instantánea de antes del turno `ident`; un fallo queda apuntado y el turno sigue.
    `estado` (el del runtime) marca desde qué fila de `changes` son de este turno."""
    _instantanea(hogar, task, ident, 'antes', estado)


def al_terminar_turno(hogar, task, ident, estado=None):
    _instantanea(hogar, task, ident, 'despues', estado)


def _limpiar_de_vez_en_cuando(hogar):
    """Una vez al día, todos los almacenes del hogar (y fuera los de carpetas borradas)."""
    marca = Path(hogar) / CARPETA / 'limpieza'
    try:
        if marca.is_file() and time.time() - marca.stat().st_mtime < LIMPIEZA_CADA:
            return
        marca.touch()
        limpiar_todo(hogar)
    except (OSError, SinInstantanea, subprocess.SubprocessError):
        pass


# MARK: - Qué dejó el agente

def _llamadas(mensajes):
    """Nombres de las herramientas llamadas en estos mensajes."""
    nombres = set()
    for mensaje in mensajes or []:
        if isinstance(mensaje, dict) and mensaje.get('role') == 'assistant':
            for llamada in mensaje.get('tool_calls') or []:
                funcion = llamada.get('function') if isinstance(llamada, dict) else None
                if isinstance(funcion, dict) and isinstance(funcion.get('name'), str):
                    nombres.add(funcion['name'])
    return nombres


def _registrados(estado, datos):
    """{ruta: 'propio' | 'adoptado'} de lo que el agente apuntó en `changes` durante el turno, o None
    si no se sabe. Cuenta la última fila de cada ruta: si la tomó una orden al acabar
    (`via: 'run_command'`) es `adoptado`, porque puede llevar lo que tú cambiaste mientras corría."""
    desde, hasta = datos.get('cambios_desde'), datos.get('cambios_hasta')
    cambios = (estado or {}).get('changes')
    if not isinstance(desde, int) or not isinstance(hasta, int) or not isinstance(cambios, list):
        return None
    ultima = {}
    for cambio in cambios[desde:hasta]:
        if isinstance(cambio, dict) and isinstance(cambio.get('path'), str):
            ultima[cambio['path']] = cambio.get('via')
    return {ruta: 'adoptado' if via == 'run_command' else 'propio' for ruta, via in ultima.items()}


def _pasos(task, estado, desde):
    """Los turnos y los deshacer/rehacer desde `desde`, en orden: {fecha, antes, despues, registrados, actua}."""
    pasos = []
    mensajes = (estado or {}).get('messages') or []
    for turno in _turnos(task):
        if (turno.get('inicio') or 0) >= desde:
            datos = turno.get('instantanea') or {}
            fin = turno.get('hasta') if isinstance(turno.get('hasta'), int) else len(mensajes)
            nombres = _llamadas(mensajes[turno.get('desde') or 0:fin])
            pasos.append({'fecha': turno.get('inicio') or 0, 'antes': datos.get('antes'),
                          'despues': datos.get('despues'), 'registrados': _registrados(estado, datos),
                          'actua': bool(nombres & ACTUAN) or any(n.startswith('mcp__') for n in nombres)})
    for paso in task.get('deshechos') or []:
        if isinstance(paso, dict) and (paso.get('fecha') or 0) >= desde:
            # Lo restauró AlfalfaCode: cuenta como del agente.
            pasos.append({'fecha': paso['fecha'], 'antes': paso.get('antes'), 'despues': paso.get('despues'),
                          'registrados': True, 'actua': False})
    return sorted(pasos, key=lambda paso: paso['fecha'])


def _dejado(almacen, pasos):
    """({ruta: estado al final del último paso que la cambió}, {ruta: origen}, ¿falta alguno?).

    El origen es `agente` (apuntado en `changes` en ese turno), `adoptado` (su última huella la
    tomó una orden), `orden` (sin apuntar, en un turno con órdenes o subagentes) o `ajeno` (sin
    apuntar y sin nada que lo explique)."""
    dejado, origen, incompleto = {}, {}, False
    for paso in pasos:
        antes, despues = paso['antes'], paso['despues']
        if not antes or not despues or not almacen.existe(antes) or not almacen.existe(despues):
            incompleto = True
            continue
        registrados = paso['registrados']
        for ruta, estado in almacen.cambios(antes, despues).items():
            dejado[ruta] = estado
            if registrados is True or (registrados is not None and registrados.get(ruta) == 'propio'):
                origen[ruta] = AGENTE
            elif registrados is not None and registrados.get(ruta) == 'adoptado':
                origen[ruta] = ADOPTADO
            elif paso['actua'] or registrados is None:
                origen[ruta] = ORDEN
            else:
                origen[ruta] = AJENO
    return dejado, origen, incompleto


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


def _solo_permisos(almacen, task, turno, pasos, antes, ahora, excluidos_antes, excluidos_ahora, objetivo):
    """Lo que cambió sólo de permisos (un `chmod 644` de una clave 0600, un `chmod 755` de una carpeta
    0700). git no lo ve; lo dicen los modos de cada instantánea.

    Devuelve ({archivo: (conflicto, entrada en `antes`)}, {carpeta/: (conflicto, modo)},
    {ignorado: motivo}). Van a conflicto: `orden` si algún turno tuvo órdenes, `ajeno` si no, y
    `editado_despues` si cambiaron después del último turno. Un ignorado no se guarda: sólo se avisa."""
    antes_, ahora_ = excluidos_antes or {}, excluidos_ahora or {}
    ultimo = next((t for t in reversed(_turnos(task)) if (t.get('instantanea') or {}).get('despues')
                   and (t.get('inicio') or 0) >= (turno.get('inicio') or 0)), None)
    final = (almacen.excluidos(ultimo['instantanea']['despues']) if ultimo else None) or {}
    actua = any(paso['actua'] for paso in pasos)

    def distintos(clave, defecto=None):
        a, b = antes_.get(clave), ahora_.get(clave)
        if not isinstance(a, dict) or not isinstance(b, dict):
            return []
        return sorted(r for r in set(a) | set(b) if a.get(r, defecto) != b.get(r, defecto) and r not in objetivo)

    def motivo(clave, ruta, defecto=None):
        modos_final = final.get(clave)
        if isinstance(modos_final, dict) and modos_final.get(ruta, defecto) != ahora_[clave].get(ruta, defecto):
            return EDITADO
        return ORDEN if actua else AJENO

    archivos = {}
    candidatos = distintos('modos')
    if candidatos:
        en_antes = almacen.entradas(antes, candidatos)
        en_ahora = almacen.entradas(ahora, candidatos)
        for ruta in candidatos:
            if en_antes.get(ruta) is not None and en_antes.get(ruta) == en_ahora.get(ruta):
                archivos[ruta] = ({'ruta': ruta, 'accion': 'permisos', 'motivo': motivo('modos', ruta)},
                                  en_antes[ruta])
    carpetas = {}
    candidatas = distintos('modos_carpetas', 0o755)
    if candidatas:
        en_antes = almacen.entradas(antes, candidatas)
        for carpeta in candidatas:
            path = almacen.cwd / carpeta
            if en_antes.get(carpeta) and path.is_dir() and not path.is_symlink():
                fila = {'ruta': carpeta + '/', 'accion': 'permisos', 'motivo': motivo('modos_carpetas', carpeta, 0o755)}
                carpetas[carpeta + '/'] = (fila, antes_['modos_carpetas'].get(carpeta, 0o755))
    ignorados = {ruta: 'ignorado' for ruta in distintos('modos_ignorados')
                 if (almacen.cwd / ruta).exists() and ruta in (antes_.get('modos_ignorados') or {})}
    return archivos, carpetas, ignorados


def _creados(task, estado, turno):
    """Las rutas que el agente apuntó como nuevas (`before` vacío) desde el turno."""
    creados = set()
    cambios = (estado or {}).get('changes')
    for otro in _turnos(task):
        datos = otro.get('instantanea') or {}
        desde, hasta = datos.get('cambios_desde'), datos.get('cambios_hasta')
        if (otro.get('inicio') or 0) < (turno.get('inicio') or 0) or not isinstance(cambios, list) \
                or not isinstance(desde, int) or not isinstance(hasta, int):
            continue
        creados.update(c['path'] for c in cambios[desde:hasta] if isinstance(c, dict)
                       and isinstance(c.get('path'), str) and c.get('before') in (None, 'NEW', 'DELETED'))
    return creados


def _no_cubiertos_registrados(almacen, task, estado, turno, excluidos_antes):
    """Lo que el agente apuntó en `changes` desde el turno y no guardan las instantáneas."""
    salida = {}
    for otro in _turnos(task):
        if (otro.get('inicio') or 0) < (turno.get('inicio') or 0):
            continue
        datos = otro.get('instantanea') or {}
        rutas = set(_registrados(estado, datos) or ())
        finales = almacen.excluidos(datos['despues']) if datos.get('despues') else None
        for ruta in rutas:
            motivo = cubre(excluidos_antes, ruta) or cubre(finales, ruta)
            if motivo:
                salida.setdefault(ruta, motivo)
    return salida


# MARK: - Deshacer

def vista_deshacer(hogar, task, estado, ident):
    """Lo que haría deshacer hasta antes del turno `ident`, sin tocar nada.

    {disponible, motivo?, turno, restaurables: [{ruta, accion}], conflictos: [{ruta, accion,
    motivo}], no_cubiertos: [{ruta, motivo}], grandes, irreversibles}; las claves con `_` son
    internas (para `deshacer`)."""
    turno = turno_de(task, ident)
    datos = turno.get('instantanea') or {}
    cwd = task.get('cwd') or '.'
    avisos = irreversibles.de_mensajes((estado or {}).get('messages', [])[turno.get('desde') or 0:], cwd)
    avisos = irreversibles.juntar(avisos, irreversibles.de_subagentes(
        Path(hogar) / 'sesiones' / str(task.get('id')) / 'subagentes', turno.get('inicio') or 0, cwd))
    vista = {'turno': ident, 'disponible': False, 'restaurables': [], 'conflictos': [], 'no_cubiertos': [],
             'grandes': list(datos.get('grandes') or []), 'irreversibles': avisos,
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
    excluidos_antes = almacen.excluidos(datos['antes'])
    excluidos_ahora = almacen.excluidos(ahora['commit'])
    objetivo = almacen.cambios(ahora['commit'], datos['antes'])
    actuales = almacen.cambios(datos['antes'], ahora['commit'])
    pasos = _pasos(task, estado, turno.get('inicio') or 0)
    dejado, origen, incompleto = _dejado(almacen, pasos)
    ultimas = huella(estado)
    creados = _creados(task, estado, turno)
    no_cubiertos = _no_cubiertos_registrados(almacen, task, estado, turno, excluidos_antes)
    for ruta in sorted(objetivo):
        destino, actual = objetivo[ruta], actuales.get(ruta)
        if '160000' in (destino or ('',))[0] + (actual or ('',))[0]:
            continue  # Un repositorio anidado no es contenido de esta carpeta.
        fila = {'ruta': ruta, 'accion': _accion(destino, actual)}
        en_disco = almacen.cwd / ruta
        if destino is None:
            # Ya existía al empezar el turno aunque no estuviera en la instantánea: no se borra nunca.
            motivo = cubre(excluidos_antes, ruta)
            if motivo:
                no_cubiertos[ruta] = motivo
                continue
            # Sin la lista completa de lo que no entró, sólo se borra lo que el agente dijo crear.
            incompleta = (excluidos_antes is None or excluidos_antes.get('recortado')
                          or excluidos_antes.get('incompleta'))
            if incompleta and ruta not in creados:
                no_cubiertos[ruta] = 'sin_registro'
                continue
        if actual is None and (en_disco.exists() or en_disco.is_symlink()):
            # Está, pero no en la instantánea de ahora: no se puede comparar con lo que dejó el agente.
            vista['conflictos'].append(dict(fila, motivo=cubre(excluidos_ahora, ruta) or GRANDE))
        elif ruta in dejado and dejado[ruta] == actual:
            if origen.get(ruta) == AGENTE and ruta in ultimas and sha256_de(en_disco) != ultimas[ruta]:
                # Igual que al final del turno, pero no igual que la última escritura del agente:
                # alguien lo cambió durante el turno, después de él.
                vista['conflictos'].append(dict(fila, motivo=EDITADO))
            elif origen.get(ruta) == AGENTE:
                vista['restaurables'].append(fila)
            else:
                vista['conflictos'].append(dict(fila, motivo=origen.get(ruta, AJENO)))
        elif ruta in ultimas and (incompleto or ruta not in dejado) and sha256_de(en_disco) == ultimas[ruta]:
            # Sin la instantánea final de algún turno, vale la huella que dejó el agente.
            vista['restaurables'].append(fila)
        else:
            vista['conflictos'].append(dict(fila, motivo=EDITADO if ruta in dejado else AJENO))
    archivos, carpetas, ignorados = _solo_permisos(almacen, task, turno, pasos, datos['antes'], ahora['commit'],
                                                   excluidos_antes, excluidos_ahora, objetivo)
    for ruta, (fila, entrada) in archivos.items():
        vista['conflictos'].append(fila)
        objetivo[ruta] = entrada
    vista['conflictos'] += [fila for fila, _ in carpetas.values()]
    for ruta, motivo in ignorados.items():
        no_cubiertos.setdefault(ruta, motivo)
    vista['no_cubiertos'] = [{'ruta': r, 'motivo': m} for r, m in sorted(no_cubiertos.items())]
    if no_cubiertos:
        vista['irreversibles'] = irreversibles.juntar(vista['irreversibles'], [irreversibles.no_cubiertos(
            vista['no_cubiertos'])])
    vista.update(disponible=True, _repo=almacen.clave, _ahora=ahora['commit'], _objetivo=datos['antes'],
                 _destino={r: v for r, v in objetivo.items() if v is not None}, _desde=turno.get('inicio'),
                 _carpetas={ruta: modo for ruta, (_, modo) in carpetas.items()})
    return vista


def _aplicar(hogar, task, estado, vista, forzar, tipo, extra):
    almacen = Almacen.de_clave(hogar, vista['_repo'], task['cwd'])
    forzar = set(forzar or ())
    rutas = [f['ruta'] for f in vista['restaurables']] + [c['ruta'] for c in vista['conflictos']
                                                          if c['ruta'] in forzar]
    ident = uuid4().hex[:12]
    almacen.guardar_ref(vista['_ahora'], task['id'], tipo + '-' + ident + '-antes')
    carpetas = vista.get('_carpetas') or {}
    hechas, fallos = almacen.restaurar([r for r in rutas if r not in carpetas], vista['_destino'],
                                       creadas_desde=vista.get('_desde'), commit=vista['_objetivo'])
    for carpeta in [r for r in rutas if r in carpetas]:
        try:
            almacen.permisos_de_carpeta(carpeta, carpetas[carpeta])
            hechas.append(carpeta)
        except OSError as exc:
            fallos.append({'ruta': carpeta, 'error': str(exc)[:200]})
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
             'conflictos': [], 'no_cubiertos': [], 'grandes': [], 'irreversibles': []}
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
    actuales = almacen.cambios(paso['antes'], ahora['commit'])
    excluidos_ahora = almacen.excluidos(ahora['commit'])
    restaurados = set(paso.get('restaurados') or ())
    for ruta in sorted(restaurados & set(objetivo)):
        en_disco = (almacen.cwd / ruta).exists() or (almacen.cwd / ruta).is_symlink()
        fila = {'ruta': ruta, 'accion': _accion(objetivo[ruta], True if en_disco else None)}
        if en_disco and ruta in actuales and actuales[ruta] is None:
            # Está en disco pero no en la instantánea de ahora (ignorado, grande…): es de otro, no se pisa.
            vista['conflictos'].append(dict(fila, motivo=cubre(excluidos_ahora, ruta) or GRANDE))
        elif ruta in desde_deshacer:
            vista['conflictos'].append(dict(fila, motivo=EDITADO))
        else:
            vista['restaurables'].append(fila)
    vista.update(disponible=True, _repo=almacen.clave, _ahora=ahora['commit'], _objetivo=paso['antes'],
                 _destino={r: v for r, v in objetivo.items() if v is not None}, _desde=paso.get('fecha'))
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
