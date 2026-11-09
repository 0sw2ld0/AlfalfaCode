"""run_checks con Python no ensucia el workspace con __pycache__.

En un repo sin .gitignore, `run_checks python3 uso.py` (uso.py importa
calculo.py) creaba __pycache__/ durante la comprobación: la evidencia la daba
por insuficiente («workspace_changed_during_check») y la sesión terminaba
bloqueada aunque todo estaba bien (prueba real de apply_patch, 06/11).
"""
import asyncio
import subprocess
import sys
from unittest.mock import patch

import pytest

from alfalfa.proveedores.nativo import historial
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.terminal import TerminalManager


@pytest.mark.skipif(sys.platform != 'darwin', reason='run_checks corre en Seatbelt, sólo en macOS')
def test_run_checks_con_python_no_crea_pycache_y_la_comprobacion_vale(tmp_path, monkeypatch):
    raiz = tmp_path / 'repo'
    raiz.mkdir()
    subprocess.run(['git', 'init', '-q'], cwd=raiz, check=True)
    (raiz / 'calculo.py').write_text('def suma(a, b):\n    return a + b\n')
    (raiz / 'uso.py').write_text('from calculo import suma\nprint(suma(2, 3))\n')
    monkeypatch.chdir(raiz)
    pasos = [{'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'run_checks', 'arguments': {
        'argv': [sys.executable, 'uso.py'], 'purpose': 'Comprobar que uso.py imprime 5'}}}]},
             {'role': 'assistant', 'content': 'uso.py imprime 5.'}]

    async def request(self, client, endpoint, payload, **kwargs):
        if endpoint == '/api/show':
            return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
        return dict(message=pasos.pop(0), done=True, done_reason='stop', prompt_eval_count=900, eval_count=10)
    m = TerminalManager('Comprueba uso.py.', home=tmp_path / 'alfalfa')
    try:
        with patch.object(NativeRuntime, 'request', request):
            codigo = asyncio.run(asyncio.wait_for(m.run('Comprueba uso.py.'), 120))
        # Desde la v4, las comprobaciones van en checks.jsonl: historial.leer las junta.
        estado = historial.leer(m.directory / 'native' / m.task['id'] / 'session.json')
        check, = estado['checks']
        assert check['exit_code'] == 0 and '5' in check['output']
        assert check['verification_status'] == 'sufficient'
        assert not (raiz / '__pycache__').exists()
        assert codigo == 0 and m.task['status'] == 'completed'
    finally:
        m.close()
