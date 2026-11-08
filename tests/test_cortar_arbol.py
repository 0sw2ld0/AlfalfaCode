"""Cortar una orden también corta a los descendientes que se fueron a su propio grupo.

SwiftPM lanza swift-driver y swift-frontend con setpgid: al agotar el tiempo, matar
sólo el grupo de la orden dejaba la compilación viva y el reintento competía con ella.
"""
import asyncio
import os
import sys
import time

from alfalfa import procesos, segundo_plano
from alfalfa.terminal import TerminalManager

# Un padre que lanza un nieto en su propio grupo (como llbuild) y se queda esperando.
PADRE = r'''
import os, subprocess, sys, time
hijo = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], process_group=0)
print(hijo.pid, flush=True)
time.sleep(120)
'''


async def _lanzar():
    proc = await asyncio.create_subprocess_exec(sys.executable, '-c', PADRE, stdout=asyncio.subprocess.PIPE,
                                                start_new_session=True)
    nieto = int((await proc.stdout.readline()).decode())
    assert os.getpgid(nieto) == nieto != proc.pid
    return proc, nieto


def _vivo(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # Un zombi ya no compila: launchd lo recoge enseguida.
    return 'Z' not in os.popen('ps -o stat= -p ' + str(pid)).read()


def _esperar_muerte(pid, plazo=5):
    fin = time.monotonic() + plazo
    while time.monotonic() < fin and _vivo(pid):
        time.sleep(0.05)
    return not _vivo(pid)


def test_descendientes_en_otro_grupo_se_encuentran():
    async def caso():
        proc, nieto = await _lanzar()
        try:
            assert procesos.grupos_descendientes(proc.pid) == [nieto]
        finally:
            os.killpg(nieto, 9)
            os.killpg(proc.pid, 9)
            await proc.wait()
    asyncio.run(caso())


def test_nunca_devuelve_grupos_ajenos():
    assert procesos.grupos_descendientes(os.getpid()) == [] or all(
        g != os.getpgid(0) for g in procesos.grupos_descendientes(os.getpid()))
    assert procesos.grupos_descendientes(1) == []
    assert procesos.grupos_descendientes(None) == []


def test_cortar_una_orden_mata_al_nieto_en_su_grupo():
    async def caso():
        proc, nieto = await _lanzar()
        await TerminalManager.terminate(None, proc)
        return nieto
    assert _esperar_muerte(asyncio.run(caso()))


def test_detener_un_proceso_en_segundo_plano_mata_al_nieto():
    async def caso():
        proc, nieto = await _lanzar()
        await segundo_plano._matar(proc)
        return nieto
    assert _esperar_muerte(asyncio.run(caso()))


def test_terminar_grupo_mata_al_nieto():
    async def caso():
        proc, nieto = await _lanzar()
        await procesos.terminar_grupo(proc, espera=1, gracia=0.1)
        return nieto
    assert _esperar_muerte(asyncio.run(caso()))
