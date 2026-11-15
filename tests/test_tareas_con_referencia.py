"""Lo que la app pide detener y corre sin esperar no se pierde ni pierde su error.

Antes `stdio` lanzaba `asyncio.create_task(…)` sin guardar la tarea (detener la cola, detener un
proceso en segundo plano): el recolector podía llevársela a medias y, si fallaba con algo que no fuera
ValueError, la app no se enteraba de que no se detuvo nada.
"""
import asyncio
import io
import json

from alfalfa.stdio import Transport


def filas(salida):
    return [json.loads(linea) for linea in salida.getvalue().splitlines()]


class Cola:
    activa = True

    def __init__(self, error=None):
        self.error, self.detenida = error, False

    async def detener(self):
        await asyncio.sleep(0.01)
        if self.error:
            raise self.error
        self.detenida = True


def interrumpir(cola):
    salida = io.StringIO()
    vivas = []

    async def ir():
        transporte = Transport(salida)
        transporte.cola_encargos = cola
        assert transporte.interrumpir_turno() is True
        vivas.append(len(getattr(transporte, 'sueltas', ())))
        await asyncio.sleep(0.1)
        vivas.append(len(getattr(transporte, 'sueltas', ())))
    asyncio.run(ir())
    return filas(salida), vivas


def test_detener_la_cola_que_falla_lo_dice():
    eventos, _ = interrumpir(Cola(RuntimeError('el disco no responde')))
    errores = [f['texto'] for f in eventos if f['tipo'] == 'error']
    assert errores == ['Detener la cola falló: el disco no responde'], eventos


def test_la_tarea_tiene_referencia_hasta_que_acaba():
    cola = Cola()
    eventos, vivas = interrumpir(cola)
    assert cola.detenida and vivas == [1, 0]
    assert not [f for f in eventos if f['tipo'] == 'error']
