"""Contrato JSONL y cancelación con procesos reales; inferencia sustituida explícitamente."""
import asyncio
import io
import json
import os
from pathlib import Path
import select
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from alfalfa.stdio import Transport, entry, sessions


@pytest.mark.parametrize('answer', [True, False, 'true', 1, None])
def test_permission_exact_boolean_and_recovery(answer):
    async def exercise():
        out = io.StringIO()
        t = Transport(out)
        approval = t.approval('.', False)
        task = asyncio.create_task(approval(dict(argv=['touch', 'x'], purpose='test')))
        await asyncio.sleep(0)
        ident = next(iter(t.pending))
        t.receive('invalid')
        t.receive(json.dumps(dict(tipo='permiso', id='unknown', aprobado=True)))
        assert not task.done()
        t.receive(json.dumps(dict(tipo='permiso', id=ident, aprobado=answer)))
        if answer is True:
            await task
        else:
            with pytest.raises(ValueError):
                await task
        t.receive(json.dumps(dict(tipo='permiso', id=ident, aprobado=True)))
        rows = [json.loads(line) for line in out.getvalue().splitlines()]
        assert len([r for r in rows if r['tipo'] == 'error']) == 3
        assert [r['aprobado'] for r in rows if r['tipo'] == 'permiso_resuelto'] == [answer is True]
    asyncio.run(exercise())


@pytest.mark.parametrize('method', ['cancelar', 'eof'])
@pytest.mark.parametrize('phase', ['permission', 'command'])
def test_real_process_cancellation(tmp_path, method, phase):
    root = Path(__file__).resolve().parents[1]
    code = '''
import sys, asyncio
from pathlib import Path
from unittest.mock import patch
from alfalfa.terminal import main
from alfalfa.proveedores.nativo.runtime import NativeRuntime
async def work(self, prompt):
    self.task['status'] = 'running'
    await self.tools.call('run_command', dict(argv=[sys.executable, '-c',
        "import os,time; open('child.pid','w').write(str(os.getpid())); time.sleep(120)"], purpose='test',
        needs_network=True))
    await asyncio.sleep(120)
with patch.object(NativeRuntime, 'run', work):
    sys.exit(main(['--stdio', 'test']))
'''
    env = dict(os.environ, PYTHONPATH=str(root), ALFALFA_HOME=str(tmp_path / 'home'),
               OLLAMA_API_KEY='DECOY-SECRET')
    proc = subprocess.Popen([sys.executable, '-c', code], cwd=tmp_path, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    rows = []
    def read():
        assert select.select([proc.stdout], [], [], 10)[0], 'stdout timeout'
        row = json.loads(proc.stdout.readline())
        rows.append(row)
        return row
    try:
        assert read()['tipo'] == 'sesion'
        row = read()
        # Una orden normal ya no pregunta: needs_network fuerza el permiso «red».
        assert row['tipo'] == 'permiso' and row['motivo'] == 'red'
        if phase == 'command':
            proc.stdin.write(json.dumps(dict(tipo='permiso', id=row['id'], aprobado=True)) + '\n')
            proc.stdin.flush()
            assert read()['tipo'] == 'permiso_resuelto'
            import time
            deadline = time.monotonic() + 10
            while not (tmp_path / 'child.pid').exists() and time.monotonic() < deadline:
                time.sleep(.02)
            assert (tmp_path / 'child.pid').exists()
        if method == 'cancelar':
            proc.stdin.write('{"tipo":"cancelar"}\n{"tipo":"cancelar"}\n')
            proc.stdin.flush()
        else:
            proc.stdin.close()
        proc.wait(timeout=15)
        rows.extend(json.loads(line) for line in proc.stdout.read().splitlines())
        stderr = proc.stderr.read()
        assert proc.returncode == 130, stderr
        assert rows[-1]['tipo'] == 'fin' and rows[-1]['codigo_salida'] == 130
        assert sum(r['tipo'] == 'fin' for r in rows) == 1
        assert all(r['version'] == 2 for r in rows)
        task = json.loads(next((tmp_path / 'home').rglob('task.json')).read_text())
        assert task['status'] == 'interrupted'
        if phase == 'command':
            pid = int((tmp_path / 'child.pid').read_text())
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        else:
            assert not (tmp_path / 'child.pid').exists()
            assert any(r['tipo'] == 'permiso_resuelto' and not r['aprobado'] for r in rows)
        assert 'DECOY-SECRET' not in json.dumps(rows) + stderr
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_sessions_valid_corrupt_and_order(tmp_path, monkeypatch):
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path))
    for ident, date in [('old', 1), ('new', 2), ('broken', 0)]:
        directory = tmp_path / 'sesiones' / ident
        directory.mkdir(parents=True)
        (directory / 'task.json').write_text(json.dumps(dict(id=ident, objective='x', cwd='/repo',
            status='interrupted', model='m', effort='low', updated_at=date)) if date else '{')
    rows = sessions()
    assert [r['id'] for r in rows] == ['new', 'old', 'broken']
    assert rows[-1]['estado'] == 'corrupta'


@pytest.mark.parametrize('failed', [False, True])
def test_models_json(failed, capsys):
    args = SimpleNamespace(sesiones=False, modelos=True)
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'models': [{'name': 'test'}]})
    with patch('httpx.Client.get', side_effect=OSError('offline') if failed else None, return_value=response):
        assert entry(args) == int(failed)
    row = json.loads(capsys.readouterr().out)
    assert row['tipo'] == ('error' if failed else 'modelos')


def test_event_failure_and_saved_evidence_redacted(tmp_path, monkeypatch):
    from alfalfa.terminal import TerminalManager
    from alfalfa.proveedores.nativo.runtime import NativeRuntime
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('OLLAMA_API_KEY', 'DECOY-SECRET')
    (tmp_path / '.env').write_text('PRIVATE-CONTENT-DECOY')
    output = io.StringIO()
    transport = Transport(output)
    manager = TerminalManager('test', home=tmp_path / 'home', transport=transport)

    async def fail(rt, prompt):
        with pytest.raises(ValueError):
            await rt.tools.call('read_file', dict(path='.env'))
        rt.manager.event(rt.task, 'assistant', 'DECOY-SECRET')
        rt.state['messages'].append(dict(role='assistant', content='DECOY-SECRET'))
        raise ValueError('DECOY-SECRET')

    try:
        with patch.object(NativeRuntime, 'run', fail):
            assert asyncio.run(manager.run('test')) == 1
        texts = output.getvalue() + ''.join(p.read_text() for p in manager.directory.rglob('*')
                                             if p.is_file())
        assert 'DECOY-SECRET' not in texts
        assert 'PRIVATE-CONTENT-DECOY' not in texts
        assert '[REDACTADO]' in texts
    finally:
        manager.close()


@pytest.mark.parametrize('failure', [False, True])
def test_cli_json_start_execution_failure_and_close(tmp_path, failure):
    code = '''
import sys, asyncio
from unittest.mock import patch
from alfalfa.terminal import main, TerminalManager
from alfalfa.proveedores.nativo.runtime import NativeRuntime
async def work(self, prompt):
    self.task['status'] = 'running'
    self.manager.event(self.task, 'assistant', 'Completado')
    if sys.argv[1] == 'True': raise ValueError('fallo')
    self.task.update(status='completed', summary='Completado')
args = ['--stdio', 'test']
if sys.argv[1] == 'True':
    m = TerminalManager('test')
    m.task['status'] = 'interrupted'
    m.save()
    args = ['--stdio', '--reanudar', m.task['id']]
    m.close()
with patch.object(NativeRuntime, 'run', work):
    sys.exit(main(args))
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
               ALFALFA_HOME=str(tmp_path / 'home'))
    # Keep stdin open until the turn ends; EOF while idle closes without interrupting.
    p = subprocess.Popen([sys.executable, '-c', code, str(failure)], cwd=tmp_path, env=env,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        rows = []
        while not rows or rows[-1]['tipo'] != 'turno_terminado':
            assert select.select([p.stdout], [], [], 10)[0], 'stdout timeout'
            rows.append(json.loads(p.stdout.readline()))
        p.stdin.close()
        p.wait(timeout=15)
        rows.extend(json.loads(line) for line in p.stdout.read().splitlines())
        assert [r['tipo'] for r in rows] == (['sesion', 'evento', 'turno_terminado']
                                             + (['error'] if failure else []) + ['fin'])
        assert rows[-1]['codigo_salida'] == rows[-2 - failure]['codigo_salida'] == p.returncode == int(failure)
        assert rows[-1]['estado'] == ('failed' if failure else 'completed')
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
        p.stdin.close()


@pytest.mark.parametrize('texto', ['', '   \n', 'x' * 20001, None, 7])
def test_invalid_message_reports_error_and_keeps_session(texto):
    async def exercise():
        out = io.StringIO()
        t = Transport(out)
        t.receive(json.dumps(dict(tipo='mensaje', texto=texto)))
        assert t.inbox == []
        t.receive(json.dumps(dict(tipo='mensaje', texto='x' * 20000)))
        assert await asyncio.wait_for(t.next_message(), 1) == 'x' * 20000
        rows = [json.loads(line) for line in out.getvalue().splitlines()]
        assert [r['tipo'] for r in rows] == ['error'] and rows[0]['version'] == 2
        t.stop()
        t.receive(json.dumps(dict(tipo='mensaje', texto='tarde')))
        assert t.inbox == [] and await t.next_message() is None
    asyncio.run(exercise())


# Inferencia sustituida: registra cada petición y decide la respuesta según el
# historial real del motor. El resto (herramientas, permisos, procesos,
# persistencia y lector JSONL) es el código de producción en un proceso aparte.
MESSAGES_SESSION = '''
import json, sys
from unittest.mock import patch
from alfalfa.terminal import main
from alfalfa.proveedores.nativo.runtime import NativeRuntime
WAIT = ("import os, pathlib, time\\n"
        "open('child.pid', 'w').write(str(os.getpid()))\\n"
        "while not pathlib.Path('release').exists(): time.sleep(.02)\\n"
        "open('done', 'w').write('ok')\\n")
async def inference(self, client, endpoint, payload, **kwargs):
    if endpoint == '/api/show':
        return {'capabilities': ['tools']}
    with open('requests.jsonl', 'a') as stream:
        stream.write(json.dumps(payload['messages']) + '\\n')
    calls = []
    if sys.argv[1] == 'tool' and not any(m['role'] == 'tool' for m in self.state['messages']):
        calls = [dict(function=dict(name='run_command', arguments=dict(
            argv=[sys.executable, '-c', WAIT], purpose='Esperar', needs_network=True)))]
    last = payload['messages'][-1]
    return dict(message=dict(role='assistant', content='' if calls else 'Respuesta a: ' + last['content'][:40],
                             tool_calls=calls), done=True, done_reason='stop', prompt_eval_count=1, eval_count=1)
with patch.object(NativeRuntime, 'request', inference):
    sys.exit(main(['--stdio', 'Encargo inicial']))
'''


class Client:
    def __init__(self, tmp_path, mode):
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
                   ALFALFA_HOME=str(tmp_path / 'home'))
        self.cwd = tmp_path
        self.rows = []
        self.proc = subprocess.Popen([sys.executable, '-c', MESSAGES_SESSION, mode], cwd=tmp_path, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True)

    def until(self, tipo):
        while True:
            assert select.select([self.proc.stdout], [], [], 10)[0], 'stdout timeout'
            row = json.loads(self.proc.stdout.readline())
            self.rows.append(row)
            if row['tipo'] == tipo:
                return row

    def send(self, **value):
        self.proc.stdin.write(json.dumps(value) + '\n')
        self.proc.stdin.flush()

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=15)
        self.rows.extend(json.loads(line) for line in self.proc.stdout.read().splitlines())
        return self.proc.stderr.read()

    def requests(self):
        return [json.loads(line) for line in (self.cwd / 'requests.jsonl').read_text().splitlines()]

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


def test_message_during_running_tool_reaches_next_turn_without_interrupting(tmp_path):
    import time
    client = Client(tmp_path, 'tool')
    try:
        permission = client.until('permiso')
        client.send(tipo='mensaje', texto='Primero: durante el permiso')
        client.send(tipo='permiso', id=permission['id'], aprobado=True)
        client.until('permiso_resuelto')
        deadline = time.monotonic() + 10
        while not (tmp_path / 'child.pid').exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert (tmp_path / 'child.pid').exists()
        client.send(tipo='mensaje', texto='Segundo: durante la orden')
        client.send(tipo='mensaje', texto='')
        assert client.until('error')['tipo'] == 'error'
        time.sleep(.3)
        assert len(client.requests()) == 1, 'el mensaje no debe adelantar la inferencia'
        assert not (tmp_path / 'done').exists() and client.proc.poll() is None
        (tmp_path / 'release').write_text('')
        turn = client.until('turno_terminado')
        assert turn['estado'] == 'completed' and turn['codigo_salida'] == 0
        assert (tmp_path / 'done').read_text() == 'ok'
        first, second = client.requests()
        assert not any('durante' in m['content'] for m in first)
        roles = [(m['role'], m['content']) for m in second[-3:]]
        assert roles[0][0] == 'tool' and json.loads(roles[0][1])['exit_code'] == 0
        assert roles[1:] == [('user', 'Primero: durante el permiso'), ('user', 'Segundo: durante la orden')]
        users = [r['texto'] for r in client.rows if r['tipo'] == 'evento' and r['rol'] == 'user']
        assert users == ['Primero: durante el permiso', 'Segundo: durante la orden']
        stderr = client.close()
        assert client.proc.returncode == 0, stderr
        assert [r['tipo'] for r in client.rows].count('fin') == 1
        assert client.rows[-1]['tipo'] == 'fin' and client.rows[-1]['estado'] == 'completed'
    finally:
        client.kill()


def test_message_after_finish_resumes_as_new_turn_and_rejects_invalid(tmp_path):
    client = Client(tmp_path, 'plain')
    try:
        assert client.until('turno_terminado')['estado'] == 'completed'
        for texto in ['', ' \t\n', 'x' * 20001]:
            client.send(tipo='mensaje', texto=texto)
            assert '20000' in client.until('error')['texto']
        assert len(client.requests()) == 1
        client.send(tipo='mensaje', texto='y' * 20000)
        turn = client.until('turno_terminado')
        assert turn['estado'] == 'completed' and turn['codigo_salida'] == 0
        client.send(tipo='mensaje', texto='Tercer turno')
        assert client.until('turno_terminado')['resumen'] == 'Respuesta a: Tercer turno'
        requests = client.requests()
        assert len(requests) == 3
        assert requests[1][-1] == {'role': 'user', 'content': 'y' * 20000}
        assert requests[2][-1] == {'role': 'user', 'content': 'Tercer turno'}
        assert ('user', 'Encargo inicial') in [(m['role'], m['content']) for m in requests[2]]
        stderr = client.close()
        assert client.proc.returncode == 0, stderr
        tipos = [r['tipo'] for r in client.rows]
        assert tipos.count('turno_terminado') == 3 and tipos.count('fin') == 1 and tipos[-1] == 'fin'
        assert all(r['version'] == 2 for r in client.rows)
        task = json.loads(next((tmp_path / 'home').rglob('task.json')).read_text())
        assert task['status'] == 'completed'
    finally:
        client.kill()
