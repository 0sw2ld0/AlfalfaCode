"""Al acabar con una operación sin resolver, el motor se la recuerda al modelo una vez. Si el
modelo contesta exactamente lo mismo, la conversación no enseña la respuesta dos veces."""

import json
import os
import select
import subprocess
import sys
from pathlib import Path

SESION = '''
import sys
from unittest.mock import patch
from alfalfa.terminal import main
from alfalfa.proveedores.nativo.runtime import NativeRuntime
RESPUESTA = 'La prueba falla porque suma resta.'
async def inference(self, client, endpoint, payload, **kwargs):
    if endpoint == '/api/show':
        return {'capabilities': ['tools']}
    calls = []
    if not any(m['role'] == 'tool' for m in self.state['messages']):
        calls = [dict(function=dict(name='run_checks', arguments=dict(
            argv=[sys.executable, '-c', 'import sys; sys.exit(1)'], purpose='Pasar la prueba',
            description='Pasar la prueba de la suma')))]
    return dict(message=dict(role='assistant', content='' if calls else RESPUESTA, tool_calls=calls),
                done=True, done_reason='stop', prompt_eval_count=1, eval_count=1)
with patch.object(NativeRuntime, 'request', inference):
    sys.exit(main(sys.argv[1:]))
'''


def test_la_misma_respuesta_seguida_se_enseña_una_vez(tmp_path):
    raiz = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ, PYTHONPATH=raiz, ALFALFA_HOME=str(tmp_path / 'home'))
    proc = subprocess.Popen([sys.executable, '-c', SESION, '--stdio', 'Arregla la suma'], cwd=tmp_path, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    filas = []
    try:
        while True:
            assert select.select([proc.stdout], [], [], 20)[0], 'stdout timeout'
            fila = json.loads(proc.stdout.readline())
            filas.append(fila)
            if fila['tipo'] == 'permiso':
                proc.stdin.write(json.dumps(dict(tipo='permiso', id=fila['id'], aprobado=True)) + '\n')
                proc.stdin.flush()
            if fila['tipo'] == 'turno_terminado':
                break
        proc.stdin.close()
        proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    # El modelo contestó dos veces (antes y después del recordatorio), las dos igual.
    respuestas = [f['texto'] for f in filas if f['tipo'] == 'evento' and f['rol'] == 'agent']
    assert respuestas == ['La prueba falla porque suma resta.']
    sesion = next(f['id'] for f in filas if f['tipo'] == 'sesion')
    registro = next((tmp_path / 'home').rglob(sesion + '.jsonl'))
    guardadas = [json.loads(linea) for linea in registro.read_text().splitlines()]
    assert [g['text'] for g in guardadas if g['role'] == 'agent'] == respuestas
