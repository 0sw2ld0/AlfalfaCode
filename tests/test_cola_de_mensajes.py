"""Mensajes que el usuario escribe con el agente ocupado o entre turnos.

- El mensaje que abre un turno vuelve como evento `user`: la app lo marca entregado.
  Antes se quedaba «en cola» para siempre y parecía que había que cancelar.
- «Enviar ahora» (como Claude Code) corta el turno y el mensaje abre el siguiente,
  sin cerrar la sesión.
- «Redirigir» (steer de Codex) hace que el modelo lea ya el mensaje: la respuesta
  que estaba pensando se descarta, sin cortar el turno.

El proceso es el de producción (`alfalfa --stdio`); sólo se sustituye la inferencia.
"""
import io
import json

from alfalfa.stdio import Transport
from tests.test_compactar_sesion import Cliente

LENTO = '''
import asyncio, json, sys
from unittest.mock import patch
from alfalfa.terminal import main
from alfalfa.proveedores.nativo.runtime import NativeRuntime
async def inference(self, client, endpoint, payload, **kwargs):
    if endpoint == '/api/show':
        return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
    with open('requests.jsonl', 'a') as stream:
        stream.write(json.dumps(payload['messages']) + '\\n')
    last = payload['messages'][-1]['content']
    if 'LENTO' in last:
        await asyncio.sleep(60)
    return dict(message=dict(role='assistant', content='Respuesta a: ' + last[:40]), done=True,
                done_reason='stop', prompt_eval_count=900, eval_count=10)
with patch.object(NativeRuntime, 'request', inference):
    sys.exit(main(['--stdio'] + sys.argv[1:]))
'''


class ClienteLento(Cliente):
    def __init__(self, tmp_path, *argumentos):
        import tests.test_compactar_sesion as base
        original, base.SESION = base.SESION, LENTO
        try:
            super().__init__(tmp_path, *argumentos)
        finally:
            base.SESION = original

    def pensando(self):
        """Espera a que el modelo esté con la petición lenta."""
        import time
        fin = time.time() + 15
        while not self.peticiones():
            assert time.time() < fin, 'el modelo no recibió la petición'
            time.sleep(0.05)

    def usuario(self):
        return self.hasta(lambda f: f['tipo'] == 'evento' and f['rol'] == 'user')


def eventos(cliente, rol):
    return [f['texto'] for f in cliente.filas if f['tipo'] == 'evento' and f['rol'] == rol]


def test_el_mensaje_que_abre_un_turno_vuelve_como_entregado(tmp_path):
    cliente = ClienteLento(tmp_path, 'Primero')
    try:
        assert cliente.tipo('turno_terminado')['estado'] == 'completed'
        assert eventos(cliente, 'user') == []  # el encargo inicial ya lo pinta la app
        cliente.enviar(tipo='mensaje', texto='No, mejor otra cosa')
        assert cliente.usuario()['texto'] == 'No, mejor otra cosa'
        assert cliente.tipo('turno_terminado')['estado'] == 'completed'
        assert eventos(cliente, 'agent')[-1] == 'Respuesta a: No, mejor otra cosa'
        # El historial no la repite: el turno empieza por ese mensaje.
        assert eventos(cliente, 'user') == ['No, mejor otra cosa']
    finally:
        cliente.cerrar()


def test_el_eco_conserva_el_id_del_duplicado_que_se_adelanto(tmp_path):
    cliente = ClienteLento(tmp_path, 'LENTO original')
    try:
        cliente.pensando()
        cliente.enviar(tipo='mensaje', texto='Igual', id='q1')
        cliente.enviar(tipo='mensaje', texto='Igual', id='q2')
        cliente.enviar(tipo='enviar_ahora', texto='Igual', id='q2')
        primero, segundo = cliente.usuario(), cliente.usuario()
        assert primero['texto'] == segundo['texto'] == 'Igual'
        assert [primero['id_cola'], segundo['id_cola']] == ['q2', 'q1']
    finally:
        cliente.cerrar()


def test_enviar_ahora_corta_el_turno_y_sigue_la_sesion(tmp_path):
    cliente = ClienteLento(tmp_path, 'Tarea LENTO')
    try:
        cliente.pensando()
        cliente.enviar(tipo='mensaje', texto='Para y haz esto')
        cliente.enviar(tipo='enviar_ahora', texto='Para y haz esto')
        cortado = cliente.tipo('turno_terminado')
        assert cortado['estado'] == 'cortado' and cortado['resumen'] == 'Turno interrumpido por el usuario.'
        assert cliente.usuario()['texto'] == 'Para y haz esto'
        siguiente = cliente.tipo('turno_terminado')
        assert siguiente['estado'] == 'completed'
        assert eventos(cliente, 'agent')[-1] == 'Respuesta a: Para y haz esto'
        assert not [f for f in cliente.filas if f['tipo'] == 'fin']  # la sesión sigue abierta
    finally:
        cliente.cerrar()


def test_redirigir_hace_que_el_modelo_lea_ya_el_mensaje(tmp_path):
    cliente = ClienteLento(tmp_path, 'Tarea LENTO')
    try:
        cliente.pensando()
        cliente.enviar(tipo='mensaje', texto='Céntrate en la prueba')
        cliente.enviar(tipo='redirigir')
        assert cliente.usuario()['texto'] == 'Céntrate en la prueba'
        fin = cliente.tipo('turno_terminado')
        assert fin['estado'] == 'completed'
        assert any(t.startswith('Redirigido') for t in eventos(cliente, 'system'))
        # Un solo turno: la segunda petición ya lleva el mensaje dentro.
        assert len([f for f in cliente.filas if f['tipo'] == 'turno_terminado']) == 1
        ultima = cliente.peticiones()[-1]
        assert ultima[-1] == {'role': 'user', 'content': 'Céntrate en la prueba'}
        assert eventos(cliente, 'agent')[-1] == 'Respuesta a: Céntrate en la prueba'
    finally:
        cliente.cerrar()


def test_enviar_ahora_sin_turno_es_un_mensaje_normal(tmp_path):
    cliente = ClienteLento(tmp_path, 'Hola')
    try:
        cliente.tipo('turno_terminado')
        cliente.enviar(tipo='enviar_ahora', texto='Otra')
        cliente.enviar(tipo='mensaje', texto='Otra')
        assert cliente.usuario()['texto'] == 'Otra'
        assert cliente.tipo('turno_terminado')['estado'] == 'completed'
    finally:
        cliente.cerrar()
    assert json.dumps(cliente.filas).count('"cortado"') == 0


# «Quitar» y «Editar» de un mensaje en cola: la app le da un id y pide sacarlo antes de que
# entre en el contexto del modelo.

def _transporte():
    salida = io.StringIO()
    return Transport(salida), salida


def _filas(salida):
    return [json.loads(linea) for linea in salida.getvalue().splitlines()]


def test_quitar_de_cola_saca_el_mensaje_con_su_id():
    t, salida = _transporte()
    t.receive(json.dumps({'tipo': 'mensaje', 'texto': 'uno', 'id': 'a'}))
    t.receive(json.dumps({'tipo': 'mensaje', 'texto': 'uno', 'id': 'b'}))
    t.receive(json.dumps({'tipo': 'mensaje', 'texto': 'sin id'}))
    t.receive(json.dumps({'tipo': 'quitar_de_cola', 'id': 'b'}))
    # Sigue siendo su texto para el resto del motor; con el mismo texto, sale el del id pedido.
    assert t.inbox == ['uno', 'sin id'] and t.inbox[0].id == 'a'
    assert _filas(salida) == [{'version': 2, 'tipo': 'quitado_de_cola', 'id': 'b', 'quitado': True}]


def test_quitar_de_cola_un_mensaje_que_ya_entro_o_que_no_existe():
    t, salida = _transporte()
    t.receive(json.dumps({'tipo': 'mensaje', 'texto': 'ya dentro', 'id': 'a'}))
    assert t.take_messages() == ['ya dentro']
    t.receive(json.dumps({'tipo': 'quitar_de_cola', 'id': 'a'}))
    t.receive(json.dumps({'tipo': 'quitar_de_cola', 'id': 'nunca'}))
    filas = _filas(salida)
    assert [(f['quitado'], f['motivo']) for f in filas] == [(False, 'entregado'), (False, 'desconocido')]


def test_quitar_el_ultimo_en_cola_anula_el_redirigir_pendiente():
    t, _ = _transporte()
    t.in_turn = True
    t.receive(json.dumps({'tipo': 'mensaje', 'texto': 'lee esto', 'id': 'a'}))
    t.receive(json.dumps({'tipo': 'redirigir'}))
    assert t.redireccion.is_set()
    t.receive(json.dumps({'tipo': 'quitar_de_cola', 'id': 'a'}))
    assert not t.redireccion.is_set()


def test_ids_invalidos_y_mensajes_sin_id():
    t, salida = _transporte()
    for ident in (7, '', 'x' * 101):
        t.receive(json.dumps({'tipo': 'mensaje', 'texto': 'hola', 'id': ident}))
    assert t.inbox == []
    t.receive(json.dumps({'tipo': 'mensaje', 'texto': 'hola'}))
    assert t.inbox == ['hola'] and not hasattr(t.inbox[0], 'id')
    for ident in (None, 7, ''):
        t.receive(json.dumps({'tipo': 'quitar_de_cola', 'id': ident}))
    filas = _filas(salida)
    assert [f['tipo'] for f in filas] == ['error'] * 6
    assert t.inbox == ['hola']


def test_quitar_de_cola_antes_de_entrar_y_despues(tmp_path):
    cliente = ClienteLento(tmp_path, 'Tarea LENTO')
    try:
        cliente.pensando()
        cliente.enviar(tipo='mensaje', texto='Mejor no', id='q1')
        cliente.enviar(tipo='mensaje', texto='Céntrate en la prueba', id='q2')
        cliente.enviar(tipo='quitar_de_cola', id='q1')
        assert cliente.tipo('quitado_de_cola') == {'version': 2, 'tipo': 'quitado_de_cola', 'id': 'q1',
                                                   'quitado': True}
        cliente.enviar(tipo='redirigir')
        assert cliente.usuario()['texto'] == 'Céntrate en la prueba'
        assert cliente.tipo('turno_terminado')['estado'] == 'completed'
        # Ya lo leyó el modelo: no se quita y la respuesta lo dice.
        cliente.enviar(tipo='quitar_de_cola', id='q2')
        respuesta = cliente.tipo('quitado_de_cola')
        assert respuesta['quitado'] is False and respuesta['motivo'] == 'entregado'
        # El quitado nunca llegó al modelo.
        assert 'Mejor no' not in json.dumps(cliente.peticiones())
        assert eventos(cliente, 'user') == ['Céntrate en la prueba']
    finally:
        cliente.cerrar()


# «Enviar ahora» con el id del mensaje en cola: va justo ese, aunque otro diga lo mismo, y si ya
# entró no corta el turno ni se repite.

def test_enviar_ahora_con_id_adelanta_justo_ese_mensaje():
    t, salida = _transporte()
    for ident, texto in (('a', 'igual'), ('b', 'otro'), ('c', 'igual')):
        t.receive(json.dumps({'tipo': 'mensaje', 'texto': texto, 'id': ident}))
    t.receive(json.dumps({'tipo': 'enviar_ahora', 'texto': 'igual', 'id': 'c'}))
    assert [m.id for m in t.inbox] == ['c', 'a', 'b']
    # Un id que ya no está en la cola no mueve nada; uno inválido es un error.
    t.receive(json.dumps({'tipo': 'enviar_ahora', 'texto': 'otro', 'id': 'ya-entro'}))
    assert [m.id for m in t.inbox] == ['c', 'a', 'b']
    t.receive(json.dumps({'tipo': 'enviar_ahora', 'texto': 'otro', 'id': 7}))
    assert [f['tipo'] for f in _filas(salida)] == ['error']


def test_enviar_ahora_con_id_corta_el_turno_y_abre_el_siguiente_con_ese(tmp_path):
    cliente = ClienteLento(tmp_path, 'Tarea LENTO')
    try:
        cliente.pensando()
        cliente.enviar(tipo='mensaje', texto='Primero en cola', id='q1')
        cliente.enviar(tipo='mensaje', texto='Para y haz esto', id='q2')
        cliente.enviar(tipo='enviar_ahora', texto='Para y haz esto', id='q2')
        cortado = cliente.tipo('turno_terminado')
        assert cortado['estado'] == 'cortado' and cortado['resumen'] == 'Turno interrumpido por el usuario.'
        # El siguiente turno lo abre q2, no el primero de la cola.
        assert cliente.usuario()['texto'] == 'Para y haz esto'
        assert cliente.tipo('turno_terminado')['estado'] == 'completed'
        assert not [f for f in cliente.filas if f['tipo'] == 'fin']
        # Cada mensaje entra una sola vez; q1 sigue su camino detrás.
        assert eventos(cliente, 'user') == ['Para y haz esto', 'Primero en cola']
        enviados = [m['content'] for m in cliente.peticiones()[-1] if m['role'] == 'user']
        assert enviados.count('Para y haz esto') == 1 and enviados.count('Primero en cola') == 1
        # Ya entró: otro «Enviar ahora» de q2 no abre ningún turno más.
        cliente.enviar(tipo='enviar_ahora', texto='Para y haz esto', id='q2')
        cliente.enviar(tipo='quitar_de_cola', id='q2')
        assert cliente.tipo('quitado_de_cola')['motivo'] == 'entregado'
        assert len([f for f in cliente.filas if f['tipo'] == 'turno_terminado']) == 2
    finally:
        cliente.cerrar()
    assert eventos(cliente, 'user') == ['Para y haz esto', 'Primero en cola']


def test_enviar_ahora_de_un_mensaje_que_ya_entro_no_corta_el_turno(tmp_path):
    cliente = ClienteLento(tmp_path, 'Tarea LENTO')
    try:
        cliente.pensando()
        cliente.enviar(tipo='mensaje', texto='Céntrate LENTO', id='q1')
        cliente.enviar(tipo='redirigir')
        assert cliente.usuario()['texto'] == 'Céntrate LENTO'
        # Otro mensaje espera en cola: antes, «Enviar ahora» de q1 cortaba el turno por éste.
        cliente.enviar(tipo='mensaje', texto='Y luego esto', id='q2')
        cliente.enviar(tipo='enviar_ahora', texto='Céntrate LENTO', id='q1')
        # quitar_de_cola responde en orden: el «Enviar ahora» ya se atendió.
        cliente.enviar(tipo='quitar_de_cola', id='q1')
        assert cliente.tipo('quitado_de_cola')['motivo'] == 'entregado'
        assert not [f for f in cliente.filas if f['tipo'] == 'turno_terminado']
        # q2 sigue en cola, sin enviar.
        cliente.enviar(tipo='quitar_de_cola', id='q2')
        assert cliente.tipo('quitado_de_cola')['quitado'] is True
        cliente.enviar(tipo='interrumpir')
        assert cliente.tipo('turno_terminado')['estado'] == 'cortado'
    finally:
        cliente.cerrar()
    assert eventos(cliente, 'user') == ['Céntrate LENTO']
    assert len([f for f in cliente.filas if f['tipo'] == 'turno_terminado']) == 1
