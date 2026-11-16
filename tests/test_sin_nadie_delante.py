"""Un encargo sin nadie delante (un temporizador de systemd, cron, un guion): lo que pide permiso se niega en el
acto, queda en el registro y el proceso termina con un código propio (3) en vez de 0.

Antes terminaba con 0 («completed») aunque se hubiera negado algo porque no había quien respondiera: quien lo
lanzaba no podía distinguirlo de un encargo hecho."""
import json
import os
import subprocess
import sys
from pathlib import Path

ORDEN = '''
import sys
from unittest.mock import patch
from alfalfa.terminal import main
from alfalfa.proveedores.nativo.runtime import NativeRuntime
PIDE_RED = sys.argv.pop(1) == 'red'
async def inference(self, client, endpoint, payload, **kwargs):
    if endpoint == '/api/show':
        return {'capabilities': ['tools']}
    calls = []
    if PIDE_RED and not any(m['role'] == 'tool' for m in self.state['messages']):
        calls = [dict(function=dict(name='run_command', arguments=dict(
                     argv=[sys.executable, '-c', 'print(1)'], purpose='Con red', description='Con red',
                     needs_network=True)))]
    return dict(message=dict(role='assistant', content='' if calls else 'Hecho.', tool_calls=calls),
                done=True, done_reason='stop', prompt_eval_count=1, eval_count=1)
with patch.object(NativeRuntime, 'request', inference):
    sys.exit(main(sys.argv[1:]))
'''


def lanzar(tmp_path, pide):
    proyecto = tmp_path / 'proyecto'
    proyecto.mkdir()
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), ALFALFA_HOME=str(tmp_path / 'home'),
               OLLAMA_API_KEY='falsa-0123456789')
    return subprocess.run([sys.executable, '-c', ORDEN, pide, '--modelo', 'deepseek-v4.1-flash:cloud', 'Encargo'],
                          cwd=proyecto, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)


def registro(tmp_path):
    textos = []
    for archivo in (tmp_path / 'home').rglob('*.jsonl'):
        for linea in archivo.read_text().splitlines():
            try:
                fila = json.loads(linea)
            except ValueError:
                continue
            if isinstance(fila, dict):
                textos.append(str(fila.get('text') or fila.get('texto') or ''))
    return '\n'.join(textos)


def test_sin_terminal_lo_que_pide_permiso_se_niega_y_sale_con_3(tmp_path):
    hecho = lanzar(tmp_path, 'red')
    assert hecho.returncode == 3, (hecho.returncode, hecho.stdout[-500:], hecho.stderr[-500:])
    assert 'Sin terminal no hay quien responda' in hecho.stdout
    assert 'Un permiso negado sin nadie que respondiera' in hecho.stderr and '--modo' in hecho.stderr
    texto = registro(tmp_path)
    assert 'Comando rechazado' in texto and 'Un permiso negado sin nadie que respondiera' in texto


def test_sin_terminal_y_sin_nada_que_preguntar_sale_con_0(tmp_path):
    hecho = lanzar(tmp_path, 'nada')
    assert hecho.returncode == 0, (hecho.stdout[-500:], hecho.stderr[-500:])
    assert 'sin nadie' not in hecho.stderr
