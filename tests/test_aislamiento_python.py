"""Verifica el guard antes de llamar a leer: nunca inspecciona una clave real."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CODIGO = '''
import json
from alfalfa.proveedores import llavero
assert isinstance(llavero._almacen, dict), "Llavero de IA sin aislamiento"
llavero._almacen['anthropic'] = 'dato-ficticio-de-la-prueba'
assert llavero.leer('anthropic') == 'dato-ficticio-de-la-prueba'
try:
    llavero._security()
except AssertionError:
    print(json.dumps({'simulado': True, 'real_bloqueado': True}))
else:
    raise AssertionError('El acceso real al Llavero no está bloqueado')
'''
CODIGO = 'import sys\nsys.path.insert(0, ' + repr(str(Path(__file__).resolve().parents[1])) + ')\n' + CODIGO


@pytest.mark.parametrize('aislado', [None, '-I', '-E', '-IB'])
def test_hijo_con_env_propio_e_importacion_diferida(aislado):
    env = {'PATH': os.environ['PATH'], 'HOME': os.environ['HOME'], 'PYTHONPATH': os.getcwd()}
    resultado = subprocess.run([sys.executable, *([aislado] if aislado else []), '-c', CODIGO],
                               env=env, capture_output=True, text=True, timeout=20)
    assert resultado.returncode == 0, resultado.stderr
    assert json.loads(resultado.stdout) == {'simulado': True, 'real_bloqueado': True}


def test_un_nieto_tambien_hereda_el_aislamiento():
    codigo = 'import subprocess,sys\nsubprocess.run([sys.executable,"-c",' + repr(CODIGO) + '],check=True)'
    resultado = subprocess.run([sys.executable, '-c', codigo], capture_output=True, text=True, timeout=20)
    assert resultado.returncode == 0, resultado.stderr
    assert json.loads(resultado.stdout)['real_bloqueado'] is True


def test_security_se_rechaza_antes_de_arrancar_el_proceso():
    with pytest.raises(AssertionError, match='Llavero real'):
        subprocess.run(['/usr/bin/security', 'no-debe-ejecutarse'], check=True)
