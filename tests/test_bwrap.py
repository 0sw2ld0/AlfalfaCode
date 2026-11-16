"""Sandbox bubblewrap de AlfalfaCode en Linux: la misma política que Seatbelt, con montajes.

Las unitarias corren en cualquier sistema. Las «real_» lanzan /usr/bin/bwrap de verdad: se saltan, con
el motivo, fuera de Linux o donde bwrap no arranca (en Ubuntu 24.04, sin el perfil de AppArmor que
explica docs/USO.md). Cada orden tiene plazo y su grupo de procesos se comprueba muerto.
"""
import asyncio
import os
import socket
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from alfalfa import seatbelt
from alfalfa.procesos import grupo_vivo
from alfalfa.seatbelt import SandboxUnavailable, SeatbeltSandbox, sandbox_del_sistema
from alfalfa.seatbelt import bwrap as bw
from alfalfa.seatbelt.bwrap import BWRAP, BwrapSandbox, filtro_sin_red
from alfalfa.terminal import TerminalManager
from tests.sandbox_del_sistema import motivo_sin_sandbox

LINUX = sys.platform.startswith('linux')
linux = pytest.mark.skipif(not LINUX, reason='el lanzador de bwrap usa memfd y /proc: sólo Linux')
RARO = 'wörk #1 "q" (x)+$&-~[a]{b}'


NO_ARRANCA = motivo_sin_sandbox() if LINUX else 'requiere Linux'
real = pytest.mark.skipif(NO_ARRANCA is not None, reason=str(NO_ARRANCA))


def pares(args, opcion):
    """Las rutas de cada `opcion SRC DEST` de la lista de bwrap."""
    return [args[i + 1] for i, a in enumerate(args) if a == opcion]


def manager(tmp_path, monkeypatch, name='project'):
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    monkeypatch.chdir(root)
    return TerminalManager('Prueba de sandbox.', home=tmp_path / 'alfalfa'), root


# ---------------------------------------------------------------- unitarias

def test_el_sistema_elige_bwrap_en_linux_y_seatbelt_en_macos(tmp_path, monkeypatch):
    monkeypatch.setattr(seatbelt.sys, 'platform', 'linux')
    assert type(sandbox_del_sistema(str(tmp_path))) is BwrapSandbox
    assert seatbelt.nombre_del_sandbox() == 'bwrap'
    monkeypatch.setattr(seatbelt.sys, 'platform', 'darwin')
    assert type(sandbox_del_sistema(str(tmp_path))) is SeatbeltSandbox
    assert seatbelt.nombre_del_sandbox() == 'Seatbelt'
    assert BwrapSandbox.etiqueta in seatbelt.AISLADAS and SeatbeltSandbox.etiqueta in seatbelt.AISLADAS


def test_lee_todo_el_disco_y_escribe_solo_en_las_raices(tmp_path):
    ws = tmp_path / 'ws'
    tmpdir = tmp_path / 'tmpdir'
    ws.mkdir()
    tmpdir.mkdir()
    sandbox = BwrapSandbox(str(ws), tmpdir=str(tmpdir), home=str(tmp_path / 'casa'))
    roots = sandbox.writable_roots()
    assert roots == [os.path.realpath(ws), os.path.realpath(tmpdir), os.path.realpath('/tmp')]
    args = sandbox.montajes()
    # Primero todo de solo lectura; después las raíces, de la menos a la más profunda.
    assert args.index('--ro-bind') == args.index('/') - 1
    assert sorted(pares(args, '--bind')[:3]) == sorted(roots)
    assert args.index('--ro-bind') < args.index('--bind')
    for opcion in ('--unshare-user', '--unshare-pid', '--unshare-ipc', '--unshare-net', '--new-session',
                   '--die-with-parent'):
        assert opcion in args
    assert args[-2:] == ['--cap-drop', 'ALL']
    # La variante con red sólo quita el corte de red.
    con_red = BwrapSandbox(str(ws), tmpdir=str(tmpdir), network=True).montajes()
    assert '--unshare-net' not in con_red
    assert [a for a in args if a != '--unshare-net'] == con_red


def test_un_tmpdir_que_abarca_la_casa_se_ignora(tmp_path):
    ws = tmp_path / 'casa' / 'ws'
    ws.mkdir(parents=True)
    roots = BwrapSandbox(str(ws), tmpdir=str(tmp_path), home=str(tmp_path / 'casa')).writable_roots()
    assert os.path.realpath(tmp_path) not in roots


def test_git_queda_fijado_con_hooks_y_config_de_solo_lectura(tmp_path):
    ws = tmp_path / 'ws'
    (ws / '.git').mkdir(parents=True)
    (ws / '.git' / 'config').write_text('[core]\n')
    (ws / '.git' / 'config.worktree').write_text('')
    (ws / '.git' / 'HEAD').write_text('ref: refs/heads/main\n')
    anidado = ws / 'a' / 'b' / '.git'
    (anidado / 'hooks').mkdir(parents=True)
    (anidado / 'config').write_text('')
    modulo = ws / '.git' / 'modules' / 'lib'
    modulo.mkdir(parents=True)
    (modulo / 'HEAD').write_text('x')
    (modulo / 'config').write_text('')
    args = BwrapSandbox(str(ws)).montajes()
    real = os.path.realpath(ws)
    binds, ro = pares(args, '--bind'), pares(args, '--ro-bind')
    # El propio .git y el submódulo son puntos de montaje: no se renombran ni se borran.
    for ruta in ('/.git', '/a/b/.git', '/.git/modules/lib'):
        assert real + ruta in binds, ruta
    # hooks y config*, de solo lectura; el hooks que faltaba se creó antes (como `git init`).
    for ruta in ('/.git/hooks', '/.git/config', '/.git/config.worktree', '/a/b/.git/hooks', '/a/b/.git/config',
                 '/.git/modules/lib/hooks', '/.git/modules/lib/config'):
        assert real + ruta in ro, ruta
    assert (ws / '.git' / 'hooks').is_dir() and not any((ws / '.git' / 'hooks').iterdir())
    # Los antecesores del .git anidado quedan fijados; HEAD, refs e índice siguen siendo datos.
    assert real + '/a' in binds and real + '/a/b' in binds
    assert not any(p.endswith(('/HEAD', '/refs', '/index', '/objects')) for p in ro)
    # Después de la raíz: lo protegido prevalece.
    assert args.index(real + '/.git/hooks') > args.index(real)


def test_un_git_sin_hooks_ni_config_los_tiene_vacios_y_de_solo_lectura(tmp_path):
    """Seatbelt niega crearlos por nombre; un montaje sólo protege lo que existe, así que se crean antes."""
    ws = tmp_path / 'ws'
    (ws / '.git').mkdir(parents=True)
    ro = pares(BwrapSandbox(str(ws)).montajes(), '--ro-bind')
    real = os.path.realpath(ws)
    assert real + '/.git/hooks' in ro and real + '/.git/config' in ro
    assert (ws / '.git' / 'config').read_text() == '' and not any((ws / '.git' / 'hooks').iterdir())


def test_un_git_de_worktree_protege_tambien_su_repositorio_comun(tmp_path):
    principal = tmp_path / 'principal' / '.git'
    (principal / 'worktrees' / 'w').mkdir(parents=True)
    (principal / 'hooks').mkdir()
    (principal / 'config').write_text('[extensions]\n\tworktreeConfig = true\n')
    (principal / 'worktrees' / 'w' / 'commondir').write_text('../..\n')
    ws = tmp_path / 'w'
    ws.mkdir()
    (ws / '.git').write_text('gitdir: ' + str(principal / 'worktrees' / 'w') + '\n')
    # Todo vive en $TMPDIR, que es escribible: el repositorio común también se protege.
    args = BwrapSandbox(str(ws), tmpdir=str(tmp_path)).montajes()
    ro = pares(args, '--ro-bind')
    comun = os.path.realpath(principal)
    assert os.path.realpath(ws / '.git') in ro
    assert comun + '/hooks' in ro and comun + '/config' in ro
    # Con worktreeConfig activo, el config.worktree que faltaba existe vacío y no se puede escribir.
    assert comun + '/worktrees/w/config.worktree' in ro
    assert (principal / 'worktrees' / 'w' / 'config.worktree').read_text() == ''


@pytest.mark.parametrize('que', ['.git', '.claude'])
def test_un_protegido_enlazado_no_arranca(tmp_path, que):
    ws = tmp_path / 'ws'
    ws.mkdir()
    destino = tmp_path / 'destino'
    destino.mkdir()
    (ws / que).symlink_to(destino)
    with pytest.raises(SandboxUnavailable, match='enlace simbólico'):
        BwrapSandbox(str(ws)).montajes()


def test_un_hooks_enlazado_deja_el_git_entero_de_solo_lectura(tmp_path):
    ws = tmp_path / 'ws'
    (ws / '.git').mkdir(parents=True)
    (tmp_path / 'hooks-de-fuera').mkdir()
    (ws / '.git' / 'hooks').symlink_to(tmp_path / 'hooks-de-fuera')
    args = BwrapSandbox(str(ws)).montajes()
    assert os.path.realpath(ws / '.git') in pares(args, '--ro-bind')


def test_instrucciones_hogar_e_ilegibles(tmp_path):
    ws = tmp_path / 'ws'
    for carpeta in ('.alfalfa', 'sub/.claude', 'hogar/x'):
        (ws / carpeta).mkdir(parents=True)
    oculta = ws / 'oculta'
    oculta.mkdir()
    oculta.chmod(0)
    try:
        args = BwrapSandbox(str(ws), protegidas=(ws / 'hogar',)).montajes()
    finally:
        oculta.chmod(0o755)
    real = os.path.realpath(ws)
    ro, binds = pares(args, '--ro-bind'), pares(args, '--bind')
    assert real + '/.alfalfa' in ro and real + '/sub/.claude' in ro and real + '/hogar' in ro
    assert real + '/sub' in binds  # antecesor de una carpeta de instrucciones anidada
    if os.geteuid() != 0:
        # root lee el directorio igual: no queda sellado.
        assert real + '/oculta' in ro
    # El hogar va al final: nada de lo anterior lo reabre.
    assert args.index(real + '/hogar') > max(i for i, a in enumerate(args) if a == real)


def test_el_hogar_que_contiene_la_carpeta_la_deja_de_solo_lectura(tmp_path):
    hogar = tmp_path / 'hogar'
    ws = hogar / 'proyecto'
    ws.mkdir(parents=True)
    args = BwrapSandbox(str(ws), protegidas=(hogar,)).montajes()
    real_ws, real_hogar = os.path.realpath(ws), os.path.realpath(hogar)
    assert real_hogar in pares(args, '--ro-bind')
    assert args.index(real_hogar) > args.index(real_ws)


def correr_bpf(programa, nr, arquitectura):
    """Intérprete mínimo de BPF clásico para seccomp_data {nr, arch, ip, args}."""
    datos = struct.pack('=iIQ6Q', nr, arquitectura, 0, 0, 0, 0, 0, 0, 0)
    instrucciones = [struct.unpack('=HBBI', programa[i:i + 8]) for i in range(0, len(programa), 8)]
    pc = acumulador = 0
    while True:
        codigo, si, no, k = instrucciones[pc]
        if codigo == 0x20:
            acumulador = struct.unpack_from('=I', datos, k)[0]
            pc += 1
        elif codigo == 0x15:
            pc += 1 + (si if acumulador == k else no)
        elif codigo == 0x35:
            pc += 1 + (si if acumulador >= k else no)
        elif codigo == 0x06:
            return k
        else:
            raise AssertionError('instrucción desconocida ' + hex(codigo))


@pytest.mark.parametrize('maquina,arquitectura,socket_nr,otra', [
    ('x86_64', 0xC000003E, 41, 0x40000003), ('aarch64', 0xC00000B7, 198, 0x40000028)])
def test_el_filtro_niega_sockets_e_io_uring_y_mata_otra_arquitectura(maquina, arquitectura, socket_nr, otra):
    programa = filtro_sin_red(maquina)
    eperm, permitir, matar = 0x00050001, 0x7FFF0000, 0x80000000
    assert correr_bpf(programa, socket_nr, arquitectura) == eperm
    assert correr_bpf(programa, 425, arquitectura) == eperm  # io_uring_setup
    for otra_llamada in (0, 1, 2, 59, 221, socket_nr + 1):  # read, write, socketpair (x86_64: 53), execve…
        assert correr_bpf(programa, otra_llamada, arquitectura) == permitir
    assert correr_bpf(programa, socket_nr, otra) == matar  # 32 bits: socketcall() esquivaría el filtro
    if maquina == 'x86_64':
        assert correr_bpf(programa, 41 | 0x40000000, arquitectura) == matar  # x32
    with pytest.raises(SandboxUnavailable, match='arquitectura'):
        filtro_sin_red('riscv64')


def test_sin_bwrap_no_hay_alternativa_silenciosa(tmp_path, monkeypatch):
    monkeypatch.setattr(bw.sys, 'platform', 'linux')
    with pytest.raises(SandboxUnavailable, match='sudo apt install bubblewrap'):
        asyncio.run(BwrapSandbox(str(tmp_path), executable=str(tmp_path / 'no-existe')).prepare(['true']))
    monkeypatch.setattr(bw.sys, 'platform', 'darwin')
    with pytest.raises(SandboxUnavailable, match='sólo existe en Linux'):
        asyncio.run(BwrapSandbox(str(tmp_path)).prepare(['true']))


def test_la_pista_de_apparmor(tmp_path, monkeypatch):
    archivo = tmp_path / 'restrict'
    archivo.write_text('1\n')
    monkeypatch.setattr(bw, 'APPARMOR', str(archivo))
    assert 'AppArmor' in bw.pista('bwrap: setting up uid map: Permission denied')
    assert 'AppArmor' in bw.pista('bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted')
    archivo.write_text('0\n')
    assert bw.pista('bwrap: setting up uid map: Permission denied') == ''
    assert 'espacios de nombres' in bw.pista('bwrap: No permissions to create new namespace, likely because …')


@linux
def test_la_orden_sin_red_pasa_el_filtro_por_un_memfd_sellado(tmp_path):
    command = BwrapSandbox(str(tmp_path), executable='/bin/true').command_args(['echo', 'hola'])
    assert command[:4] == [bw.SH, '-c', bw.LANZADOR, bw.NOMBRE_LANZADOR]
    ruta = command[4]
    assert ruta.startswith('/proc/' + str(os.getpid()) + '/fd/')
    with open(ruta, 'rb') as archivo:
        assert archivo.read() == filtro_sin_red()
    with pytest.raises(OSError):
        with open(ruta, 'r+b') as archivo:
            archivo.write(b'x')
    assert command[5] == '/bin/true' and command[-5:] == ['--seccomp', bw.FD_FILTRO, '--', 'echo', 'hola']
    assert command[-7:-5] == ['--unsetenv', '_']  # lo que añade la shell del lanzador
    con_red = BwrapSandbox(str(tmp_path), executable='/bin/true', network=True).command_args(['echo'])
    assert con_red[0] == '/bin/true' and '--seccomp' not in con_red


@linux
def test_un_bwrap_que_no_arranca_lo_dice_con_la_pista(tmp_path, monkeypatch):
    falso = tmp_path / 'bwrap-roto'
    falso.write_text('#!/bin/sh\necho "bwrap: setting up uid map: Permission denied" >&2\nexit 1\n')
    falso.chmod(0o755)
    archivo = tmp_path / 'restrict'
    archivo.write_text('1\n')
    monkeypatch.setattr(bw, 'APPARMOR', str(archivo))
    with pytest.raises(SandboxUnavailable) as error:
        asyncio.run(BwrapSandbox(str(tmp_path), executable=str(falso)).prepare(['true']))
    texto = str(error.value)
    assert 'código 1' in texto and 'setting up uid map' in texto and 'AppArmor' in texto


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


class Recorder:
    """Política de aprobación que registra cada petición y niega las que salen del sandbox."""

    def __init__(self):
        self.calls, self.denied = [], 0

    async def __call__(self, args):
        self.calls.append(dict(args))
        if args.get('sin_sandbox'):
            self.denied += 1
            raise ValueError('Comando rechazado; no se ejecutó.')


@real
def test_real_run_command_corre_en_bwrap_sin_pedir_permiso(tmp_path, monkeypatch, fuera_del_sandbox):
    m, root = manager(tmp_path, monkeypatch, name=RARO)
    (root / 'escape').symlink_to(fuera_del_sandbox)
    tag = 'alfalfa-bwrap-' + str(os.getpid())
    targets = {'workspace': str(root / 'dentro.txt'), 'tmp': '/tmp/' + tag,
               'home': str(fuera_del_sandbox / 'fuera.txt'), 'symlink': str(root / 'escape' / 'por-enlace.txt')}
    try:
        rt = m.runtime()
        rt.approval_policy = Recorder()
        results = {}
        for name, target in targets.items():
            argv = [sys.executable, '-I', '-B', '-c', 'import sys; open(sys.argv[1], "w").write("x")', target]
            results[name] = asyncio.run(rt.tools.call('run_command', dict(argv=argv, purpose='Probar ' + name)))
        assert all(r['sandbox'] == 'bwrap' for r in results.values()), results
        for name in ('workspace', 'tmp'):
            assert results[name]['exit_code'] == 0, results[name]['output']
            assert Path(targets[name]).read_text() == 'x'
        for name in ('home', 'symlink'):
            assert results[name]['exit_code'] != 0
            assert 'Read-only file system' in results[name]['output']
            # La denegación pidió un reintento sin sandbox, que se negó.
            assert results[name]['denegacion_probable'] is True
            assert results[name]['reintento'].startswith('rechazado')
        assert os.listdir(fuera_del_sandbox) == []
        # Sólo las dos escrituras bloqueadas pidieron permiso, y fue de reintento: nada de «SIN SANDBOX».
        assert [c['motivo'] for c in rt.approval_policy.calls] == ['reintento', 'reintento']
    finally:
        m.close()
        Path(targets['tmp']).unlink(missing_ok=True)


@real
def test_real_git_metadata_stays_read_only(tmp_path):
    root = tmp_path / RARO
    (root / 'sub').mkdir(parents=True)
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'}
    subprocess.run(['git', 'init', '-q', str(root)], check=True, timeout=30, env=env)
    subprocess.run(['git', 'init', '-q', str(root / 'sub')], check=True, timeout=30, env=env)
    hook = root / '.git' / 'hooks' / 'pre-commit'
    hook.write_text('#!/bin/sh\nexit 0\n')
    before = {p: p.read_bytes() for p in (root / '.git').rglob('*') if p.is_file()}
    sandbox = BwrapSandbox(str(root))
    attempts = {
        'modify_hook': ['/bin/sh', '-c', 'echo "curl evil" >> .git/hooks/pre-commit'],
        'new_hook': ['/bin/sh', '-c', 'echo x > .git/hooks/post-checkout'],
        'delete_hooks': ['/bin/rm', '-rf', '.git/hooks'],
        'rename_hooks': ['/bin/mv', '.git/hooks', 'hooks-fuera'],
        'rename_git': ['/bin/mv', '.git', 'git-fuera'],
        'hardlink_hook': ['/bin/ln', '.git/hooks/pre-commit', 'enlace-duro'],
        'git_config': ['git', 'config', 'core.hooksPath', '/tmp'],
        'nested_git': ['/bin/sh', '-c', 'echo x >> sub/.git/config'],
        'replace_config': ['/bin/sh', '-c', 'echo "[core]" > c && mv c .git/config'],
    }
    for name, argv in attempts.items():
        code, output = sandboxed(sandbox, argv, root)
        assert code != 0, (name, output)
    assert hook.read_text() == '#!/bin/sh\nexit 0\n'
    assert {p: p.read_bytes() for p in (root / '.git').rglob('*') if p.is_file()} == before
    # `mv .git/hooks` cruza de un montaje a otro: mv recibe EXDEV y copia, pero no puede borrar el
    # original. La copia en la carpeta de trabajo no la usa git; los hooks de verdad siguen intactos.
    assert not (root / 'git-fuera').exists() and (root / '.git' / 'hooks').is_dir()
    code, output = sandboxed(sandbox, ['/bin/sh', '-c', 'echo x > .gitignore && mkdir -p a/b && echo y > a/b/c'], root)
    assert code == 0, output
    code, output = sandboxed(sandbox, ['/bin/sh', '-c', 'mv a z && rm -rf z'], root)
    assert code == 0, output


@real
def test_real_git_local_funciona_sin_salir_del_sandbox(tmp_path):
    root = tmp_path / RARO
    root.mkdir(parents=True)
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'}
    for argv in (['git', 'init', '-q', '-b', 'main'], ['git', 'config', 'user.email', 'p@example.com'],
                 ['git', 'config', 'user.name', 'Prueba'], ['git', 'commit', '-q', '--allow-empty', '-m', 'uno']):
        subprocess.run(argv, cwd=root, check=True, timeout=30, env=env, capture_output=True)
    config = (root / '.git' / 'config').read_bytes()
    sandbox = BwrapSandbox(str(root))
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
def test_real_nested_git_cannot_escape_by_moving_its_ancestors(tmp_path):
    root = tmp_path / RARO
    nested = root / 'sub' / 'deep'
    nested.mkdir(parents=True)
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'}
    subprocess.run(['git', 'init', '-q', str(nested)], check=True, timeout=30, env=env)
    hooks = nested / '.git' / 'hooks'
    before = sorted(p.name for p in hooks.iterdir())
    for argv in (['/bin/mv', 'sub', 'otro'], ['/bin/mv', 'sub/deep', 'sub/otro'], ['/bin/rm', '-rf', 'sub'],
                 ['/bin/sh', '-c', 'echo "curl evil" > sub/deep/.git/hooks/pre-commit']):
        code, output = sandboxed(BwrapSandbox(str(root)), argv, root)
        assert code != 0, (argv, output)
    assert not (root / 'otro').exists() and (nested / '.git').is_dir()
    assert sorted(p.name for p in hooks.iterdir()) == before


@real
@pytest.mark.skipif(os.geteuid() == 0, reason='root lee directorios sin permisos')
def test_real_unreadable_directory_stays_read_only(tmp_path):
    root = tmp_path / RARO
    hooks = root / 'hidden' / 'child' / '.git' / 'hooks'
    hooks.mkdir(parents=True)
    (hooks / 'pre-commit').write_text('#!/bin/sh\nexit 0\n')
    hidden = root / 'hidden'
    hidden.chmod(0)
    try:
        # El sandbox se construye con hidden/ ilegible, como la vería el recorrido.
        command = asyncio.run(BwrapSandbox(str(root)).prepare(
            ['/bin/sh', '-c', 'chmod 755 hidden && echo "curl evil" > hidden/child/.git/hooks/pre-commit']))
        proc = subprocess.run(command, cwd=root, capture_output=True, timeout=30, start_new_session=True)
        assert proc.returncode != 0, proc.stdout + proc.stderr
        assert b'Read-only file system' in proc.stdout + proc.stderr
    finally:
        hidden.chmod(0o755)
    assert (hooks / 'pre-commit').read_text() == '#!/bin/sh\nexit 0\n'


@real
def test_real_el_hogar_en_un_temporal_sigue_de_solo_lectura(tmp_path):
    hogar = tmp_path / 'hogar'
    (hogar / 'sesiones').mkdir(parents=True)
    ws = tmp_path / 'ws'
    ws.mkdir()
    sandbox = BwrapSandbox(str(ws), tmpdir=str(tmp_path), protegidas=(hogar,))
    for argv in (['/bin/sh', '-c', 'echo x > "$0"/nuevo', str(hogar / 'sesiones')], ['/bin/mv', str(hogar), str(ws)],
                 ['/bin/mv', str(tmp_path / 'hogar'), str(tmp_path / 'otro')]):
        code, output = sandboxed(sandbox, argv, ws)
        assert code != 0, (argv, output)
    assert sorted(os.listdir(hogar)) == ['sesiones'] and not os.listdir(hogar / 'sesiones')
    # El resto del temporal sigue abierto.
    code, output = sandboxed(sandbox, ['/bin/sh', '-c', 'echo x > "$0"/libre', str(tmp_path)], ws)
    assert code == 0, output


@real
def test_real_sin_red_ni_sockets_unix_y_con_red_si(tmp_path):
    server = socket.socket()
    server.bind(('127.0.0.1', 0))
    server.listen(1)
    server.settimeout(0.5)
    port = server.getsockname()[1]
    # Un socket Unix del anfitrión en una raíz escribible: tmux, Docker o el bus de la sesión.
    ruta_unix = tempfile.mktemp(prefix='alfalfa-bwrap-', dir='/tmp')
    unix = socket.socket(socket.AF_UNIX)
    unix.bind(ruta_unix)
    unix.listen(1)
    unix.settimeout(0.5)
    probe = ('import socket, sys\n'
             'familia, destino = (socket.AF_UNIX, sys.argv[1]) if sys.argv[1].startswith("/") '
             'else (socket.AF_INET, ("127.0.0.1", int(sys.argv[1])))\n'
             'try:\n'
             '    s = socket.socket(familia); s.settimeout(5); s.connect(destino); s.close()\n'
             'except OSError as exc:\n'
             '    print("BLOQUEADA", exc); sys.exit(3)\n'
             'print("CONECTADA")\n')
    try:
        for destino in (str(port), ruta_unix):
            code, output = sandboxed(BwrapSandbox(str(tmp_path)),
                                     [sys.executable, '-I', '-B', '-c', probe, destino], tmp_path)
            assert code == 3 and 'BLOQUEADA' in output and 'Operation not permitted' in output, output
        for escucha in (server, unix):
            with pytest.raises(socket.timeout):
                escucha.accept()
        code, output = sandboxed(BwrapSandbox(str(tmp_path), network=True),
                                 [sys.executable, '-I', '-B', '-c', probe, str(port)], tmp_path)
        assert code == 0 and 'CONECTADA' in output, output
    finally:
        server.close()
        unix.close()
        os.unlink(ruta_unix)


@real
def test_real_sin_capacidades_ni_nuevos_espacios_de_usuario(tmp_path):
    code, output = sandboxed(BwrapSandbox(str(tmp_path)), ['/bin/sh', '-c', 'grep CapEff /proc/self/status'], tmp_path)
    assert code == 0 and output.split() == ['CapEff:', '0000000000000000'], output
    if '--disable-userns' in bw.opciones_admitidas(BWRAP):
        code, output = sandboxed(BwrapSandbox(str(tmp_path)), ['unshare', '-U', 'true'], tmp_path)
        assert code != 0, output
    # El descriptor del filtro no llega a la orden, ni lo que la shell del lanzador añade al entorno.
    code, output = sandboxed(BwrapSandbox(str(tmp_path)), ['/bin/sh', '-c', 'ls /proc/self/fd'], tmp_path)
    assert bw.FD_FILTRO not in output.split(), output
    command = asyncio.run(BwrapSandbox(str(tmp_path)).prepare(['/usr/bin/env']))
    entorno = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=30,
                             env={'PATH': '/usr/bin:/bin', 'ALFALFA_PRUEBA': '1'}).stdout.split()
    assert sorted(entorno) == ['ALFALFA_PRUEBA=1', 'PATH=/usr/bin:/bin', 'PWD=' + str(tmp_path)], entorno


@real
def test_real_lo_que_la_orden_deja_en_segundo_plano_muere_con_ella(tmp_path):
    """Cada orden tiene su espacio de procesos: `servidor &` no sobrevive a la orden, como sí en macOS.
    Para dejar algo abierto entre turnos está run_command en segundo plano."""
    segundos = str(3000 + os.getpid() % 1000)
    code, output = sandboxed(BwrapSandbox(str(tmp_path)), ['/bin/sh', '-c', 'sleep "$0" & echo lanzado', segundos],
                             tmp_path)
    assert code == 0 and 'lanzado' in output, output
    quedan = subprocess.run(['pgrep', '-f', '^sleep ' + segundos + '$'], capture_output=True, text=True).stdout
    assert quedan == '', quedan


def test_las_credenciales_de_systemd_quedan_vacias(tmp_path, monkeypatch):
    credenciales = tmp_path / 'credentials'
    (credenciales / 'alfalfa.service').mkdir(parents=True)
    monkeypatch.setattr(bw, 'CREDENCIALES', (str(credenciales),))
    monkeypatch.delenv('XDG_RUNTIME_DIR', raising=False)
    monkeypatch.delenv('CREDENTIALS_DIRECTORY', raising=False)
    args = BwrapSandbox(str(tmp_path)).montajes()
    i = args.index('--tmpfs')
    real = os.path.realpath(credenciales)
    assert args[i - 2:i + 4] == ['--perms', '0555', '--tmpfs', real, '--remount-ro', real]
    # Después de las raíces escribibles: /tmp no las reabre.
    assert i > max(j for j, a in enumerate(args) if a == '--bind')
    monkeypatch.setattr(bw, 'CREDENCIALES', (str(tmp_path / 'no-existe'),))
    assert '--tmpfs' not in BwrapSandbox(str(tmp_path)).montajes()


def test_tambien_la_carpeta_de_credenciales_de_este_proceso(tmp_path, monkeypatch):
    """$CREDENTIALS_DIRECTORY puede estar fuera de /run/credentials; dentro de ella no se tapa dos veces."""
    otra = tmp_path / 'otra-ruta'
    otra.mkdir()
    monkeypatch.setattr(bw, 'CREDENCIALES', ())
    monkeypatch.delenv('XDG_RUNTIME_DIR', raising=False)
    monkeypatch.setenv('CREDENTIALS_DIRECTORY', str(otra))
    assert bw.carpetas_de_credenciales() == [os.path.realpath(otra)]
    comun = tmp_path / 'credentials'
    (comun / 'alfalfa.service').mkdir(parents=True)
    monkeypatch.setattr(bw, 'CREDENCIALES', (str(comun),))
    monkeypatch.setenv('CREDENTIALS_DIRECTORY', str(comun / 'alfalfa.service'))
    assert bw.carpetas_de_credenciales() == [os.path.realpath(comun)]


@real
def test_real_la_clave_de_systemd_no_llega_a_la_orden(tmp_path, monkeypatch):
    """LoadCredentialEncrypted= deja la clave descifrada en /run/credentials/<servicio>/, legible por el usuario
    del servicio, que es el de las órdenes."""
    credenciales = tmp_path / 'credentials'
    (credenciales / 'alfalfa.service').mkdir(parents=True)
    (credenciales / 'alfalfa.service' / 'ollama').write_text('sk-ollama-secreta')
    monkeypatch.setattr(bw, 'CREDENCIALES', (str(credenciales),))
    monkeypatch.delenv('CREDENTIALS_DIRECTORY', raising=False)
    ws = tmp_path / 'ws'
    ws.mkdir()
    leer = 'cat "$0"/alfalfa.service/ollama; echo "[$(ls -A "$0")]"'
    code, output = sandboxed(BwrapSandbox(str(ws)), ['/bin/sh', '-c', leer, str(credenciales)], ws)
    assert 'sk-ollama-secreta' not in output and 'No such file' in output and '[]' in output, output
    code, output = sandboxed(BwrapSandbox(str(ws)), ['/bin/sh', '-c', 'touch "$0"/x', str(credenciales)], ws)
    assert code != 0 and not (credenciales / 'x').exists(), output
    # Y la de este proceso, aunque systemd la ponga en otra ruta.
    propia = tmp_path / 'propia'
    propia.mkdir()
    (propia / 'ollama').write_text('sk-ollama-propia')
    monkeypatch.setattr(bw, 'CREDENCIALES', ())
    monkeypatch.setenv('CREDENTIALS_DIRECTORY', str(propia))
    code, output = sandboxed(BwrapSandbox(str(ws)), ['/bin/cat', str(propia / 'ollama')], ws)
    assert code != 0 and 'sk-ollama-propia' not in output, output


def _clave_de_firma():
    """La clave de firma del código de cortesía donde la crea `--activar-codigo` fuera del Mac."""
    from alfalfa.proveedores import configuracion
    from alfalfa.proveedores.alfalfa import CARPETA_FIRMA
    carpeta = configuracion.ruta().parent / CARPETA_FIRMA
    carpeta.mkdir(mode=0o700, parents=True)
    clave = carpeta / 'clave.pem'
    clave.write_text('-----BEGIN PRIVATE KEY-----\nsecreta-de-firma\n')
    clave.chmod(0o600)
    return carpeta, clave


def test_la_clave_de_firma_queda_tapada(tmp_path, monkeypatch):
    monkeypatch.setattr(bw, 'CREDENCIALES', ())
    monkeypatch.delenv('CREDENTIALS_DIRECTORY', raising=False)
    monkeypatch.delenv('XDG_RUNTIME_DIR', raising=False)
    monkeypatch.delenv('ALFALFA_FIRMA_CLAVE', raising=False)
    carpeta, _ = _clave_de_firma()
    args = BwrapSandbox(str(tmp_path)).montajes()
    real = os.path.realpath(carpeta)
    i = args.index('--tmpfs')
    assert args[i - 2:i + 4] == ['--perms', '0555', '--tmpfs', real, '--remount-ro', real]
    # Una clave fuera de esa carpeta (ALFALFA_FIRMA_CLAVE) se tapa con /dev/null.
    otra = tmp_path / 'otra.pem'
    otra.write_text('x')
    monkeypatch.setenv('ALFALFA_FIRMA_CLAVE', str(otra))
    args = BwrapSandbox(str(tmp_path)).montajes()
    assert args[args.index(os.path.realpath(otra)) - 2:][:3] == ['--ro-bind', '/dev/null', os.path.realpath(otra)]


@real
def test_real_una_orden_no_lee_la_clave_de_firma(tmp_path, monkeypatch):
    monkeypatch.delenv('ALFALFA_FIRMA_CLAVE', raising=False)
    _, clave = _clave_de_firma()
    externa = tmp_path / 'firma-externa.pem'
    externa.write_text('secreta-externa')
    ws = tmp_path / 'ws'
    ws.mkdir()
    code, output = sandboxed(BwrapSandbox(str(ws)), ['/bin/cat', str(clave)], ws)
    assert code != 0 and 'secreta-de-firma' not in output, output
    monkeypatch.setenv('ALFALFA_FIRMA_CLAVE', str(externa))
    code, output = sandboxed(BwrapSandbox(str(ws)), ['/bin/cat', str(externa)], ws)
    assert 'secreta-externa' not in output, output
