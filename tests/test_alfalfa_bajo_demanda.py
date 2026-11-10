"""Política on-request de AlfalfaCode: una decisión por intento, sandbox real y motivos.

Las pruebas «real_» lanzan /usr/bin/sandbox-exec; se saltan fuera de macOS.
La política sigue AskForApproval::OnRequest de OpenAI Codex (Apache-2.0; ver
alfalfa/seatbelt/NOTICE y PROCEDENCIA.md).
"""
import asyncio
import io
import json
import os
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from alfalfa.permisos.bajo_demanda import MOTIVOS, OnRequestPolicy, request_options
from alfalfa.permisos.terminal import MOTIVO_TERMINAL, TerminalApproval, describe
from alfalfa.proveedores.nativo import herramientas
from alfalfa.proveedores.nativo.herramientas import ON_REQUEST_TOOLS, TOOLS
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.seatbelt import SANDBOX_EXEC
from alfalfa.stdio import Transport
from alfalfa.terminal import ESCRITURA, TerminalManager

REAL = sys.platform == 'darwin' and os.access(SANDBOX_EXEC, os.X_OK)
real = pytest.mark.skipif(not REAL, reason='requiere /usr/bin/sandbox-exec (macOS)')
PY = [sys.executable, '-I', '-B', '-c']


class Recorder:
    """Aprobación que registra cada petición y responde por motivo ('yes' o 'no')."""

    def __init__(self, **answers):
        self.answers, self.calls, self.denied = answers, [], 0

    async def __call__(self, args):
        self.calls.append(dict(args))
        if self.answers.get(args.get('motivo'), 'no') == 'yes':
            return
        self.denied += 1
        raise ValueError('Comando rechazado; no se ejecutó.')


@pytest.fixture
def session(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    root.mkdir()
    monkeypatch.chdir(root)
    m = TerminalManager('Prueba on-request.', home=tmp_path / 'alfalfa')
    spawned = []
    original = asyncio.create_subprocess_exec

    async def spy(*argv, **kwargs):
        spawned.append(list(argv))
        return await original(*argv, **kwargs)

    monkeypatch.setattr(herramientas.asyncio, 'create_subprocess_exec', spy)
    rt = m.runtime()

    def call(recorder, tool='run_command', **arguments):
        rt.approval_policy = recorder
        spawned.clear()
        return asyncio.run(rt.tools.call(tool, dict(purpose='Prueba', **arguments)))

    try:
        yield rt, root, call, spawned
    finally:
        m.close()


@pytest.fixture
def outside_home():
    """Carpeta real en $HOME, fuera de $TMPDIR, /private/tmp y la carpeta de trabajo."""
    base = Path.home() / 'Library' / 'Caches'
    if not base.is_dir():
        base = Path.home()
    path = Path(tempfile.mkdtemp(prefix='alfalfa-bajo-demanda-prueba ', dir=base))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def commands(spawned):
    """Órdenes lanzadas, sin las sondas /usr/bin/true del sandbox."""
    return [argv for argv in spawned if argv[-1] != '/usr/bin/true']


# ---------------------------------------------------------------- activación

def test_policy_is_enabled_only_by_terminal_manager_and_integrators_keep_their_catalog(session):
    rt, *_ = session
    assert isinstance(rt.command_policy, OnRequestPolicy)
    # El catálogo on-request y, detrás, las herramientas de aprendizaje de AlfalfaCode (alfalfa/aprendizaje).
    assert rt.tool_specs[:len(ON_REQUEST_TOOLS)] == ON_REQUEST_TOOLS
    assert [t['function']['name'] for t in rt.tool_specs[len(ON_REQUEST_TOOLS):]] == ['memoria', 'habilidad']
    integrador = NativeRuntime(rt.manager, dict(rt.task, id='integrador'))
    assert integrador.command_policy is None and integrador.process_sandbox is None and integrador.tool_specs is TOOLS
    run = next(t for t in TOOLS if t['function']['name'] == 'run_command')['function']
    # cwd y timeout_seconds son de ambos (test_run_checks_carpeta.py); lo on-request no.
    assert set(run['parameters']['properties']) == {'argv', 'purpose', 'description', 'cwd', 'timeout_seconds'}
    assert run['description'].startswith('Request user approval for exact argv')
    with pytest.raises(ValueError, match='Sobran: needs_network'):
        asyncio.run(integrador.tools.call('run_command', dict(argv=['pwd'], purpose='x', needs_network=True)))


def test_read_only_still_neither_offers_nor_runs_write_and_exec_tools(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    m = TerminalManager('Solo lectura.', home=tmp_path / 'alfalfa', read_only=True)
    try:
        rt = m.runtime()
        offered = {t['function']['name'] for t in rt.tool_specs if t['function']['name'] not in rt.excluded_tools}
        assert not offered & set(ESCRITURA)
        for tool in ESCRITURA:
            with pytest.raises(ValueError, match='deshabilitada'):
                asyncio.run(rt.tools.call(tool, dict(argv=['pwd'], purpose='x', needs_network=True)))
    finally:
        m.close()


# ---------------------------------------------------------------- contrato

def test_contract_declares_network_and_escalation_with_justification():
    for name in ('run_command', 'run_checks'):
        spec = next(t for t in ON_REQUEST_TOOLS if t['function']['name'] == name)['function']
        props = spec['parameters']['properties']
        assert props['needs_network']['type'] == 'boolean'
        assert props['sandbox_permissions']['enum'] == ['use_default', 'require_escalated']
        assert props['justification']['type'] == 'string'
        assert spec['parameters']['required'] == ['argv', 'purpose']
        assert 'sandbox' in spec['description'] and 'without asking' in spec['description']
        assert 'user approval' not in spec['description']
    assert request_options(dict(argv=['x'], purpose='p')) == (False, False, None)
    assert request_options(dict(sandbox_permissions='require_escalated',
                                justification='  Escribe en ~/.config  ')) == (True, False, 'Escribe en ~/.config')


@pytest.mark.parametrize('extra,error', [
    ({'sandbox_permissions': 'require_escalated'}, 'Solicitud de salida del sandbox rechazada'),
    ({'sandbox_permissions': 'require_escalated', 'justification': 'corta'}, 'justification debe'),
    ({'sandbox_permissions': 'require_escalated', 'justification': '          '}, 'justification debe'),
    ({'sandbox_permissions': 'require_escalated', 'justification': 'Necesito\x07salir del sandbox'},
     'justification debe'),
    ({'sandbox_permissions': 'require_escalated', 'justification': 'x' * 501}, 'justification debe'),
    ({'sandbox_permissions': 'always'}, 'valores permitidos'),
    ({'needs_network': 'true'}, 'booleano'),
    ({'justification': 7}, 'texto'),
])
def test_invalid_exception_requests_are_rejected_before_asking_or_spawning(session, extra, error):
    rt, root, call, spawned = session
    recorder = Recorder(salir_sandbox='yes', red='yes')
    with pytest.raises(ValueError, match=error):
        call(recorder, argv=['touch', 'marca'], **extra)
    assert recorder.calls == [] and spawned == []
    assert not (root / 'marca').exists()


# ---------------------------------------------------------------- normal

@real
@pytest.mark.parametrize('tool', ['run_command', 'run_checks'])
def test_real_normal_command_runs_sandboxed_without_asking(session, tool):
    rt, root, call, spawned = session
    recorder = Recorder()
    result = call(recorder, tool, argv=PY + ['open("hecho", "w").write("x")'])
    assert result['exit_code'] == 0 and result['sandbox'] == 'seatbelt'
    assert result['permisos'] == [] and result['red'] is False
    assert recorder.calls == []
    assert (root / 'hecho').read_text() == 'x'
    [launched] = commands(spawned)
    assert launched[0] == SANDBOX_EXEC and 'network-outbound' not in launched[2]


# ---------------------------------------------------------------- red

def listener():
    server = socket.socket()
    server.bind(('127.0.0.1', 0))
    server.listen(1)
    threading.Thread(target=lambda: server.accept()[0].close(), daemon=True).start()
    return server


@real
def test_real_network_is_asked_before_enabling_and_keeps_write_restrictions(session, outside_home):
    rt, root, call, spawned = session
    server = listener()
    connect = PY + ['import socket,sys; socket.create_connection(("127.0.0.1", int(sys.argv[1])), 5); '
                    # Desde Python 3.13 el error enseña la línea de código: el texto que
                    # se busca no puede aparecer tal cual en ella.
                    'print("cone" + "ctado")',
                    str(server.getsockname()[1])]
    try:
        closed = call(Recorder(), argv=connect)
        assert closed['exit_code'] != 0 and 'conectado' not in closed['output']
        recorder = Recorder(red='yes')
        opened = call(recorder, argv=connect, needs_network=True, justification='Consultar el servicio local')
        assert opened['exit_code'] == 0 and 'conectado' in opened['output'], opened['output']
        [request] = recorder.calls
        assert request['motivo'] == 'red' and request['red'] is True and 'sin_sandbox' not in request
        assert request['justificacion'] == 'Consultar el servicio local'
        [launched] = commands(spawned)
        assert launched[0] == SANDBOX_EXEC and '(allow network-outbound)' in launched[2]
    finally:
        server.close()
    target = outside_home / 'fuera.txt'
    written = call(Recorder(red='yes'), argv=PY + ['open(__import__("sys").argv[1], "w").write("x")', str(target)],
                   needs_network=True)
    assert written['exit_code'] != 0 and 'Operation not permitted' in written['output']
    assert written['sandbox'] == 'seatbelt' and written['red'] is True
    assert not target.exists()


# ---------------------------------------------------------------- peligroso

@real
def test_real_approved_dangerous_command_stays_sandboxed_without_network(session, outside_home):
    rt, root, call, spawned = session
    (root / 'basura').mkdir()
    (root / 'basura' / 'x').write_text('x')
    recorder = Recorder(peligroso='yes')
    result = call(recorder, argv=['rm', '-rf', 'basura'])
    assert result['exit_code'] == 0 and not (root / 'basura').exists()
    [request] = recorder.calls
    assert request['motivo'] == 'peligroso' and request['regla'] == 'rm con opción de fuerza'
    assert 'sin_sandbox' not in request and 'red' not in request
    [launched] = commands(spawned)
    assert launched[0] == SANDBOX_EXEC and 'network-outbound' not in launched[2]
    assert result['sandbox'] == 'seatbelt' and result['red'] is False
    # La misma aprobación no permite salir: rm -rf fuera del sandbox falla y pide reintento.
    keep = outside_home / 'conservar'
    keep.write_text('x')
    recorder = Recorder(peligroso='yes')
    outside = call(recorder, argv=['rm', '-rf', str(keep)])
    assert outside['exit_code'] != 0 and keep.exists()
    assert [c['motivo'] for c in recorder.calls] == ['peligroso', 'reintento']
    assert recorder.calls[1]['motivos'] == ['reintento', 'peligroso']


def test_dangerous_rejection_launches_nothing(session):
    rt, root, call, spawned = session
    (root / 'src').mkdir()
    recorder = Recorder()
    with pytest.raises(ValueError, match='rechazado'):
        call(recorder, argv=['sudo', 'ls'])
    assert [c['motivo'] for c in recorder.calls] == ['peligroso']
    assert recorder.calls[0]['regla'] == 'sudo'
    assert commands(spawned) == []


# ---------------------------------------------------------------- una decisión por intento

@real
def test_real_combined_reasons_are_one_decision(session):
    rt, root, call, spawned = session
    recorder = Recorder(red='yes')
    (root / 'tmp').mkdir()
    result = call(recorder, argv=['rm', '-rf', 'tmp'], needs_network=True)
    assert result['exit_code'] == 0
    [request] = recorder.calls
    assert request['motivos'] == ['red', 'peligroso']


def test_escalation_is_one_unsandboxed_decision(session):
    rt, root, call, spawned = session
    recorder = Recorder(salir_sandbox='yes')
    result = call(recorder, argv=['touch', 'marca'], sandbox_permissions='require_escalated',
                  justification='Prueba de ejecución sin sandbox')
    assert result['exit_code'] == 0 and result['sandbox'] == 'none' and result['red'] is True
    [request] = recorder.calls
    assert request['motivo'] == 'salir_sandbox' and request['sin_sandbox'] == 'Prueba de ejecución sin sandbox'
    # Sin sonda ni sandbox-exec: el argv tal cual.
    assert spawned == [['touch', 'marca']]


def test_sandbox_failure_and_danger_are_one_decision(session, tmp_path):
    rt, root, call, spawned = session
    rt.process_sandbox.executable = str(tmp_path / 'no-existe')
    recorder = Recorder()
    with pytest.raises(ValueError, match='rechazado'):
        call(recorder, argv=['rm', '-rf', 'x'])
    [request] = recorder.calls
    assert request['motivos'] == ['salir_sandbox', 'peligroso']
    assert request['fallo_sandbox'].startswith('No existe ') and spawned == []


# ---------------------------------------------------------------- reintento

@real
def test_real_ordinary_failure_does_not_offer_retry(session):
    rt, root, call, spawned = session
    recorder = Recorder(reintento='yes')
    result = call(recorder, argv=PY + ['import sys; print("1 failed"); sys.exit(3)'])
    assert result['exit_code'] == 3 and 'denegacion_probable' not in result
    assert recorder.calls == [] and len(commands(spawned)) == 1


@real
def test_real_denied_write_keeps_result_and_asks_explicit_retry(session, outside_home):
    rt, root, call, spawned = session
    target = outside_home / 'fuera.txt'
    argv = PY + ['open(__import__("sys").argv[1], "w").write("x")', str(target)]
    recorder = Recorder()
    result = call(recorder, argv=argv)
    assert result['exit_code'] != 0 and 'Operation not permitted' in result['output']
    assert result['denegacion_probable'] is True and result['reintento'].startswith('rechazado')
    [request] = recorder.calls
    assert request['motivo'] == 'reintento' and request['reintento_de'] == result['intento']
    assert request['argv'] == argv and request['sin_sandbox'].startswith('El intento ' + result['intento'])
    # Quien decide ve la línea que delató el bloqueo.
    assert 'PermissionError: [Errno 1] Operation not permitted' in request['sin_sandbox']
    assert not target.exists() and len(commands(spawned)) == 1
    # El intento fallido quedó registrado con su resultado.
    recorded = [op for op in rt.state['operations'] if op.get('intento') == result['intento']]
    assert recorded and recorded[0]['exit_code'] == result['exit_code']


@real
def test_real_approved_retry_runs_exact_command_once_and_links_attempts(session, outside_home):
    rt, root, call, spawned = session
    target = outside_home / 'fuera.txt'
    # Falla también sin sandbox con una palabra indicadora: no debe haber un segundo reintento.
    argv = PY + ['import sys; open(sys.argv[1], "w").write("x"); print("Permission denied"); sys.exit(1)', str(target)]
    recorder = Recorder(reintento='yes')
    result = call(recorder, argv=argv)
    assert [c['motivo'] for c in recorder.calls] == ['reintento']
    first, second = commands(spawned)
    assert first[0] == SANDBOX_EXEC and first[first.index('--') + 1:] == argv
    assert second == argv
    assert target.read_text() == 'x'
    assert result['sandbox'] == 'none' and result['reintento_de'] == recorder.calls[0]['reintento_de']
    assert result['intento_previo']['intento'] == result['reintento_de']
    assert result['intento_previo']['sandbox'] == 'seatbelt' and result['intento_previo']['exit_code'] != 0
    assert result['intento'] == recorder.calls[0]['intento'] != result['reintento_de']
    ids = [op.get('intento') for op in rt.state['operations']]
    assert ids[-2:] == [result['reintento_de'], result['intento']]


# ---------------------------------------------------------------- motivos visibles

REQUESTS = {
    'peligroso': dict(argv=['rm', '-rf', 'x'], purpose='Borrar', motivo='peligroso', motivos=['peligroso'],
                      intento='a1', regla='rm con opción de fuerza'),
    'red': dict(argv=['curl', 'https://example.org'], purpose='Descargar', motivo='red', motivos=['red'],
                intento='a2', red=True, justificacion='Necesita la especificación'),
    'salir_sandbox': dict(argv=['touch', '/etc/x'], purpose='Configurar', motivo='salir_sandbox',
                          motivos=['salir_sandbox'], intento='a3', justificacion='Escribe fuera del proyecto',
                          sin_sandbox='Escribe fuera del proyecto'),
    'reintento': dict(argv=['touch', '/etc/x'], purpose='Configurar', motivo='reintento', motivos=['reintento'],
                      intento='a5', reintento_de='a4', sin_sandbox='El intento a4 terminó con código 1'),
}


def test_the_four_reasons_are_the_same_everywhere():
    # 'regla' (reglas del usuario, test_alfalfa_reglas.py) se suma a los cuatro de la política.
    # 'modo' lo añaden Manual y Aceptar ediciones (permisos/modos.py); 'edicion' y 'aprendizaje' (guardar
    # memoria o una habilidad, alfalfa/aprendizaje) no son de la política.
    assert MOTIVOS == ('peligroso', 'red', 'salir_sandbox', 'reintento', 'regla', 'modo')
    assert set(MOTIVO_TERMINAL) == set(MOTIVOS) | {'edicion', 'aprendizaje', 'contenido_externo'}
    assert set(MOTIVOS) == set(REQUESTS) | {'regla', 'modo'}


@pytest.mark.parametrize('motivo', list(REQUESTS))
def test_terminal_request_names_reason_command_purpose_and_justification(motivo):
    text = describe(REQUESTS[motivo], ' '.join(REQUESTS[motivo]['argv']))
    label = {'peligroso': 'orden peligrosa', 'red': 'necesita red', 'salir_sandbox': 'salir del sandbox',
             'reintento': 'reintento sin sandbox'}[motivo]
    assert text.startswith('AlfalfaCode · Permiso: ' + label)
    assert 'Orden: ' + ' '.join(REQUESTS[motivo]['argv']) in text and 'Propósito: ' in text
    if 'justificacion' in REQUESTS[motivo]:
        assert 'Justificación: ' + REQUESTS[motivo]['justificacion'] in text
    if motivo == 'reintento':
        assert 'Reintento del intento a4' in text


@pytest.mark.parametrize('motivo', list(REQUESTS))
def test_stdio_permission_event_carries_reason_fields(motivo):
    out = io.StringIO()

    async def scenario():
        transport = Transport(out)
        pending = asyncio.ensure_future(transport.approval('.', True)(REQUESTS[motivo]))
        await asyncio.sleep(0.01)
        assert not pending.done(), '--auto no concede excepciones'
        transport.resolve(next(iter(transport.pending)), False, 'Respuesta del cliente')
        with pytest.raises(ValueError):
            await pending

    asyncio.run(scenario())
    event = json.loads(out.getvalue().splitlines()[0])
    request = REQUESTS[motivo]
    assert event['tipo'] == 'permiso' and event['motivo'] == motivo and event['motivos'] == [motivo]
    assert event['argv'] == request['argv'] and event['proposito'] == request['purpose']
    for key in ('justificacion', 'reintento_de', 'regla', 'intento'):
        assert event.get(key) == request.get(key)


# ---------------------------------------------------------------- nunca autoriza por omisión

@pytest.mark.parametrize('answer', [False, 'true', 1, None, 'sí', [True], {'aprobado': True}])
@pytest.mark.parametrize('motivo', list(REQUESTS))
def test_only_boolean_true_grants_an_exception(motivo, answer):
    out = io.StringIO()

    async def scenario():
        transport = Transport(out)
        pending = asyncio.ensure_future(transport.approval('.', True)(REQUESTS[motivo]))
        await asyncio.sleep(0)
        ident = next(iter(transport.pending))
        transport.receive(json.dumps(dict(tipo='permiso', id='desconocido', aprobado=True)))
        assert not pending.done()
        transport.receive(json.dumps(dict(tipo='permiso', id=ident, aprobado=answer)))
        with pytest.raises(ValueError):
            await pending
        transport.receive(json.dumps(dict(tipo='permiso', id=ident, aprobado=True)))

    asyncio.run(scenario())
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r['aprobado'] for r in rows if r['tipo'] == 'permiso_resuelto'] == [False]
    assert sum(r['tipo'] == 'error' for r in rows) == 2


@pytest.mark.parametrize('motivo', list(REQUESTS))
def test_eof_and_cancellation_deny_pending_exceptions(motivo):
    out = io.StringIO()

    async def scenario():
        transport = Transport(out)
        pending = asyncio.ensure_future(transport.approval('.', True)(REQUESTS[motivo]))
        await asyncio.sleep(0)
        transport.stop()  # lo que hacen EOF y `cancelar`
        with pytest.raises(ValueError):
            await pending
        task = asyncio.ensure_future(transport.approval('.', True)(REQUESTS[motivo]))
        await asyncio.sleep(0)
        assert task.done()
        with pytest.raises(ValueError):
            await task

    asyncio.run(scenario())
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r['aprobado'] for r in rows if r['tipo'] == 'permiso_resuelto'] == [False, False]


@pytest.mark.parametrize('motivo', list(REQUESTS))
def test_terminal_auto_and_redirected_yes_never_grant_exceptions(tmp_path, motivo):
    request = dict(REQUESTS[motivo], argv=['pwd'])  # pwd está en la lista cerrada de --auto
    approval = TerminalApproval(tmp_path, auto=True, stdin=io.StringIO('s\n'), stdout=io.StringIO())
    with pytest.raises(ValueError, match='rechazado'):
        asyncio.run(approval(request))
    assert approval.denied == 1


def test_cancelled_exception_request_never_runs(session):
    rt, root, call, spawned = session

    class Cancel:
        denied = 0

        async def __call__(self, args):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        call(Cancel(), argv=['rm', '-rf', 'x'])
    assert commands(spawned) == []


# ---------------------------------------------------------------- --stdio de punta a punta

STDIO_SESSION = '''
import json, sys
from unittest.mock import patch
from alfalfa.terminal import main
from alfalfa.proveedores.nativo.runtime import NativeRuntime
CALLS = [dict(argv=[sys.executable, '-I', '-c', 'open("normal", "w").write("x")'], purpose='Orden normal'),
         dict(argv=['rm', '-rf', 'basura'], purpose='Borrar la carpeta desechable')]
async def inference(self, client, endpoint, payload, **kwargs):
    if endpoint == '/api/show':
        return {'capabilities': ['tools']}
    done = sum(m['role'] == 'tool' for m in self.state['messages'])
    calls = [dict(function=dict(name='run_command', arguments=CALLS[done]))] if done < len(CALLS) else []
    return dict(message=dict(role='assistant', content='' if calls else 'Hecho; sin pruebas.', tool_calls=calls),
                done=True, done_reason='stop', prompt_eval_count=1, eval_count=1)
with patch.object(NativeRuntime, 'request', inference):
    sys.exit(main(['--stdio', 'Encargo']))
'''


@real
def test_real_stdio_normal_command_without_permission_and_dangerous_with_reason(tmp_path):
    (tmp_path / 'basura').mkdir()
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), ALFALFA_HOME=str(tmp_path / 'home'))
    proc = subprocess.Popen([sys.executable, '-c', STDIO_SESSION], cwd=tmp_path, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False, bufsize=0)
    rows = []
    try:
        while True:
            assert select.select([proc.stdout], [], [], 30)[0], 'stdout timeout'
            row = json.loads(proc.stdout.readline())
            rows.append(row)
            if row['tipo'] == 'permiso':
                proc.stdin.write((json.dumps(dict(tipo='permiso', id=row['id'], aprobado=True)) + '\n').encode('utf-8'))
                proc.stdin.flush()
            if row['tipo'] == 'turno_terminado':
                break
        proc.stdin.close()
        proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    permissions = [r for r in rows if r['tipo'] == 'permiso']
    assert [p['motivo'] for p in permissions] == ['contenido_externo', 'peligroso']
    assert all(p['argv'] == ['rm', '-rf', 'basura'] for p in permissions)
    assert permissions[1]['proposito'] == 'Borrar la carpeta desechable'
    assert (tmp_path / 'normal').read_text() == 'x' and not (tmp_path / 'basura').exists()
    assert rows[-1]['tipo'] == 'turno_terminado' and rows[-1]['estado'] == 'completed', rows[-1]


@real
def test_real_plain_terminal_runs_normal_command_without_prompt_and_denies_exceptions(tmp_path, monkeypatch):
    """`alfalfa` sin --stdio ni --auto: sin terminal interactiva cualquier pregunta se niega,
    así que la orden normal sólo puede correr si no se preguntó nada."""
    monkeypatch.chdir(tmp_path)
    m = TerminalManager('Terminal.', home=tmp_path / 'alfalfa')
    try:
        assert isinstance(m.approval.interior, TerminalApproval) and m.approval.auto is False
        m.approval.stdin, m.approval.stdout = io.StringIO('s\n'), io.StringIO()
        rt = m.runtime()
        result = asyncio.run(rt.tools.call('run_command', dict(argv=['touch', 'hecho'], purpose='Crear')))
        assert result['exit_code'] == 0 and result['sandbox'] == 'seatbelt' and (tmp_path / 'hecho').exists()
        assert m.approval.stdout.getvalue() == '' and m.approval.denied == 0
        with pytest.raises(ValueError, match='rechazado'):
            asyncio.run(rt.tools.call('run_command', dict(argv=['rm', '-f', 'hecho'], purpose='Borrar')))
        assert (tmp_path / 'hecho').exists() and m.approval.denied == 1
        assert 'AlfalfaCode · Permiso: orden peligrosa' in m.approval.stdout.getvalue()
    finally:
        m.close()
