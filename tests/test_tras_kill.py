"""Tras un kill -9 del motor, la sesión no se queda `running` para siempre ni su orden huérfana.

Antes: con una orden larga en marcha y el motor muerto de golpe, `--sesiones` seguía diciendo
`running`, reabrir y cerrar no lo corregía, la orden seguía viva (con sus puertos y su CPU) y el
turno quedaba abierto.
"""
import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from alfalfa.stdio import sessions
from alfalfa.terminal import TerminalManager

ORDEN = '''
import sys, asyncio
from unittest.mock import patch
from alfalfa.terminal import main
from alfalfa.proveedores.nativo.runtime import NativeRuntime
async def work(self, prompt):
    self.task['status'] = 'running'
    await self.tools.call('run_command', dict(argv=[sys.executable, '-c',
        "import os,time; open('hijo.pid','w').write(str(os.getpid())); time.sleep(120)", 'alfalfa-hijo-' + 'tras-kill'],
        purpose='larga'))
with patch.object(NativeRuntime, 'run', work):
    sys.exit(main(['--stdio', '--auto', 'larga']))
'''


def vivo(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def pid_del_hijo():
    """El pid de la orden visto desde fuera. Con bwrap la orden tiene su propio espacio de procesos y el
    que escribe en hijo.pid es el de dentro; además el propio bwrap lleva la orden entre sus argumentos."""
    marca = 'alfalfa-hijo-tras-kill'
    pids = subprocess.run(['pgrep', '-f', marca], capture_output=True, text=True).stdout.split()
    for pid in pids:
        orden = subprocess.run(['ps', '-ww', '-o', 'args=', '-p', pid], capture_output=True, text=True).stdout.strip()
        if orden.startswith(sys.executable + ' -c ') and orden.endswith(marca):
            return int(pid)
    raise AssertionError('no se encontró la orden entre ' + str(pids))


def con_bwrap():
    from alfalfa.seatbelt import sandbox_del_sistema
    return sandbox_del_sistema('.').etiqueta == 'bwrap'


@pytest.fixture
def muerto_de_golpe(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    hogar = tmp_path / 'home'
    env = dict(os.environ, PYTHONPATH=str(root), ALFALFA_HOME=str(hogar))
    motor = subprocess.Popen([sys.executable, '-c', ORDEN], cwd=tmp_path, env=env, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    hijo = None
    try:
        assert select.select([motor.stdout], [], [], 15)[0], 'el motor no arrancó'
        sesion = json.loads(motor.stdout.readline())
        assert sesion['tipo'] == 'sesion'
        fin = time.monotonic() + 20
        while not (tmp_path / 'hijo.pid').exists() and time.monotonic() < fin:
            time.sleep(0.05)
        hijo = pid_del_hijo()
        time.sleep(0.3)
        motor.kill()  # kill -9: ni finally ni guardado
        motor.wait(timeout=10)
        monkeypatch.setenv('ALFALFA_HOME', str(hogar))
        monkeypatch.chdir(tmp_path)
        yield sesion['id'], hogar, hijo
    finally:
        if motor.poll() is None:
            motor.kill()
        for tubo in (motor.stdin, motor.stdout, motor.stderr):
            tubo.close()
        if hijo and vivo(hijo):
            os.kill(hijo, 9)


def test_el_listado_no_dice_running_si_nadie_trabaja_en_ella(muerto_de_golpe):
    ident, hogar, hijo = muerto_de_golpe
    tarea = json.loads((hogar / 'sesiones' / ident / 'task.json').read_text())
    assert tarea['status'] == 'running', 'el motor murió sin cerrar: así queda en disco'
    fila = next(f for f in sessions() if f['id'] == ident)
    assert fila['estado'] == 'interrupted'


def test_al_reabrirla_se_recoge_la_orden_y_se_cierra_el_turno(muerto_de_golpe):
    ident, hogar, hijo = muerto_de_golpe
    if con_bwrap():
        # bwrap (--die-with-parent) se lleva la orden con el motor: no queda huérfana que recoger.
        fin = time.monotonic() + 10
        while vivo(hijo) and time.monotonic() < fin:
            time.sleep(0.05)
        assert not vivo(hijo), 'la orden sigue viva sin su motor'
    else:
        # Seatbelt la deja huérfana (viva) hasta que se reabre la sesión.
        assert vivo(hijo), 'la orden quedó huérfana'
    m = TerminalManager(None, resume=ident, home=hogar, wait=True)
    try:
        assert m.task['status'] == 'interrupted'
        assert 'turno_actual' not in m.task and 'process_group' not in m.task
        turno = m.task['turnos'][-1]
        assert turno['estado'] == 'interrupted' and turno.get('fin')
    finally:
        m.close()
    fin = time.monotonic() + 10
    while vivo(hijo) and time.monotonic() < fin:
        time.sleep(0.05)
    assert not vivo(hijo), 'la orden de antes sigue viva'
    guardada = json.loads((hogar / 'sesiones' / ident / 'task.json').read_text())
    assert guardada['status'] == 'interrupted'


def test_una_sesion_terminada_no_se_toca_al_reabrirla(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    m = TerminalManager('x', home=tmp_path / 'home')
    m.task['status'] = 'completed'
    m.save()
    ident = m.task['id']
    m.close()
    otra = TerminalManager('sigue', resume=ident, home=tmp_path / 'home')
    try:
        assert otra.task['status'] == 'completed'
    finally:
        otra.close()
