"""Verificación independiente de los arreglos de aprendizaje con datos ficticios.

No toca aplicación ni credenciales. Escena usa TerminalManager en este mismo
pytest con su conftest y sustituye NativeRuntime.request por un modelo falso.
Los hijos sólo ejecutan pruebas o recetas del proyecto ficticio.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

from alfalfa.complementos.cargador import CargadorComplementos
from alfalfa.seatbelt import SandboxUnavailable
from tests.test_aprendizaje_sesion import CREAR_PASAR_PRUEBAS, llamada, resultado, skill

FIN = {'role': 'assistant', 'content': 'Terminado.'}
pytest_plugins = ('tests.test_aprendizaje_sesion',)

MACOS = pytest.mark.skipif(sys.platform != 'darwin', reason='Seatbelt real')


def check(argv, **extra):
    return llamada('run_checks', dict(argv=argv, purpose='Verificación ficticia',
                                     description='Verificar el proyecto ficticio', **extra))


def comprobar_rechazo(s, pasos, nombre):
    _, modelo = s.correr([*pasos, CREAR_PASAR_PRUEBAS, FIN])
    estado = s.m.runtime().state
    assert estado['checks'], [m for m in modelo.payloads[-1]['messages'] if m.get('role') == 'tool']
    comprobacion = estado['checks'][-1]
    respuesta = resultado(modelo.payloads[-1])
    print(json.dumps({'sonda': nombre, 'sandbox': comprobacion.get('sandbox'),
                      'exit_code': comprobacion['exit_code'], 'output': comprobacion['output'],
                      'respuesta': respuesta}, ensure_ascii=False))
    assert comprobacion.get('sandbox') == 'seatbelt', comprobacion
    assert 'run_checks en verde' in respuesta.get('error', ''), respuesta
    assert not s.permisos()


@pytest.mark.parametrize('formato', ['alfalfa', 'agents', 'claude', 'codex'])
@pytest.mark.parametrize('modo', ['auto', 'omitir'])
def test_escritura_original_bloqueada(escena, formato, modo):
    s = escena(modo=modo, respuesta=False)
    with pytest.raises(ValueError, match='tarjeta de aprobación'):
        s.herramienta('write_file', {'path': f'.{formato}/skills/demo/SKILL.md',
                                   'content': skill('demo'), 'expected_sha256': 'NEW'})


@MACOS
def test_make_true_original_rechazado(escena):
    s = escena()
    (s.root / 'Makefile').write_text('test:\n\ttrue\n')
    comprobar_rechazo(s, [check(['make', 'test'])], 'make-true-original')


@MACOS
def test_unittest_cero_original_rechazado(escena):
    s = escena()
    comprobar_rechazo(s, [check([sys.executable, '-m', 'unittest', 'discover'])], 'unittest-cero-original')


@MACOS
def test_make_objetivo_actualizado_no_abre_puerta(escena):
    s = escena()
    contenido = 'test:\n\t' + sys.executable + ' -m pytest -q -p no:cacheprovider test_nota.py\n'
    comprobar_rechazo(s, [
        llamada('write_file', {'path': 'Makefile', 'content': contenido, 'expected_sha256': 'NEW'}),
        llamada('write_file', {'path': 'test', 'content': 'marcador ficticio\n', 'expected_sha256': 'NEW'}),
        check(['make', 'test']),
    ], 'make-objetivo-actualizado')


@MACOS
@pytest.mark.parametrize('forma', ['cwd', 'shell'])
def test_make_inspecciona_carpeta_real(escena, forma):
    s = escena()
    (s.root / 'Makefile').write_text('test:\n\t' + sys.executable + ' -m pytest -q -p no:cacheprovider test_nota.py\n')
    (s.root / 'sub').mkdir()
    (s.root / 'sub/Makefile').write_text('test:\n\ttrue\n')
    orden = check(['make', 'test'], cwd='sub') if forma == 'cwd' else check(['sh', '-c', 'cd sub && make test'])
    comprobar_rechazo(s, [orden], 'make-carpeta-' + forma)


@MACOS
@pytest.mark.skipif(shutil.which('npm') is None, reason='npm no disponible')
def test_npm_prefijo_inspecciona_carpeta_real(escena):
    s = escena()
    (s.root / 'package.json').write_text(json.dumps({'scripts': {'test': 'node --test'}}))
    (s.root / 'sub').mkdir()
    (s.root / 'sub/package.json').write_text(json.dumps({'scripts': {'test': 'true'}}))
    comprobar_rechazo(s, [check(['npm', 'test', '--prefix', 'sub'])], 'npm-prefijo')


@pytest.mark.parametrize('formato', ['alfalfa', 'agents', 'claude', 'codex'])
def test_habilidad_anidada_no_se_planta_para_siguiente_cwd(escena, formato):
    s = escena(modo='auto', respuesta=False)
    (s.root / '.git').mkdir()  # Sólo marcador ficticio: raíz del monorepo.
    ruta = f'paquete/.{formato}/skills/persistente/SKILL.md'
    try:
        s.herramienta('write_file', {'path': ruta, 'content': skill('persistente'),
                                   'expected_sha256': 'NEW'})
    except ValueError as exc:
        assert 'tarjeta de aprobación' in str(exc)
        return
    cargador = CargadorComplementos(s.home, s.root / 'paquete').cargar()
    ids = [h for p in cargador.complementos for h in p.habilidades]
    assert not any(h.endswith(':persistente') for h in ids), ids


@MACOS
def test_make_real_con_pytest_guarda_con_tarjeta(escena):
    s = escena()
    (s.root / 'Makefile').write_text('test:\n\t' + sys.executable + ' -m pytest -q -p no:cacheprovider test_nota.py\n')
    _, modelo = s.correr([check(['make', 'test']), CREAR_PASAR_PRUEBAS, FIN])
    comprobacion = s.m.runtime().state['checks'][-1]
    assert comprobacion['sandbox'] == 'seatbelt'
    assert '1 passed' in comprobacion['output']
    assert resultado(modelo.payloads[-1])['guardada']
    assert len(s.permisos()) == 1


@MACOS
@pytest.mark.parametrize('formato', ['alfalfa', 'agents', 'claude', 'codex'])
@pytest.mark.parametrize('operacion', ['copia', 'renombrar_destino', 'renombrar_antecesor',
                                      'crear_hardlink', 'hardlink_previo'])
def test_habilidad_anidada_sandbox_no_cambia_por_alias(escena, formato, operacion):
    s = escena(modo='auto', respuesta=False)
    relativa = f'paquete/.{formato}/skills/demo/SKILL.md'
    archivo = s.root / relativa
    archivo.parent.mkdir(parents=True)
    original = skill('demo')
    archivo.write_text(original)
    (s.root / 'staging').mkdir()
    (s.root / 'staging/SKILL.md').write_text(skill('nuevo'))
    if operacion == 'hardlink_previo':
        os.link(archivo, s.root / 'alias.txt')
    ordenes = {
        'copia': ['cp', '-R', 'staging', f'paquete/.{formato}/skills/nuevo'],
        'renombrar_destino': ['mv', 'staging', f'paquete/.{formato}/skills/nuevo'],
        'renombrar_antecesor': ['mv', 'paquete', 'otro'],
        'crear_hardlink': ['sh', '-c', 'ln ' + relativa + ' alias.txt && printf CAMBIADO > alias.txt'],
        'hardlink_previo': [sys.executable, '-c', 'from pathlib import Path; Path("alias.txt").write_text("CAMBIADO")'],
    }
    sandbox = s.m.runtime().process_sandbox
    try:
        argv = sandbox.command_args(ordenes[operacion])
    except SandboxUnavailable as exc:
        assert operacion == 'hardlink_previo' and 'enlaces duros' in str(exc), str(exc)
        return  # Rechazar abrir el sandbox es una defensa válida ante un enlace previo.
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG') if k in os.environ}
    hecho = subprocess.run(argv, cwd=s.root, env=env, capture_output=True, text=True, timeout=20)
    print(json.dumps({'operacion': operacion, 'formato': formato, 'exit_code': hecho.returncode,
                      'output': hecho.stdout + hecho.stderr,
                      'contenido_despues': archivo.read_text() if archivo.exists() else None}, ensure_ascii=False))
    assert hecho.returncode != 0
    assert archivo.read_text() == original
    assert not (s.root / f'paquete/.{formato}/skills/nuevo').exists()
    # Misma política: la denegación no puede ser sólo un error de arranque/SBPL.
    libre = argv[:argv.index('--') + 1] + ['/usr/bin/touch', 'libre.txt']
    permitido = subprocess.run(libre, cwd=s.root, env=env, capture_output=True, text=True, timeout=20)
    assert permitido.returncode == 0, permitido.stderr
    assert (s.root / 'libre.txt').exists()


@MACOS
def test_make_no_confunde_gnumakefile_con_makefile(escena):
    s = escena()
    (s.root / 'Makefile').write_text('test:\n\t' + sys.executable + ' -m pytest -q -p no:cacheprovider test_nota.py\n')
    (s.root / 'GNUmakefile').write_text('test:\n\ttrue\n')
    comprobar_rechazo(s, [check(['make', 'test'])], 'make-manifiesto-preferido')


@MACOS
def test_make_include_no_acredita_receta_que_no_se_ejecuta(escena):
    s = escena()
    (s.root / 'Makefile').write_text('test:\n\t' + sys.executable
                                    + ' -m pytest -q -p no:cacheprovider test_nota.py\ninclude otra.mk\n')
    (s.root / 'otra.mk').write_text('test:\n\ttrue\n')
    comprobar_rechazo(s, [check(['make', 'test'])], 'make-include')


@MACOS
@pytest.mark.skipif(shutil.which('npm') is None, reason='npm no disponible')
def test_npm_workspaces_no_acredita_script_de_raiz_no_ejecutado(escena):
    s = escena()
    (s.root / 'package.json').write_text(json.dumps({'private': True, 'workspaces': ['sub'],
                                                   'scripts': {'test': 'node --test'}}))
    (s.root / 'sub').mkdir()
    (s.root / 'sub/package.json').write_text(json.dumps({'name': 'paquete-ficticio', 'scripts': {'test': 'true'}}))
    comprobar_rechazo(s, [check(['npm', 'test', '--workspaces'])], 'npm-workspaces')


@MACOS
@pytest.mark.skipif(shutil.which('node') is None, reason='node no disponible')
def test_node_sin_pruebas_no_abre_puerta(escena):
    s = escena()
    (s.root / 'package.json').write_text('{}')
    comprobar_rechazo(s, [check(['node', '--test'])], 'node-cero-pruebas')


@MACOS
def test_pytest_solo_skips_no_abre_puerta(escena):
    s = escena()
    (s.root / 'test_nota.py').write_text('import pytest\n@pytest.mark.skip(reason="ficticia")\n'
                                       'def test_nota():\n    assert False\n')
    comprobar_rechazo(s, [check([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'test_nota.py'])],
                      'pytest-solo-skips')
