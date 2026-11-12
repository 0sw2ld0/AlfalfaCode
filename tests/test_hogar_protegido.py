"""El hogar de AlfalfaCode sigue protegido cuando cae dentro del workspace.

Sólo se usan hogares de prueba; nunca el del usuario ni su Llavero.
"""
import asyncio
import io
import os
import subprocess
import sys
import unicodedata
from types import SimpleNamespace

import pytest

from alfalfa import seatbelt
from alfalfa.permisos.bajo_demanda import with_network
from alfalfa.proveedores.nativo.herramientas import ON_REQUEST_TOOLS, WorkspaceTools
from alfalfa.seatbelt import SANDBOX_EXEC, SandboxUnavailable, SeatbeltSandbox
from alfalfa.stdio import Transport
from alfalfa.terminal import TerminalManager

real = pytest.mark.skipif(sys.platform != 'darwin' or not os.access(SANDBOX_EXEC, os.X_OK),
                          reason='requiere Seatbelt (macOS)')


def herramientas(root, hogar=None):
    return WorkspaceTools(SimpleNamespace(task={'cwd': str(root)}, manager=SimpleNamespace(home=hogar),
                                          excluded_tools=(), tool_specs=ON_REQUEST_TOOLS))


@pytest.fixture
def escena(tmp_path, monkeypatch):
    root = tmp_path / 'casa'
    root.mkdir()
    hogar = root / '.alfalfa'
    hogar.mkdir()
    (hogar / 'hooks.json').write_text('contenido original\n')
    monkeypatch.chdir(root)
    monkeypatch.setenv('ALFALFA_HOME', str(hogar))
    return root, hogar


@pytest.mark.parametrize('nombre,args', [
    ('read_file', {'path': '.alfalfa/hooks.json'}),
    ('write_file', {'path': '.alfalfa/proveedores.json', 'content': '{}', 'expected_sha256': 'NEW'}),
    ('edit_file', {'path': '.alfalfa/hooks.json', 'old': 'original', 'new': 'alterado', 'expected_sha256': '0' * 64}),
    ('list_files', {'path': '.alfalfa'}),
    ('search', {'path': '.alfalfa', 'query': 'original'}),
    ('inspect_image', {'path': '.alfalfa/foto.png', 'question': '¿Qué ves?'}),
    ('apply_patch', {'input': '*** Begin Patch\n*** Add File: .alfalfa/reglas.toml\n+alterado\n*** End Patch'}),
    ('apply_patch', {'input': '*** Begin Patch\n*** Delete File: .alfalfa/hooks.json\n*** End Patch'}),
])
def test_herramientas_rechazan_el_hogar(escena, nombre, args):
    root, hogar = escena
    tools = herramientas(root, hogar)
    with pytest.raises(ValueError, match='hogar de AlfalfaCode'):
        asyncio.run(tools.call(nombre, args))
    assert (hogar / 'hooks.json').read_text() == 'contenido original\n'
    assert sorted(p.name for p in hogar.iterdir()) == ['hooks.json']


def test_listado_y_busqueda_no_entran_en_ninguno_de_los_hogares(escena):
    root, entorno = escena
    propio = root / 'configuracion'
    propio.mkdir()
    (propio / 'reglas.toml').write_text('contenido original\n')
    (root / 'normal.txt').write_text('contenido original\n')
    tools = herramientas(root, propio)
    assert asyncio.run(tools.call('list_files', {'path': '.'}))['files'] == ['normal.txt']
    encontrados = asyncio.run(tools.call('search', {'path': '.', 'query': 'original'}))['matches']
    assert [m['path'] for m in encontrados] == ['normal.txt']
    assert tools.path('.alfalfa-proyecto/nuevo.txt') == root / '.alfalfa-proyecto/nuevo.txt'
    for hogar in (entorno, propio):
        with pytest.raises(ValueError, match='hogar de AlfalfaCode'):
            tools.path(str(hogar / 'nuevo.txt'))


@pytest.mark.parametrize('nombre', ['.ALFALFA', 'Configuración', '.Straße', '.İnfo'])
def test_rutas_con_otras_mayusculas_y_unicode(escena, nombre):
    root, _ = escena
    hogar = root / nombre
    tools = herramientas(root, hogar)
    for variante in (nombre.lower(), nombre.upper(), unicodedata.normalize('NFD', nombre)):
        with pytest.raises(ValueError, match='hogar de AlfalfaCode'):
            tools.path(variante + '/hooks.json')


def test_hogar_relativo_se_fija_antes_de_cambiar_de_carpeta(escena, monkeypatch):
    root, hogar = escena
    monkeypatch.setenv('ALFALFA_HOME', '.alfalfa')
    sandbox = SeatbeltSandbox(root)
    tools = herramientas(root)
    monkeypatch.chdir(root.parent)
    assert str(hogar) in sandbox.protegidas
    with pytest.raises(ValueError, match='hogar de AlfalfaCode'):
        tools.path('.alfalfa/hooks.json')


def test_terminal_y_variante_con_red_conservan_el_hogar_explicito(escena):
    root, _ = escena
    propio = root / 'hogar-explicito'
    m = TerminalManager('Prueba.', home=propio, transport=Transport(io.StringIO()))
    try:
        sandbox = m.runtime().process_sandbox
        assert str(propio) in sandbox.protegidas
        assert with_network(sandbox).protegidas == sandbox.protegidas
        assert propio in m.diagnosticos.sandbox.protegidas
        with pytest.raises(ValueError, match='hogar de AlfalfaCode'):
            m.runtime().tools.path('hogar-explicito/hooks.json')
    finally:
        m.close()


@real
@pytest.mark.parametrize('orden', [
    'echo alterado > .alfalfa/hooks.json',
    'echo alterado > .ALFALFA/proveedores.json',
    'mkdir -p .alfalfa/skills/nueva',
    'mv .alfalfa fuera',
    'rm -rf .alfalfa',
    'chmod 777 .alfalfa/hooks.json',
    'ln -s .alfalfa enlace && echo alterado > enlace/hooks.json',
    'ln .alfalfa/hooks.json alias && echo alterado > alias',
    'cp -R normal .alfalfa',
])
def test_ordenes_reales_no_modifican_el_hogar(escena, orden):
    root, hogar = escena
    (root / 'normal').mkdir()
    (root / 'normal' / 'nuevo').write_text('nuevo\n')
    sandbox = SeatbeltSandbox(root)
    resultado = subprocess.run(sandbox.command_args(['/bin/sh', '-c', orden]), cwd=root,
                               capture_output=True, timeout=30)
    assert resultado.returncode != 0, resultado.stderr
    assert (hogar / 'hooks.json').read_text() == 'contenido original\n'
    assert sorted(p.name for p in hogar.iterdir()) == ['hooks.json']
    libre = subprocess.run(sandbox.command_args(['/bin/sh', '-c', 'echo libre > libre.txt']), cwd=root,
                           capture_output=True, timeout=30)
    assert libre.returncode == 0, libre.stderr
    assert (root / 'libre.txt').read_text() == 'libre\n'


@real
@pytest.mark.parametrize('nombre', ['.alfalfa', 'Configuración', '.Straße', '.İnfo'])
def test_cambiar_solo_mayusculas_o_unicode_no_reabre_el_hogar(escena, nombre):
    root, _ = escena
    hogar = root / nombre
    hogar.mkdir(exist_ok=True)
    (hogar / 'dato').write_text('original')
    sandbox = SeatbeltSandbox(root, protegidas=(hogar,))
    # APFS permite algunos renombrados cosméticos sin mediarlos con Seatbelt.
    variante = unicodedata.normalize('NFD', nombre.upper())
    subprocess.run(sandbox.command_args(['/bin/mv', nombre, variante]), cwd=root,
                   capture_output=True, timeout=30)
    resultado = subprocess.run(sandbox.command_args(['/bin/sh', '-c', 'echo alterado > "$1/dato"', 'sh', variante]),
                               cwd=root, capture_output=True, timeout=30)
    assert resultado.returncode != 0, resultado.stderr
    assert (hogar / 'dato').read_text() == 'original'


@real
def test_no_se_mueven_antecesores_ni_se_escribe_desde_un_workspace_anidado(escena):
    root, _ = escena
    hogar = root / 'configuracion' / 'alfalfa'
    hogar.mkdir(parents=True)
    (hogar / 'dato').write_text('original')
    sandbox = SeatbeltSandbox(root, protegidas=(hogar,))
    movido = subprocess.run(sandbox.command_args(['/bin/mv', 'configuracion', 'fuera']), cwd=root,
                            capture_output=True, timeout=30)
    assert movido.returncode != 0, movido.stderr
    interno = SeatbeltSandbox(hogar, protegidas=(hogar,))
    escrito = subprocess.run(interno.command_args(['/bin/sh', '-c', 'echo alterado > dato']), cwd=hogar,
                             capture_output=True, timeout=30)
    assert escrito.returncode != 0, escrito.stderr
    assert (hogar / 'dato').read_text() == 'original'


@real
def test_un_antecesor_con_otras_mayusculas_sigue_anclado(escena):
    root, _ = escena
    hogar = root / 'configuracion' / 'alfalfa'
    hogar.mkdir(parents=True)
    (hogar / 'dato').write_text('original')
    sandbox = SeatbeltSandbox(str(root).upper(), protegidas=(hogar,))
    subprocess.run(sandbox.command_args(['/bin/mv', 'configuracion', 'CONFIGURACION']), cwd=root,
                   capture_output=True, timeout=30)
    resultado = subprocess.run(sandbox.command_args(['/bin/mv', 'CONFIGURACION', 'fuera']), cwd=root,
                               capture_output=True, timeout=30)
    assert resultado.returncode != 0, resultado.stderr
    assert (hogar / 'dato').read_text() == 'original'


def test_un_enlace_duro_preexistente_no_reabre_el_hogar(escena):
    root, hogar = escena
    alias = root / 'alias'
    alias.hardlink_to(hogar / 'hooks.json')
    sandbox = SeatbeltSandbox(root)
    with pytest.raises(SandboxUnavailable, match='enlaces duros'):
        sandbox.command_args(['/bin/sh', '-c', 'echo alterado > alias'])
    with pytest.raises(ValueError, match='enlaces duros'):
        herramientas(root, hogar).path('alias')
    assert (hogar / 'hooks.json').read_text() == 'contenido original\n'


def test_un_hogar_demasiado_grande_no_se_abre_sin_comprobarlo(escena, monkeypatch):
    root, _ = escena
    monkeypatch.setattr(seatbelt, 'HOME_SCAN_LIMIT', 0)
    with pytest.raises(SandboxUnavailable, match='hogar.*demasiado grande'):
        SeatbeltSandbox(root).command_args(['/usr/bin/true'])


@pytest.mark.skipif(os.geteuid() == 0, reason='root puede recorrer carpetas sin permisos')
def test_un_hogar_ilegible_no_se_abre_sin_comprobarlo(escena):
    root, hogar = escena
    oculto = hogar / 'oculto'
    oculto.mkdir()
    oculto.chmod(0)
    try:
        with pytest.raises(SandboxUnavailable, match='No se pudieron comprobar'):
            SeatbeltSandbox(root).command_args(['/usr/bin/true'])
    finally:
        oculto.chmod(0o700)
