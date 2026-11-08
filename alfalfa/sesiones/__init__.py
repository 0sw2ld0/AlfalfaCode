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
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
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


def cerrar_turno(task, ident, estado, hasta, cierre=None):
    """Por id: la limpieza de secretos del transporte sustituye los dicts de task."""
    for turno in task.get('turnos', []):
        if isinstance(turno, dict) and turno.get('id') == ident:
            turno.update(fin=time.time(), estado=estado, hasta=hasta)
            if cierre:
                turno['cierre'] = cierre
    task.pop('turno_actual', None)


def turnos(task):
    return [dict(id=t['id'], mensaje=t.get('mensaje', ''), estado=t.get('estado'), inicio=t.get('inicio'),
                 fin=t.get('fin')) for t in task.get('turnos', []) if isinstance(t, dict) and 'id' in t]


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
            estado = json.loads(estado_origen.read_text())
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
            _escribir(nativo / 'session.json', json.dumps(estado, ensure_ascii=False))
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
        _escribir(destino / (nuevo + '.jsonl'), ''.join(l + '\n' for l in filas))
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
