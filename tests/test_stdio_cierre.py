"""El motor --stdio nunca queda huérfano: fin de stdin, señales o padre muerto lo cierran."""
import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]

# Un turno que pide un permiso de red y lo espera. Con `terco`, además ignora la cancelación
# (como un turno que se quedó colgado): sólo la salida forzada lo termina.
MOTOR = r'''
import asyncio, sys
from unittest.mock import patch
from alfalfa.terminal import main
from alfalfa.proveedores.nativo.runtime import NativeRuntime
terco = sys.argv[1] == 'terco'
async def work(self, prompt):
    self.task['status'] = 'running'
    self.save()
    while True:
        try:
            await self.approve({'argv': ['curl', 'https://ejemplo.test'], 'purpose': 'Red', 'motivo': 'red'})
        except BaseException:
            if not terco:
                raise
        await asyncio.sleep(0.05)
with patch.object(NativeRuntime, 'run', work):
    sys.exit(main(['--stdio', 'Prueba de cierre']))
'''


def _env(tmp_path):
    return dict(os.environ, PYTHONPATH=str(RAIZ), ALFALFA_HOME=str(tmp_path / 'home'))


def _hasta_el_permiso(salida, plazo=20):
    filas = []
    fin = time.time() + plazo
    while time.time() < fin:
        assert select.select([salida], [], [], fin - time.time())[0], 'sin salida del motor'
        fila = json.loads(salida.readline())
        filas.append(fila)
        if fila['tipo'] == 'permiso':
            return filas
    raise AssertionError('no llegó el permiso')


def _estado(tmp_path):
    tareas = list((tmp_path / 'home' / 'sesiones').glob('*/task.json'))
    assert len(tareas) == 1
    return json.loads(tareas[0].read_text())['status']


def _vivo(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # un zombi ya no corre
        estado = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout
    except OSError:
        return True
    return bool(estado.strip()) and not estado.strip().startswith('Z')


def _espera_muerte(pid, segundos):
    fin = time.time() + segundos
    while time.time() < fin:
        if not _vivo(pid):
            return True
        time.sleep(0.1)
    return False


def test_eof_esperando_un_permiso_lo_niega_y_cierra_interrumpida(tmp_path):
    p = subprocess.Popen([sys.executable, '-c', MOTOR, 'normal'], cwd=tmp_path, env=_env(tmp_path),
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        permiso = _hasta_el_permiso(p.stdout)[-1]
        p.stdin.close()
        assert p.wait(timeout=10) is not None
        resto = [json.loads(linea) for linea in p.stdout.read().splitlines()]
        resuelto = next(f for f in resto if f['tipo'] == 'permiso_resuelto')
        assert resuelto['id'] == permiso['id'] and resuelto['aprobado'] is False
        assert resto[-1]['tipo'] == 'fin' and resto[-1]['estado'] == 'interrupted'
        assert _estado(tmp_path) == 'interrupted'
    finally:
        if p.poll() is None:
            p.kill()


def test_sigterm_esperando_un_permiso_termina_aunque_el_turno_no_ceda(tmp_path):
    p = subprocess.Popen([sys.executable, '-c', MOTOR, 'terco'], cwd=tmp_path, env=_env(tmp_path),
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _hasta_el_permiso(p.stdout)
        inicio = time.time()
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=15)
        assert time.time() - inicio < 10, 'SIGTERM debe terminar en pocos segundos'
        assert _estado(tmp_path) == 'interrupted'
    finally:
        if p.poll() is None:
            p.kill()


def test_dos_senales_salen_ya(tmp_path):
    p = subprocess.Popen([sys.executable, '-c', MOTOR, 'terco'], cwd=tmp_path, env=_env(tmp_path),
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _hasta_el_permiso(p.stdout)
        inicio = time.time()
        p.send_signal(signal.SIGINT)
        time.sleep(0.3)
        p.send_signal(signal.SIGINT)
        p.wait(timeout=10)
        assert time.time() - inicio < 2
        assert p.returncode == 130
    finally:
        if p.poll() is None:
            p.kill()


def test_padre_muerto_sin_eof_cierra_el_motor(tmp_path):
    """La app muere pero otro proceso conserva el pipe de stdin: no hay EOF."""
    lectura, escritura = os.pipe()  # este test guarda el extremo de escritura: stdin nunca acaba
    padre = subprocess.Popen(
        [sys.executable, '-c', 'import subprocess, sys, time\n'
         'p = subprocess.Popen([sys.executable, "-c", sys.argv[1], "normal"], stdin=int(sys.argv[2]),'
         ' stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, pass_fds=(int(sys.argv[2]),))\n'
         'print(p.pid, flush=True)\n'
         'linea = p.stdout.readline()\n'
         'while b\'"permiso"\' not in linea:\n'
         '    linea = p.stdout.readline()\n'
         'print("permiso", flush=True)\n'
         'time.sleep(600)\n', MOTOR, str(lectura)],
        cwd=tmp_path, env=_env(tmp_path), stdout=subprocess.PIPE, text=True, pass_fds=(lectura,))
    os.close(lectura)
    motor = None
    try:
        motor = int(padre.stdout.readline())
        assert select.select([padre.stdout], [], [], 20)[0], 'el motor no llegó al permiso'
        assert padre.stdout.readline().strip() == 'permiso'
        padre.kill()  # la app muere de golpe
        padre.wait(timeout=5)
        assert _espera_muerte(motor, 12), 'el motor siguió vivo sin su padre'
        assert _estado(tmp_path) == 'interrupted'
    finally:
        os.close(escritura)
        if padre.poll() is None:
            padre.kill()
        if motor and _vivo(motor):
            os.kill(motor, signal.SIGKILL)
