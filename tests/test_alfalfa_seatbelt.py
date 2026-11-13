"""Sandbox Seatbelt de AlfalfaCode: argumentos, límites, .git y fallo de arranque.

Las pruebas «real_» lanzan /usr/bin/sandbox-exec de verdad; se saltan fuera de
macOS. Cada orden tiene plazo y su grupo de procesos se comprueba muerto.
"""
import asyncio
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from alfalfa import seatbelt
from alfalfa.permisos.terminal import TerminalApproval
from alfalfa.procesos import grupo_vivo
from alfalfa.proveedores.nativo import herramientas
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.seatbelt import (
    SANDBOX_EXEC,
    SandboxUnavailable,
    SeatbeltSandbox,
    protected_metadata_name_regex,
    regex_escape,
)
from alfalfa.stdio import Transport
from alfalfa.terminal import TerminalManager

REAL = sys.platform == 'darwin' and os.access(SANDBOX_EXEC, os.X_OK)
real = pytest.mark.skipif(not REAL, reason='requiere /usr/bin/sandbox-exec (macOS)')
RARO = 'wörk #1 "q" (x)+$&-~[a]{b}'


def roots_of(command):
    return {a[2:].split('=', 1)[0]: a[2:].split('=', 1)[1] for a in command if a.startswith('-D')}


def outer_ancestors(sandbox):
    """Antecesores de la carpeta de trabajo dentro de otra raíz escribible.

    Dependen de dónde viva tmp_path: con un $TMPDIR bajo /private/tmp (el que
    da un integrador, por ejemplo) los padres de la carpeta también quedan anclados.
    """
    roots = sandbox.writable_roots()
    parent = os.path.dirname(roots[0])
    return {p for root in roots[1:] for p in seatbelt._ancestors(parent, root)}


def manager(tmp_path, monkeypatch, name='project'):
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    monkeypatch.chdir(root)
    return TerminalManager('Prueba de sandbox.', home=tmp_path / 'alfalfa'), root


# ---------------------------------------------------------------- unitarias

def test_command_args_follow_codex_shape(tmp_path):
    tmp = tmp_path / 'tmpdir'
    tmp.mkdir()
    root = tmp_path / RARO
    root.mkdir()
    argv = ['sh', '-c', 'echo $(id) > x; rm -rf /', 'a b']
    sandbox = SeatbeltSandbox(str(root), tmpdir=str(tmp), home=str(tmp_path / 'home'))
    command = sandbox.command_args(argv)
    assert command[:2] == ['/usr/bin/sandbox-exec', '-p']
    split = command.index('--')
    assert command[split + 1:] == argv
    params = roots_of(command[3:split])
    assert {k: v for k, v in params.items() if k.startswith('WRITABLE_ROOT_')} == {
        'WRITABLE_ROOT_0': os.path.realpath(root),
        'WRITABLE_ROOT_1': os.path.realpath(tmp),
        'WRITABLE_ROOT_2': '/private/tmp'}
    assert {v for k, v in params.items() if not k.startswith('WRITABLE_ROOT_')} == outer_ancestors(sandbox)
    assert all(a.startswith('-D') for a in command[3:split])
    policy = command[2]
    assert policy.startswith(seatbelt.BASE_POLICY)
    # Las rutas viajan por -D: el texto sólo lleva la forma escapada de la regex.
    assert os.path.realpath(root) not in policy
    assert '(subpath (param "WRITABLE_ROOT_1"))' in policy


def test_policy_files_are_verbatim_codex_copies():
    here = Path(seatbelt.__file__).parent
    for name in ('seatbelt_base_policy.sbpl', 'seatbelt_network_policy.sbpl', 'seatbelt_preferences_policy.sbpl'):
        assert (here / name).is_file()
    assert seatbelt.BASE_POLICY.startswith('(version 1)\n\n; inspired by Chrome')
    assert '(deny default)' in seatbelt.BASE_POLICY
    # El NOTICE original separa «Copyright 2025 OpenAI» con espacios duros: se conserva tal cual.
    assert ' '.join((here / 'NOTICE').read_text().split()).startswith('OpenAI Codex Copyright 2025 OpenAI')
    assert 'Apache License' in (here / 'LICENSE').read_text()
    assert 'faeb379646bdbad016e11f7c9ca91113cad4bc9d' in (here / 'PROCEDENCIA.md').read_text()


def test_network_closed_by_default_and_variant_is_explicit(tmp_path):
    closed, _ = SeatbeltSandbox(str(tmp_path)).policy()
    assert 'network-outbound' not in closed and 'network-inbound' not in closed
    assert seatbelt.NETWORK_POLICY not in closed
    opened, _ = SeatbeltSandbox(str(tmp_path), network=True).policy()
    assert '(allow network-outbound)\n(allow network-inbound)\n' + seatbelt.NETWORK_POLICY in opened
    # Sólo True selecciona la red: un valor «verdadero» cualquiera no.
    assert 'network-outbound' not in SeatbeltSandbox(str(tmp_path), network='yes').policy()[0]


def test_terminal_manager_enables_closed_sandbox_and_integrator_default_is_untouched(tmp_path, monkeypatch):
    m, root = manager(tmp_path, monkeypatch)
    try:
        rt = m.runtime()
        assert isinstance(rt.process_sandbox, SeatbeltSandbox)
        assert rt.process_sandbox.network is False
        assert rt.process_sandbox.root == str(root.resolve())
        assert NativeRuntime(m, dict(m.task, id='otra')).process_sandbox is None
    finally:
        m.close()


def test_cli_offers_no_network_switch(capsys):
    from alfalfa.terminal import main
    with pytest.raises(SystemExit):
        main(['--help'])
    help_text = capsys.readouterr().out
    assert 'red' not in help_text.replace('reanudar', '').casefold()


def test_writable_roots_resolve_symlinks_and_ignore_wide_tmpdir(tmp_path):
    real_root = tmp_path / 'real'
    real_root.mkdir()
    link = tmp_path / 'enlace'
    link.symlink_to(real_root)
    home = tmp_path / 'home'
    home.mkdir()
    roots = SeatbeltSandbox(str(link), tmpdir=str(tmp_path), home=str(home)).writable_roots()
    # $TMPDIR abarca el hogar: se ignora en lugar de ampliar la escritura.
    assert roots == [os.path.realpath(real_root), '/private/tmp']
    assert SeatbeltSandbox(str(real_root), tmpdir=str(home), home=str(home)).writable_roots()[1:] == ['/private/tmp']
    assert SeatbeltSandbox(str(real_root), tmpdir='/', home=str(home)).writable_roots()[1:] == ['/private/tmp']
    assert SeatbeltSandbox(str(real_root), tmpdir='relativo', home=str(home)).writable_roots()[1:] == ['/private/tmp']
    with pytest.raises(SandboxUnavailable, match='raíz'):
        SeatbeltSandbox('/').writable_roots()
    with pytest.raises(SandboxUnavailable, match='control'):
        SeatbeltSandbox(str(tmp_path / 'a\nb')).writable_roots()
    with pytest.raises(SandboxUnavailable, match='existente'):
        SeatbeltSandbox(str(tmp_path / 'falta')).writable_roots()


def test_protected_metadata_regex_matches_codex_and_nested_git():
    # Como Codex, pero el nombre sin distinguir mayúsculas: en APFS `.GIT` es el mismo `.git`.
    assert protected_metadata_name_regex('/a.b/', '.git', nested=False) == r'^/a\.b/\.[gG][iI][tT](/.*)?$'
    assert protected_metadata_name_regex('/', '.git', nested=False) == r'^/\.[gG][iI][tT](/.*)?$'
    root = '/p/' + RARO
    rx = re.compile(protected_metadata_name_regex(root, '.git'))
    for path in ('/.git', '/.git/hooks', '/.git/hooks/pre-commit', '/sub/.git', '/sub/x/.git/config'):
        assert rx.match(root + path), path
    for path in ('/.GIT/hooks', '/sub/.Git/config'):
        assert rx.match(root + path), path
    for path in ('/.gitignore', '/.github/x', '/x.git', '/sub/.gitmodules'):
        assert not rx.match(root + path), path
    assert not rx.match('/p/otro/.git/hooks')
    assert '"' not in regex_escape(root)


def test_git_is_denied_last_so_tmpdir_cannot_reopen_it(tmp_path):
    workspace = tmp_path / 'ws'
    workspace.mkdir()
    sandbox = SeatbeltSandbox(str(workspace), tmpdir=str(tmp_path), home='/nonexistent-home')
    policy, params = sandbox.policy()
    assert [k for k, _ in params if k.startswith('WRITABLE_ROOT_')] == [
        'WRITABLE_ROOT_0', 'WRITABLE_ROOT_1', 'WRITABLE_ROOT_2']
    assert {v for k, v in params if k.startswith('PROTECTED_ANCESTOR_')} == outer_ancestors(sandbox)
    for rx in seatbelt.git_regexes(os.path.realpath(workspace)):
        deny = '(deny file-write* (regex #"' + rx + '"))'
        assert deny in policy
        assert policy.index(deny) > policy.index('(allow file-write*')
    for index in range(3):
        assert ('(deny file-write-unlink (require-all (literal (param "WRITABLE_ROOT_' + str(index)
                + '")) (vnode-type DIRECTORY)))') in policy


def anchored(policy, params, key_prefix='PROTECTED_ANCESTOR_'):
    """Rutas cuyo directorio no se puede borrar ni renombrar según la política."""
    found = set()
    for key, value in params:
        rule = '(deny file-write-unlink (require-all (literal (param "' + key + '")) (vnode-type DIRECTORY)))'
        if key.startswith(key_prefix) and rule in policy:
            found.add(value)
    return found


def test_nested_git_ancestors_cannot_be_renamed(tmp_path):
    ws = tmp_path / RARO
    for rel in ('a/b/.git', 'a/c', 'd/.git', 'e/f'):
        (ws / rel).mkdir(parents=True)
    (ws / '.git').mkdir()
    (ws / 'e' / 'f' / '.git').write_text('gitdir: /otro\n')  # worktree: .git es un archivo
    (ws / 'enlace').symlink_to(tmp_path / 'fuera')
    (tmp_path / 'fuera' / 'g' / '.git').mkdir(parents=True)
    real = os.path.realpath(ws)
    sandbox = SeatbeltSandbox(str(ws), tmpdir=str(tmp_path / 'no'), home='/nonexistent-home')
    policy, params = sandbox.policy()
    assert anchored(policy, params) == ({os.path.join(real, p) for p in ('a', 'a/b', 'd', 'e', 'e/f')}
                                        | outer_ancestors(sandbox))
    # Las rutas viajan como -D, nunca dentro del texto de la política.
    assert real + '/a' not in policy
    assert '(subpath (param "WRITABLE_ROOT_0")) (vnode-type DIRECTORY)' not in policy


def test_workspace_ancestors_inside_other_roots_cannot_be_renamed(tmp_path):
    ws = tmp_path / 'x' / 'y' / 'ws'
    ws.mkdir(parents=True)
    real_tmp = os.path.realpath(tmp_path)
    sandbox = SeatbeltSandbox(str(ws), tmpdir=str(tmp_path), home='/nonexistent-home')
    policy, params = sandbox.policy()
    assert {real_tmp + '/x', real_tmp + '/x/y'} <= outer_ancestors(sandbox)
    assert anchored(policy, params) == outer_ancestors(sandbox)


def test_too_large_tree_forbids_renaming_any_directory(tmp_path, monkeypatch):
    ws = tmp_path / 'ws'
    (ws / 'a' / '.git').mkdir(parents=True)
    (ws / 'b').mkdir()
    monkeypatch.setattr(seatbelt, 'SCAN_LIMIT', 1)
    assert seatbelt.nested_metadata_ancestors(os.path.realpath(ws), limit=1) is None
    policy, params = SeatbeltSandbox(str(ws), home='/nonexistent-home').policy()
    assert ('(deny file-write-unlink (require-all (subpath (param "WRITABLE_ROOT_0")) (vnode-type DIRECTORY)))'
            in policy)


def sealed(policy, params):
    found = set()
    for key, value in params:
        rule = '(deny file-write-unlink (require-all (subpath (param "' + key + '")) (vnode-type DIRECTORY)))'
        if key.startswith('SEALED_SUBTREE_') and rule in policy:
            found.add(value)
    return found


@pytest.mark.skipif(os.geteuid() == 0, reason='root lee directorios sin permisos')
def test_unreadable_directory_seals_its_whole_subtree(tmp_path):
    # Un directorio ilegible puede ocultar un .git a cualquier profundidad.
    ws = tmp_path / 'ws'
    (ws / 'hidden' / 'child' / '.git' / 'hooks').mkdir(parents=True)
    (ws / 'hidden' / 'inner').mkdir()
    (ws / 'open' / 'x').mkdir(parents=True)
    hidden = ws / 'hidden'
    real = os.path.realpath(ws)
    hidden.chmod(0)
    try:
        assert seatbelt.nested_metadata_ancestors(real) == ([], [real + '/hidden'])
        sandbox = SeatbeltSandbox(str(ws), tmpdir=str(tmp_path / 'no'), home='/nonexistent-home')
        # Sin poder descartar habilidades con hardlinks previos no se abre para escribir.
        with pytest.raises(SandboxUnavailable, match='comprobar.*habilidades'):
            sandbox.policy()
    finally:
        hidden.chmod(0o755)
    # Uno ilegible dentro de otro: basta con sellar el exterior.
    (ws / 'hidden' / 'inner').chmod(0)
    hidden.chmod(0)
    try:
        assert seatbelt.nested_metadata_ancestors(real) == ([], [real + '/hidden'])
    finally:
        hidden.chmod(0o755)
        (ws / 'hidden' / 'inner').chmod(0o755)


class Recorder:
    """Política de aprobación que registra cada petición y responde según `answer`."""

    def __init__(self, answer):
        self.answer, self.calls, self.denied = answer, [], 0

    async def __call__(self, args):
        self.calls.append(dict(args))
        if not args.get('sin_sandbox'):
            return
        if self.answer == 'yes':
            return
        if self.answer == 'cancel':
            raise asyncio.CancelledError()
        if self.answer == 'silence':
            await asyncio.Event().wait()
        self.denied += 1
        raise ValueError('Comando rechazado; no se ejecutó.')


def run_marker(tmp_path, monkeypatch, answer, executable):
    m, root = manager(tmp_path, monkeypatch)
    try:
        rt = m.runtime()
        rt.process_sandbox.executable = str(executable)
        rt.approval_policy = Recorder(answer)
        spawned = []
        original = asyncio.create_subprocess_exec

        async def spy(*argv, **kwargs):
            spawned.append(list(argv))
            return await original(*argv, **kwargs)

        monkeypatch.setattr(herramientas.asyncio, 'create_subprocess_exec', spy)
        monkeypatch.setattr(herramientas, 'UNSANDBOXED_APPROVAL_SECONDS', 0.2)
        call = rt.tools.call('run_command', dict(argv=['touch', 'marca'], purpose='Crear marca'))
        try:
            outcome = asyncio.run(call)
        except BaseException as exc:  # noqa: BLE001 - se examina abajo
            outcome = exc
        return outcome, rt.approval_policy, spawned, root, m
    finally:
        m.close()


@pytest.mark.parametrize('answer,error', [('no', 'rechazado'), ('silence', 'Sin respuesta'),
                                          ('cancel', None)])
def test_missing_sandbox_exec_never_falls_back_silently(tmp_path, monkeypatch, answer, error):
    outcome, policy, spawned, root, m = run_marker(tmp_path, monkeypatch, answer, tmp_path / 'no-existe')
    assert not (root / 'marca').exists()
    assert not any('touch' in argv for argv in spawned)
    if error:
        assert isinstance(outcome, ValueError) and error in str(outcome)
        assert policy.denied == 1
    else:
        assert isinstance(outcome, asyncio.CancelledError)
    # Una sola decisión por intento: la orden normal no preguntó; sólo la salida del sandbox.
    assert [bool(c.get('sin_sandbox')) for c in policy.calls] == [True]
    assert policy.calls[0]['motivo'] == 'salir_sandbox'
    assert policy.calls[0]['fallo_sandbox'].startswith('No existe ')
    assert policy.calls[0]['purpose'] == 'Crear marca'
    assert policy.calls[0]['argv'] == ['touch', 'marca']
    events = (m.directory / (m.task['id'] + '.jsonl')).read_text()
    assert 'Sandbox Seatbelt no disponible' in events


def test_failed_sandbox_start_asks_and_explicit_yes_runs_unsandboxed(tmp_path, monkeypatch):
    fake = tmp_path / 'sandbox-exec-roto'
    fake.write_text('#!/bin/sh\necho "sandbox-exec: sandbox_apply: Operation not permitted" >&2\nexit 65\n')
    fake.chmod(0o755)
    outcome, policy, spawned, root, _ = run_marker(tmp_path, monkeypatch, 'yes', fake)
    assert outcome['sandbox'] == 'none' and outcome['exit_code'] == 0
    assert (root / 'marca').exists()
    assert 'código 65' in policy.calls[0]['sin_sandbox'] and 'sandbox_apply' in policy.calls[0]['sin_sandbox']
    assert len(policy.calls) == 1
    # Sólo la sonda pasó por el falso sandbox-exec; la orden salió tras el sí explícito.
    assert spawned[0][0] == str(fake) and spawned[0][-1] == '/usr/bin/true'
    assert spawned[-1] == ['touch', 'marca']


def test_failed_sandbox_start_with_rejection_launches_nothing(tmp_path, monkeypatch):
    fake = tmp_path / 'sandbox-exec-roto'
    fake.write_text('#!/bin/sh\nexit 71\n')
    fake.chmod(0o755)
    outcome, _, spawned, root, _ = run_marker(tmp_path, monkeypatch, 'no', fake)
    assert isinstance(outcome, ValueError)
    assert not (root / 'marca').exists()
    assert [argv[-1] for argv in spawned] == ['/usr/bin/true']


def test_unsandboxed_request_is_never_auto_approved(tmp_path):
    out = __import__('io').StringIO()
    approval = TerminalApproval(tmp_path, auto=True, stdin=__import__('io').StringIO(), stdout=out)
    asyncio.run(approval(dict(argv=['pwd'], purpose='Consulta')))
    with pytest.raises(ValueError, match='rechazado'):
        asyncio.run(approval(dict(argv=['pwd'], purpose='SIN SANDBOX (x). Consulta', sin_sandbox='x')))
    assert approval.denied == 1


def test_transport_flags_unsandboxed_request_and_waits_for_client(tmp_path):
    out = __import__('io').StringIO()

    async def scenario():
        transport = Transport(out)
        approval = transport.approval(tmp_path, True)
        await approval(dict(argv=['pwd'], purpose='Consulta'))
        pending = asyncio.ensure_future(approval(dict(argv=['pwd'], purpose='SIN SANDBOX', sin_sandbox='falta')))
        await asyncio.sleep(0.05)
        assert not pending.done()
        ident = next(iter(transport.pending))
        transport.resolve(ident, False, 'Respuesta del cliente')
        with pytest.raises(ValueError):
            await pending

    asyncio.run(scenario())
    import json
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    requests = [r for r in rows if r['tipo'] == 'permiso']
    assert 'sin_sandbox' not in requests[0] and requests[1]['sin_sandbox'] == 'falta'
    assert [r['aprobado'] for r in rows if r['tipo'] == 'permiso_resuelto'] == [True, False]


# ---------------------------------------------------------------- reales

def sandboxed(sandbox, argv, cwd, timeout=30):
    command = asyncio.run(sandbox.prepare(argv))
    proc = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True, env={**os.environ, 'LC_ALL': 'C'})
    try:
        output, _ = proc.communicate(timeout=timeout)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, 9)
            proc.wait()
    assert not grupo_vivo(proc.pid), 'quedó vivo el grupo del sandbox'
    return proc.returncode, output.decode(errors='replace')


@pytest.fixture
def outside_home():
    """Carpeta real en $HOME, fuera de $TMPDIR, /private/tmp y la carpeta de trabajo."""
    base = Path.home() / 'Library' / 'Caches'
    if not base.is_dir():
        base = Path.home()
    path = Path(tempfile.mkdtemp(prefix='alfalfa-seatbelt-prueba ', dir=base))
    tmp = os.path.realpath(os.environ.get('TMPDIR') or '/private/tmp')
    assert not os.path.realpath(path).startswith(tmp.rstrip('/') + '/')
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@real
def test_real_run_command_writes_only_inside_allowed_roots(tmp_path, monkeypatch, outside_home):
    m, root = manager(tmp_path, monkeypatch, name=RARO)
    tmpdir = os.environ.get('TMPDIR')
    assert tmpdir, 'la prueba necesita $TMPDIR'
    (root / 'escape').symlink_to(outside_home)
    tag = 'alfalfa-seatbelt-' + str(os.getpid())
    targets = {'workspace': str(root / 'dentro.txt'), 'tmpdir': os.path.join(tmpdir, tag),
               'private_tmp': '/private/tmp/' + tag, 'home': str(outside_home / 'fuera.txt'),
               'symlink': str(root / 'escape' / 'por-enlace.txt')}
    try:
        rt = m.runtime()
        rt.approval_policy = Recorder('no')
        results = {}
        for name, target in targets.items():
            argv = [sys.executable, '-I', '-B', '-c',
                    'import sys; open(sys.argv[1], "w").write("x")', target]
            results[name] = asyncio.run(rt.tools.call('run_command', dict(argv=argv, purpose='Probar ' + name)))
        assert all(r['sandbox'] == 'seatbelt' for r in results.values())
        for name in ('workspace', 'tmpdir', 'private_tmp'):
            assert results[name]['exit_code'] == 0, results[name]['output']
            assert Path(targets[name]).read_text() == 'x'
        for name in ('home', 'symlink'):
            assert results[name]['exit_code'] != 0
            assert 'Operation not permitted' in results[name]['output']
            # La denegación pidió un reintento sin sandbox, que se negó.
            assert results[name]['denegacion_probable'] is True
            assert results[name]['reintento'].startswith('rechazado')
        assert os.listdir(outside_home) == []
        # Sólo las dos escrituras bloqueadas pidieron permiso, y fue de reintento.
        assert [c['motivo'] for c in rt.approval_policy.calls] == ['reintento', 'reintento']
    finally:
        m.close()
        for name in ('tmpdir', 'private_tmp'):
            Path(targets[name]).unlink(missing_ok=True)


@real
@pytest.mark.parametrize('where', ['tmpdir', 'home'])
def test_real_git_metadata_stays_read_only(tmp_path, request, where):
    # Dentro de $TMPDIR la protección depende de la denegación final; fuera, de la raíz.
    base = tmp_path if where == 'tmpdir' else request.getfixturevalue('outside_home')
    root = base / RARO
    (root / 'sub').mkdir(parents=True)
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'}
    subprocess.run(['git', 'init', '-q', str(root)], check=True, timeout=30, env=env)
    subprocess.run(['git', 'init', '-q', str(root / 'sub')], check=True, timeout=30, env=env)
    hook = root / '.git' / 'hooks' / 'pre-commit'
    hook.write_text('#!/bin/sh\nexit 0\n')
    before = {p: p.read_bytes() for p in (root / '.git').rglob('*') if p.is_file()}
    sandbox = SeatbeltSandbox(str(root))
    attempts = {
        'modify_hook': ['/bin/sh', '-c', 'echo "curl evil" >> .git/hooks/pre-commit'],
        'new_hook': ['/bin/sh', '-c', 'echo x > .git/hooks/post-checkout'],
        'delete_hooks': ['/bin/rm', '-rf', '.git/hooks'],
        'rename_hooks': ['/bin/mv', '.git/hooks', 'hooks-fuera'],
        'rename_git': ['/bin/mv', '.git', 'git-fuera'],
        'hardlink_hook': ['/bin/ln', '.git/hooks/pre-commit', 'enlace-duro'],
        'git_config': ['git', 'config', 'core.hooksPath', '/private/tmp'],
        'nested_git': ['/bin/sh', '-c', 'echo x >> sub/.git/config'],
    }
    for name, argv in attempts.items():
        code, output = sandboxed(sandbox, argv, root)
        assert code != 0, (name, output)
    assert hook.read_text() == '#!/bin/sh\nexit 0\n'
    assert {p: p.read_bytes() for p in (root / '.git').rglob('*') if p.is_file()} == before
    assert not (root / 'hooks-fuera').exists() and not (root / 'git-fuera').exists()
    # El resto de la carpeta sigue siendo escribible, .gitignore incluido.
    code, output = sandboxed(sandbox, ['/bin/sh', '-c', 'echo x > .gitignore && mkdir -p a/b && echo y > a/b/c'], root)
    assert code == 0, output
    # Los directorios sin .git se siguen moviendo y borrando.
    code, output = sandboxed(sandbox, ['/bin/sh', '-c', 'mv a z && rm -rf z'], root)
    assert code == 0, output


def test_git_regexes_protegen_hooks_config_y_el_propio_git():
    root = '/p/ws'
    rxs = [re.compile(rx) for rx in seatbelt.git_regexes(root)]

    def protegido(path):
        return any(rx.match(root + path) for rx in rxs)

    for path in ('/.git', '/.GIT', '/sub/.git', '/.git/hooks', '/.git/hooks/pre-commit', '/.git/HOOKS/x',
                 '/.git/config', '/.git/config.lock', '/.git/config.worktree', '/.git/modules/s/hooks/x',
                 '/.git/modules/s/config', '/.git/worktrees/w/config.worktree', '/sub/.git/config'):
        assert protegido(path), path
    for path in ('/.git/HEAD', '/.git/index', '/.git/index.lock', '/.git/refs/heads/rama', '/.git/objects/ab/cd',
                 '/.git/logs/HEAD', '/.git/ORIG_HEAD', '/.git/COMMIT_EDITMSG', '/.git/refs/stash',
                 '/.gitignore', '/config', '/hooks/x', '/src/config.py'):
        assert not protegido(path), path


@real
@pytest.mark.parametrize('where', ['tmpdir', 'home'])
def test_real_git_local_funciona_sin_salir_del_sandbox(tmp_path, request, where):
    """Como el sandbox de Claude Code: ramas, commits, stash y merge escriben en .git sin permiso."""
    base = tmp_path if where == 'tmpdir' else request.getfixturevalue('outside_home')
    root = base / RARO
    root.mkdir(parents=True)
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'}
    for argv in (['git', 'init', '-q', '-b', 'main'], ['git', 'config', 'user.email', 'p@example.com'],
                 ['git', 'config', 'user.name', 'Prueba'], ['git', 'commit', '-q', '--allow-empty', '-m', 'uno']):
        subprocess.run(argv, cwd=root, check=True, timeout=30, env=env, capture_output=True)
    config = (root / '.git' / 'config').read_bytes()
    sandbox = SeatbeltSandbox(str(root))
    for argv in (['git', 'switch', '-q', '-c', 'rama'], ['/bin/sh', '-c', 'echo b > b && git add b'],
                 ['git', 'commit', '-q', '-m', 'dos'], ['/bin/sh', '-c', 'echo c > b && git stash -q'],
                 ['git', 'stash', 'pop', '-q'], ['git', 'checkout', '-q', '--', 'b'], ['git', 'switch', '-q', 'main'],
                 ['git', 'merge', '-q', 'rama'], ['git', 'branch', '-q', '-D', 'rama'], ['git', 'tag', 'v1']):
        code, output = sandboxed(sandbox, argv, root)
        assert code == 0, (argv, output)
    log = subprocess.run(['git', 'log', '--format=%s'], cwd=root, capture_output=True, text=True, env=env).stdout
    assert log.split() == ['dos', 'uno']
    assert (root / '.git' / 'config').read_bytes() == config


@real
def test_real_nested_git_cannot_escape_by_renaming_its_ancestors(tmp_path):
    # Mover sub/ a /private/tmp sacaba su .git de la expresión protegida.
    root = tmp_path / RARO
    nested = root / 'sub' / 'deep'
    nested.mkdir(parents=True)
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'}
    subprocess.run(['git', 'init', '-q', str(nested)], check=True, timeout=30, env=env)
    hooks = nested / '.git' / 'hooks'
    before = sorted(p.name for p in hooks.iterdir())
    away = '/private/tmp/alfalfa-seatbelt-h001-' + str(os.getpid())
    script = ('mv "$1" "$2" && echo "curl evil" > "$2"/{}.git/hooks/pre-commit')
    attempts = {
        'move_sub': ['/bin/sh', '-c', script.format('deep/'), 'x', 'sub', away],
        'move_deep': ['/bin/sh', '-c', script.format(''), 'x', 'sub/deep', away],
        'rename_in_place': ['/bin/mv', 'sub', 'otro'],
        'delete_sub': ['/bin/rm', '-rf', 'sub'],
    }
    try:
        sandbox = SeatbeltSandbox(str(root))
        for name, argv in attempts.items():
            code, output = sandboxed(sandbox, argv, root)
            assert code != 0, (name, output)
        assert not os.path.lexists(away) and not (root / 'otro').exists()
        assert sorted(p.name for p in hooks.iterdir()) == before
        # Si la carpeta vive en $TMPDIR, tampoco se mueve su padre.
        code, output = sandboxed(SeatbeltSandbox(str(root)), ['/bin/mv', str(tmp_path), str(tmp_path) + '-x'], root)
        assert code != 0, output
        assert root.is_dir()
    finally:
        shutil.rmtree(away, ignore_errors=True)


@real
@pytest.mark.skipif(os.geteuid() == 0, reason='root lee directorios sin permisos')
def test_real_unreadable_directory_cannot_hide_a_git_from_renaming(tmp_path):
    # `chmod` dentro del sandbox volvía legible hidden/ y su hijo se movía
    # con su .git a /private/tmp, donde los hooks ya eran escribibles.
    root = tmp_path / RARO
    hooks = root / 'hidden' / 'child' / '.git' / 'hooks'
    hooks.mkdir(parents=True)
    (hooks / 'pre-commit').write_text('#!/bin/sh\nexit 0\n')
    hidden = root / 'hidden'
    away = '/private/tmp/alfalfa-seatbelt-h001b-' + str(os.getpid())
    script = 'chmod 755 hidden && mv hidden/child "$1" && echo "curl evil" > "$1"/.git/hooks/pre-commit'
    hidden.chmod(0)
    try:
        # La política se construye con hidden/ ilegible, como la vería el recorrido.
        with pytest.raises(SandboxUnavailable, match='comprobar.*habilidades'):
            SeatbeltSandbox(str(root)).command_args(['/bin/sh', '-c', script, 'x', away])
        hidden.chmod(0o755)
        assert not os.path.lexists(away)
        assert (hooks / 'pre-commit').read_text() == '#!/bin/sh\nexit 0\n'
        for argv in (['/bin/mv', 'hidden/child', 'otro'], ['/bin/rm', '-rf', 'hidden'],
                     ['/bin/mv', 'hidden', 'otro']):
            hidden.chmod(0)
            with pytest.raises(SandboxUnavailable, match='comprobar.*habilidades'):
                SeatbeltSandbox(str(root)).command_args(argv)
            hidden.chmod(0o755)
        assert (hooks / 'pre-commit').is_file() and not (root / 'otro').exists()
        # Los archivos del subárbol sellado se siguen editando.
        code, output = sandboxed(SeatbeltSandbox(str(root)), ['/bin/sh', '-c', 'echo y > hidden/nota'], root)
        assert code == 0, output
    finally:
        hidden.chmod(0o755)
        shutil.rmtree(away, ignore_errors=True)


@real
def test_real_network_connection_is_blocked(tmp_path):
    server = socket.socket()
    server.bind(('127.0.0.1', 0))
    server.listen(1)
    server.settimeout(0.5)
    port = server.getsockname()[1]
    probe = ('import socket, sys\n'
             'try:\n'
             '    socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=5).close()\n'
             'except OSError as exc:\n'
             '    print("BLOQUEADA", exc); sys.exit(3)\n'
             'print("CONECTADA")\n')
    try:
        code, output = sandboxed(SeatbeltSandbox(str(tmp_path)),
                                 [sys.executable, '-I', '-B', '-c', probe, str(port)], tmp_path)
        assert code == 3 and 'BLOQUEADA' in output, output
        with pytest.raises(socket.timeout):
            server.accept()
        code, output = sandboxed(SeatbeltSandbox(str(tmp_path)),
                                 ['/usr/bin/curl', '-sS', '-m', '5', 'http://1.1.1.1/'], tmp_path)
        assert code != 0, output
    finally:
        server.close()


@real
def test_real_git_with_other_case_stays_protected(tmp_path):
    """Seatbelt no media el renombrado que sólo cambia mayúsculas (`mv .git .GIT`): ninguna regla
    lo niega, ni `literal` ni `subpath`. Por eso la protección no depende del nombre exacto: con
    `.GIT` los hooks siguen sin poder escribirse y la carpeta sigue sin poder salir de su sitio."""
    root = tmp_path / 'ws'
    (root / '.git' / 'hooks').mkdir(parents=True)
    sandbox = SeatbeltSandbox(str(root))
    sandboxed(sandbox, ['/bin/mv', '.git', '.GIT'], root)  # puede cambiar el nombre visible
    for argv in (['/bin/sh', '-c', 'echo x > .GIT/hooks/pre-commit'], ['/bin/sh', '-c', 'echo x > .git/hooks/x'],
                 ['/bin/mv', '.GIT', 'fuera'], ['/bin/mv', '.git', 'fuera'], ['/bin/rm', '-rf', '.GIT/hooks']):
        code, output = sandboxed(SeatbeltSandbox(str(root)), argv, root)
        assert code != 0, (argv, output)
    assert [p.name.casefold() for p in root.iterdir()] == ['.git']
    assert (root / '.git' / 'hooks').is_dir() and not any((root / '.git' / 'hooks').iterdir())
