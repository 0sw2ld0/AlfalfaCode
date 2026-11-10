"""Regresiones con proyectos y claves ficticios para la puerta y las tarjetas."""
import sys

import pytest

from alfalfa.aprendizaje.comprobacion import prueba_el_proyecto
from tests.test_aprendizaje_sesion import CREAR_PASAR_PRUEBAS, llamada, resultado, skill

pytest_plugins = ('tests.test_aprendizaje_sesion',)
FIN = {'role': 'assistant', 'content': 'Terminado.'}


@pytest.mark.parametrize('formato', ['alfalfa', 'agents', 'claude', 'codex'])
@pytest.mark.parametrize('modo', ['auto', 'omitir'])
def test_archivos_no_escriben_habilidades_sin_tarjeta(escena, formato, modo):
    s = escena(modo=modo, respuesta=False)
    with pytest.raises(ValueError, match='tarjeta de aprobación'):
        s.herramienta('write_file', {'path': f'.{formato}/skills/demo/SKILL.md',
                                    'content': skill('demo'), 'expected_sha256': 'NEW'})
    assert not (s.root / f'.{formato}/skills/demo/SKILL.md').exists()


def test_parche_no_escribe_habilidades_sin_tarjeta(escena):
    s = escena(modo='omitir')
    with pytest.raises(ValueError, match='tarjeta de aprobación'):
        s.herramienta('apply_patch', {'input': '*** Begin Patch\n*** Add File: .agents/skills/demo/SKILL.md\n'
                                    '+persistencia sin tarjeta\n*** End Patch'})


@pytest.mark.parametrize('receta', ['true', 'cd .', 'echo correcto', 'python3 -c "assert 1"', 'pytest --co',
                                  '-pytest', 'pytest || true'])
def test_make_no_acredita_recetas_vacias_o_ambiguas(tmp_path, receta):
    (tmp_path / 'Makefile').write_text('test:\n\t' + receta + '\n')
    assert not prueba_el_proyecto(['make', 'test'], tmp_path)


def test_scripts_npm_vacios_tampoco_acreditan_pruebas(tmp_path):
    (tmp_path / 'package.json').write_text('{"scripts":{"test":"true"}}')
    assert not prueba_el_proyecto(['npm', 'test'], tmp_path)


@pytest.mark.parametrize('salida, valida', [('Ran 0 tests in 0s\nOK', False),
                                          ('OK', False), ('Ran 1 test in 0s\nOK', True)])
def test_unittest_exige_ejecucion(tmp_path, salida, valida):
    (tmp_path / 'test_demo.py').write_text('')
    assert prueba_el_proyecto(['python3', '-m', 'unittest', 'discover'], tmp_path, salida=salida) is valida


@pytest.mark.skipif(sys.platform != 'darwin', reason='Seatbelt real en macOS')
@pytest.mark.parametrize('vacia', ['make', 'unittest'])
def test_comprobacion_vacia_no_abre_la_puerta_real(escena, vacia):
    s = escena()
    pasos = []
    if vacia == 'make':
        pasos.append(llamada('write_file', {'path': 'Makefile', 'content': 'test:\n\ttrue\n',
                                           'expected_sha256': 'NEW'}))
    argv = ['make', 'test'] if vacia == 'make' else [sys.executable, '-m', 'unittest', 'discover']
    pasos.extend([llamada('run_checks', {'argv': argv, 'purpose': 'Verificar', 'description': 'Verificar'}),
                  CREAR_PASAR_PRUEBAS, FIN])
    _, modelo = s.correr(pasos)
    assert s.m.runtime().state['checks'][-1]['sandbox'] == 'seatbelt'
    assert 'run_checks en verde' in resultado(modelo.payloads[-1]).get('error', '')
    assert not s.permisos()


@pytest.mark.skipif(sys.platform != 'darwin', reason='Seatbelt real en macOS')
def test_make_con_pytest_real_si_abre_la_puerta(escena):
    s = escena()
    (s.root / 'Makefile').write_text('test:\n\t' + sys.executable + ' -m pytest -q -p no:cacheprovider test_nota.py\n')
    _, modelo = s.correr([llamada('run_checks', {'argv': ['make', 'test'], 'purpose': 'Verificar',
                                              'description': 'Verificar'}), CREAR_PASAR_PRUEBAS, FIN])
    assert s.m.runtime().state['checks'][-1]['sandbox'] == 'seatbelt'
    assert resultado(modelo.payloads[-1])['guardada']
    assert len(s.permisos()) == 1
