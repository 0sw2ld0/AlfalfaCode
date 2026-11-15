"""Un segundo Detener (o Ctrl-C) mientras el turno se cierra no se salta el cierre.

Antes el segundo `cancel()` caía en uno de los `await` del `finally` de `_run` (la espera de la
instantánea de antes dura hasta 90 s): no se cerraba el turno, no se guardaba y la sesión quedaba
`running`, con `turno_actual` puesto y sin instantáneas.
"""
import asyncio
import io
import json
from unittest.mock import patch

from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.stdio import Transport
from alfalfa.terminal import TerminalManager


async def sin_fin(self, prompt):
    self.task['status'] = 'running'
    await asyncio.sleep(60)


def lento(m):
    original = m._esperar_instantanea

    async def esperar(*args, **kwargs):
        await asyncio.sleep(0.4)  # la instantánea de una carpeta grande
        return await original(*args, **kwargs)

    m._esperar_instantanea = esperar


def test_dos_cancelaciones_seguidas_cierran_el_turno_igual(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    m = TerminalManager('Trabaja', home=tmp_path / 'alfalfa')
    lento(m)

    async def ejercicio():
        tarea = asyncio.create_task(m.run('Trabaja'))
        await asyncio.sleep(0.3)
        tarea.cancel()
        await asyncio.sleep(0.15)  # ya dentro del cierre
        tarea.cancel()
        try:
            await tarea
        except asyncio.CancelledError:
            pass

    try:
        with patch.object(NativeRuntime, 'run', sin_fin):
            asyncio.run(ejercicio())
        guardada = json.loads((m.directory / 'task.json').read_text())
    finally:
        m.close()
    assert guardada['status'] != 'running'
    assert 'turno_actual' not in guardada
    turno = guardada['turnos'][-1]
    assert turno.get('fin') and turno.get('estado') != 'running'


def test_en_la_app_el_segundo_detener_no_vuelve_a_cancelar(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    transporte = Transport(io.StringIO())
    cancelaciones = []

    class Trabajo:
        def done(self):
            return False

        def cancel(self):
            cancelaciones.append(1)

    transporte.worker, transporte.in_turn = Trabajo(), True
    assert transporte.interrumpir_turno() is True
    assert transporte.interrumpir_turno() is True
    assert cancelaciones == [1]
