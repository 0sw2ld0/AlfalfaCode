"""«Compactar sesión» entre turnos se puede detener.

Antes, con el proveedor sin contestar al resumen, Detener se ignoraba (`interrumpir_turno` no hacía
nada fuera de un turno), los mensajes se quedaban en cola y sólo cerrar la sesión la sacaba de ahí:
la petición del resumen puede esperar hasta 600 s.
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
import sys, asyncio
from unittest.mock import patch
from alfalfa.compactacion import PROMPT
from alfalfa.terminal import main
from alfalfa.proveedores.nativo.runtime import NativeRuntime
async def inference(self, client, endpoint, payload, **kwargs):
    if endpoint == '/api/show':
        return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
    if str(payload['messages'][-1].get('content', '')).startswith(PROMPT):
        await asyncio.sleep(600)  # el proveedor no contesta el resumen
    return dict(message=dict(role='assistant', content='Respuesta.'), done=True, done_reason='stop',
                prompt_eval_count=900, eval_count=10)
with patch.object(NativeRuntime, 'request', inference):
    sys.exit(main(['--stdio', 'Hola']))
'''


class Cliente:
    def __init__(self, tmp_path):
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ, PYTHONPATH=str(root), ALFALFA_HOME=str(tmp_path / 'home'))
        self.proc = subprocess.Popen([sys.executable, '-c', MOTOR], cwd=tmp_path, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def enviar(self, **fila):
        self.proc.stdin.write(json.dumps(fila) + '\n')
        self.proc.stdin.flush()

    def hasta(self, condicion, espera=15):
        fin = time.monotonic() + espera
        while time.monotonic() < fin:
            if select.select([self.proc.stdout], [], [], 0.2)[0]:
                fila = json.loads(self.proc.stdout.readline())
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


def compactacion(fila, estado):
    return isinstance(fila.get('compactacion'), dict) and fila['compactacion'].get('estado') == estado


def test_detener_corta_la_compactacion_y_la_sesion_sigue(cliente):
    assert cliente.hasta(lambda f: f['tipo'] == 'turno_terminado'), 'el primer turno no terminó'
    cliente.enviar(tipo='compactar')
    assert cliente.hasta(lambda f: compactacion(f, 'en_curso')), 'no empezó a compactar'
    cliente.enviar(tipo='interrumpir')
    detenida = cliente.hasta(lambda f: compactacion(f, 'fallida'), espera=5)
    assert detenida and 'detenida' in detenida.get('texto', '').lower()
    # La sesión sigue: un mensaje abre un turno nuevo.
    cliente.enviar(tipo='mensaje', texto='¿sigues ahí?')
    assert cliente.hasta(lambda f: f['tipo'] == 'turno_terminado', espera=15), 'el mensaje quedó en cola'
