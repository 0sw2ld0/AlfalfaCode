"""Observadores de una sesión `--pacai` en marcha (alfalfa/pacai/observadores.py): el socket de la sesión, la foto
inicial (`snapshot`), la copia en vivo de lo que ve el dueño, la decisión del dueño reenviada (`relayed`), que el
observador sólo mire y que nunca frene al motor. Cada transcripción del observador pasa el esquema y las reglas de
sesión de PACAI (con las del observador, propuesta §6.1)."""
import asyncio
import json
import os
import queue
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from alfalfa.pacai import observadores
from alfalfa.pacai.observadores import ultima_hora
from alfalfa.pacai.verificador import Session
from tests.pacai_comun import Cliente, abrir, cerrar, fin_de_herramienta, herramienta, problemas, reposo
from tests.test_pacai_sesion import MODELO, Proceso

OBSERVADOR = 's_observador_0123456789abcdef'


@pytest.fixture
def carpeta():
    """La carpeta de una sesión en un temporal corto: macOS sólo admite 103 bytes en la ruta de un socket."""
    raiz = Path(tempfile.mkdtemp(prefix='alf', dir='/tmp'))
    sesion = raiz / 'sesiones' / '0123456789abcdef'
    sesion.mkdir(parents=True, mode=0o700)
    yield sesion
    shutil.rmtree(raiz, ignore_errors=True)


def gestor(carpeta):
    return SimpleNamespace(directory=carpeta, task={'id': carpeta.name, 'cwd': str(carpeta)})


class Mirando:
    """Un observador por el socket: manda su `hello` y lee lo que le llega. `log` es su transcripción entera."""

    def __init__(self, reader, writer):
        self.reader, self.writer, self.log, self.seq = reader, writer, [], 0

    @classmethod
    async def conectar(cls, ruta, saludar=True, **hello):
        reader, writer = await asyncio.open_unix_connection(str(ruta))
        o = cls(reader, writer)
        if saludar:
            o.enviar('hello', dict(dict(versions=['0.3'], profiles=['work'], extensions=['alfalfa'], observe=True),
                                   **hello))
        return o

    def enviar(self, tipo, payload=None, **sobre):
        self.seq += 1
        m = dict(pacai='0.3', id='o_%08d' % self.seq, type=tipo, session=OBSERVADOR, sender='client', seq=self.seq,
                 time='2026-11-17T10:00:00Z', payload=payload or {}, **sobre)
        self.log.append(m)
        self.writer.write((json.dumps(m) + '\n').encode())
        return m

    async def leer(self, n=1, segundos=5):
        mensajes = []
        for _ in range(n):
            linea = await asyncio.wait_for(self.reader.readline(), segundos)
            assert linea, 'el agente cerró la conexión: ' + str([m['type'] for m in self.log])
            mensajes.append(json.loads(linea))
            self.log.append(mensajes[-1])
        return mensajes

    async def hasta(self, cumple, segundos=5):
        while True:
            m = (await self.leer(segundos=segundos))[0]
            if cumple(m):
                return m

    async def eof(self, segundos=5):
        """Lee hasta que el agente cierra; devuelve lo que llegó."""
        resto = []
        while linea := await asyncio.wait_for(self.reader.readline(), segundos):
            resto.append(json.loads(linea))
            self.log.append(resto[-1])
        return resto

    def del_agente(self, tipo=None):
        return [m for m in self.log if m['sender'] == 'agent' and (tipo is None or m['type'] == tipo)]


async def pedir_permiso(c, argv=('touch', 'x')):
    herramienta(c, 'run_command', {'argv': list(argv), 'purpose': 'p'})
    tarea = asyncio.create_task(c.t.approval('.', False)(dict(argv=list(argv), purpose='p')))
    await reposo()
    return tarea, c.del_agente('approval_request')[-1]


# -- el socket -------------------------------------------------------------
async def test_el_socket_nace_en_0600_dentro_de_la_sesion_y_se_borra_al_salir(carpeta):
    c = Cliente()
    c.t.conectar(gestor(carpeta))
    ruta = carpeta / 'vivo.sock'
    info = os.lstat(ruta)
    assert stat.S_ISSOCK(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
    assert stat.S_IMODE(os.stat(carpeta).st_mode) == 0o700
    await c.t.al_salir()
    assert not ruta.exists() and c.t.mirador is None


async def test_un_socket_viejo_se_sustituye_pero_otra_cosa_con_ese_nombre_no_se_toca(carpeta):
    viejo = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    viejo.bind(str(carpeta / 'vivo.sock'))          # de un proceso que murió sin borrarlo (kill -9)
    viejo.close()
    c = Cliente()
    c.t.conectar(gestor(carpeta))
    assert c.t.mirador is not None
    o = await Mirando.conectar(carpeta / 'vivo.sock')
    assert [m['type'] for m in await o.leer(3)] == ['hello', 'session_started', 'snapshot']
    await c.t.al_salir()

    otra = Path(tempfile.mkdtemp(prefix='alf', dir='/tmp'))
    try:
        (otra / 'vivo.sock').write_text('no es un socket')
        d = Cliente()
        d.t.conectar(SimpleNamespace(directory=otra, task={'id': 'x'}))
        assert d.t.mirador is None and (otra / 'vivo.sock').read_text() == 'no es un socket'
    finally:
        shutil.rmtree(otra, ignore_errors=True)


async def test_con_una_ruta_demasiado_larga_no_hay_socket_y_la_sesion_sigue(carpeta):
    larga = carpeta / ('x' * 120)
    larga.mkdir()
    c = Cliente()
    c.t.conectar(SimpleNamespace(directory=larga, task={'id': 'y'}))
    assert c.t.mirador is None and not (larga / 'vivo.sock').exists()
    await abrir(c)
    await cerrar(c)
    c.comprobar()


def test_sin_bucle_en_marcha_no_hay_socket(carpeta):
    c = Cliente()
    c.t.conectar(gestor(carpeta))
    assert c.t.mirador is None and not (carpeta / 'vivo.sock').exists()


# -- la foto y la copia ----------------------------------------------------
async def test_el_observador_ve_la_foto_y_despues_lo_mismo_que_el_dueno(carpeta):
    c = Cliente()
    c.t.conectar(gestor(carpeta))
    c.t.emit('sesion', id=carpeta.name, cwd='/x', modelo='m', esfuerzo='medium', modo='auto')
    run = await abrir(c)
    c.t.emit('contexto', tokens=1000, limite=200000)
    c.t.emit('uso_sesion', tokens=1500, coste=0.25)
    c.t.emit('plan', pasos=[{'paso': 'Leer', 'estado': 'in_progress'}])
    tarea, peticion = await pedir_permiso(c)
    antes = len(c.del_agente())

    o = await Mirando.conectar(carpeta / 'vivo.sock')
    hola, empezada, foto = await o.leer(3)
    assert hola['type'] == 'hello' and hola['session'] == OBSERVADOR and hola['seq'] == 1
    assert empezada['type'] == 'session_started' and empezada['reply_to'] == o.log[0]['id']
    assert empezada['payload']['observe'] is True and empezada['payload']['extensions'] == ['alfalfa']
    assert foto['type'] == 'snapshot' and foto['reply_to'] == o.log[0]['id'] and foto['seq'] == 3
    p = foto['payload']
    assert p['runs'] == [{'run_id': run, 'status': 'active'}]
    assert [a['id'] for a in p['pending_approvals']] == [peticion['id']]
    assert p['pending_approvals'][0]['session'] == OBSERVADOR
    tipos = [m['type'] for m in p['last_messages']]
    assert tipos[0] == 'alfalfa.session' and 'run_started' in tipos and 'tool_start' in tipos
    for esperado in ('alfalfa.context', 'usage', 'plan_updated'):
        assert esperado in tipos
    ultimos = {m['type']: m for m in p['last_messages']}
    assert ultimos['run_started']['id'] == run
    assert ultimos['tool_start']['id'] == peticion['payload']['tool_call_id']
    assert p['alfalfa']['session_id'] == carpeta.name and p['alfalfa']['history_until'] == 0.0

    # Desde aquí, copia: el dueño aprueba, la herramienta termina, el agente escribe y el turno se cierra.
    c.enviar('approval_result', {'decision': 'approved', 'scope': 'once'}, reply_to=peticion['id'])
    reenviada = await o.hasta(lambda m: m['type'] == 'approval_result')
    assert reenviada['reply_to'] == peticion['id']
    assert reenviada['payload'] == {'decision': 'approved', 'relayed': True, 'scope': 'once'}
    await asyncio.wait_for(tarea, 1)
    fin_de_herramienta(c, 'run_command', {'codigo': 0, 'salida': 'ok'})
    c.t.emit('evento', rol='agent', texto='Listo', hora=3.0)
    await cerrar(c)
    c.t.emit('fin', estado='completed', resumen='Hecho', codigo_salida=0)
    c.drenar()
    await c.t.al_salir()
    await o.eof()

    del_dueno = c.del_agente()[antes:]
    copias = [m for m in o.del_agente()[3:] if not m['payload'].get('relayed')]
    assert [m['id'] for m in copias] == [m['id'] for m in del_dueno]
    assert all(a['payload'] == b['payload'] and a['type'] == b['type'] for a, b in zip(copias, del_dueno))
    assert [m['seq'] for m in o.del_agente()] == list(range(1, len(o.del_agente()) + 1))
    assert all(m['session'] == OBSERVADOR for m in o.del_agente())
    assert o.del_agente()[-1]['type'] == 'close'
    assert problemas(o.log) == [], problemas(o.log)
    c.comprobar()


async def test_un_observador_que_llega_entre_turnos_ve_lo_que_viene_y_no_el_saludo_del_dueno(carpeta):
    c = Cliente()
    c.t.conectar(gestor(carpeta))
    o = await Mirando.conectar(carpeta / 'vivo.sock')     # antes incluso del hello del dueño
    _, _, foto = await o.leer(3)
    assert foto['payload']['runs'] == [] and foto['payload']['pending_approvals'] == []
    run = await abrir(c)
    c.t.emit('evento', rol='agent', texto='Hola', hora=1.0)
    await cerrar(c)
    llegados = [m['type'] for m in o.del_agente()[3:]]
    while 'run_finished' not in llegados:
        llegados.append((await o.leer())[0]['type'])
    assert 'hello' not in llegados and 'session_started' not in llegados
    assert llegados[0] == 'run_started' and o.del_agente('run_started')[0]['id'] == run
    for esperado in ('message_start', 'message_delta', 'message_end', 'alfalfa.closing'):
        assert esperado in llegados
    await c.t.al_salir()
    assert problemas(o.log) == [], problemas(o.log)


async def test_lo_que_negocio_el_observador_filtra_lo_que_ve(carpeta):
    c = Cliente()
    c.t.conectar(gestor(carpeta))
    await abrir(c)
    c.t.emit('plan', pasos=[{'paso': 'Leer', 'estado': 'pending'}])
    o = await Mirando.conectar(carpeta / 'vivo.sock', profiles=[], extensions=[])
    _, empezada, foto = await o.leer(3)
    assert empezada['payload']['profiles'] == [] and empezada['payload']['extensions'] == []
    assert 'alfalfa' not in foto['payload']
    assert all(m['type'] not in ('plan_updated',) and not m['type'].startswith('alfalfa.')
               for m in foto['payload']['last_messages'])
    c.t.emit('presupuesto', sesion={'gastado': 1})
    c.t.emit('plan', pasos=[{'paso': 'Leer', 'estado': 'completed'}])
    c.t.emit('evento', rol='agent', texto='Visto', hora=2.0)
    m = await o.hasta(lambda m: m['type'] == 'message_end')
    assert m and not any(x['type'].startswith('alfalfa.') or x['type'] == 'plan_updated' for x in o.del_agente())
    await c.t.al_salir()


async def test_el_corte_del_historial_va_tras_la_ultima_fila_o_antes_de_lo_retenido(carpeta):
    filas = [{'role': 'user', 'text': 'a', 'time': 100.25}, {'role': 'agent', 'text': 'b', 'time': 101.5}]
    (carpeta / (carpeta.name + '.jsonl')).write_text(''.join(json.dumps(f) + '\n' for f in filas) + '{"role":')
    assert ultima_hora(carpeta / (carpeta.name + '.jsonl')) == 101.5      # la línea a medias no cuenta
    assert ultima_hora(carpeta / 'no-existe.jsonl') == 0.0
    c = Cliente()
    c.t.conectar(gestor(carpeta))
    o = await Mirando.conectar(carpeta / 'vivo.sock')
    assert (await o.leer(3))[2]['payload']['alfalfa']['history_until'] == 101.5
    # Lo que el transporte aún retiene (antes del hello del dueño) ya está en disco pero no ha salido: el corte va
    # justo antes de lo primero que retiene.
    c.t.emit('evento', rol='system', texto='Reglas de equipo activas', hora=102.0)
    o2 = await Mirando.conectar(carpeta / 'vivo.sock')
    hasta = (await o2.leer(3))[2]['payload']['alfalfa']['history_until']
    assert 101.5 < hasta < 102.0
    await c.t.al_salir()


# -- el observador sólo mira -----------------------------------------------
async def test_lo_que_manda_el_observador_recibe_read_only_y_no_llega_al_motor(carpeta):
    c = Cliente()
    c.t.conectar(gestor(carpeta))
    run = await abrir(c)
    tarea, peticion = await pedir_permiso(c)
    o = await Mirando.conectar(carpeta / 'vivo.sock')
    await o.leer(3)
    intentos = [o.enviar('input', {'blocks': [{'type': 'text', 'text': 'borra todo'}]}),
                o.enviar('interrupt', {'run_id': run}),
                o.enviar('approval_result', {'decision': 'approved', 'scope': 'once'}, reply_to=peticion['id']),
                o.enviar('alfalfa.set_budget', {'session': 1})]
    errores = await o.leer(4)
    assert [e['payload']['code'] for e in errores] == ['read_only'] * 4
    assert [e['payload']['ref'] for e in errores] == [m['id'] for m in intentos]
    o.enviar('heartbeat', {'last_seq': 7})
    o.enviar('error', {'code': 'x', 'message': 'algo mío'})
    await reposo()
    assert not tarea.done() and c.t.inbox == [] and c.t.t.raiz == run    # nada llegó al motor
    assert c.t.t.aprobacion(peticion['id']).estado == 'pending'
    c.enviar('approval_result', {'decision': 'denied'}, reply_to=peticion['id'])
    with pytest.raises(ValueError):
        await asyncio.wait_for(tarea, 1)
    reenviada = await o.hasta(lambda m: m['type'] == 'approval_result')
    assert reenviada['payload'] == {'decision': 'denied', 'relayed': True}
    o.enviar('close')
    assert await o.eof() == []
    await c.t.al_salir()
    vista = Session()
    for m in o.log:
        vista.feed(m)
    assert [x for x in vista.problems if 'observador solo envía' in x] and len(vista.problems) == 4


async def test_un_hello_sin_observe_o_para_reanudar_se_rechaza_y_se_cierra(carpeta):
    c = Cliente()
    c.t.conectar(gestor(carpeta))
    o = await Mirando.conectar(carpeta / 'vivo.sock', observe=False)
    respuesta = await o.eof()
    assert [m['type'] for m in respuesta] == ['error'] and respuesta[0]['payload']['code'] == 'invalid_message'
    o = await Mirando.conectar(carpeta / 'vivo.sock', resume_after_seq=3)
    assert [m['payload']['code'] for m in await o.eof()] == ['retention_expired']
    o = await Mirando.conectar(carpeta / 'vivo.sock', versions=['0.2'])
    assert [m['payload']['code'] for m in await o.eof()] == ['version_incompatible']
    o = await Mirando.conectar(carpeta / 'vivo.sock', saludar=False)
    o.writer.write(b'esto no es json\n')
    o.enviar('input', {'blocks': [{'type': 'text', 'text': 'x'}]})
    assert [m['payload']['code'] for m in await o.leer(2)] == ['invalid_message', 'invalid_message']
    await c.t.al_salir()
    assert c.t.inbox == []


def test_por_stdio_habla_el_dueno_y_un_hello_de_observador_se_rechaza():
    c = Cliente()
    salida = c.saludar(observe=True)
    assert [m['type'] for m in salida] == ['error'] and salida[0]['payload']['code'] == 'invalid_message'
    assert 'vivo.sock' in salida[0]['payload']['message'] and c.t.stopped and not c.t.empezado


async def test_como_mucho_ocho_observadores(carpeta, monkeypatch):
    monkeypatch.setattr(observadores, 'MAX_OBSERVADORES', 2)
    c = Cliente()
    c.t.conectar(gestor(carpeta))
    dentro = [await Mirando.conectar(carpeta / 'vivo.sock') for _ in range(2)]
    for o in dentro:
        await o.leer(3)
    tercero = await Mirando.conectar(carpeta / 'vivo.sock')
    assert [m['payload']['code'] for m in await tercero.eof()] == ['alfalfa.too_many_observers']
    await c.t.al_salir()


async def test_un_observador_que_no_lee_se_corta_sin_frenar_al_dueno(carpeta, monkeypatch):
    monkeypatch.setattr(observadores, 'MAX_PENDIENTE', 64 * 1024)
    c = Cliente()
    c.t.conectar(gestor(carpeta))
    await abrir(c)
    lento = await Mirando.conectar(carpeta / 'vivo.sock')
    rapido = await Mirando.conectar(carpeta / 'vivo.sock')
    await reposo()
    assert len(c.t.mirador.observadores) == 2
    await rapido.leer(3)
    recibidos = []

    async def leer_todo():                        # el otro sí lee, todo lo que le llega
        while linea := await rapido.reader.readline():
            recibidos.append(json.loads(linea))
    lector = asyncio.create_task(leer_todo())
    for n in range(400):                          # 1,2 MB que el lento no lee
        c.t.emit('evento', rol='agent', texto=str(n) + ' ' + 'x' * 3000, hora=float(n))
        await asyncio.sleep(0.001)                # al ritmo de un modelo (muy rápido)
    for _ in range(200):
        if len(recibidos) >= 1200:
            break
        await asyncio.sleep(0.01)
    assert len(recibidos) == 1200 and recibidos[-1]['type'] == 'message_end'
    assert len(c.drenar()) > 0 and len(c.t.mirador.observadores) == 1
    assert await lento.eof(segundos=5) is not None      # al lento se le cortó la conexión
    await c.t.al_salir()
    await asyncio.wait_for(lector, 5)


# -- el verificador con observadores ---------------------------------------
def test_relayed_fuera_de_la_sesion_de_un_observador_es_un_fallo():
    c = Cliente()
    c.saludar()
    c.enviar('input', {'blocks': [{'type': 'text', 'text': 'x'}]})
    log = list(c.log) + [dict(pacai='0.3', id='m_falso_000001', type='approval_result', session=c.log[0]['session'],
                              sender='agent', seq=3, time='2026-11-17T10:00:00Z', reply_to=c.log[1]['id'],
                              payload={'decision': 'approved', 'scope': 'once', 'relayed': True})]
    assert any('relayed' in x for x in problemas(log))


# -- con el motor de verdad ------------------------------------------------


class Viendo:
    """Un observador síncrono contra el socket de un `alfalfa --pacai` de verdad."""

    def __init__(self, ruta):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(ruta))
        self.archivo = self.sock.makefile('rwb')
        self.log, self.cola = [], queue.Queue()
        m = dict(pacai='0.3', id='o_00000001', type='hello', session=OBSERVADOR, sender='client', seq=1,
                 time='2026-11-17T10:00:00Z',
                 payload=dict(versions=['0.3'], profiles=['work'], extensions=['alfalfa'], observe=True))
        self.log.append(m)
        self.archivo.write((json.dumps(m) + '\n').encode())
        self.archivo.flush()
        threading.Thread(target=self._leer, daemon=True).start()

    def _leer(self):
        for linea in self.archivo:
            self.cola.put(json.loads(linea))
        self.cola.put(None)

    def hasta(self, cumple, segundos=30):
        while True:
            m = self.cola.get(timeout=segundos)
            assert m is not None, 'el agente cerró el socket: ' + str([x['type'] for x in self.log])
            self.log.append(m)
            if cumple(m):
                return m

    def resto(self, segundos=30):
        while (m := self.cola.get(timeout=segundos)) is not None:
            self.log.append(m)


def test_con_el_motor_de_verdad_el_observador_ve_el_encargo_en_vivo():
    hogar = Path(tempfile.mkdtemp(prefix='alf', dir='/tmp'))
    p = Proceso.__new__(Proceso)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), ALFALFA_HOME=str(hogar))
    p.proc = subprocess.Popen([sys.executable, '-c', MODELO, 'tool'], cwd=hogar, env=env, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    p.log, p.seq, p.cola = [], 0, queue.Queue()
    threading.Thread(target=p._leer, daemon=True).start()
    try:
        p.saludar()
        sesion = p.tipo('alfalfa.session')['payload']['id']
        ruta = hogar / 'sesiones' / sesion / 'vivo.sock'
        assert stat.S_IMODE(os.lstat(ruta).st_mode) == 0o600
        p.entrada('Prueba una orden')
        run = p.tipo('run_started')
        peticion = p.tipo('approval_request')

        o = Viendo(ruta)
        foto = o.hasta(lambda m: m['type'] == 'snapshot')
        assert foto['payload']['runs'] == [{'run_id': run['id'], 'status': 'active'}]
        assert [a['id'] for a in foto['payload']['pending_approvals']] == [peticion['id']]
        assert foto['payload']['alfalfa']['session_id'] == sesion
        assert foto['payload']['alfalfa']['history_until'] > 0          # el encargo ya está en la transcripción

        p.enviar('approval_result', {'decision': 'approved', 'scope': 'once'}, reply_to=peticion['id'])
        assert o.hasta(lambda m: m['type'] == 'approval_result')['payload']['relayed'] is True
        fin = o.hasta(lambda m: m['type'] == 'run_finished')
        assert fin['payload'] == {'run_id': run['id'], 'outcome': 'success'}
        assert p.tipo('run_finished')['id'] == fin['id']
        p.terminar()
        o.resto()
        assert o.log[-1]['type'] == 'close' and p.proc.returncode == 0
        assert not ruta.exists()
        texto = ''.join(b['text'] for m in o.log if m['type'] == 'message_delta' for b in m['payload']['blocks'])
        assert 'Hecho:' in texto
        assert problemas(o.log) == [], problemas(o.log)
        assert problemas(p.log) == [], problemas(p.log)
    finally:
        p.kill()
        shutil.rmtree(hogar, ignore_errors=True)
