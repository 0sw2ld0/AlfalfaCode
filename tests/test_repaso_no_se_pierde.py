"""El repaso de aprendizaje del turno anterior termina si le falta poco cuando llega otro mensaje.

Antes se cancelaba en el acto: quien escribía rápido no aprendía nunca nada («cancelada»).
"""
import asyncio

from alfalfa import stdio


class Transport:
    repaso = None


def test_espera_al_repaso_que_le_falta_poco():
    async def ejercicio():
        t = Transport()
        hecho = []

        async def repaso():
            await asyncio.sleep(0.05)
            hecho.append(True)

        t.repaso = asyncio.create_task(repaso())
        await stdio._esperar_repaso(t, segundos=2)
        assert hecho == [True] and t.repaso is None
    asyncio.run(ejercicio())


def test_cancela_el_que_no_acaba_a_tiempo():
    async def ejercicio():
        t = Transport()

        async def repaso():
            await asyncio.sleep(10)

        tarea = asyncio.create_task(repaso())
        t.repaso = tarea
        await stdio._esperar_repaso(t, segundos=0.05)
        assert tarea.cancelled() and t.repaso is None
    asyncio.run(ejercicio())
