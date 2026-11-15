"""Un error inesperado del arnés en un turno no cierra la sesión de la app.

Antes subía hasta el final de `stdio.run`: la sesión entera se cerraba con «No se pudo iniciar o
ejecutar la sesión» y la excepción no quedaba en ningún sitio. Ahora ese turno falla, la sesión
sigue con el mensaje siguiente y la traza queda en `errores.log` de la sesión.
"""
import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

MOTOR = '''
import sys
from unittest.mock import patch
from alfalfa.terminal import main
from alfalfa.proveedores.nativo.runtime import NativeRuntime
llamadas = []
original = NativeRuntime.run
async def run(self, *args, **kwargs):
    llamadas.append(1)
    if len(llamadas) == 1:
        raise RuntimeError('fallo-interno-de-prueba')
    return await original(self, *args, **kwargs)
async def inference(self, client, endpoint, payload, **kwargs):
    if endpoint == '/api/show':
        return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
    return dict(message=dict(role='assistant', content='Respuesta.'), done=True, done_reason='stop',
                prompt_eval_count=900, eval_count=10)
with patch.object(NativeRuntime, 'request', inference), patch.object(NativeRuntime, 'run', run):
    sys.exit(main(['--stdio', 'Hola']))
'''


class Cliente:
    def __init__(self, tmp_path):
        root = Path(__file__).resolve().parents[1]
        self.hogar = tmp_path / 'home'
        env = dict(os.environ, PYTHONPATH=str(root), ALFALFA_HOME=str(self.hogar))
        self.proc = subprocess.Popen([sys.executable, '-c', MOTOR], cwd=tmp_path, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.filas = []

    def enviar(self, **fila):
        self.proc.stdin.write(json.dumps(fila) + '\n')
        self.proc.stdin.flush()

    def hasta(self, condicion, espera=20):
        fin = time.monotonic() + espera
        while time.monotonic() < fin and self.proc.poll() is None:
            if select.select([self.proc.stdout], [], [], 0.2)[0]:
                linea = self.proc.stdout.readline()
                if not linea:
                    break
                fila = json.loads(linea)
                self.filas.append(fila)
                if condicion(fila):
                    return fila
        return None

    def cerrar(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        for tubo in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            tubo.close()


@pytest.fixture
def cliente(tmp_path):
    c = Cliente(tmp_path)
    yield c
    c.cerrar()


def test_el_turno_falla_y_la_sesion_sigue(cliente):
    primero = cliente.hasta(lambda f: f['tipo'] == 'turno_terminado')
    assert primero and primero['estado'] == 'failed', cliente.filas[-5:]
    errores = [f['texto'] for f in cliente.filas if f['tipo'] == 'error']
    assert any('fallo-interno-de-prueba' in t for t in errores), errores
    assert not any('No se pudo iniciar' in t for t in errores)
    # La sesión sigue: el mensaje siguiente abre un turno que termina bien.
    cliente.enviar(tipo='mensaje', texto='¿sigues ahí?')
    segundo = cliente.hasta(lambda f: f['tipo'] == 'turno_terminado')
    assert segundo and segundo['estado'] == 'completed', cliente.filas[-5:]
    registro = next((cliente.hogar / 'sesiones').glob('*/errores.log'))
    texto = registro.read_text()
    assert 'RuntimeError: fallo-interno-de-prueba' in texto and 'Traceback' in texto
