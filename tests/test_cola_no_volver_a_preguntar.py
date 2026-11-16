"""En una cola de encargos, «No volver a preguntar» y el sitio resuelven la tarjeta de permiso.

La tarjeta de un encargo la emite su transporte hijo y la contesta la app al transporte padre.
Antes el hijo solo compartía `pending` y `for_session`: «No volver a preguntar» (`siempre`) y la
elección de sitio daban «id de permiso desconocido», la tarjeta seguía en pantalla y el encargo
quedaba parado. «Permitir» a secas sí funcionaba.
"""
import asyncio
import io
import json

import pytest

from alfalfa.stdio import Transport
from alfalfa.terminal import TerminalManager


async def tarjeta(proyecto, hogar, desde_cola, peticion, respuesta):
    salida = io.StringIO()
    padre = Transport(salida)
    manager = TerminalManager('Cola de encargos', model='ollama/demo', transport=padre, read_only=True,
                              cwd=proyecto, home=hogar, reglas_equipo=None)
    try:
        manager.cola_encargos.preparar(['encargo uno'])
        ident = manager.task['cola_encargos']['encargos'][0]['id']
        transporte = manager.cola_encargos._transporte(ident) if desde_cola else padre
        espera = asyncio.create_task(transporte.approval(str(proyecto), False)(peticion))
        await asyncio.sleep(0.05)
        tarjeta = next(iter(padre.pending))
        padre.receive(json.dumps(dict(tipo='permiso', id=tarjeta, aprobado=True, **respuesta)))
        resultado = await asyncio.wait_for(espera, 2)
        filas = [json.loads(linea) for linea in salida.getvalue().splitlines()]
        return resultado, [f for f in filas if f['tipo'] == 'error'], tarjeta in padre.pending
    finally:
        manager.close()


NPM = dict(argv=['npm', 'install'], purpose='Instalar', motivo='red', prefijo=['npm', 'install'], para_sesion=True)
SITIO = dict(argv=['open', 'https://ejemplo.test'], purpose='Abrir', motivo='red',
             sitio={'host': 'ejemplo.test'})


@pytest.mark.parametrize('desde_cola', [False, True])
@pytest.mark.parametrize('peticion, respuesta, esperado', [
    (NPM, dict(siempre=True), 'siempre'),
    (NPM, dict(sesion=True), 'sesion'),
    (NPM, {}, None),  # «Permitir» a secas: aprobada sin recordar nada
    (SITIO, dict(sitio='proyecto'), 'proyecto'),
])
def test_la_respuesta_resuelve_la_tarjeta_tambien_en_la_cola(tmp_path, monkeypatch, desde_cola, peticion,
                                                             respuesta, esperado):
    proyecto = tmp_path / 'proyecto'
    proyecto.mkdir()
    monkeypatch.chdir(proyecto)
    # Hogar temporal: sin él, cada pasada dejaba 8 sesiones «Cola de encargos» en el ~/.alfalfa de verdad.
    resultado, errores, sigue = asyncio.run(tarjeta(proyecto, tmp_path / 'hogar', desde_cola, peticion, respuesta))
    assert resultado == esperado and not errores and not sigue
