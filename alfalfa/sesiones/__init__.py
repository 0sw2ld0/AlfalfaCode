"""Sesiones de AlfalfaCode con metadatos, id por turno y bifurcación.

Adaptado del modelo de sesiones de OpenAI Codex (Apache-2.0; ver NOTICE,
LICENSE y PROCEDENCIA.md de esta carpeta):

- `SessionMeta` (protocol/src/protocol.rs): id, fecha, cwd, modelo, proveedor,
  originador y `GitInfo` (commit, rama y URL del remoto sin credenciales,
  sanitized_git_url.rs). Aquí vive en `task.json`, campo `meta`.
- Turnos (core/src/thread_rollout_truncation.rs): cada mensaje que abre un turno
  le da un id; el turno guarda dónde empieza y termina en el historial.
- Bifurcar (`thread/fork`): copiar la sesión truncada al final de un turno, con
  un id nuevo y `bifurcada_de` = {sesion, turno}. La carpeta de trabajo no se
  copia: la bifurcación sigue en la misma, como en Codex.

Propio de AlfalfaCode: `continuar` abre una sesión nueva que arranca sólo con el
resumen de la última compactación de otra («Seguir en una sesión nueva» del aviso
de tarifa), con `continuada_de` = {sesion}.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

ORIGINADOR = 'alfalfa'
MAX_MENSAJE_TURNO = 200
# Claves del estado nativo que guardan posiciones del historial.
INDICES = ('user_messages',)


def _git(cwd, *args):
    try:
        salida = subprocess.run(['git', '-C', str(cwd), *args], capture_output=True, text=True, timeout=5,
                                env={k: os.environ[k] for k in ('PATH', 'HOME', 'LANG') if k in os.environ})
    except (OSError, subprocess.SubprocessError):
        return None
    return (salida.stdout.strip() or None) if salida.returncode == 0 else None


def url_sin_credenciales(url):
    """SanitizedGitUrl, simplificado: sin usuario ni clave, sin consulta ni fragmento.

    Un ayudante de remoto (`transporte::…`) o un texto con espacios puede llevar
    una orden con secretos: no se guarda nada.
    """
    if not isinstance(url, str) or not url or '::' in url or any(c.isspace() for c in url):
        return None
    partes = urlsplit(url)
    if partes.scheme and partes.netloc:
        servidor = partes.netloc.rsplit('@', 1)[-1]
        usuario = partes.netloc.rsplit('@', 1)[0] if '@' in partes.netloc else ''
        if partes.scheme in ('ssh', 'git+ssh') and usuario and ':' not in usuario:
            servidor = usuario + '@' + servidor  # git@github.com no es un secreto
        return urlunsplit((partes.scheme, servidor, partes.path, '', ''))
    if '@' in url and ':' in url.split('@', 1)[1]:
        usuario, resto = url.split('@', 1)
        return url if ':' not in usuario else resto  # forma scp: usuario@servidor:ruta
    return url if not partes.scheme else None


def git_info(cwd):
    if _git(cwd, 'rev-parse', '--is-inside-work-tree') != 'true':
        return None
    info = {'commit': _git(cwd, 'rev-parse', 'HEAD'), 'rama': _git(cwd, 'rev-parse', '--abbrev-ref', 'HEAD'),
            'url': url_sin_credenciales(_git(cwd, 'remote', 'get-url', 'origin'))}
    return {k: v for k, v in info.items() if v}


def _proveedor(modelo):
    from ..proveedores import separar
    return separar(modelo)[0]


def metadatos(ident, cwd, modelo, esfuerzo):
    meta = dict(id=ident, fecha=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), cwd=cwd, modelo=modelo,
                esfuerzo=esfuerzo, proveedor=_proveedor(modelo), originador=ORIGINADOR)
    git = git_info(cwd)
    if git:
        meta['git'] = git
    return meta


def abrir_turno(task, mensaje, desde):
    turno = dict(id=uuid4().hex[:16], inicio=time.time(), desde=desde, mensaje=mensaje[:MAX_MENSAJE_TURNO],
                 estado='running')
    task.setdefault('turnos', []).append(turno)
    task['turno_actual'] = turno['id']
    return turno


def gasto_del_turno(task, tokens_antes, coste_antes):
    """{tokens, coste?} gastados desde (tokens_antes, coste_antes); el coste sólo si hay precio."""
    gasto = {'tokens': max(0, (task.get('total_tokens') or 0) - (tokens_antes or 0))}
    coste = task.get('coste_usd')
    if isinstance(coste, (int, float)) and not isinstance(coste, bool):
        gasto['coste'] = max(0.0, coste - (coste_antes or 0.0))
    return gasto


def cerrar_turno(task, ident, estado, hasta, cierre=None, gasto=None):
    """Por id: la limpieza de secretos del transporte sustituye los dicts de task."""
    for turno in task.get('turnos', []):
        if isinstance(turno, dict) and turno.get('id') == ident:
            turno.update(fin=time.time(), estado=estado, hasta=hasta)
            if cierre:
                turno['cierre'] = cierre
            if gasto:
                turno.update(gasto)
    task.pop('turno_actual', None)


def turnos(task):
    return [dict(id=t['id'], mensaje=t.get('mensaje', ''), estado=t.get('estado'), inicio=t.get('inicio'),
                 fin=t.get('fin'), **{k: t[k] for k in ('tokens', 'coste') if isinstance(t.get(k), (int, float))})
            for t in task.get('turnos', []) if isinstance(t, dict) and 'id' in t]


def _privada(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def _escribir(path, valor):
    temporal = path.with_name(path.name + '.tmp')
    fd = os.open(temporal, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as salida:
        salida.write(valor)
    os.replace(temporal, path)


def bifurcar(sesiones, ident, turno=None):
    """Nueva sesión con el historial hasta el final de `turno` (por defecto el último cerrado).

    Devuelve el id nuevo. La sesión de origen no cambia; si está en ejecución,
    no se bifurca.
    """
    origen = Path(sesiones) / ident
    if not origen.is_dir() or origen.is_symlink():
        raise ValueError('No existe esa sesión en el hogar de AlfalfaCode.')
    cerrojo = os.open(origen / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(cerrojo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('La sesión está en ejecución; espera a que termine para bifurcarla.') from None
        task = json.loads((origen / 'task.json').read_text())
        cerrados = [t for t in task.get('turnos', []) if isinstance(t, dict) and isinstance(t.get('hasta'), int)]
        if not cerrados:
            raise ValueError('La sesión no tiene ningún turno terminado que bifurcar.')
        elegido = cerrados[-1] if turno is None else next((t for t in cerrados if t['id'] == turno), None)
        if elegido is None:
            raise ValueError('Esa sesión no tiene un turno terminado con ese id: ' + str(turno)[:40])
        nuevo = uuid4().hex[:16]
        destino = _privada(Path(sesiones) / nuevo)
        conservados = task['turnos'][:task['turnos'].index(elegido) + 1]
        ids = {t['id'] for t in conservados}
        estado_origen = origen / 'native' / ident / 'session.json'
        nativo = _privada(_privada(destino / 'native') / nuevo)
        if estado_origen.is_file():
            from ..proveedores.nativo import historial
            # Cualquier versión: la 3 guarda la conversación en messages.jsonl.
            estado = historial.leer(estado_origen)
            hasta = elegido['hasta']
            estado['messages'] = estado.get('messages', [])[:hasta]
            estado['pending'] = []
            for clave in INDICES:
                if isinstance(estado.get(clave), list):
                    estado[clave] = [i for i in estado[clave] if isinstance(i, int) and i < hasta]
            punto = estado.get('checkpoint')
            if isinstance(punto, dict) and punto.get('through', 0) > hasta:
                estado.pop('checkpoint', None)
            estado.pop('context_usage', None)
            historial.escribir(nativo, estado)
            for previa in estado_origen.parent.glob('before-*'):
                if previa.is_file() and not previa.is_symlink():
                    shutil.copy2(previa, nativo / previa.name)
        fin = elegido.get('fin') or time.time()
        filas = []
        transcripcion = origen / (ident + '.jsonl')
        if transcripcion.is_file():
            for linea in transcripcion.read_text().splitlines():
                fila = json.loads(linea)
                if fila.get('turno') in ids or ('turno' not in fila and fila.get('time', 0) <= fin):
                    filas.append(linea)
        _escribir(destino / (nuevo + '.jsonl'), ''.join(fila + '\n' for fila in filas))
        # Las aprobaciones «en esta sesión» no pasan a la bifurcada: es otra sesión.
        for clave in ('turno_actual', 'approval', 'approval_token', 'summary', 'process_group', 'aprobadas_en_sesion'):
            task.pop(clave, None)
        meta = dict(task.get('meta') or {}, id=nuevo, fecha=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        task.update(id=nuevo, status='created', turnos=conservados, meta=meta,
                    bifurcada_de=dict(sesion=ident, turno=elegido['id']), updated_at=time.time())
        _escribir(destino / 'task.json', json.dumps(task, ensure_ascii=False))
        return nuevo
    finally:
        os.close(cerrojo)


# Lo que una sesión continuada hereda de la de origen.
HEREDADO = ('cwd', 'provider', 'runner', 'model', 'effort', 'modo', 'contexto_modo', 'objective', 'padre')
# Lo que no hereda su estado nativo: las incidencias (y sus resoluciones) señalan operaciones de una
# conversación que la nueva ya no ve, y el bucle le pediría resolverlas sin contexto; los avisos
# ya dados y el detector de repeticiones son de la otra conversación. Sí hereda los cambios
# (`changes`: qué archivos tocó el agente, para no tomar por suyos los ajenos), las operaciones y
# comprobaciones que los respaldan, y el plan.
NO_HEREDADO = ('context_usage', 'recortes_tarifa', 'review_steps', 'review_deadline', 'review_closing',
               'length_recovery', 'unresolved', 'unresolved_records', 'resolution_records',
               'legacy_unresolved_records', 'legacy_resolution_records', 'legacy_unresolved_index',
               'resolutions_not_relevant', 'unresolved_nudged', 'verification_nudged', 'empty_nudges',
               'tool_repeats', 'compactacion_sin_efecto', 'aviso_compactacion_sin_efecto')


def continuar(sesiones, ident):
    """Sesión nueva cuyo contexto es sólo el resumen de la última compactación de `ident`.

    La llama el propio proceso de `ident` entre turnos (ya tiene su cerrojo), justo
    después de compactar. Devuelve el id nuevo; la sesión de origen no cambia."""
    origen = Path(sesiones) / ident
    if not origen.is_dir() or origen.is_symlink():
        raise ValueError('No existe esa sesión en el hogar de AlfalfaCode.')
    task = json.loads((origen / 'task.json').read_text())
    estado_origen = origen / 'native' / ident / 'session.json'
    if not estado_origen.is_file():
        raise ValueError('La sesión no tiene conversación que continuar.')
    from ..proveedores.nativo import historial
    estado = historial.leer(estado_origen)
    punto = estado.get('checkpoint')
    if not isinstance(punto, dict) or punto.get('estilo') != 'codex' or not isinstance(punto.get('text'), str):
        raise ValueError('Compacta la sesión antes de seguir en una nueva: hace falta su resumen.')
    nuevo = uuid4().hex[:16]
    destino = _privada(Path(sesiones) / nuevo)
    nativo = _privada(_privada(destino / 'native') / nuevo)
    # Sólo las instrucciones de sistema (el motor las rehace en cada turno) y el resumen,
    # como al compactar entre turnos: el último mensaje del usuario ya está contestado.
    estado['messages'] = list(estado.get('messages') or [])[:1]
    estado['pending'] = []
    for clave in INDICES:
        estado[clave] = []
    estado['checkpoint'] = dict(punto, through=len(estado['messages']), entre_turnos=True)
    for clave in NO_HEREDADO:
        estado.pop(clave, None)
    historial.escribir(nativo, estado)
    for previa in estado_origen.parent.glob('before-*'):
        if previa.is_file() and not previa.is_symlink():
            shutil.copy2(previa, nativo / previa.name)
    ahora = time.time()
    fila = dict(role='system', time=ahora,
                text='Sesión continuada de ' + ident + ': empieza con el resumen de su conversación.',
                compactacion=dict(estado='hecha', motivo='continuacion', antes=punto.get('tokens_antes') or 0,
                                  despues=0, resumen=punto['text']))
    _escribir(destino / (nuevo + '.jsonl'), json.dumps(fila, ensure_ascii=False) + '\n')
    nueva = {k: task[k] for k in HEREDADO if k in task}
    nueva.update(id=nuevo, status='created', turnos=[], continuada_de=dict(sesion=ident), updated_at=ahora,
                 meta=metadatos(nuevo, nueva.get('cwd', ''), nueva.get('model'), nueva.get('effort')))
    _escribir(destino / 'task.json', json.dumps(nueva, ensure_ascii=False))
    return nuevo
