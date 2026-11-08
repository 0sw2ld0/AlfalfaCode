"""Órdenes en segundo plano, como los background terminals de Codex."""
import asyncio
import io
import json
import sys

import pytest

from alfalfa import segundo_plano
from alfalfa.stdio import Transport
from alfalfa.terminal import TerminalManager

PY = [sys.executable, '-I', '-B', '-c']
SERVIDOR = ('import time, sys\nprint("listo", flush=True)\nwhile True:\n'
            '    line = sys.stdin.readline() if False else None\n    time.sleep(0.2)\n')


class Si:
    denied = 0

    async def __call__(self, args):
        return None


@pytest.fixture
def motor(tmp_path, monkeypatch):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    monkeypatch.chdir(raiz)
    monkeypatch.setattr(segundo_plano, 'ESPERA_INICIAL', 1.0)
    salida = io.StringIO()
    m = TerminalManager('Lanza el juego', home=tmp_path / 'alfalfa', transport=Transport(salida))
    rt = m.runtime()
    rt.approval_policy = Si()

    def llamar(nombre, **argumentos):
        return asyncio.get_event_loop().run_until_complete(rt.tools.call(nombre, argumentos))

    asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        yield m, rt, llamar, salida
    finally:
        asyncio.get_event_loop().run_until_complete(m.fondo.detener_todos())
        m.close()


def filas(salida, tipo):
    return [json.loads(l) for l in salida.getvalue().splitlines() if json.loads(l)['tipo'] == tipo]


def test_un_programa_que_sigue_abierto_vuelve_con_su_id_y_no_bloquea(motor):
    m, rt, llamar, salida = motor
    resultado = llamar('run_command', argv=PY + [SERVIDOR], purpose='Abrir el juego', background=True)
    assert resultado['en_segundo_plano'] is True and resultado['proceso'] == 'p1'
    assert resultado['exit_code'] is None and 'listo' in resultado['output']
    assert resultado['classification']['status'] == 'running'
    assert rt.state.get('unresolved') == {}  # seguir vivo no es un fallo
    procesos = filas(salida, 'procesos')[-1]['procesos']
    assert procesos[0]['id'] == 'p1' and procesos[0]['vivo'] is True and procesos[0]['ultimas'] == ['listo']
    estado = llamar('command_status', process='p1')
    assert estado['vivo'] is True and estado['salida_nueva'] == ''
    parado = llamar('command_status', process='p1', stop=True)
    assert parado['vivo'] is False and parado['detenido'] is True
    assert filas(salida, 'procesos')[-1]['procesos'][0]['vivo'] is False
    assert any('Proceso p1 detenido' in f['texto'] for f in filas(salida, 'evento'))


def test_lo_que_termina_pronto_devuelve_su_codigo(motor):
    m, rt, llamar, salida = motor
    resultado = llamar('run_command', argv=PY + ['print("hola"); raise SystemExit(3)'], purpose='Probar',
                       background=True)
    assert resultado['exit_code'] == 3 and 'en_segundo_plano' not in resultado and 'hola' in resultado['output']
    assert rt.state['unresolved']  # un fallo de verdad sigue contando


def test_command_status_espera_salida_nueva_y_avisa_al_terminar(motor):
    m, rt, llamar, salida = motor
    codigo = 'import time\nprint("a", flush=True)\ntime.sleep(1.5)\nprint("b", flush=True)\ntime.sleep(0.5)\n'
    llamar('run_command', argv=PY + [codigo], purpose='Probar', background=True)
    estado = llamar('command_status', process='p1', wait_seconds=5)
    assert 'b' in estado['salida_nueva']
    llamar('command_status', process='p1', wait_seconds=3)
    asyncio.get_event_loop().run_until_complete(asyncio.sleep(1))
    final = llamar('command_status', process='p1')
    assert final['vivo'] is False and final['exit_code'] == 0
    assert any('terminó con código 0' in f['texto'] for f in filas(salida, 'evento'))
    with pytest.raises(ValueError, match='No hay un proceso'):
        llamar('command_status', process='p9')


def test_el_cliente_puede_detener_procesos(motor):
    m, rt, llamar, salida = motor
    llamar('run_command', argv=PY + [SERVIDOR], purpose='Abrir', background=True)
    t = m.transport
    t.stop_process = lambda ident: asyncio.ensure_future(m.fondo.detener(ident))
    t.receive(json.dumps({'tipo': 'detener_proceso', 'id': 'p1'}))
    asyncio.get_event_loop().run_until_complete(asyncio.sleep(0.5))
    assert not m.fondo.vivos()
    t.receive(json.dumps({'tipo': 'detener_proceso', 'id': 7}))
    assert 'inválido' in filas(salida, 'error')[-1]['texto']


def test_command_status_y_background_estan_en_el_contrato():
    from alfalfa.proveedores.nativo.herramientas import ON_REQUEST_TOOLS, TOOLS
    nombres = [t['function']['name'] for t in ON_REQUEST_TOOLS]
    assert 'command_status' in nombres and 'command_status' not in [t['function']['name'] for t in TOOLS]
    run = next(t for t in ON_REQUEST_TOOLS if t['function']['name'] == 'run_command')['function']
    checks = next(t for t in ON_REQUEST_TOOLS if t['function']['name'] == 'run_checks')['function']
    assert 'background' in run['parameters']['properties'] and 'background' not in checks['parameters']['properties']


COMPILACION = ('import time\nprint("compilando 1/3", flush=True)\ntime.sleep(1.5)\n'
               'print("compilando 2/3", flush=True)\ntime.sleep(1.5)\nprint("Build complete!", flush=True)\n')


def test_una_orden_que_agota_su_tiempo_pasa_a_segundo_plano_y_termina(motor):
    """Como Codex: una compilación larga no se corta; el modelo espera su final."""
    m, rt, llamar, salida = motor
    resultado = llamar('run_command', argv=PY + [COMPILACION], purpose='Compilar en release', timeout_seconds=1)
    assert resultado['en_segundo_plano'] is True and resultado['proceso'] == 'p1'
    assert 'timed_out' not in resultado and 'compilando 1/3' in resultado['output']
    assert resultado['classification']['status'] == 'running'
    assert 'wait_for_exit' in resultado['nota'] and 'no es un bloqueo' in resultado['nota']
    assert rt.state.get('unresolved') == {}
    assert any('pasó a segundo plano como p1' in f['texto'] for f in filas(salida, 'evento'))
    final = llamar('command_status', process='p1', wait_seconds=30, wait_for_exit=True)
    assert final['vivo'] is False and final['exit_code'] == 0
    assert 'compilando 1/3' not in final['salida_nueva']  # sólo lo nuevo
    assert 'compilando 2/3' in final['salida_nueva'] and 'Build complete!' in final['salida_nueva']


def test_run_checks_sigue_cortandose_al_agotar_su_tiempo(motor):
    m, rt, llamar, salida = motor
    resultado = llamar('run_checks', argv=PY + [COMPILACION], purpose='Pruebas', timeout_seconds=1)
    assert resultado.get('timed_out') is True and 'en_segundo_plano' not in resultado
    assert not m.fondo.procesos


def test_sin_sitio_en_segundo_plano_se_corta_como_antes(motor, monkeypatch):
    m, rt, llamar, salida = motor
    monkeypatch.setattr(segundo_plano, 'MAX_PROCESOS', 0)
    resultado = llamar('run_command', argv=PY + [COMPILACION], purpose='Compilar', timeout_seconds=1)
    assert resultado.get('timed_out') is True and 'en_segundo_plano' not in resultado
