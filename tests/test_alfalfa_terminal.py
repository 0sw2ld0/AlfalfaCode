"""S1: contratos reales del runtime; sólo se sustituye inferencia en unitarias."""
import asyncio
import io
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from alfalfa.permisos.terminal import TerminalApproval
from alfalfa.proveedores.nativo.herramientas import digest
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.terminal import TerminalManager, main

REPO = Path(__file__).resolve().parents[1]


def manager(tmp_path, monkeypatch, **kwargs):
    root = tmp_path / 'project'
    root.mkdir(exist_ok=True)
    monkeypatch.chdir(root)
    return TerminalManager('Corrige una línea.', home=tmp_path / 'alfalfa', **kwargs)


def test_identity_parameters_and_session_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path / 'chosen'))
    monkeypatch.chdir(tmp_path)
    m = TerminalManager('Corrige una línea.')
    try:
        rt = m.runtime()
        text = rt.system_instructions(m.task['model'])
        assert 'AlfalfaCode' in text
        for forbidden in ('ticket', 'MCP', 'inspect_image', 'imágenes'):
            assert forbidden not in text
        assert 'fuera de la carpeta' in text and 'commit ni push' in text
        assert m.directory.parent == tmp_path / 'chosen/sesiones'
        for path in [m.directory, *m.directory.rglob('*')]:
            if path.is_dir():
                assert path.stat().st_mode & 0o777 == 0o700
        assert (m.directory / 'task.json').stat().st_mode & 0o777 == 0o600
        with pytest.raises(ValueError, match='ya está en ejecución'):
            TerminalManager(resume=m.task['id'], home=tmp_path / 'chosen')
        with pytest.raises(ValueError, match='No se pudo leer'):
            asyncio.run(rt.tools.call('read_web', dict(url='http://localhost:9/')))
        with pytest.raises(ValueError):
            asyncio.run(rt.tools.call('write_file', dict(path='../outside', content='x', expected_sha256='NEW')))
        override = NativeRuntime(m, m.task, instructions='RULES', agent_tone='TONE',
                                 engine_text=lambda e: 'ENGINE', manual=lambda model: 'MANUAL')
        with patch('alfalfa.proveedores.nativo.runtime.entorno', return_value='ENV'):
            assert override.system_instructions('x').startswith('TONERULES\nENGINE')
    finally:
        m.close()


def test_default_home_and_invalid_resume(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'user'))
    monkeypatch.delenv('ALFALFA_HOME', raising=False)
    monkeypatch.chdir(tmp_path)
    m = TerminalManager('Consulta local')
    try:
        assert m.directory.parent == tmp_path / 'user/.alfalfa/sesiones'
    finally:
        m.close()
    with pytest.raises(ValueError, match='inválido'):
        TerminalManager(resume='../escape')
    with pytest.raises(ValueError, match='No existe'):
        TerminalManager(resume='missing')


def test_auto_git_status_does_not_execute_project_fsmonitor(tmp_path, monkeypatch):
    m = manager(tmp_path, monkeypatch, auto=True)
    try:
        subprocess.run(['git', 'init'], check=True, capture_output=True)
        hook = Path.cwd() / 'hook.sh'
        hook.write_text('#!/bin/sh\ntouch monitor-executed\n')
        hook.chmod(0o755)
        subprocess.run(['git', 'config', 'core.fsmonitor', str(hook)], check=True)
        result = asyncio.run(m.runtime().tools.call('run_command', dict(argv=['git', 'status', '--short'],
                                                                           purpose='Consultar cambios')))
        assert result['exit_code'] == 0
        assert not (Path.cwd() / 'monitor-executed').exists()
    finally:
        m.close()


@pytest.mark.parametrize('tool', ['run_command', 'run_checks'])
def test_auto_rm_in_untracked_directory_denied(tmp_path, monkeypatch, tool):
    m = manager(tmp_path, monkeypatch, auto=True)
    (Path.cwd() / 'src').mkdir()
    target = Path.cwd() / 'src/valuable'
    target.write_text('keep')
    m.approval.stdin, m.approval.stdout = io.StringIO('s\n'), io.StringIO()
    try:
        with pytest.raises(ValueError, match='rechazado'):
            asyncio.run(m.runtime().tools.call(tool, dict(argv=['rm', '-rf', 'src'], purpose='No borrar')))
        assert target.read_text() == 'keep'
    finally:
        m.close()


async def local_inference(self, client, endpoint, payload, **kwargs):
    if endpoint == '/api/show':
        return {'capabilities': ['tools']}
    names = [t['function']['name'] for t in payload['tools']]
    assert {'web_search', 'read_web'} <= set(names) and 'inspect_image' not in names
    turns = sum(msg.get('role') == 'assistant' for msg in self.state['messages'])
    function = None
    if turns == 0:
        function = dict(name='read_file', arguments=dict(path='value.txt'))
    elif turns == 1:
        function = dict(name='edit_file', arguments=dict(path='value.txt', old='bad', new='good',
                                                       expected_sha256=digest(b'bad')))
    return dict(message=dict(role='assistant',
                             content='' if function else 'Corregido value.txt; pruebas no ejecutadas.',
                             tool_calls=[dict(function=function)] if function else []),
                done=True, done_reason='stop', prompt_eval_count=10, eval_count=2)


def test_native_runtime_completes_local_task_without_cuysearch(tmp_path, monkeypatch):
    monkeypatch.delenv('ALFALFA_CUYSEARCH_URL', raising=False)
    m = manager(tmp_path, monkeypatch)
    (Path.cwd() / 'value.txt').write_text('bad')
    permisos = []

    class Aprobar:
        denied = 0

        async def __call__(self, args):
            permisos.append(args)

    m.approval.interior = Aprobar()
    try:
        with patch.object(NativeRuntime, 'request', local_inference):
            assert asyncio.run(m.run(m.task['objective'])) == 0
        assert (Path.cwd() / 'value.txt').read_text() == 'good'
        assert m.task['status'] == 'completed'
        assert m.task['total_tokens'] == 36
        assert permisos == []  # leer el proyecto no obliga a aprobar la edición en modo auto
        for name in ('event', 'save', 'transcript', 'context', 'conversation_task_error', 'terminate',
                     'directory', 'tasks', 'runners', 'processes', 'native_approvals', 'vision_slots'):
            assert hasattr(m, name)
    finally:
        m.close()


# Con la política on-request sólo preguntan las excepciones: órdenes peligrosas,
# red y salida del sandbox. Rechazarlas nunca lanza la orden.
DENIED = [
    (['rm', '-rf', 'src'], {}), (['rm', '-f', 'tracked'], {}), (['/bin/rm', '-fr', 'src'], {}),
    (['sudo', 'ls'], {}), (['env', 'X=1', 'rm', '-rf', 'src'], {}), (['sh', '-c', 'rm -rf src'], {}),
    (['bash', '-lc', 'cd src && rm --force valuable'], {}),
    (['python3', 'script.py'], {'needs_network': True}),
    (['git', 'push'], {'needs_network': True, 'justification': 'Publicar la rama de prueba'}),
    (['touch', 'unwanted'], {'sandbox_permissions': 'require_escalated',
                             'justification': 'Necesita escribir fuera del sandbox'}),
]


def only_sandbox_probe(original):
    """create_subprocess_exec que sólo deja pasar la sonda /usr/bin/true del sandbox."""
    async def spawn(*argv, **kwargs):
        if argv[-1] != '/usr/bin/true':
            raise AssertionError('must not spawn')
        return await original(*argv, **kwargs)
    return spawn


@pytest.mark.parametrize('auto', [False, True])
@pytest.mark.parametrize('tool', ['run_command', 'run_checks'])
@pytest.mark.parametrize('argv,extra', DENIED)
def test_denial_never_changes_real_dirty_repo(tmp_path, monkeypatch, auto, tool, argv, extra):
    m = manager(tmp_path, monkeypatch, auto=auto)
    root = Path.cwd()
    def git(*args):
        return subprocess.run(['git', *args], check=True, capture_output=True).stdout
    git('init', '-b', 'main')
    (root / 'tracked').write_text('saved')
    git('add', 'tracked')
    git('-c', 'user.name=Test', '-c', 'user.email=test@local', 'commit', '-m', 'fixture')
    git('branch', 'keep')
    (root / 'tracked').write_text('unsaved')
    (root / 'src').mkdir()
    (root / 'src/valuable').write_text('untracked')
    (root / 'script.py').write_text("open('unwanted','w').write('x')")
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    m.approval.stdin = io.StringIO('s\n')  # redirected yes must never grant
    m.approval.stdout = io.StringIO()
    try:
        with patch('asyncio.create_subprocess_exec', only_sandbox_probe(asyncio.create_subprocess_exec)):
            with pytest.raises(ValueError, match='rechazado'):
                asyncio.run(m.runtime().tools.call(tool, dict(argv=argv, purpose='Prueba de rechazo', **extra)))
        after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        assert before == after
        assert m.approval.denied == 1
        assert 'AlfalfaCode · Permiso: ' in m.approval.stdout.getvalue()
    finally:
        m.close()


def test_auto_read_grant_and_noninteractive_deny(tmp_path, monkeypatch):
    m = manager(tmp_path, monkeypatch, auto=True)
    try:
        m.approval.stdin = io.StringIO('s\n')
        m.approval.stdout = io.StringIO()
        result = asyncio.run(m.runtime().tools.call('run_command', dict(argv=['pwd'], purpose='Consultar carpeta')))
        assert result['exit_code'] == 0 and str(Path.cwd()) in result['output']
        # --auto no concede excepciones: ni una lectura de la lista cerrada si pide red.
        with pytest.raises(ValueError, match='rechazado'):
            asyncio.run(asyncio.wait_for(m.runtime().tools.call('run_command', dict(
                argv=['pwd'], purpose='Consulta', needs_network=True)), 5))
        assert 'pwd' in m.approval.stdout.getvalue() and 'Consulta' in m.approval.stdout.getvalue()
        assert 'necesita red' in m.approval.stdout.getvalue()
    finally:
        m.close()


@pytest.mark.parametrize('answer,accepted', [('s\n', True), ('n\n', False), ('\n', False), ('', False)])
def test_interactive_query_and_answer(tmp_path, answer, accepted):
    import pty
    master, slave = pty.openpty()
    class Output(io.StringIO):
        def isatty(self): return True
    output = Output()
    async def exercise():
        with os.fdopen(slave, 'r') as stream:
            approval = TerminalApproval(tmp_path, stdin=stream, stdout=output)
            async def reply():
                await asyncio.sleep(.01)
                os.write(master, answer.encode() if answer else b'\x04')
            reply_task = asyncio.create_task(reply())
            try:
                if accepted:
                    await asyncio.wait_for(approval(dict(argv=['pwd'], purpose='Comprobar ubicación')), 1)
                else:
                    with pytest.raises(ValueError, match='rechazado'):
                        await asyncio.wait_for(approval(dict(argv=['pwd'], purpose='Comprobar ubicación')), 1)
            finally:
                await reply_task
    try:
        asyncio.run(exercise())
        assert 'pwd' in output.getvalue() and 'Comprobar ubicación' in output.getvalue()
        assert '[s/N]' in output.getvalue()
    finally:
        os.close(master)


def test_resume_uses_engine_uncertainty_and_blocks_new_duplicate(tmp_path, monkeypatch):
    m = manager(tmp_path, monkeypatch)
    ident = m.task['id']
    rt = m.runtime()
    function = dict(name='run_command', arguments=dict(argv=['touch', 'must-not-exist'], purpose='No repetir'))
    rt.state['pending'] = [dict(function=function, status='executing')]
    m.task['status'] = 'interrupted'
    rt.save()
    m.close()
    m = TerminalManager(resume=ident, home=tmp_path / 'alfalfa')
    try:
        rt = m.runtime()
        with patch('asyncio.create_subprocess_exec', side_effect=AssertionError('uncertain replay')):
            asyncio.run(rt.pending())
            assert 'incierto' in rt.state['messages'][-1]['content']
            rt.state['pending'] = [dict(function=function, status='queued')]
            asyncio.run(rt.pending())
        assert not (Path.cwd() / 'must-not-exist').exists()
        assert 'No se ejecutó' in rt.state['messages'][-1]['content']
    finally:
        m.close()


def test_failure_and_interruption_exit_codes(tmp_path, monkeypatch):
    for failure, expected in [(ValueError('provider unavailable'), 1), (asyncio.CancelledError(), 130)]:
        m = manager(tmp_path, monkeypatch)
        try:
            with patch.object(NativeRuntime, 'run', side_effect=failure):
                assert asyncio.run(m.run('test')) == expected
            assert m.task['status'] != 'completed'
        finally:
            m.close()


def test_launcher_from_other_directory_and_symlink(tmp_path):
    link = tmp_path / 'alfalfa'
    link.symlink_to(REPO / 'scripts/alfalfa')
    result = subprocess.run([str(link), '--help'], cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and 'AlfalfaCode' in result.stdout


def test_execute_resume_no_integrator_data_or_imports(tmp_path):
    code = '''
import sys, os, asyncio, json
from pathlib import Path
blocked = Path.home() / 'Library/Application Support/Integrador'
def audit(event, args):
    if event in ('open', 'os.listdir', 'os.scandir', 'os.mkdir', 'os.rename', 'os.remove'):
        for arg in args:
            if isinstance(arg, (str, bytes)) and str(blocked) in str(arg):
                raise AssertionError('integrator data accessed: ' + event)
sys.addaudithook(audit)
from unittest.mock import patch
from alfalfa.terminal import TerminalManager
from alfalfa.proveedores.nativo.runtime import NativeRuntime
async def inference(self, client, endpoint, payload, **kwargs):
    if endpoint == '/api/show': return {'capabilities':['tools']}
    return {'message':{'role':'assistant','content':'Tarea local completada.'}, 'done':True}
m = TerminalManager('Consulta local')
ident = m.task['id']
async def interrupted(self, prompt): raise asyncio.CancelledError()
with patch.object(NativeRuntime, 'run', interrupted):
    assert asyncio.run(m.run('Consulta local')) == 130
m.close()
m = TerminalManager(resume=ident)
with patch.object(NativeRuntime, 'request', inference):
    assert asyncio.run(m.run('Continúa')) == 0
assert m.directory.parent == Path(os.environ['ALFALFA_HOME']) / 'sesiones'
m.close()
for module in sys.modules:
    assert module != 'app' and not module.startswith('app.'), module
'''
    env = dict(os.environ, HOME=str(tmp_path / 'home'), ALFALFA_HOME=str(tmp_path / 'only-alfalfa'),
               PYTHONPATH=str(REPO))
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / 'home/Library').exists()


async def read_only_inference(self, client, endpoint, payload, **kwargs):
    if endpoint == '/api/show':
        return {'capabilities': ['tools']}
    names = {t['function']['name'] for t in payload['tools']}
    assert {'read_file', 'search', 'list_files'} <= names
    assert not names & {'write_file', 'edit_file', 'run_command', 'run_checks'}
    return dict(message=dict(role='assistant', content='Sólo leí el proyecto.', tool_calls=[]),
                done=True, done_reason='stop', prompt_eval_count=5, eval_count=2)


@pytest.mark.parametrize('tool', ['write_file', 'edit_file', 'run_command', 'run_checks'])
def test_read_only_rejects_write_and_exec_tools(tmp_path, monkeypatch, tool):
    m = manager(tmp_path, monkeypatch, read_only=True)
    try:
        with pytest.raises(ValueError, match='deshabilitada'):
            asyncio.run(m.runtime().tools.call(tool, dict(path='x', content='x', argv=['pwd'], purpose='p')))
    finally:
        m.close()


def test_read_only_does_not_offer_write_tools_to_the_model(tmp_path, monkeypatch):
    m = manager(tmp_path, monkeypatch, read_only=True)
    try:
        with patch.object(NativeRuntime, 'request', read_only_inference):
            asyncio.run(m.run(m.task['objective']))
        assert not (Path.cwd() / 'x').exists()
    finally:
        m.close()


def test_read_only_flag_is_parsed(tmp_path, monkeypatch):
    seen = {}

    async def fake(args):
        seen['solo_lectura'] = args.solo_lectura
        return 0

    monkeypatch.setattr('alfalfa.terminal.execute', fake)
    assert main(['--solo-lectura', 'mira']) == 0 and seen == {'solo_lectura': True}


def test_context_is_emitted_to_transport_only_with_integers(tmp_path, monkeypatch):
    rows = []

    class Transport:
        def emit(self, tipo, **fields):
            rows.append((tipo, fields))

        def clean(self, value):
            return value

        def approval(self, root, auto):
            return TerminalApproval(root, auto)

    m = manager(tmp_path, monkeypatch, transport=Transport())
    try:
        m.context(m.task, 1200, 32768)
        m.context(m.task, None, 32768)
        assert rows == [('contexto', dict(tokens=1200, limite=32768))]
    finally:
        m.close()
