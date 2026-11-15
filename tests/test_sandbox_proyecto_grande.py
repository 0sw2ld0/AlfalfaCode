"""El sandbox no se pierde porque el proyecto sea grande, tenga una carpeta ilegible o viva
dentro de `.claude/worktrees`.

Antes, cualquiera de esos casos hacía que toda orden pidiera «Salir del sandbox» con el motivo
«No se pudieron comprobar las carpetas de habilidades del proyecto.». Ahora lo que no se puede
comprobar queda sellado y la orden corre dentro.
"""
import os
import subprocess
import sys

import pytest

from alfalfa import seatbelt
from alfalfa.seatbelt import SANDBOX_EXEC, SandboxUnavailable, SeatbeltSandbox

real = pytest.mark.skipif(sys.platform != 'darwin' or not os.access(SANDBOX_EXEC, os.X_OK),
                          reason='requiere Seatbelt (macOS)')
sin_root = pytest.mark.skipif(os.geteuid() == 0, reason='root lee directorios sin permisos')
ENTERA = '(deny file-write-unlink (require-all (subpath (param "WRITABLE_ROOT_0")) (vnode-type DIRECTORY)))'


@pytest.fixture
def proyecto(tmp_path, monkeypatch):
    hogar = tmp_path / 'hogar'
    hogar.mkdir()
    monkeypatch.setenv('ALFALFA_HOME', str(hogar))
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    return raiz


def caja(raiz, tmp_path):
    return SeatbeltSandbox(str(raiz), tmpdir=str(tmp_path / 'no'), home='/nonexistent-home')


def con_regla(policy, params, prefijo, filtro):
    """Rutas de los parámetros `prefijo…` que la política usa con `filtro` para negar renombrados."""
    return {valor for clave, valor in params if clave.startswith(prefijo)
            and '(deny file-write-unlink (require-all (' + filtro + ' (param "' + clave
            + '")) (vnode-type DIRECTORY)))' in policy}


def sellados(policy, params):
    return con_regla(policy, params, 'SEALED_SUBTREE_', 'subpath')


def anclados(policy, params):
    return con_regla(policy, params, 'PROTECTED_ANCESTOR_', 'literal')


def llenar(carpeta, paquetes=10, archivos=10):
    for i in range(paquetes):
        (carpeta / ('p' + str(i))).mkdir(parents=True)
        for j in range(archivos):
            (carpeta / ('p' + str(i)) / (str(j) + '.js')).touch()


# ---------------------------------------------------------------- carpeta ilegible
@sin_root
def test_una_carpeta_ilegible_se_sella_y_el_sandbox_sigue(proyecto, tmp_path):
    (proyecto / 'datos' / 'dentro').mkdir(parents=True)
    (proyecto / 'src').mkdir()
    (proyecto / 'datos').chmod(0)
    try:
        policy, params = caja(proyecto, tmp_path).policy()
    finally:
        (proyecto / 'datos').chmod(0o755)
    assert sellados(policy, params) == {os.path.realpath(proyecto / 'datos')}
    assert ENTERA not in policy


@real
@sin_root
def test_real_con_una_carpeta_ilegible_las_ordenes_corren_dentro(proyecto, tmp_path):
    (proyecto / 'datos' / 'dentro').mkdir(parents=True)
    (proyecto / 'datos').chmod(0)
    try:
        argv = asyncio_run(caja(proyecto, tmp_path).prepare(['/usr/bin/touch', 'nuevo.txt']))
        hecho = subprocess.run(argv, cwd=proyecto, capture_output=True, timeout=30)
        assert hecho.returncode == 0, hecho.stderr
        assert (proyecto / 'nuevo.txt').exists()
        # Lo sellado no se puede sacar de la carpeta de trabajo ni dentro del sandbox.
        movido = subprocess.run(caja(proyecto, tmp_path).command_args(['/bin/mv', 'datos', '/private/tmp/']),
                                cwd=proyecto, capture_output=True, timeout=30)
        assert movido.returncode != 0
        assert (proyecto / 'datos').is_dir()
    finally:
        (proyecto / 'datos').chmod(0o755)


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


# ---------------------------------------------------------------- proyecto enorme
def test_un_proyecto_enorme_solo_sella_la_carpeta_que_no_termino(proyecto, tmp_path, monkeypatch):
    (proyecto / 'src' / 'paquete' / '.git').mkdir(parents=True)
    (proyecto / 'src' / '.claude' / 'skills').mkdir(parents=True)
    llenar(proyecto / 'node_modules')
    monkeypatch.setattr(seatbelt, 'SCAN_LIMIT', 40)
    monkeypatch.setattr(seatbelt, 'HOME_SCAN_LIMIT', 40)
    policy, params = caja(proyecto, tmp_path).policy()
    raiz = os.path.realpath(proyecto)
    # La carpeta pequeña se recorrió entera aunque la grande agotara el tope…
    assert sellados(policy, params) == {raiz + '/node_modules'}
    assert {raiz + '/src', raiz + '/src/paquete'} <= anclados(policy, params)
    assert ENTERA not in policy
    # …y sus carpetas de instrucciones siguen protegidas como las del hogar.
    assert raiz + '/src/.claude' in {v for k, v in params if k.startswith('ALFALFA_HOME_')}


def test_las_carpetas_pequenas_no_esperan_a_la_enorme(proyecto):
    for nombre in ('a', 'b', 'c'):
        (proyecto / nombre / 'x' / '.git').mkdir(parents=True)
    llenar(proyecto / 'enorme', paquetes=30)
    raiz = os.path.realpath(proyecto)
    anclas, sellos, _, entera = seatbelt.recorrer_carpeta_de_trabajo(raiz, limit=60)
    assert not entera and sellos == [raiz + '/enorme']
    assert anclas == sorted(raiz + '/' + n + s for n in ('a', 'b', 'c') for s in ('', '/x'))


def test_demasiadas_carpetas_a_medias_sellan_el_proyecto_entero(proyecto, tmp_path, monkeypatch):
    for nombre in ('a', 'b', 'c'):
        llenar(proyecto / nombre)
    monkeypatch.setattr(seatbelt, 'SCAN_LIMIT', 40)
    monkeypatch.setattr(seatbelt, 'MAX_SEALED', 2)
    policy, params = caja(proyecto, tmp_path).policy()
    assert ENTERA in policy and not sellados(policy, params)


@real
def test_real_en_un_proyecto_enorme_se_trabaja_fuera_de_lo_sellado(proyecto, tmp_path, monkeypatch):
    (proyecto / 'src' / 'viejo').mkdir(parents=True)
    llenar(proyecto / 'node_modules')
    monkeypatch.setattr(seatbelt, 'SCAN_LIMIT', 40)
    sandbox = caja(proyecto, tmp_path)
    for orden, sale_bien in ((['/bin/mv', 'src/viejo', 'src/nuevo'], True),
                             (['/bin/rmdir', 'src/nuevo'], True),
                             (['/usr/bin/touch', 'node_modules/p0/otro.js'], True),
                             (['/bin/mv', 'node_modules/p0', '/private/tmp/'], False)):
        hecho = subprocess.run(sandbox.command_args(orden), cwd=proyecto, capture_output=True, timeout=30)
        assert (hecho.returncode == 0) is sale_bien, (orden, hecho.stderr)
    assert (proyecto / 'node_modules' / 'p0' / 'otro.js').exists()


# ---------------------------------------------------------------- un recorrido por orden
def test_el_hogar_se_comprueba_una_vez_por_orden(proyecto, tmp_path, monkeypatch):
    (proyecto / '.claude' / 'skills').mkdir(parents=True)
    llamadas, original = [], seatbelt.comprobar_enlaces_del_hogar

    def contar(hogares):
        llamadas.append(tuple(hogares))
        return original(hogares)

    monkeypatch.setattr(seatbelt, 'comprobar_enlaces_del_hogar', contar)
    caja(proyecto, tmp_path).policy()
    assert len(llamadas) == 1
    assert os.path.realpath(proyecto / '.claude') in llamadas[0]


def test_un_enlace_duro_en_una_habilidad_anidada_sigue_negando_el_sandbox(proyecto, tmp_path):
    habilidad = proyecto / 'paquete' / '.codex' / 'skills' / 'demo'
    habilidad.mkdir(parents=True)
    (habilidad / 'SKILL.md').write_text('original\n')
    os.link(habilidad / 'SKILL.md', proyecto / 'alias.txt')
    with pytest.raises(SandboxUnavailable, match='enlaces duros'):
        caja(proyecto, tmp_path).policy()


# ---------------------------------------------------------------- dentro de .claude/worktrees
@pytest.mark.parametrize('carpeta', ['.claude', '.codex', '.agents', '.alfalfa'])
def test_un_worktree_dentro_de_una_carpeta_de_instrucciones_se_abre(proyecto, tmp_path, carpeta):
    rama = proyecto / carpeta / 'worktrees' / 'rama'
    rama.mkdir(parents=True)
    (rama / '.git').write_text('gitdir: ' + str(proyecto / '.git' / 'worktrees' / 'rama') + '\n')
    assert caja(rama, tmp_path).writable_roots()[0] == os.path.realpath(rama)


@pytest.mark.parametrize('carpeta', ['.claude', '.codex', '.agents', '.alfalfa'])
def test_una_subcarpeta_de_instrucciones_sin_git_propio_no_se_abre(proyecto, tmp_path, carpeta):
    (proyecto / '.git').mkdir()
    agentes = proyecto / carpeta / 'agents'
    agentes.mkdir(parents=True)
    for ruta in (agentes, proyecto / carpeta):
        with pytest.raises(SandboxUnavailable, match='dentro de ' + carpeta.replace('.', r'\.')):
            caja(ruta, tmp_path).writable_roots()


def test_dentro_de_un_git_no_se_abre_ni_con_git_propio(proyecto, tmp_path):
    modulo = proyecto / '.git' / 'modules' / 'x'
    (modulo / '.git').mkdir(parents=True)
    for ruta in (proyecto / '.git', modulo):
        with pytest.raises(SandboxUnavailable, match=r'dentro de un \.git'):
            caja(ruta, tmp_path).writable_roots()


@real
def test_real_en_un_worktree_de_claude_se_escribe_sin_salir_del_sandbox(proyecto, tmp_path):
    (proyecto / '.claude').mkdir()
    (proyecto / '.claude' / 'settings.json').write_text('{}\n')
    rama = proyecto / '.claude' / 'worktrees' / 'rama'
    (rama / '.claude').mkdir(parents=True)
    (rama / '.git').write_text('gitdir: ' + str(proyecto / '.git' / 'worktrees' / 'rama') + '\n')
    sandbox = caja(rama, tmp_path)
    for orden, sale_bien in ((['/usr/bin/touch', 'nuevo.txt'], True),
                             # Ni las instrucciones del worktree ni las de la carpeta que lo contiene.
                             (['/usr/bin/touch', '.claude/settings.json'], False),
                             (['/bin/sh', '-c', 'echo x > "$1"', 'sh',
                               str(proyecto / '.claude' / 'settings.json')], False),
                             (['/bin/sh', '-c', 'echo x > .git'], False)):
        hecho = subprocess.run(sandbox.command_args(orden), cwd=rama, capture_output=True, timeout=30)
        assert (hecho.returncode == 0) is sale_bien, (orden, hecho.stderr)
    assert (rama / 'nuevo.txt').exists()
    assert (proyecto / '.claude' / 'settings.json').read_text() == '{}\n'
