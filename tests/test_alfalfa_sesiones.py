"""Sesiones de AlfalfaCode con metadatos, id por turno y bifurcación (modelo de Codex)."""
import asyncio
import fcntl
import io
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from alfalfa import sesiones as registro
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.sesiones import bifurcar, url_sin_credenciales
from alfalfa.stdio import Transport, entry, sessions
from alfalfa.terminal import TerminalManager, main


@pytest.mark.parametrize('url,limpia', [
    ('https://usuario:clave@github.com/o/r.git?token=x#y', 'https://github.com/o/r.git'),
    ('https://ghp_secreto@github.com/o/r.git', 'https://github.com/o/r.git'),
    ('ssh://git@github.com/o/r.git', 'ssh://git@github.com/o/r.git'),
    ('ssh://git:clave@github.com/o/r.git', 'ssh://github.com/o/r.git'),
    ('git@github.com:o/r.git', 'git@github.com:o/r.git'),
    ('usuario:clave@host:o/r.git', 'host:o/r.git'),
    ('/ruta/local/repo', '/ruta/local/repo'),
    ('ext::sh -c "curl secreto"', None),
    ('https://github.com/o/r con espacio', None),
    ('', None),
])
def test_url_del_remoto_sin_credenciales(url, limpia):
    assert url_sin_credenciales(url) == limpia


def git(cwd, *args):
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, 'GIT_AUTHOR_NAME': 'p', 'GIT_AUTHOR_EMAIL': 'p@x', 'GIT_COMMITTER_NAME': 'p',
                        'GIT_COMMITTER_EMAIL': 'p@x'})


class Modelo:
    def __init__(self, respuestas):
        self.respuestas, self.payloads = list(respuestas), []

    def request(self):
        modelo = self

        async def request(rt, client, endpoint, payload, **kwargs):
            if endpoint == '/api/show':
                return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
            modelo.payloads.append(json.loads(json.dumps(payload)))
            texto = modelo.respuestas.pop(0) if modelo.respuestas else 'Hecho.'
            return dict(message={'role': 'assistant', 'content': texto}, done=True, done_reason='stop',
                        prompt_eval_count=900, eval_count=10)
        return request


def turno(m, prompt, respuesta='Hecho.'):
    modelo = Modelo([respuesta])

    async def run():
        with patch.object(NativeRuntime, 'request', modelo.request()):
            return await m.run(prompt)
    asyncio.run(asyncio.wait_for(run(), 60))
    return modelo


@pytest.fixture
def proyecto(tmp_path, monkeypatch):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    git(raiz, 'init', '-q', '-b', 'trabajo')
    (raiz / 'a.txt').write_text('a\n')
    git(raiz, 'add', 'a.txt')
    git(raiz, 'commit', '-q', '-m', 'inicio')
    git(raiz, 'remote', 'add', 'origin', 'https://usuario:clave-secreta@example.com/o/r.git')
    monkeypatch.chdir(raiz)
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path / 'alfalfa'))
    return raiz


def test_metadatos_de_la_sesion(proyecto, tmp_path):
    m = TerminalManager('x')
    try:
        meta = json.loads((m.directory / 'task.json').read_text())['meta']
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=proyecto, capture_output=True, text=True).stdout.strip()
        assert meta['id'] == m.task['id'] and meta['cwd'] == str(proyecto.resolve())
        assert meta['modelo'] == 'deepseek-v4.1-flash:cloud' and meta['proveedor'] == 'ollama'
        assert meta['originador'] == 'alfalfa' and meta['fecha'].endswith('Z')
        assert meta['git'] == {'commit': commit, 'rama': 'trabajo', 'url': 'https://example.com/o/r.git'}
        assert 'clave-secreta' not in json.dumps(meta)
    finally:
        m.close()
    suelta = tmp_path / 'suelta'
    suelta.mkdir()
    os.chdir(suelta)
    m = TerminalManager('x')
    try:
        assert 'git' not in m.task['meta']
    finally:
        m.close()


def test_cada_turno_tiene_id_tramo_y_estado(proyecto):
    salida = io.StringIO()
    m = TerminalManager('Primero.', transport=Transport(salida))
    try:
        turno(m, 'Primero.', 'Uno.')
        turno(m, 'Segundo.', 'Dos.')
        t1, t2 = m.task['turnos']
        assert t1['id'] != t2['id'] and len(t1['id']) == 16
        assert (t1['mensaje'], t1['estado'], t2['mensaje'], t2['estado']) == ('Primero.', 'completed', 'Segundo.', 'completed')
        mensajes = json.loads((m.directory / 'native' / m.task['id'] / 'session.json').read_text())['messages']
        assert t1['desde'] == 0 and t1['hasta'] == t2['desde'] and t2['hasta'] == len(mensajes)
        assert mensajes[t2['desde']] == {'role': 'user', 'content': 'Segundo.'}
        assert 'turno_actual' not in m.task
        filas = [json.loads(l) for l in (m.directory / (m.task['id'] + '.jsonl')).read_text().splitlines()]
        assert {f['turno'] for f in filas if f['text'] in ('Uno.', 'Dos.')} == {t1['id'], t2['id']}
        eventos = [json.loads(l) for l in salida.getvalue().splitlines()]
        assert {e['turno'] for e in eventos if e['tipo'] == 'evento' and e['rol'] == 'agent'} == {t1['id'], t2['id']}
    finally:
        m.close()


def test_listados_de_sesiones_y_turnos(proyecto, capsys):
    m = TerminalManager('Primero.')
    try:
        turno(m, 'Primero.')
        turno(m, 'Segundo.')
        ident, ids = m.task['id'], [t['id'] for t in m.task['turnos']]
    finally:
        m.close()
    fila, = [s for s in sessions() if s['id'] == ident]
    assert fila['turnos'] == 2 and 'bifurcada_de' not in fila
    capsys.readouterr()
    assert main(['--turnos', ident, '--json']) == 0
    salida = json.loads(capsys.readouterr().out)
    assert salida['tipo'] == 'turnos' and salida['sesion'] == ident
    assert [t['id'] for t in salida['turnos']] == ids and salida['turnos'][1]['mensaje'] == 'Segundo.'
    assert main(['--turnos', '../fuera', '--json']) == 1


def preparar(proyecto):
    m = TerminalManager('Primero.')
    try:
        turno(m, 'Primero.', 'Respuesta uno.')
        turno(m, 'Segundo.', 'Respuesta dos.')
        return m.task['id'], [t['id'] for t in m.task['turnos']], m.directory.parent
    finally:
        m.close()


def test_bifurcar_desde_un_turno_y_seguir(proyecto):
    ident, (t1, t2), base = preparar(proyecto)
    original = (base / ident / 'task.json').read_text()
    nuevo = bifurcar(base, ident, t1)
    assert nuevo != ident and (base / ident / 'task.json').read_text() == original
    task = json.loads((base / nuevo / 'task.json').read_text())
    assert task['bifurcada_de'] == {'sesion': ident, 'turno': t1} and task['status'] == 'created'
    assert [t['id'] for t in task['turnos']] == [t1] and task['meta']['id'] == nuevo
    estado = json.loads((base / nuevo / 'native' / nuevo / 'session.json').read_text())
    assert [m['content'] for m in estado['messages'] if m['role'] != 'system'] == ['Primero.', 'Respuesta uno.']
    filas = [json.loads(l) for l in (base / nuevo / (nuevo + '.jsonl')).read_text().splitlines()]
    assert filas and all(f.get('turno') == t1 for f in filas if 'turno' in f)
    assert not any('Respuesta dos.' in f['text'] for f in filas)
    for path in [base / nuevo, *(base / nuevo).rglob('*')]:
        assert (path.stat().st_mode & 0o777) in (0o700, 0o600, 0o644)
    # Seguir la bifurcación: el modelo ve el turno 1 y el mensaje nuevo, no el 2.
    m = TerminalManager(resume=nuevo)
    try:
        modelo = turno(m, 'Otra rama.')
        textos = [x['content'] for x in modelo.payloads[0]['messages'] if x['role'] != 'system']
        assert textos[:3] == ['Primero.', 'Respuesta uno.', 'Otra rama.'] and 'Segundo.' not in textos
        assert [t['mensaje'] for t in m.task['turnos']] == ['Primero.', 'Otra rama.']
    finally:
        m.close()
    fila, = [s for s in sessions() if s['id'] == nuevo]
    assert fila['bifurcada_de'] == {'sesion': ident, 'turno': t1}


def test_bifurcar_por_defecto_desde_el_ultimo_turno_y_por_la_orden(proyecto, capsys):
    ident, (t1, t2), base = preparar(proyecto)
    nuevo = bifurcar(base, ident)
    assert json.loads((base / nuevo / 'task.json').read_text())['bifurcada_de']['turno'] == t2
    modelo = Modelo(['Desde la orden.'])
    with patch.object(NativeRuntime, 'request', modelo.request()):
        assert main(['--bifurcar', ident, '--turno', t1, 'Sigue por aquí.']) == 0
    textos = [x['content'] for x in modelo.payloads[0]['messages'] if x['role'] != 'system']
    assert textos[:3] == ['Primero.', 'Respuesta uno.', 'Sigue por aquí.']


def test_bifurcar_conserva_solo_indices_y_punto_de_control_previos(proyecto):
    ident, (t1, t2), base = preparar(proyecto)
    ruta = base / ident / 'native' / ident / 'session.json'
    estado = json.loads(ruta.read_text())
    task = json.loads((base / ident / 'task.json').read_text())
    hasta = task['turnos'][0]['hasta']
    estado['user_messages'] = [1, hasta]
    estado['checkpoint'] = {'estilo': 'codex', 'through': hasta + 1, 'text': 'r', 'usuario': []}
    estado['context_usage'] = {'tokens': 1, 'messages': 1}
    ruta.write_text(json.dumps(estado))
    nuevo = bifurcar(base, ident, t1)
    copia = json.loads((base / nuevo / 'native' / nuevo / 'session.json').read_text())
    assert copia['user_messages'] == [1] and 'checkpoint' not in copia and 'context_usage' not in copia


def test_errores_al_bifurcar(proyecto, capsys):
    ident, (t1, t2), base = preparar(proyecto)
    with pytest.raises(ValueError, match='No existe'):
        bifurcar(base, 'no-existe')
    with pytest.raises(ValueError, match='ese id'):
        bifurcar(base, ident, 'turno-inventado')
    cerrojo = os.open(base / ident / 'lock', os.O_RDWR)
    try:
        fcntl.flock(cerrojo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match='en ejecución'):
            bifurcar(base, ident)
    finally:
        os.close(cerrojo)
    vacia = TerminalManager('x')
    vacia.close()
    with pytest.raises(ValueError, match='ningún turno terminado'):
        bifurcar(base, vacia.task['id'])
    assert main(['--bifurcar', ident]) == 1
    assert 'Indica el encargo' in capsys.readouterr().err
    assert main(['--turno', t1, 'x']) == 1
    with pytest.raises(SystemExit):
        main(['--bifurcar', ident, '--reanudar', ident, 'x'])


def test_licencia_y_procedencia():
    carpeta = Path(registro.__file__).resolve().parent
    assert 'Copyright 2025 OpenAI' in (carpeta / 'NOTICE').read_text()
    assert 'SessionMeta' in (carpeta / 'PROCEDENCIA.md').read_text()


def test_una_sesion_terminada_admite_un_turno_nuevo_con_mensaje(proyecto):
    ident, (t1, t2), base = preparar(proyecto)
    with pytest.raises(ValueError, match='mensaje nuevo'):
        TerminalManager(resume=ident)
    m = TerminalManager('Tercero.', resume=ident)
    try:
        modelo = turno(m, 'Tercero.')
        textos = [x['content'] for x in modelo.payloads[0]['messages'] if x['role'] != 'system']
        assert textos[:5] == ['Primero.', 'Respuesta uno.', 'Segundo.', 'Respuesta dos.', 'Tercero.']
        assert [t['mensaje'] for t in m.task['turnos']] == ['Primero.', 'Segundo.', 'Tercero.']
    finally:
        m.close()
