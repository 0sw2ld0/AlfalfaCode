"""El transporte PACAI en el mismo proceso: saludo, validación de lo que llega, cola de entradas, aprobaciones y
herramientas del cliente. Cada transcripción se comprueba con el esquema y las reglas de sesión de PACAI."""
import asyncio
import json
from unittest.mock import Mock

import pytest

from alfalfa.pacai.transporte import COMANDOS
from alfalfa.stdio import Transport
from tests.pacai_comun import (
    SESION,
    Cliente,
    RelojFalso,
    abrir,
    cerrar,
    fin_de_herramienta,
    herramienta,
    problemas,
    reposo,
)


def tipos(mensajes):
    return [m['type'] for m in mensajes]


def codigo(mensajes):
    errores = [m for m in mensajes if m['type'] == 'error']
    assert len(errores) == 1, mensajes
    return errores[0]['payload']['code']


# -- saludo y negociación (§4.1) -------------------------------------------
def test_el_saludo_negocia_y_suelta_lo_que_el_motor_dijo_mientras_tanto():
    c = Cliente(perfiles=('work', 'voice'), extensiones=('alfalfa', 'otra'))
    c.t.emit('sesion', id='abc', cwd='/x')
    c.t.emit('presupuesto', sesion=5)
    assert c.drenar() == []                                # nada sale antes del saludo
    salida = c.saludar()
    assert tipos(salida) == ['hello', 'session_started', 'alfalfa.session', 'alfalfa.budget']
    hola, empezada = salida[0], salida[1]
    assert hola['sender'] == 'agent' and hola['session'] == SESION and hola['seq'] == 1
    assert hola['payload']['versions'] == ['0.3'] and hola['payload']['profiles'] == ['work']
    assert hola['payload']['extensions'] == ['alfalfa'] and hola['payload']['implementation']['name'] == 'alfalfa'
    assert empezada['reply_to'] == c.ultimo['id'] and empezada['session'] == SESION
    assert empezada['payload']['version'] == '0.3'
    assert empezada['payload']['profiles'] == ['work'] and empezada['payload']['extensions'] == ['alfalfa']
    assert empezada['payload']['agent']['name'] == 'alfalfa'
    c.comprobar()


def test_sin_perfiles_ni_extensiones_la_interseccion_es_vacia():
    c = Cliente(perfiles=(), extensiones=())
    salida = c.saludar()
    assert salida[1]['payload']['profiles'] == [] and salida[1]['payload']['extensions'] == []
    c.comprobar()


def test_sin_version_comun_el_agente_responde_version_incompatible_y_cierra():
    c = Cliente()
    assert codigo(c.saludar(versions=['9.9', '0.1'])) == 'version_incompatible'
    assert c.t.stopped and not c.t.empezado


def test_un_cliente_de_la_0_2_recibe_version_incompatible_y_lo_encadenado_se_descarta():
    c = Cliente()
    hola = dict(c.mensaje('hello', dict(c.hello, versions=['0.2'])), pacai='0.2')
    assert codigo(c.crudo(hola)) == 'version_incompatible'
    assert c.t.stopped and not c.t.empezado
    # Lo que el cliente encadenó tras su `hello` no recibe respuesta: no hay sesión (§4.1).
    assert c.crudo(dict(c.mensaje('input', {'blocks': [{'type': 'text', 'text': 'hola'}]}), pacai='0.2')) == []
    assert c.enviar('input', {'blocks': [{'type': 'text', 'text': 'hola'}]}) == []


def test_reanudar_no_esta_soportado_y_se_dice():
    c = Cliente()
    assert codigo(c.saludar(resume_after_seq=3)) == 'retention_expired'
    assert c.t.stopped


def test_una_sesion_se_negocia_una_sola_vez():
    c = Cliente()
    c.saludar()
    assert codigo(c.saludar()) == 'invalid_message'
    assert not c.t.stopped


def test_antes_del_saludo_solo_se_admiten_hello_error_close_y_heartbeat():
    c = Cliente()
    error = c.entrada()
    assert codigo(error) == 'invalid_message' and 'antes del hello' in error[0]['payload']['message']
    assert c.enviar('heartbeat', {'last_seq': 0}) == []
    assert c.enviar('error', {'code': 'x', 'message': 'y'}) == []


async def test_lo_encadenado_tras_el_hello_se_procesa_con_las_reglas_de_la_sesion_negociada():
    c = Cliente(extensiones=())
    hola = c.mensaje('hello', dict(c.hello))
    entrada = c.mensaje('input', {'blocks': [{'type': 'text', 'text': 'sin esperar'}]})
    ajena = c.mensaje('alfalfa.compact', {})
    for m in (hola, entrada, ajena):                     # sin esperar a session_started
        c.log.append(m)
        c.t.receive(json.dumps(m))
    respuesta = c.drenar()
    assert tipos(respuesta) == ['hello', 'session_started', 'error']
    assert respuesta[2]['payload']['code'] == 'not_negotiated' and respuesta[2]['payload']['ref'] == ajena['id']
    assert (await asyncio.wait_for(c.t.next_message(), 1)).id == entrada['id']
    # El único fallo de la transcripción es del cliente: mandó una extensión que no negoció.
    assert [p.split(': ', 1)[1] for p in problemas(c.log)] == ['extensión alfalfa no negociada']


def test_sin_saludo_el_fin_del_motor_cuenta_sus_errores_y_cierra():
    c = Cliente()
    c.t.emit('error', texto='No hay modelo configurado.')
    c.t.emit('fin', estado='failed', resumen='Fallo', codigo_salida=1)
    salida = c.drenar()
    assert tipos(salida) == ['error', 'close']
    assert salida[0]['payload']['message'] == 'No hay modelo configurado.'
    assert c.t.cerrado


# -- lo que llega mal (§2, §4.8) -------------------------------------------
def malos(c):
    def nuevo(tipo, payload):
        m = c.mensaje(tipo, payload)
        c.seq -= 1                                          # todos son «el siguiente» mensaje del cliente
        return m
    base = nuevo('input', {'blocks': [{'type': 'text', 'text': 'hola'}]})
    return {
        'no es JSON': ('esto no es json', 'invalid_message'),
        'no es un objeto': ('[1, 2]', 'invalid_message'),
        'sin campos': ({'pacai': '0.3'}, 'invalid_message'),
        'versión rara': (dict(base, pacai='9'), 'invalid_message'),
        'del lado del agente': (dict(base, sender='agent'), 'invalid_message'),
        'otra sesión': (dict(base, session='s_otra_0123456789abcdef'), 'invalid_message'),
        'sólo del agente': (nuevo('run_started', {'input_id': 'm_00000001'}), 'invalid_message'),
        'tipo desconocido': (nuevo('foo', {}), 'invalid_message'),
        'perfil no negociado': (nuevo('voice_turn', {'state': 'barge_in'}), 'not_negotiated'),
        'estado compartido no negociado': (nuevo('state_snapshot', {'client': {}}), 'not_negotiated'),
        'extensión no negociada': (nuevo('otra.cosa', {}), 'not_negotiated'),
        'extensión de otro': (nuevo('alfalfa.set_budget', {}), None),
    }


@pytest.mark.parametrize('nombre', list(malos(Cliente())))
def test_los_mensajes_mal_formados_reciben_su_error_y_la_sesion_sigue(nombre):
    c = Cliente(extensiones=())
    c.saludar()
    mensaje, esperado = malos(c)[nombre]
    salida = c.crudo(mensaje)
    if esperado is None:
        assert codigo(salida) == 'not_negotiated'       # `alfalfa` no se negoció en este cliente
    else:
        assert codigo(salida) == esperado, salida
    assert not c.t.stopped
    if esperado is not None:
        assert c.t.inbox == []


def test_un_salto_de_seq_del_cliente_es_irrecuperable_y_cierra():
    c = Cliente()
    c.saludar()
    c.seq += 4
    assert codigo(c.entrada()) == 'seq_unrecoverable'
    assert c.t.stopped


def test_una_retransmision_se_descarta_y_un_id_repetido_con_otro_contenido_es_un_error():
    c = Cliente()
    c.saludar()
    c.entrada('uno')
    primero = c.ultimo
    assert c.crudo(primero) == [] and len(c.t.inbox) == 1                 # misma línea: se descarta
    distinto = dict(primero, payload={'blocks': [{'type': 'text', 'text': 'otro'}]})
    assert codigo(c.crudo(distinto)) == 'invalid_message' and len(c.t.inbox) == 1
    atras = c.mensaje('heartbeat', {'last_seq': 1})
    atras['seq'] = 1
    assert codigo(c.crudo(atras)) == 'invalid_message'


def test_heartbeat_close_y_error_del_cliente():
    c = Cliente()
    c.saludar()
    assert c.enviar('heartbeat', {'last_seq': 2}) == []
    assert c.enviar('error', {'code': 'x', 'message': 'avisa'}) == [] and not c.t.stopped
    c.enviar('close', {'reason': 'adiós'})
    assert c.t.stopped
    c.comprobar()


# -- entradas y ejecuciones (§4.2) -----------------------------------------
@pytest.mark.parametrize('bloques', [
    [{'type': 'file', 'name': 'a.txt', 'media_type': 'text/plain', 'data': 'aG9sYQ=='}],
    [{'type': 'structured', 'schema': 'urn:x', 'data': {}}],
    [{'type': 'text', 'text': 'ok'}, {'type': 'file', 'name': 'a', 'media_type': 'x/y', 'url': 'http://x'}],
    [{'type': 'text', 'text': '   '}], [], [{'type': 'text', 'text': 'x' * 20001}]])
def test_un_input_que_el_motor_no_puede_atender_se_rechaza_con_su_ref(bloques):
    c = Cliente()
    c.saludar()
    salida = c.enviar('input', {'blocks': bloques})
    assert codigo(salida) == 'invalid_message' and salida[0]['payload']['ref'] == c.ultimo['id']
    assert c.t.inbox == [] and not c.t._ocupado


def test_un_id_de_input_demasiado_largo_para_la_cola_se_rechaza():
    c = Cliente()
    c.saludar()
    m = c.mensaje('input', {'blocks': [{'type': 'text', 'text': 'hola'}]})
    m['id'] = 'i' * 101
    assert codigo(c.crudo(m)) == 'invalid_message' and c.t.inbox == []


def test_varios_bloques_de_texto_se_juntan():
    c = Cliente()
    c.saludar()
    c.enviar('input', {'blocks': [{'type': 'text', 'text': 'uno'}, {'type': 'text', 'text': 'dos'}]})
    assert c.t.inbox == ['uno\n\ndos']


async def test_el_primer_input_abre_la_ejecucion_y_su_id_es_el_input_id():
    c = Cliente()
    c.saludar()
    c.entrada('Corrige el test')
    prompt = await asyncio.wait_for(c.t.next_message(), 1)
    assert prompt == 'Corrige el test' and prompt.id == c.ultimo['id']
    c.t.turno_empieza(prompt)
    inicio = c.drenar()[-1]
    assert inicio['type'] == 'run_started' and inicio['payload'] == {'input_id': c.ultimo['id']}
    await cerrar(c)
    c.comprobar()


async def test_un_new_run_con_una_ejecucion_en_curso_espera_a_que_termine():
    c = Cliente()
    run1 = await abrir(c, 'uno')
    primero = c.ultimo['id']
    c.entrada('dos')
    segundo = c.ultimo['id']
    c.entrada('tres', mode='new_run')
    tercero = c.ultimo['id']
    assert c.t.inbox == [] and [i for i, _ in c.t._cola] == [segundo, tercero]    # no se cuelan en el turno en curso
    cierre = await cerrar(c)
    assert cierre[-1]['payload']['run_id'] == run1
    # Al terminar la primera pasa la segunda, y la tercera sigue esperando.
    siguiente = await asyncio.wait_for(c.t.next_message(), 1)
    assert siguiente == 'dos' and siguiente.id == segundo and [i for i, _ in c.t._cola] == [tercero]
    c.t.turno_empieza(siguiente)
    run2 = c.drenar()[-1]
    assert run2['type'] == 'run_started' and run2['payload']['input_id'] == segundo and primero != segundo
    await cerrar(c)
    tercera = await asyncio.wait_for(c.t.next_message(), 1)
    assert tercera.id == tercero and c.t._cola == type(c.t._cola)()
    c.t.turno_empieza(tercera)
    c.drenar()
    await cerrar(c)
    c.comprobar()


async def test_steer_entra_en_la_ejecucion_en_curso_sin_esperar():
    c = Cliente()
    run = await abrir(c, 'uno')
    c.t.in_turn = True
    c.entrada('mejor usa pytest', mode='steer', run_id=run)
    assert c.t.inbox == ['mejor usa pytest'] and c.t.redireccion.is_set() and not c.t._cola
    await cerrar(c)
    c.comprobar()


async def test_steer_de_una_ejecucion_desconocida_es_unknown_run_y_de_una_terminada_se_trata_como_new_run():
    c = Cliente()
    run = await abrir(c, 'uno')
    assert codigo(c.entrada('x', mode='steer', run_id='m_noexiste00')) == 'unknown_run'
    await cerrar(c)
    c.entrada('otra vez', mode='steer', run_id=run)
    assert c.t.inbox == ['otra vez'] and not c.t.redireccion.is_set()


def test_un_steer_sin_run_id_no_pasa_el_esquema():
    c = Cliente()
    c.saludar()
    assert codigo(c.enviar('input', {'blocks': [{'type': 'text', 'text': 'x'}], 'mode': 'steer'})) == \
        'invalid_message'


async def test_steer_a_una_subejecucion_va_al_subagente():
    c = Cliente()
    await abrir(c)
    c.t.emit('subagentes', subagentes=[{'id': 'a1', 'nombre': 'A', 'tipo': 'general', 'estado': 'trabajando'}])
    sub = c.drenar()[0]['id']
    enviado = []

    async def enviar(args):
        enviado.append(args)
    c.t.manager = Mock(subagentes=Mock(enviar=enviar))
    c.entrada('mira también X', mode='steer', run_id=sub)
    await reposo()
    assert enviado == [{'agent': 'a1', 'message': 'mira también X', 'background': True}] and c.t.inbox == []


async def test_interrupt_corta_la_ejecucion_raiz_o_dice_que_no_existe():
    c = Cliente()
    run = await abrir(c)
    c.t.interrumpir_turno = Mock(return_value=True)
    assert c.enviar('interrupt', {'run_id': run}) == []
    c.t.interrumpir_turno.assert_called_once_with()
    assert codigo(c.enviar('interrupt', {'run_id': 'm_noexiste00'})) == 'unknown_run'
    await cerrar(c, 'cortado')
    assert codigo(c.enviar('interrupt', {'run_id': run})) == 'unknown_run'      # ya terminó
    c.comprobar()


async def test_interrupt_de_una_subejecucion_cierra_solo_a_ese_subagente():
    c = Cliente()
    await abrir(c)
    c.t.emit('subagentes', subagentes=[{'id': 'a2', 'nombre': 'B', 'tipo': 'general', 'estado': 'trabajando'}])
    sub = c.drenar()[0]['id']
    cerrados = []

    async def cerrar_agente(args):
        cerrados.append(args)
    c.t.manager = Mock(subagentes=Mock(cerrar=cerrar_agente))
    c.t.interrumpir_turno = Mock()
    c.enviar('interrupt', {'run_id': sub})
    await reposo()
    assert cerrados == [{'agent': 'a2'}]
    c.t.interrumpir_turno.assert_not_called()


# -- aprobaciones (§4.5) ---------------------------------------------------
async def pedir(c, argv=('touch', 'x'), **extra):
    """Abre su herramienta y pide permiso con la política de verdad de `--stdio`; devuelve (tarea, petición)."""
    herramienta(c, 'run_command', {'argv': list(argv), 'purpose': 'p'})
    tarea = asyncio.create_task(c.t.approval('.', False)(dict(argv=list(argv), purpose='p', **extra)))
    await reposo()
    peticiones = c.del_agente('approval_request')
    return tarea, peticiones[-1]


def responder(c, peticion, decision='approved', scope='once', **cambios):
    payload = {'decision': decision, **({'scope': scope} if decision == 'approved' else {})}
    return c.enviar('approval_result', payload, reply_to=peticion['id'], **cambios)


async def test_la_peticion_lleva_el_digest_de_los_argumentos_y_sus_campos_obligatorios():
    from alfalfa.pacai.jcs import digest
    c = Cliente()
    run = await abrir(c)
    tarea, peticion = await pedir(c, motivo='peligroso', justificacion='Borra archivos')
    inicio = c.del_agente('tool_start')[-1]
    p = peticion['payload']
    assert p['run_id'] == run and p['tool_call_id'] == inicio['id'] and p['name'] == 'run_command'
    assert p['arguments'] == inicio['payload']['arguments'] and p['digest'] == digest(p['arguments'])
    assert p['reason'] == 'dangerous' and p['scopes'] == ['once'] and 'expires' not in p
    assert p['alfalfa']['purpose'] == 'p' and p['alfalfa']['justification'] == 'Borra archivos'
    responder(c, peticion)
    assert await asyncio.wait_for(tarea, 1) is None
    fin_de_herramienta(c, 'run_command', {'exit_code': 0})
    await cerrar(c)
    c.comprobar()


async def test_aprobar_una_vez_ejecuta_y_negar_no_ejecuta():
    c = Cliente()
    await abrir(c)
    tarea, peticion = await pedir(c)
    assert not tarea.done()
    assert responder(c, peticion) == []
    assert await asyncio.wait_for(tarea, 1) is None
    assert 'approval_result' not in tipos(c.del_agente())             # el agente sólo declara `expired`
    fin_de_herramienta(c, 'run_command', {'exit_code': 0})

    tarea2, peticion2 = await pedir(c, argv=('rm', 'y'))
    responder(c, peticion2, 'denied')
    with pytest.raises(ValueError, match='rechazado'):
        await asyncio.wait_for(tarea2, 1)
    fin = fin_de_herramienta(c, 'run_command', {'error': 'Comando rechazado; no se ejecutó.'})
    assert fin[0]['payload']['status'] == 'error'
    await cerrar(c, 'blocked', resumen='Se negó un permiso')
    c.comprobar()


@pytest.mark.parametrize('payload', [{'decision': 'true'}, {'decision': True}, {'decision': 'yes', 'scope': 'once'},
                                     {'decision': 'approved'}, {'decision': 'denied', 'scope': 'once'},
                                     {'decision': 'expired'}])
async def test_solo_valen_los_valores_exactos_de_la_decision(payload):
    c = Cliente()
    await abrir(c)
    tarea, peticion = await pedir(c)
    assert codigo(c.enviar('approval_result', payload, reply_to=peticion['id'])) == 'invalid_message'
    assert not tarea.done()                                   # sigue pendiente: no vale como aprobación
    responder(c, peticion, 'denied')
    with pytest.raises(ValueError):
        await asyncio.wait_for(tarea, 1)


async def test_un_alcance_que_no_se_ofreció_no_vale_y_la_peticion_sigue_pendiente():
    c = Cliente()
    await abrir(c)
    tarea, peticion = await pedir(c)
    assert peticion['payload']['scopes'] == ['once']
    assert codigo(responder(c, peticion, scope='session')) == 'invalid_message'
    assert not tarea.done()
    responder(c, peticion)
    await asyncio.wait_for(tarea, 1)


async def test_un_reply_to_desconocido_o_ya_resuelto_es_unknown_approval_y_nunca_una_aprobacion():
    c = Cliente()
    await abrir(c)
    tarea, peticion = await pedir(c)
    assert codigo(c.enviar('approval_result', {'decision': 'approved', 'scope': 'once'},
                           reply_to='m_desconocido')) == 'unknown_approval'
    assert not tarea.done()
    responder(c, peticion)
    await asyncio.wait_for(tarea, 1)
    assert codigo(responder(c, peticion, 'denied')) == 'unknown_approval'      # ya se resolvió una vez


async def test_aprobar_para_la_sesion_solo_vale_para_el_mismo_nombre_y_argumentos():
    c = Cliente()
    await abrir(c)
    tarea, peticion = await pedir(c, para_sesion=True)
    assert peticion['payload']['scopes'] == ['once', 'session']
    responder(c, peticion, scope='session')
    assert await asyncio.wait_for(tarea, 1) == 'sesion'
    fin_de_herramienta(c, 'run_command', {'exit_code': 0})
    # La repetición exacta lleva approval_id y no vuelve a preguntar; otros argumentos, nada.
    igual = herramienta(c, 'run_command', {'argv': ['touch', 'x'], 'purpose': 'p'})[0]
    assert igual['payload']['approval_id'] == peticion['id']
    fin_de_herramienta(c, 'run_command', {'exit_code': 0})
    otra = herramienta(c, 'run_command', {'argv': ['touch', 'z'], 'purpose': 'p'})[0]
    assert 'approval_id' not in otra['payload']
    fin_de_herramienta(c, 'run_command', {'exit_code': 0})
    otro_nombre = herramienta(c, 'write_file', {'argv': ['touch', 'x'], 'purpose': 'p'})[0]
    assert 'approval_id' not in otro_nombre['payload']
    fin_de_herramienta(c, 'write_file', {'ok': True})
    await cerrar(c)
    c.comprobar()


async def test_una_politica_que_resuelve_en_el_acto_no_molesta_al_cliente():
    c = Cliente()
    await abrir(c)
    herramienta(c, 'run_command', {'argv': ['ls'], 'purpose': 'Listar'})
    aprobacion = c.t.approval('.', True)                  # auto: las lecturas permitidas se aprueban solas
    assert await asyncio.wait_for(aprobacion(dict(argv=['ls'], purpose='Listar', cwd='.')), 1) is None
    await reposo()
    assert not c.del_agente('approval_request')
    fin_de_herramienta(c, 'run_command', {'exit_code': 0})
    await cerrar(c)
    c.comprobar()


async def test_eof_o_cerrar_la_sesion_niegan_lo_pendiente_y_nada_se_ejecuta():
    c = Cliente()
    await abrir(c)
    tarea, peticion = await pedir(c)
    c.t.stop()                                           # lo que hace el lector al llegar a EOF
    with pytest.raises(ValueError, match='rechazado'):
        await asyncio.wait_for(tarea, 1)
    # La ejecución termina con la petición sin contestar: el agente la cancela antes de cerrar su herramienta.
    cancelada = c.del_agente('approval_result')
    assert [(m['payload'], m['reply_to']) for m in cancelada] == [({'decision': 'cancelled'}, peticion['id'])]
    fin_de_herramienta(c, 'run_command', {'error': 'Comando rechazado; no se ejecutó.'})
    assert c.del_agente('tool_end')[-1]['payload']['status'] == 'cancelled'
    await cerrar(c, 'interrupted')
    c.comprobar()


async def test_sin_respuesta_a_tiempo_el_agente_declara_expired_y_no_ejecuta():
    c = Cliente(caducidad=0.1)
    await abrir(c)
    tarea, peticion = await pedir(c)
    await asyncio.sleep(0.4)
    caducada = c.del_agente('approval_result')
    assert len(caducada) == 1 and caducada[0]['payload'] == {'decision': 'expired'}
    assert caducada[0]['reply_to'] == peticion['id'] and caducada[0]['time'] >= peticion['payload']['expires']
    with pytest.raises(ValueError, match='rechazado'):
        await asyncio.wait_for(tarea, 1)
    # Un sí que llega después del expired no cuenta ni da error.
    assert responder(c, peticion) == []
    fin_de_herramienta(c, 'run_command', {'error': 'Comando rechazado; no se ejecutó.'})
    await cerrar(c, 'blocked', resumen='Permiso caducado')
    c.comprobar()


async def test_el_agente_nunca_declara_expired_antes_de_expires():
    reloj = RelojFalso()
    c = Cliente(caducidad=600, reloj=reloj)
    await abrir(c)
    tarea, peticion = await pedir(c)
    c.t._caducar(peticion['id'])                          # el temporizador se adelantó: no vale
    assert c.drenar() == [] and not tarea.done()
    # Una respuesta tardía no cuenta aunque el temporizador aún no haya saltado.
    reloj.avanzar(601)
    assert responder(c, peticion) == [] and not tarea.done()
    c.t._caducar(peticion['id'])
    expirada = c.drenar()
    assert tipos(expirada) == ['approval_result'] and expirada[0]['payload'] == {'decision': 'expired'}
    with pytest.raises(ValueError):
        await asyncio.wait_for(tarea, 1)
    c.t._cerrar_sesion()


async def test_una_aprobacion_dentro_de_plazo_cancela_el_temporizador_de_caducidad():
    c = Cliente(caducidad=0.2)
    await abrir(c)
    tarea, peticion = await pedir(c)
    responder(c, peticion)
    await asyncio.wait_for(tarea, 1)
    await asyncio.sleep(0.5)
    assert 'approval_result' not in tipos(c.del_agente())


def _segundos_hasta(peticion):
    from datetime import datetime
    a, b = (datetime.fromisoformat(x.replace('Z', '+00:00'))
            for x in (peticion['time'], peticion['payload']['expires']))
    return (b - a).total_seconds()


async def test_por_defecto_una_aprobacion_no_lleva_expires_y_nunca_caduca():
    reloj = RelojFalso()
    c = Cliente(reloj=reloj)
    await abrir(c)
    tarea, peticion = await pedir(c)
    assert 'expires' not in peticion['payload'] and c.t._temporizadores == {}
    reloj.avanzar(30 * 24 * 3600)                         # un mes después sigue esperando al usuario
    c.t._caducar(peticion['id'])
    assert c.t.t.caducar(peticion['id']) == []            # sin `expires`, nunca `expired`
    assert c.drenar() == [] and not tarea.done()
    responder(c, peticion)
    assert await asyncio.wait_for(tarea, 1) is None
    c.t._cerrar_sesion()


@pytest.mark.parametrize('valor, segundos', [('90', 90), ('3600.5', 3600.5), ('', None)])
async def test_la_caducidad_se_cambia_con_alfalfa_caducidad_aprobacion(monkeypatch, valor, segundos):
    monkeypatch.setenv('ALFALFA_CADUCIDAD_APROBACION', valor)
    c = Cliente(reloj=RelojFalso())
    await abrir(c)
    tarea, peticion = await pedir(c)
    assert (_segundos_hasta(peticion) if 'expires' in peticion['payload'] else None) == segundos
    tarea.cancel()
    c.t._cerrar_sesion()


@pytest.mark.parametrize('valor', ['0', '-5', 'nunca', 'nan', str(366 * 24 * 3600)])
def test_una_caducidad_que_no_es_un_numero_razonable_de_segundos_es_un_error(monkeypatch, valor):
    from alfalfa.pacai.transporte import caducidad_de
    with pytest.raises(ValueError, match='--caducidad-aprobacion'):
        caducidad_de(valor)
    monkeypatch.setenv('ALFALFA_CADUCIDAD_APROBACION', valor)
    with pytest.raises(ValueError, match='ALFALFA_CADUCIDAD_APROBACION'):
        caducidad_de()


@pytest.mark.parametrize('argumentos', [['--pacai', '--caducidad-aprobacion', '0'],
                                        ['--caducidad-aprobacion', '60', '--stdio']])
def test_la_opcion_de_caducidad_se_valida_y_solo_vale_con_pacai(argumentos, capsys):
    from alfalfa.terminal import main
    with pytest.raises(SystemExit) as salida:
        main(argumentos)
    assert salida.value.code == 2 and '--caducidad-aprobacion' in capsys.readouterr().err


async def test_interrumpir_con_una_aprobacion_pendiente_la_niega_cierra_su_herramienta_y_olvida_su_plazo():
    c = Cliente(caducidad=3600)
    run = await abrir(c)
    tarea, peticion = await pedir(c)
    assert peticion['id'] in c.t._temporizadores
    c.t.in_turn, c.t.worker = True, tarea                # el turno del motor espera la respuesta
    c.enviar('interrupt', {'run_id': run})
    with pytest.raises((ValueError, asyncio.CancelledError)):
        await asyncio.wait_for(tarea, 1)
    assert c.t.t.aprobacion(peticion['id']).estado == 'cancelled'
    assert c.t._temporizadores == {}                      # nada queda esperando a su `expires`
    # El motor aún cuenta que la orden se rechazó y dice algo: la herramienta sale `cancelled` y el texto,
    # como aviso (la ejecución está en cierre y no empieza mensajes nuevos).
    fin_de_herramienta(c, 'run_command', {'error': 'Comando rechazado; no se ejecutó.'})
    c.t.emit('evento', rol='agent', texto='Me detengo.', hora=3.0)
    await cerrar(c, 'cortado')
    finales = c.del_agente()[-6:]
    assert [m['type'] for m in c.del_agente() if m['type'] in ('approval_result', 'tool_end', 'run_finished')] == \
        ['approval_result', 'tool_end', 'run_finished']
    assert c.del_agente('approval_result')[0]['payload'] == {'decision': 'cancelled'}
    assert c.del_agente('tool_end')[-1]['payload'] == {'tool_call_id': peticion['payload']['tool_call_id'],
                                                       'status': 'cancelled'}
    assert c.del_agente('run_finished')[-1]['payload'] == {'run_id': run, 'outcome': 'interrupted'}
    assert 'message_start' not in tipos(finales) and 'alfalfa.notice' in tipos(finales)
    assert responder(c, peticion) == []                   # un sí tardío se ignora: no vale como aprobación
    c.comprobar()


async def test_cerrar_la_sesion_con_una_aprobacion_pendiente_la_niega_y_olvida_su_plazo():
    c = Cliente()
    await abrir(c)
    tarea, peticion = await pedir(c)
    c.t.stop()
    with pytest.raises(ValueError, match='rechazado'):
        await asyncio.wait_for(tarea, 1)
    assert c.t.t.aprobacion(peticion['id']).estado == 'cancelled' and c.t._temporizadores == {}


async def test_un_permiso_que_ninguna_herramienta_abierta_explica_abre_la_suya():
    c = Cliente()
    await abrir(c)
    herramienta(c, 'run_command', {'argv': ['git', 'status'], 'purpose': 'Ver'})
    tarea = asyncio.create_task(c.t.approval('.', False)(dict(argv=['rm', '-rf', 'x'], purpose='Otra cosa')))
    await reposo()
    sintetica = c.del_agente('tool_start')[-1]
    assert sintetica['payload']['name'] == 'request_permission'
    assert sintetica['payload']['arguments'] == {'argv': ['rm', '-rf', 'x'], 'purpose': 'Otra cosa'}
    peticion = c.del_agente('approval_request')[-1]
    assert peticion['payload']['tool_call_id'] == sintetica['id']
    responder(c, peticion)
    await asyncio.wait_for(tarea, 1)
    assert c.del_agente('tool_end')[-1]['payload'] == {'tool_call_id': sintetica['id'], 'status': 'success'}
    fin_de_herramienta(c, 'run_command', {'exit_code': 0})
    await cerrar(c)
    c.comprobar()


async def test_un_permiso_sin_ejecucion_activa_se_niega():
    c = Cliente()
    c.saludar()
    tarea = asyncio.create_task(c.t.approval('.', False)(dict(argv=['x'], purpose='fuera de un turno')))
    await reposo()
    with pytest.raises(ValueError, match='rechazado'):
        await asyncio.wait_for(tarea, 1)
    assert not c.del_agente('approval_request')


async def test_la_aprobacion_de_un_subagente_nombra_a_su_subejecucion():
    c = Cliente()
    await abrir(c)
    c.t.emit('subagentes', subagentes=[{'id': 'a1', 'nombre': 'Revisor', 'tipo': 'general', 'estado': 'trabajando'}])
    sub = c.drenar()[0]['id']
    c.t.emit('subagente_evento', agente='a1', rol='tool', texto="Inicio run_command · {'argv': ['git', 'log']}",
             hora=1.0)
    tarea = asyncio.create_task(c.t.approval('.', False)(
        dict(argv=['git', 'log'], purpose='Subagente a1 «Revisor»: leer el historial')))
    await reposo()
    peticion = c.del_agente('approval_request')[-1]
    assert peticion['payload']['run_id'] == sub
    responder(c, peticion)
    await asyncio.wait_for(tarea, 1)
    c.t.emit('subagente_evento', agente='a1', rol='tool', texto='run_command\n{"exit_code": 0}', hora=2.0)
    c.t.emit('subagentes', subagentes=[{'id': 'a1', 'nombre': 'Revisor', 'tipo': 'general', 'estado': 'terminado'}])
    await cerrar(c)
    c.comprobar()


@pytest.mark.parametrize('extra, esperado', [
    ({'motivo': 'peligroso'}, 'dangerous'), ({'motivo': 'red'}, 'network'),
    ({'motivo': 'salir_sandbox'}, 'outside_sandbox'), ({'motivo': 'reintento'}, 'sandbox_denied_retry'),
    ({'motivo': 'regla'}, 'policy'), ({'motivo': 'modo'}, 'policy'),
    ({'sin_sandbox': True}, 'outside_sandbox'), ({'mcp': 'servidor'}, 'external_tool'),
    ({'motivo': 'navegador'}, 'alfalfa.browser'), ({'motivo': 'contenido_externo'}, 'alfalfa.external_content'),
    ({}, 'policy')])
async def test_el_motivo_del_permiso_se_traduce_a_un_reason_del_protocolo(extra, esperado):
    c = Cliente()
    await abrir(c)
    tarea, peticion = await pedir(c, **extra)
    assert peticion['payload']['reason'] == esperado
    responder(c, peticion, 'denied')
    with pytest.raises(ValueError):
        await tarea


async def test_sin_la_extension_un_motivo_propio_es_policy_y_no_lleva_el_bloque_alfalfa():
    c = Cliente(extensiones=())
    await abrir(c)
    tarea, peticion = await pedir(c, motivo='navegador')
    assert peticion['payload']['reason'] == 'policy' and 'alfalfa' not in peticion['payload']
    responder(c, peticion, 'denied')
    with pytest.raises(ValueError):
        await tarea


# -- herramientas del cliente (§4.4) ---------------------------------------
def pedir_navegador(c, ident='n1', **campos):
    futuro = asyncio.get_running_loop().create_future()
    c.t.navegador_pendientes[ident] = futuro
    c.t.emit('navegador', id=ident, **dict({'accion': 'abrir', 'url': 'http://localhost:3000'}, **campos))
    return futuro, [m for m in c.drenar() if m['type'] == 'tool_start']


async def test_el_navegador_de_la_app_es_una_herramienta_del_cliente_y_su_respuesta_vuelve_al_motor():
    c = Cliente(herramientas=('browser',))
    run = await abrir(c)
    futuro, inicio = pedir_navegador(c)
    assert len(inicio) == 1
    p = inicio[0]['payload']
    assert p['side'] == 'client' and p['name'] == 'browser' and p['run_id'] == run
    assert p['arguments'] == {'accion': 'abrir', 'url': 'http://localhost:3000'}
    cuerpo = {'ok': True, 'resultado': {'t': 'x'}}
    c.enviar('tool_end', {'tool_call_id': inicio[0]['id'], 'status': 'success', 'output': [
        {'type': 'structured', 'schema': 'urn:alfalfa:browser-response', 'data': cuerpo}]}, reply_to=inicio[0]['id'])
    assert futuro.result() == {'tipo': 'navegador_respuesta', 'id': 'n1', 'ok': True, 'resultado': {'t': 'x'}}
    await cerrar(c)
    c.comprobar()


@pytest.mark.parametrize('estado, esperado', [
    ('error', {'ok': False, 'error': 'La página no cargó'}), ('cancelled', {'ok': False, 'error': 'cancelada'})])
async def test_si_el_cliente_falla_o_cancela_el_motor_recibe_ok_false(estado, esperado):
    c = Cliente(herramientas=('browser',))
    await abrir(c)
    futuro, inicio = pedir_navegador(c)
    extra = {'summary': 'La página no cargó'} if estado == 'error' else {}
    c.enviar('tool_end', {'tool_call_id': inicio[0]['id'], 'status': estado, **extra}, reply_to=inicio[0]['id'])
    assert futuro.result() == dict(esperado, tipo='navegador_respuesta', id='n1')
    await cerrar(c)
    c.comprobar()


async def test_una_respuesta_con_texto_y_sin_datos_se_entrega_como_resultado_de_texto():
    c = Cliente(herramientas=('browser',))
    await abrir(c)
    futuro, inicio = pedir_navegador(c)
    c.enviar('tool_end', {'tool_call_id': inicio[0]['id'], 'status': 'success',
                          'output': [{'type': 'text', 'text': 'listo'}]}, reply_to=inicio[0]['id'])
    assert futuro.result()['resultado'] == {'texto': 'listo'}


async def test_sin_la_herramienta_declarada_el_motor_recibe_un_no_y_no_hay_tool_start():
    c = Cliente(herramientas=())
    await abrir(c)
    futuro, inicio = pedir_navegador(c)
    assert inicio == [] and futuro.result()['ok'] is False and 'browser' in futuro.result()['error']


async def test_el_tool_end_del_cliente_debe_responder_a_su_tool_start_y_ser_de_una_herramienta_abierta():
    c = Cliente(herramientas=('browser',))
    await abrir(c)
    _, inicio = pedir_navegador(c)
    assert codigo(c.enviar('tool_end', {'tool_call_id': inicio[0]['id'], 'status': 'success'},
                           reply_to='m_otro_mensaje')) == 'invalid_message'
    assert codigo(c.enviar('tool_end', {'tool_call_id': 'm_desconocido', 'status': 'success'},
                           reply_to='m_desconocido')) == 'unknown_tool_call'
    c.enviar('tool_end', {'tool_call_id': inicio[0]['id'], 'status': 'success'}, reply_to=inicio[0]['id'])
    assert codigo(c.enviar('tool_end', {'tool_call_id': inicio[0]['id'], 'status': 'success'},
                           reply_to=inicio[0]['id'])) == 'unknown_tool_call'      # ya se cerró


async def test_una_respuesta_tardia_del_navegador_se_descarta_sin_ruido():
    c = Cliente(herramientas=('browser',))
    await abrir(c)
    futuro, inicio = pedir_navegador(c)
    c.t.navegador_pendientes.clear()                     # el motor dejó de esperar
    assert c.enviar('tool_end', {'tool_call_id': inicio[0]['id'], 'status': 'success'}, reply_to=inicio[0]['id']) == []
    assert not futuro.done()


async def test_al_terminar_la_ejecucion_el_agente_cancela_la_herramienta_del_cliente_y_el_motor_recibe_un_no():
    c = Cliente(herramientas=('browser',))
    run = await abrir(c)
    futuro, inicio = pedir_navegador(c)
    cierre = await cerrar(c, 'cortado')                  # sin esperar al cliente
    assert tipos(cierre) == ['alfalfa.closing', 'tool_end', 'run_finished']
    fin = cierre[1]
    assert fin['payload'] == {'tool_call_id': inicio[0]['id'], 'status': 'cancelled'} and 'reply_to' not in fin
    assert cierre[2]['payload'] == {'run_id': run, 'outcome': 'interrupted'}
    assert futuro.done() and futuro.result()['ok'] is False
    # El resultado tardío del cliente se ignora: ni `unknown_tool_call` ni `invalid_message`.
    assert c.enviar('tool_end', {'tool_call_id': inicio[0]['id'], 'status': 'success'}, reply_to=inicio[0]['id']) == []
    c.comprobar()


async def test_si_el_cliente_cierra_su_herramienta_a_tiempo_el_agente_no_la_cancela():
    c = Cliente(herramientas=('browser',))
    await abrir(c)
    _, inicio = pedir_navegador(c)
    c.enviar('tool_end', {'tool_call_id': inicio[0]['id'], 'status': 'success'}, reply_to=inicio[0]['id'])
    cierre = await cerrar(c)
    assert 'tool_end' not in tipos(cierre)
    c.comprobar()


async def test_cancelar_un_relevo_avisa_al_cliente_con_la_extension():
    c = Cliente(herramientas=('browser',))
    await abrir(c)
    _, inicio = pedir_navegador(c, accion='relevo', motivo='Entra tú')
    c.t.emit('navegador', id='n1', accion='cancelar_relevo')
    aviso = c.drenar()[0]
    assert aviso['type'] == 'alfalfa.browser_cancel' and aviso['payload'] == {'tool_call_id': inicio[0]['id']}


# -- órdenes propias de AlfalfaCode (§8) -----------------------------------
def test_cada_orden_de_la_extension_llega_al_motor_como_su_equivalente_v2(monkeypatch):
    c = Cliente()
    c.saludar()
    recibidas = []
    monkeypatch.setattr(Transport, 'receive', lambda self, linea: recibidas.append(json.loads(linea)))
    for nombre, v2 in COMANDOS.items():
        c.enviar('alfalfa.' + nombre, {'valor': 1})
        assert recibidas[-1] == {'valor': 1, 'tipo': v2}
    assert len(recibidas) == len(COMANDOS)


def test_compactar_ajustes_y_presupuesto_cambian_lo_que_cambiarian_en_v2():
    c = Cliente()
    c.saludar()
    c.t.compact = Mock()
    c.enviar('alfalfa.compact', {'instructions': 'resume'})
    c.t.compact.assert_called_once_with('resume')
    c.enviar('alfalfa.set_settings', {'model': 'ollama/x:cloud', 'effort': 'high'})
    assert c.t.pending_settings == {'model': 'ollama/x:cloud', 'effort': 'high'}
    c.t.budget = Mock()
    c.enviar('alfalfa.set_budget', {'session': 5})
    c.t.budget.assert_called_once()
    parada = []
    c.t.stop_process = parada.append
    c.enviar('alfalfa.stop_process', {'id': 'todos'})
    assert parada == ['todos']
    assert c.drenar() == []


def test_una_orden_invalida_de_la_extension_da_el_error_de_v2_y_una_desconocida_invalid_message():
    c = Cliente()
    c.saludar()
    assert c.enviar('alfalfa.set_budget', {'session': 5})[0]['payload']['code'] == 'alfalfa.engine_error'
    assert codigo(c.enviar('alfalfa.no_existe', {})) == 'invalid_message'
    assert codigo(c.enviar('otra.cosa', {})) == 'not_negotiated'


async def test_quitar_o_enviar_ahora_una_entrada_que_espera_su_ejecucion():
    c = Cliente()
    await abrir(c, 'uno')
    c.entrada('dos')
    segundo = c.ultimo['id']
    c.entrada('tres')
    tercero = c.ultimo['id']
    quitado = c.enviar('alfalfa.dequeue', {'id': segundo})
    assert tipos(quitado) == ['alfalfa.dequeued'] and quitado[0]['payload'] == {'id': segundo, 'removed': True}
    assert [i for i, _ in c.t._cola] == [tercero]
    nunca = c.enviar('alfalfa.dequeue', {'id': 'nunca-llego'})
    assert nunca[0]['payload'] == {'id': 'nunca-llego', 'removed': False, 'reason': 'desconocido'}
    c.t.interrumpir_turno = Mock(return_value=True)
    c.enviar('alfalfa.send_now', {'id': tercero})
    assert c.t.inbox and c.t.inbox[0].id == tercero and not c.t._cola
    c.t.interrumpir_turno.assert_called()


# -- tamaño (§4.1, §4.8) ---------------------------------------------------
async def test_ningun_mensaje_supera_max_message_bytes():
    c = Cliente()
    c.saludar(limits={'max_message_bytes': 2048})
    c.entrada()
    c.t.turno_empieza(await c.t.next_message())
    c.drenar()
    herramienta(c, 'read_file', {'path': 'grande'})
    fin = fin_de_herramienta(c, 'read_file', {'content': 'z' * 100000})
    assert fin[0]['payload']['status'] == 'success'
    assert '[recortado]' in fin[0]['payload']['output'][0]['text']
    assert all(len(linea.encode()) <= 2048 for linea in c.salida.getvalue().splitlines())
    # Lo que no se puede recortar (muchos valores cortos) no se envía y se avisa.
    c.t.emit('procesos', procesos=[{'id': 'p' + str(n)} for n in range(3000)])
    aviso = c.drenar()
    assert tipos(aviso) == ['error'] and aviso[0]['payload']['code'] == 'message_too_large'
    await cerrar(c)
    c.comprobar()


async def test_stdout_lleva_solo_mensajes_pacai_y_con_seq_correlativa():
    c = Cliente()
    await abrir(c)
    c.t.emit('presupuesto', sesion=1)
    herramienta(c, 'read_file', {'path': 'a'})
    fin_de_herramienta(c, 'read_file', {'content': 'x'})
    await cerrar(c)
    lineas = c.salida.getvalue().splitlines()
    mensajes = [json.loads(linea) for linea in lineas]               # todas son JSON
    assert all(m['pacai'] == '0.3' and m['sender'] == 'agent' for m in mensajes)
    assert [m['seq'] for m in mensajes] == list(range(1, len(mensajes) + 1))
    assert all('\n' not in linea for linea in lineas)
    c.comprobar()


async def test_los_secretos_declarados_no_salen_y_el_digest_sigue_cuadrando_con_lo_que_sale():
    secreto = 'clave-de-' + 'prueba-' + '12345'          # construido al ejecutar: sin secretos en el repositorio
    c = Cliente()
    c.t.secretos.add(secreto)
    await abrir(c)
    c.t.emit('evento', rol='agent', texto='la clave es ' + secreto, hora=1.0)
    herramienta(c, 'run_command', {'argv': ['curl', '-H', 'x: ' + secreto], 'purpose': 'Llamar'})
    orden = dict(argv=['curl', '-H', 'x: ' + secreto], purpose='Llamar')
    tarea = asyncio.create_task(c.t.approval('.', False)(orden))
    await reposo()
    peticion = c.del_agente('approval_request')[-1]
    responder(c, peticion)
    await asyncio.wait_for(tarea, 1)
    fin_de_herramienta(c, 'run_command', {'output': secreto})
    await cerrar(c)
    assert secreto not in c.salida.getvalue()
    # En los argumentos el secreto sale tachado como manda PACAI 0.3; en el texto, como en v2.
    assert c.del_agente('tool_start')[0]['payload']['arguments']['argv'] == ['curl', '-H', 'x: [redacted]']
    assert '[REDACTADO]' in c.salida.getvalue()
    c.comprobar()                                       # el digest corresponde a los argumentos ya redactados
