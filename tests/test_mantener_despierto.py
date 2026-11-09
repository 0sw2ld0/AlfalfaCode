"""Mantener el Mac despierto mientras trabaja un turno, como «Prevent sleep while running» de Codex."""
import asyncio
import io
import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from alfalfa import despierto
from alfalfa.despierto import NOMBRE, MantenerDespierto, activado_por_defecto
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.stdio import Transport
from alfalfa.terminal import TerminalManager, keep_awake, main

MAC = sys.platform == 'darwin' and Path('/usr/bin/pmset').exists()
mac = pytest.mark.skipif(not MAC, reason='requiere macOS y pmset')


class Falsa:
    """Una aserción de IOKit de mentira: apunta lo que se crea y se suelta."""

    def __init__(self, falla=False):
        self.vivas, self.creadas, self.falla = set(), 0, falla

    def crear(self, nombre):
        if self.falla:
            raise OSError('no')
        assert nombre == NOMBRE
        self.creadas += 1
        self.vivas.add(self.creadas)
        return self.creadas

    def soltar(self, ident):
        self.vivas.remove(ident)


@pytest.mark.parametrize('valor, activo', [
    (None, True), ('', True), ('1', True), ('sí', True),
    ('0', False), ('no', False), ('false', False), (' OFF ', False), ('apagado', False),
])
def test_encendido_salvo_que_la_variable_lo_apague(valor, activo):
    entorno = {} if valor is None else {'ALFALFA_MANTENER_DESPIERTO': valor}
    assert activado_por_defecto(entorno) is activo


def test_una_asercion_mientras_hay_trabajo_y_se_anida():
    falsa, avisos = Falsa(), []
    d = MantenerDespierto(True, aviso=avisos.append, sistema=lambda: falsa)
    d.empezar()
    d.empezar()           # una compactación dentro del turno
    assert falsa.vivas == {1} and d.sostenida
    d.terminar()
    assert falsa.vivas == {1}
    d.terminar()
    d.terminar()          # de más: no baja de cero
    assert falsa.vivas == set() and not d.sostenida
    assert avisos == [True, False]


def test_el_interruptor_vale_a_mitad_de_turno():
    falsa, avisos = Falsa(), []
    d = MantenerDespierto(True, aviso=avisos.append, sistema=lambda: falsa)
    d.empezar()
    d.cambiar(False)
    assert falsa.vivas == set()
    d.cambiar(True)
    assert len(falsa.vivas) == 1
    d.cerrar()
    assert falsa.vivas == set() and avisos == [True, False, True, False]


def test_apagado_no_toma_nada():
    falsa = Falsa()
    d = MantenerDespierto(False, sistema=lambda: falsa)
    d.empezar()
    assert falsa.creadas == 0


def test_sin_iokit_o_si_macos_la_niega_no_avisa_de_nada():
    avisos = []
    sin = MantenerDespierto(True, aviso=avisos.append, sistema=lambda: None)
    sin.empezar()
    niega = MantenerDespierto(True, aviso=avisos.append, sistema=lambda: Falsa(falla=True))
    niega.empezar()
    assert avisos == [] and not sin.sostenida and not niega.sostenida
    sin.terminar()
    niega.terminar()


def test_la_opcion_y_la_variable_de_la_terminal(monkeypatch):
    assert keep_awake(type('A', (), {'sin_mantener_despierto': True})()) is False
    assert keep_awake(type('A', (), {'sin_mantener_despierto': False})()) is None
    monkeypatch.setenv('ALFALFA_MANTENER_DESPIERTO', '0')
    assert MantenerDespierto().activo is False
    monkeypatch.delenv('ALFALFA_MANTENER_DESPIERTO')
    assert MantenerDespierto().activo is True
    monkeypatch.setenv('ALFALFA_MANTENER_DESPIERTO', '1')
    assert MantenerDespierto().activo is True


def test_stdio_acepta_el_interruptor_de_la_app():
    out = io.StringIO()
    t = Transport(out)
    t.receive('{"tipo":"mantener_despierto","activo":false}')   # sin sesión aún: inválido
    pedidos = []
    t.keep_awake = pedidos.append
    t.receive('{"tipo":"mantener_despierto","activo":true}')
    t.receive('{"tipo":"mantener_despierto","activo":1}')
    t.receive('{"tipo":"mantener_despierto"}')
    assert pedidos == [True]
    assert out.getvalue().count('inválido') == 3


def _modelo(contenido='Hecho.', error=None):
    async def request(rt, client, endpoint, payload, **kwargs):
        if endpoint == '/api/show':
            return {'capabilities': ['tools']}
        assert rt.manager.despierto.sostenida, 'el turno trabaja sin la aserción'
        if error:
            raise error
        return dict(message={'role': 'assistant', 'content': contenido}, done=True, done_reason='stop',
                    prompt_eval_count=1, eval_count=1)
    return request


@pytest.fixture
def con_falsa(tmp_path, monkeypatch):
    (tmp_path / 'project').mkdir()
    monkeypatch.chdir(tmp_path / 'project')
    monkeypatch.delenv('ALFALFA_MANTENER_DESPIERTO', raising=False)
    falsa = Falsa()
    monkeypatch.setattr(despierto, '_asercion_del_sistema', lambda: falsa)
    return falsa


def _turno(m, request):
    async def run():
        with patch.object(NativeRuntime, 'request', request):
            return await m.run('Hola.')
    return asyncio.run(asyncio.wait_for(run(), 60))


def test_el_turno_la_toma_y_la_suelta_y_la_app_lo_sabe(con_falsa, tmp_path):
    salida = io.StringIO()
    m = TerminalManager('x', home=tmp_path / 'alfalfa', transport=Transport(salida))
    try:
        _turno(m, _modelo())
        assert con_falsa.creadas == 1 and con_falsa.vivas == set()
        filas = [json.loads(linea) for linea in salida.getvalue().splitlines()]
        assert [f['activo'] for f in filas if f['tipo'] == 'despierto'] == [True, False]
    finally:
        m.close()


def test_se_suelta_aunque_el_turno_falle(con_falsa, tmp_path):
    m = TerminalManager('x', home=tmp_path / 'alfalfa')
    try:
        _turno(m, _modelo(error=OSError('se cayó el modelo')))
        assert m.task['status'] == 'failed'
        assert con_falsa.creadas == 1 and con_falsa.vivas == set()
    finally:
        m.close()


def test_se_suelta_al_cancelar_el_turno(con_falsa, tmp_path):
    m = TerminalManager('x', home=tmp_path / 'alfalfa')

    async def colgado(rt, client, endpoint, payload, **kwargs):
        if endpoint == '/api/show':
            return {'capabilities': ['tools']}
        await asyncio.sleep(3600)

    async def run():
        with patch.object(NativeRuntime, 'request', colgado):
            tarea = asyncio.ensure_future(m.run('Hola.'))
            while not con_falsa.vivas:
                await asyncio.sleep(0.01)
            tarea.cancel()
            return await tarea
    try:
        assert asyncio.run(asyncio.wait_for(run(), 60)) == 130
        assert con_falsa.creadas == 1 and con_falsa.vivas == set()
    finally:
        m.close()


def test_en_la_terminal_lo_dice_una_vez(con_falsa, tmp_path, capsys):
    m = TerminalManager('x', home=tmp_path / 'alfalfa')
    try:
        _turno(m, _modelo())
        assert capsys.readouterr().out.count('ALFALFA_MANTENER_DESPIERTO=0 lo apaga') == 1
    finally:
        m.close()


def test_apagado_el_turno_no_la_toma(con_falsa, tmp_path, capsys):
    m = TerminalManager('x', home=tmp_path / 'alfalfa', keep_awake=False)
    try:
        async def request(rt, client, endpoint, payload, **kwargs):
            if endpoint == '/api/show':
                return {'capabilities': ['tools']}
            return dict(message={'role': 'assistant', 'content': 'Hecho.'}, done=True, done_reason='stop',
                        prompt_eval_count=1, eval_count=1)
        _turno(m, request)
        assert con_falsa.creadas == 0 and 'lo apaga' not in capsys.readouterr().out
    finally:
        m.close()


def test_la_opcion_de_la_orden_existe(capsys):
    with pytest.raises(SystemExit) as salida:
        main(['--help'])
    assert salida.value.code == 0 and '--sin-mantener-despierto' in capsys.readouterr().out


# --- De verdad: lo que dice macOS ---

def _aserciones_de(pid):
    """Las líneas de `pmset -g assertions` con la aserción de AlfalfaCode del proceso `pid`."""
    texto = subprocess.run(['/usr/bin/pmset', '-g', 'assertions'], capture_output=True, timeout=30).stdout
    return [linea for linea in texto.decode('utf-8', 'replace').splitlines()
            if 'pid %d(' % pid in linea and NOMBRE in linea and 'PreventUserIdleSystemSleep' in linea]


SESION_QUE_ESPERA = '''
import asyncio, os, sys
from unittest.mock import patch
from alfalfa.terminal import main
from alfalfa.proveedores.nativo.runtime import NativeRuntime
async def inference(self, client, endpoint, payload, **kwargs):
    if endpoint == '/api/show':
        return {'capabilities': ['tools']}
    while not os.path.exists('seguir'):
        await asyncio.sleep(0.05)
    return dict(message=dict(role='assistant', content='Hecho.'), done=True, done_reason='stop',
                prompt_eval_count=1, eval_count=1)
with patch.object(NativeRuntime, 'request', inference):
    sys.exit(main(['--stdio', 'Encargo'] + sys.argv[1:]))
'''


def _sesion(tmp_path, *argumentos):
    proyecto = tmp_path / 'proyecto-de-prueba'
    proyecto.mkdir()
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), ALFALFA_HOME=str(tmp_path / 'home'))
    env.pop('ALFALFA_MANTENER_DESPIERTO', None)
    proc = subprocess.Popen([sys.executable, '-c', SESION_QUE_ESPERA, *argumentos], cwd=proyecto, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc, proyecto


def _hasta(proc, tipo):
    filas = []
    while True:
        assert select.select([proc.stdout], [], [], 30)[0], 'stdout timeout'
        linea = proc.stdout.readline()
        assert linea, 'el proceso se cerró: ' + proc.stderr.read()
        filas.append(json.loads(linea))
        if filas[-1]['tipo'] == tipo:
            return filas


def _cerrar(proc):
    if proc.poll() is None:
        proc.kill()
        proc.wait()


@mac
def test_real_pmset_la_ve_durante_el_turno_y_no_al_terminar(tmp_path):
    proc, proyecto = _sesion(tmp_path)
    try:
        filas = _hasta(proc, 'despierto')
        assert filas[-1]['activo'] is True
        assert len(_aserciones_de(proc.pid)) == 1
        (proyecto / 'seguir').write_text('')
        filas = _hasta(proc, 'turno_terminado')
        assert [f['activo'] for f in filas if f['tipo'] == 'despierto'] == [False]
        # La sesión sigue abierta esperando otro mensaje, pero ya no mantiene el Mac despierto.
        assert proc.poll() is None and _aserciones_de(proc.pid) == []
        proc.stdin.close()
        assert proc.wait(timeout=30) == 0
    finally:
        _cerrar(proc)


@mac
def test_real_si_el_proceso_muere_macos_la_suelta(tmp_path):
    proc, _ = _sesion(tmp_path)
    try:
        _hasta(proc, 'despierto')
        assert len(_aserciones_de(proc.pid)) == 1
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)
        limite = time.monotonic() + 10
        while _aserciones_de(proc.pid) and time.monotonic() < limite:
            time.sleep(0.2)
        assert _aserciones_de(proc.pid) == []
    finally:
        _cerrar(proc)


@mac
def test_real_apagado_no_hay_asercion(tmp_path):
    proc, proyecto = _sesion(tmp_path, '--sin-mantener-despierto')
    try:
        _hasta(proc, 'sesion')
        time.sleep(1)
        assert _aserciones_de(proc.pid) == []
        (proyecto / 'seguir').write_text('')
        filas = _hasta(proc, 'turno_terminado')
        assert not [f for f in filas if f['tipo'] == 'despierto']
        proc.stdin.close()
        proc.wait(timeout=30)
    finally:
        _cerrar(proc)
