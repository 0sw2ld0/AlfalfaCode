"""Detener a mitad de una tanda de herramientas deja la sesión lista para seguir.

Un turno entero de AlfalfaCode con el modelo y las herramientas sustituidos: el modelo pide
varias órdenes, la persona pulsa Detener (se cancela el turno) y después escribe otra cosa.

- Lo que quedó en cola no se ejecuta al seguir: antes corría antes de leer el mensaje nuevo.
- Una tarjeta de permiso sin contestar no deja la orden «incierta» ni la sesión bloqueada.
- Una orden cortada en marcha frena su repetición sólo en el turno siguiente, no para siempre.
- En la app (--stdio) guardar no cambia los objetos del estado: la orden cortada no se repite.
- «Bucle detectado» cuenta repeticiones sin cambios dentro de un turno; no se arrastra al siguiente.
"""
import asyncio
import io
import json
import subprocess
from unittest.mock import patch

import pytest

from alfalfa.proveedores.nativo.herramientas import WorkspaceTools
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.stdio import Transport
from alfalfa.terminal import TerminalManager

SECRETO = 'clave-de-prueba-0123456789'


def orden(nombre, **extra):
    return {'function': {'name': 'run_command',
                         'arguments': dict(argv=['hacer', nombre], purpose=nombre, **extra)}}


def tanda(*ordenes):
    return {'role': 'assistant', 'content': '', 'tool_calls': list(ordenes)}


class Tarjeta:
    """Aprobación que espera a la persona: `contestar(True/False)` la resuelve."""

    def __init__(self):
        self.denied, self.abiertas, self.respuesta = 0, 0, None

    async def __call__(self, args):
        self.abiertas += 1
        while self.respuesta is None:
            await asyncio.sleep(0.01)
        respuesta, self.respuesta = self.respuesta, None
        if not respuesta:
            self.denied += 1
            raise ValueError('Comando rechazado; no se ejecutó.')

    def contestar(self, respuesta):
        self.respuesta = respuesta


class Escena:
    def __init__(self, tmp_path, monkeypatch, *, app=False):
        raiz = tmp_path / 'proyecto'
        raiz.mkdir()
        subprocess.run(['git', 'init', '-q'], cwd=raiz, check=True)
        monkeypatch.chdir(raiz)
        self.pasos, self.vistos, self.hechas = [], [], []
        # Órdenes que se quedan en marcha (hasta que se corte el turno) o que piden permiso antes.
        self.largas, self.con_permiso, self.con_reintento = set(), set(), set()
        self.en_marcha = None
        self.tarjeta = Tarjeta()
        self.transporte = Transport(io.StringIO()) if app else None
        if app:
            self.transporte.secretos.add(SECRETO)
        self.m = TerminalManager('inicio', home=tmp_path / 'alfalfa', transport=self.transporte)
        self.m.approval = self.tarjeta

    async def _request(self, rt, client, endpoint, payload, **kwargs):
        if endpoint == '/api/show':
            return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
        self.vistos.append(payload['messages'])
        mensaje = self.pasos.pop(0) if self.pasos else {'role': 'assistant', 'content': 'Entendido.'}
        return dict(message=mensaje, done=True, done_reason='stop', prompt_eval_count=900, eval_count=10)

    async def _call(self, herramientas, name, args):
        nombre = args['purpose']
        if nombre in self.con_permiso:
            await herramientas.runtime.approve({'argv': args['argv'], 'purpose': nombre})
        if nombre in self.con_reintento:
            # La orden ya corrió dentro del sandbox y pide repetirse fuera: tiene efectos.
            self.hechas.append(nombre + ' (en el sandbox)')
            await herramientas.runtime.approve({'argv': args['argv'], 'purpose': nombre, 'motivo': 'reintento',
                                                'motivos': ['reintento'], 'sin_sandbox': 'bloqueada'})
        if nombre.startswith('editar'):
            # Como write_file o edit_file: el proyecto cambió.
            estado = herramientas.runtime.state
            estado['workspace_revision'] = estado.get('workspace_revision', 0) + 1
        if nombre in self.largas:
            self.largas.discard(nombre)
            self.en_marcha = nombre
            await asyncio.Event().wait()
        self.hechas.append(nombre)
        return {'exit_code': 0, 'output': 'hecho ' + nombre + ' ' + SECRETO}

    def turno(self, texto, *pasos, detener_cuando=None):
        """Un mensaje de la persona. `detener_cuando()` verdadero → pulsa Detener. Estado final del turno."""
        self.pasos[:] = pasos
        escena = self

        async def request(rt, client, endpoint, payload, **kwargs):
            return await escena._request(rt, client, endpoint, payload, **kwargs)

        async def call(herramientas, name, args):
            return await escena._call(herramientas, name, args)

        async def correr():
            tarea = asyncio.create_task(self.m.run(texto))
            while detener_cuando is not None and not tarea.done():
                if detener_cuando():
                    tarea.cancel()
                    break
                await asyncio.sleep(0.01)
            await tarea

        with patch.object(NativeRuntime, 'request', request), patch.object(WorkspaceTools, 'call', call):
            asyncio.run(asyncio.wait_for(correr(), 60))
        return self.m.task['status']

    def resultados(self, peticion=-1):
        """{orden: resultado} de las herramientas que el modelo recibió en esa petición, por orden."""
        mensajes, vistos = self.vistos[peticion], {}
        nombres = [c['function']['arguments']['purpose'] for m in mensajes for c in m.get('tool_calls') or []]
        salidas = [m['content'] for m in mensajes if m['role'] == 'tool']
        assert len(nombres) == len(salidas), (nombres, salidas)
        for nombre, salida in zip(nombres, salidas):
            vistos.setdefault(nombre, []).append(salida)
        return vistos

    def cerrar(self):
        self.m.close()


@pytest.fixture(params=[False, True], ids=['terminal', 'app'])
def escena(request, tmp_path, monkeypatch):
    e = Escena(tmp_path, monkeypatch, app=request.param)
    yield e
    e.cerrar()


@pytest.fixture
def en_la_app(tmp_path, monkeypatch):
    e = Escena(tmp_path, monkeypatch, app=True)
    yield e
    e.cerrar()


# ---------------------------------------------------------------- la cola de un turno cortado
def test_lo_que_quedo_en_cola_no_se_ejecuta_al_seguir(escena):
    escena.largas.add('B')
    estado = escena.turno('haz A, B y C', tanda(orden('A'), orden('B'), orden('C')),
                          detener_cuando=lambda: escena.en_marcha == 'B')
    assert estado == 'interrupted' and escena.hechas == ['A']
    estado = escena.turno('PARA. No hagas C.')
    # Ni C (en cola) ni B (cortada) corren antes de que el modelo lea el mensaje nuevo.
    assert escena.hechas == ['A']
    recibido = escena.resultados()
    assert list(recibido) == ['A', 'B', 'C'] and all(len(v) == 1 for v in recibido.values())
    assert 'hecho A' in recibido['A'][0]
    assert 'incierto' in recibido['B'][0]
    assert 'No se ejecutó' in recibido['C'][0] and 'incierto' not in recibido['C'][0]
    assert escena.vistos[-1][-1]['role'] == 'user' and 'PARA' in escena.vistos[-1][-1]['content']
    assert estado == 'completed'


def test_en_la_app_la_orden_cortada_no_se_repite_ni_cambia_la_terminada(en_la_app):
    escena = en_la_app
    escena.largas.add('B')
    escena.turno('haz A, B y C', tanda(orden('A'), orden('B'), orden('C')),
                 detener_cuando=lambda: escena.en_marcha == 'B')
    escena.turno('Sigue')
    assert escena.hechas == ['A']
    recibido = escena.resultados()
    assert 'hecho A' in recibido['A'][0] and 'incierto' in recibido['B'][0]


def test_en_la_app_guardar_tacha_los_secretos_sin_cambiar_los_objetos(en_la_app):
    rt = en_la_app.m.runtime()
    cola = [dict(function=orden('A')['function'], status='queued')]
    mensaje = {'role': 'tool', 'tool_name': 'run_command', 'content': 'salida con ' + SECRETO}
    rt.state['pending'] = cola
    rt.state['messages'].append(mensaje)
    rt.save()
    assert rt.state['pending'] is cola and rt.state['pending'][0] is cola[0]
    assert rt.state['messages'][-1] is mensaje
    assert mensaje['content'] == 'salida con [REDACTADO]'
    guardado = (en_la_app.m.directory / 'native' / en_la_app.m.task['id'] / 'session.json').read_text()
    assert SECRETO not in guardado and '[REDACTADO]' in guardado


# ---------------------------------------------------------------- la tarjeta sin contestar
def test_detener_con_la_tarjeta_abierta_no_bloquea_la_sesion(escena):
    escena.con_permiso.add('instalar')
    estado = escena.turno('INSTALA las dependencias', tanda(orden('instalar')),
                          detener_cuando=lambda: escena.tarjeta.abiertas == 1)
    assert estado == 'interrupted' and escena.hechas == []
    assert escena.turno('gracias, déjalo') == 'completed'
    recibido = escena.resultados()['instalar'][0]
    assert 'No se ejecutó' in recibido and 'permiso' in recibido and 'incierto' not in recibido
    assert escena.turno('¿qué hora es?') == 'completed'
    # La misma orden vuelve a sacar su tarjeta y, aprobada, se ejecuta.
    escena.tarjeta.contestar(True)
    assert escena.turno('INSTALA las dependencias, ahora sí', tanda(orden('instalar'))) == 'completed'
    assert escena.tarjeta.abiertas == 2 and escena.hechas == ['instalar']
    assert json.loads(escena.resultados()['instalar'][-1])['exit_code'] == 0


def test_detener_en_la_tarjeta_de_reintento_si_deja_la_orden_incierta(escena):
    # Esa tarjeta sale después de que la orden corriera dentro del sandbox: ya tuvo efectos.
    escena.con_reintento.add('compilar')
    escena.turno('compila', tanda(orden('compilar')), detener_cuando=lambda: escena.tarjeta.abiertas == 1)
    assert escena.hechas == ['compilar (en el sandbox)']
    escena.turno('¿cómo quedó?')
    assert 'incierto' in escena.resultados()['compilar'][0]


# ---------------------------------------------------------------- la orden cortada en marcha
def test_una_orden_cortada_en_marcha_solo_frena_su_repeticion_un_turno(escena):
    escena.largas.add('desplegar')
    escena.turno('despliega', tanda(orden('desplegar')), detener_cuando=lambda: escena.en_marcha == 'desplegar')
    assert escena.hechas == []
    # En el turno siguiente el modelo sabe que quedó incierta y no puede repetirla a ciegas…
    estado = escena.turno('inténtalo de nuevo', tanda(orden('desplegar')))
    recibido = escena.resultados()['desplegar']
    assert 'incierto' in recibido[0] and 'No se ejecutó' in recibido[1]
    assert escena.hechas == [] and estado != 'blocked'
    # …pero no queda vetada para siempre ni bloquea los turnos que vienen después.
    assert escena.turno('¿qué hora es?') == 'completed'
    assert escena.turno('ya lo revisé: despliega', tanda(orden('desplegar'))) == 'completed'
    assert escena.hechas == ['desplegar']


# ---------------------------------------------------------------- «Bucle detectado»
def test_editar_y_repetir_las_pruebas_no_es_un_bucle(escena):
    ciclos = [tanda(orden('editar %d' % k), orden('pruebas')) for k in range(5)]
    assert escena.turno('añade cinco módulos, probando tras cada uno', *ciclos) == 'completed'
    assert escena.hechas.count('pruebas') == 5


def test_la_misma_orden_sin_cambios_sigue_siendo_un_bucle_y_la_sesion_sigue(escena):
    estado = escena.turno('prueba', *[tanda(orden('pruebas'))] * 5)
    assert estado == 'failed' and 'Bucle detectado' in escena.m.task['summary']
    assert escena.hechas == ['pruebas'] * 3
    # La que disparó el aviso no corre al seguir, y el turno siguiente no falla antes de empezar.
    assert escena.turno('hola') == 'completed'
    assert escena.hechas == ['pruebas'] * 3
    assert 'No se ejecutó' in escena.resultados()['pruebas'][-1]


def test_el_contador_de_bucles_empieza_de_cero_en_cada_turno(escena):
    for _ in range(5):
        assert escena.turno('pasa las pruebas', tanda(orden('pruebas'))) == 'completed'
    assert escena.hechas == ['pruebas'] * 5
