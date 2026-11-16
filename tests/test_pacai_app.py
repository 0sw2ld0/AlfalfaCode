"""Lo que la app de Mac necesita de `--pacai` para ver lo mismo que con `--stdio` v2: adjuntos, la cola de
mensajes (`steer` sin cortar, «Redirigir», el eco), el detalle de herramientas, turnos y subagentes, los alcances de
v2 en las aprobaciones, el cierre entero de cada turno y el trabajo que pide permisos fuera de un turno. Cada
transcripción pasa el esquema y las reglas de sesión de PACAI 0.3."""
import asyncio
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from alfalfa.stdio import CONTINUAR_ENCARGO, SOLO_IMAGENES
from tests.pacai_comun import Cliente, abrir, cerrar, fin_de_herramienta, herramienta, problemas, reposo


def tipos(mensajes):
    return [m['type'] for m in mensajes]


def codigo(mensajes):
    errores = [m for m in mensajes if m['type'] == 'error']
    assert len(errores) == 1, mensajes
    return errores[0]['payload']['code']


def archivo(tmp_path, nombre, contenido=b'x'):
    ruta = tmp_path / nombre
    ruta.write_bytes(contenido)
    return ruta


def bloque(ruta, media_type):
    return {'type': 'file', 'name': ruta.name, 'media_type': media_type, 'url': ruta.as_uri()}


async def pedir(c, argv=('touch', 'x'), **extra):
    herramienta(c, 'run_command', {'argv': list(argv), 'purpose': 'p'})
    tarea = asyncio.create_task(c.t.approval('.', False)(dict(argv=list(argv), purpose='p', **extra)))
    await reposo()
    return tarea, c.del_agente('approval_request')[-1]


# -- adjuntos: bloques `file` (M1) -----------------------------------------
def test_un_input_con_bloques_file_lleva_imagenes_y_archivos_al_motor(tmp_path):
    c = Cliente()
    c.saludar()
    imagen, pdf = archivo(tmp_path, 'captura.png'), archivo(tmp_path, 'informe.pdf')
    c.enviar('input', {'blocks': [{'type': 'text', 'text': 'Mira esto'}, bloque(imagen, 'image/png'),
                                  bloque(pdf, 'application/pdf')]})
    texto = c.t.inbox[0]
    assert texto.id == c.ultimo['id'] and texto.endswith('Mira esto')
    assert 'path="' + str(imagen.resolve()) + '"' in texto and '<image ' in texto
    assert '<file name="informe.pdf" path="' + str(pdf) + '"' in texto
    c.comprobar()


def test_solo_con_adjuntos_el_texto_es_el_de_siempre(tmp_path):
    c = Cliente()
    c.saludar()
    c.enviar('input', {'blocks': [bloque(archivo(tmp_path, 'a.png'), 'image/png')]})
    assert c.t.inbox[0].endswith(SOLO_IMAGENES)


@pytest.mark.parametrize('url', ['http://x/a.png', 'file://otra-maquina/tmp/a.png', 'file:relativa.png'])
def test_un_bloque_file_que_no_es_una_ruta_absoluta_del_mac_se_rechaza(url):
    c = Cliente()
    c.saludar()
    salida = c.enviar('input', {'blocks': [{'type': 'file', 'name': 'a.png', 'media_type': 'image/png', 'url': url}]})
    assert codigo(salida) == 'invalid_message' and c.t.inbox == [] and not c.t._ocupado


def test_un_adjunto_que_el_motor_no_admite_no_deja_la_sesion_ocupada(tmp_path):
    c = Cliente()
    c.saludar()
    falta = tmp_path / 'no-existe.png'
    salida = c.enviar('input', {'blocks': [{'type': 'text', 'text': 'x'}, bloque(falta, 'image/png')]})
    assert codigo(salida) == 'alfalfa.engine_error' and 'No existe la imagen' in salida[0]['payload']['message']
    assert c.t.inbox == [] and not c.t._ocupado
    c.entrada('otra')                                      # la siguiente pasa: nadie espera una ejecución
    assert c.t.inbox == ['otra']


# -- la cola de mensajes: steer sin cortar, «Redirigir» y el eco (M2, M3, M4) ------------------------
async def test_un_steer_con_redirect_false_entra_en_el_turno_sin_cortar_la_respuesta():
    c = Cliente()
    run = await abrir(c, 'uno')
    c.t.in_turn = True
    c.entrada('y añade un test', mode='steer', run_id=run, alfalfa={'redirect': False})
    assert c.t.inbox == ['y añade un test'] and c.t.inbox[0].id == c.ultimo['id']
    assert not c.t.redireccion.is_set() and not c.t._cola
    await cerrar(c)
    c.comprobar()


async def test_redirect_sin_texto_hace_que_el_modelo_lea_ya_la_cola():
    c = Cliente()
    run = await abrir(c, 'uno')
    c.t.in_turn = True
    c.entrada('lee esto', mode='steer', run_id=run, alfalfa={'redirect': False})
    assert c.enviar('alfalfa.redirect', {}) == [] and c.t.redireccion.is_set()
    await cerrar(c)
    c.comprobar()


async def test_el_eco_del_mensaje_dice_cuando_entro_con_su_input_id_y_su_turno():
    c = Cliente()
    run = await abrir(c, 'uno')
    entrada = c.ultimo['id']
    c.t.emit('evento', rol='user', texto='uno', hora=1.0, id_cola=entrada, turno='t1')
    eco, = c.drenar()
    assert eco['type'] == 'alfalfa.user_message'
    assert eco['payload'] == {'text': 'uno', 'input_id': entrada, 'turn': 't1'}
    assert not [m for m in c.log if m['type'] == 'message_start']             # el núcleo sigue sin devolverlo
    await cerrar(c)
    assert run in [m['payload'].get('run_id') for m in c.del_agente('run_finished')]
    c.comprobar()


# -- turnos, herramientas y subagentes con su detalle de v2 (M5, M6, M7) --------------------------
async def test_los_mensajes_y_las_herramientas_llevan_el_turno_el_texto_y_la_herramienta_de_v2():
    c = Cliente()
    await abrir(c)
    c.t.emit('evento', rol='agent', texto='Voy', hora=1.0, turno='t7')
    inicio_msg = c.drenar()[0]
    assert inicio_msg['payload']['alfalfa'] == {'turn': 't7'}
    inicio, = herramienta(c, 'run_command', {'argv': ['ls'], 'purpose': 'Listar'}, turno='t7')
    extra = inicio['payload']['alfalfa']
    assert extra['text'].startswith('Inicio run_command · ') and extra['turn'] == 't7'
    assert extra['tool']['name'] == 'run_command' and extra['tool']['phase'] == 'start'
    assert extra['tool']['description'] and extra['tool']['command'] == 'ls'
    c.t.emit('evento', rol='tool', texto='run_command\n{"exit_code": 0}', hora=2.0, turno='t7',
             herramienta={'nombre': 'run_command', 'fase': 'fin', 'codigo': 0,
                          'diagnosticos': {'errores': 1, 'avisos': 2}})
    fin, = c.drenar()
    # «nombre\nresultado»: el cliente lo rehace con `name` y `output`, no va dos veces.
    assert fin['payload']['alfalfa'] == {'tool': {'name': 'run_command', 'phase': 'end', 'code': 0,
                                                  'diagnostics': {'errors': 1, 'warnings': 2}}, 'turn': 't7'}
    assert fin['payload']['output'] == [{'type': 'text', 'text': '{"exit_code": 0}'}]
    herramienta(c, 'read_file', {'path': 'a'})
    c.t.emit('evento', rol='tool', texto='read_file\n', hora=3.0, herramienta={'nombre': 'read_file', 'fase': 'fin'})
    assert c.drenar()[0]['payload']['alfalfa']['text'] == 'read_file\n'      # éste no se rehace: va entero
    await cerrar(c)
    c.comprobar()


async def test_cada_subejecucion_dice_que_subagente_es_y_lo_que_no_cabe_en_ella_no_se_pierde():
    c = Cliente()
    await abrir(c)
    c.t.emit('subagentes', subagentes=[{'id': 'a3', 'nombre': 'A', 'tipo': 'general', 'estado': 'trabajando'}])
    sub = c.drenar()[0]
    assert sub['type'] == 'run_started' and sub['payload']['alfalfa'] == {'agent': 'a3'}
    c.t.emit('subagente_evento', agente='a3', rol='tool', texto="Inicio read_file · {'path': 'x'}", hora=1.0)
    inicio = c.drenar()[0]
    assert inicio['payload']['run_id'] == sub['id']
    assert inicio['payload']['alfalfa'] == {'text': "Inicio read_file · {'path': 'x'}"}
    c.t.emit('subagente_evento', agente='a3', rol='user', texto='Revisa A', hora=2.0)
    assert [(m['type'], m['payload']) for m in c.drenar()] == [
        ('alfalfa.subagent_event', {'agent': 'a3', 'role': 'user', 'text': 'Revisa A', 'time': 2.0})]
    await cerrar(c)
    c.comprobar()


# -- aprobaciones: el motivo principal, «en esta sesión» y los alcances de v2 (M8, M9, M10) -----------
async def test_la_peticion_lleva_el_motivo_principal_y_si_ofrece_la_sesion():
    c = Cliente()
    await abrir(c)
    tarea, peticion = await pedir(c, motivo='modo', motivos=['regla', 'modo'], para_sesion=True)
    p = peticion['payload']
    assert p['reason'] == 'policy'                                   # `regla` y `modo` son `policy` en el núcleo
    assert p['alfalfa']['reason'] == 'mode' and p['alfalfa']['reasons'] == ['rule', 'mode']
    assert p['alfalfa']['for_session'] is True and p['scopes'] == ['once', 'session']
    c.enviar('approval_result', {'decision': 'denied'}, reply_to=peticion['id'])
    with pytest.raises(ValueError):
        await asyncio.wait_for(tarea, 1)
    fin_de_herramienta(c, 'run_command', {'error': 'Comando rechazado; no se ejecutó.'})
    await cerrar(c)
    c.comprobar()


async def test_no_volver_a_preguntar_es_alfalfa_scope_always():
    c = Cliente()
    await abrir(c)
    tarea, peticion = await pedir(c, argv=('npm', 'test'), motivo='modo', prefijo=['npm', 'test'],
                                  prefijo_alcance='sandbox')
    assert peticion['payload']['alfalfa']['prefix'] == ['npm', 'test']
    c.enviar('approval_result', {'decision': 'approved', 'scope': 'once', 'alfalfa': {'scope': 'always'}},
             reply_to=peticion['id'])
    assert await asyncio.wait_for(tarea, 1) == 'siempre'
    resuelto = [m for m in c.salida.getvalue().splitlines() if 'permiso_resuelto' in m]
    assert resuelto == []                                            # el cliente ya lo sabe: no vuelve
    fin_de_herramienta(c, 'run_command', {'exit_code': 0})
    await cerrar(c)
    c.comprobar()


async def test_el_permiso_de_un_sitio_para_el_proyecto_es_alfalfa_scope_project():
    c = Cliente()
    await abrir(c)
    sitio = {'origen': 'ejemplo.com:443', 'proyecto': '/p'}
    tarea, peticion = await pedir(c, argv=('open', 'https://ejemplo.com'), motivo='navegador', sitio=sitio)
    assert peticion['payload']['scopes'] == ['once', 'session']
    c.enviar('approval_result', {'decision': 'approved', 'scope': 'once', 'alfalfa': {'scope': 'project'}},
             reply_to=peticion['id'])
    assert await asyncio.wait_for(tarea, 1) == 'proyecto'
    fin_de_herramienta(c, 'run_command', {'exit_code': 0})
    await cerrar(c)
    c.comprobar()


async def test_un_alcance_de_v2_que_la_peticion_no_ofrecio_no_vale():
    c = Cliente()
    await abrir(c)
    tarea, peticion = await pedir(c, motivo='modo')                    # sin prefijo ni sitio
    for propio in ('always', 'project', 'forever'):
        salida = c.enviar('approval_result', {'decision': 'approved', 'scope': 'once', 'alfalfa': {'scope': propio}},
                          reply_to=peticion['id'])
        assert codigo(salida) == 'invalid_message' and not tarea.done()
    c.enviar('approval_result', {'decision': 'approved', 'scope': 'once'}, reply_to=peticion['id'])
    assert await asyncio.wait_for(tarea, 1) is None
    fin_de_herramienta(c, 'run_command', {'exit_code': 0})
    await cerrar(c)


async def test_una_aprobacion_que_niega_la_interrupcion_dice_su_motivo():
    c = Cliente()
    await abrir(c)
    tarea, peticion = await pedir(c, motivo='modo')
    c.t.in_turn, c.t.worker = True, asyncio.ensure_future(asyncio.sleep(5))
    c.t.interrumpir_turno()
    cancelada = c.del_agente('approval_result')[-1]
    assert cancelada['payload'] == {'decision': 'cancelled', 'reason': 'Turno interrumpido'}
    with pytest.raises(ValueError):
        await asyncio.wait_for(tarea, 1)
    await cerrar(c, 'cortado')
    c.comprobar()


# -- el cierre de cada turno y de la sesión, enteros (M11, M12, M13) ----------------------------
async def test_alfalfa_closing_lleva_el_turno_terminado_entero():
    c = Cliente()
    run = await abrir(c)
    cierre = await cerrar(c, 'cortado', turno='t2', coste=0.25, tokens_turno=7, coste_turno=0.01)
    extra = next(m for m in cierre if m['type'] == 'alfalfa.closing')['payload']
    # `cortado` («Enviar ahora») y `interrupted` son `interrupted` en el núcleo; la app los trata distinto.
    assert extra == {'run_id': run, 'status': 'cut', 'summary': 'Hecho', 'exit_code': 0, 'tokens': 10,
                     'turn': 't2', 'cost': 0.25, 'turn_tokens': 7, 'turn_cost': 0.01}
    c.entrada('otra')
    c.t.turno_empieza(await asyncio.wait_for(c.t.next_message(), 1))
    run2 = c.drenar()[-1]['id']
    cierre = await cerrar(c)                                           # sin nada especial: también sale
    assert next(m for m in cierre if m['type'] == 'alfalfa.closing')['payload']['run_id'] == run2
    c.comprobar()


def test_el_close_lleva_el_fin_de_v2_entero():
    c = Cliente()
    c.saludar()
    c.t.emit('fin', estado='completed', resumen='Todo hecho', codigo_salida=0, tokens=40, coste=0.5,
             cache={'ahorrado': 0.1})
    cierre = c.drenar()[-1]
    assert cierre['type'] == 'close' and cierre['payload']['reason'] == 'Todo hecho'
    assert cierre['payload']['alfalfa'] == {'status': 'completed', 'summary': 'Todo hecho', 'exit_code': 0,
                                            'tokens': 40, 'cost': 0.5, 'cache': {'ahorrado': 0.1}}


async def test_el_contexto_va_siempre_en_alfalfa_context():
    c = Cliente()
    run = await abrir(c)
    c.t.emit('contexto', tokens=7, limite=100)
    uso, extra = c.drenar()
    assert uso['type'] == 'usage' and uso['payload'] == {'run_id': run, 'context_tokens': 7, 'context_limit': 100}
    assert extra['type'] == 'alfalfa.context' and extra['payload'] == {'tokens': 7, 'limit': 100}
    await cerrar(c)
    c.comprobar()


# -- el navegador de la app contesta también con `status: error` (M14) --------------------------
async def test_la_respuesta_de_permiso_del_navegador_llega_entera_al_motor():
    c = Cliente(herramientas=('browser',))
    await abrir(c)
    futuro = asyncio.get_running_loop().create_future()
    c.t.navegador_pendientes['n1'] = futuro
    c.t.emit('navegador', id='n1', accion='pulsar', objetivo='#enviar')
    inicio = [m for m in c.drenar() if m['type'] == 'tool_start'][0]
    cuerpo = {'ok': False, 'permission': {'type': 'form', 'detail': 'Enviar el formulario'}, 'result': {'url': 'u'}}
    c.enviar('tool_end', {'tool_call_id': inicio['id'], 'status': 'error', 'output': [
        {'type': 'structured', 'schema': 'alfalfa.browser_reply', 'data': cuerpo}]}, reply_to=inicio['id'])
    assert futuro.result() == {'tipo': 'navegador_respuesta', 'id': 'n1', 'ok': False,
                               'permiso': {'tipo': 'formulario', 'detalle': 'Enviar el formulario'},
                               'resultado': {'url': 'u'}}
    await cerrar(c)
    c.comprobar()


# -- el turno sin eco y reanudar sin mensaje (M15) -------------------------------------------
async def test_echo_false_y_continue_abren_el_turno_sin_eco():
    c = Cliente()
    c.saludar()
    c.entrada('El encargo', alfalfa={'echo': False})
    primero = await asyncio.wait_for(c.t.next_message(), 1)
    assert primero == 'El encargo' and primero.sin_eco is True
    c.t.turno_empieza(primero)
    c.drenar()
    await cerrar(c)
    c.enviar('input', {'blocks': [], 'alfalfa': {'continue': True}})
    segundo = await asyncio.wait_for(c.t.next_message(), 1)
    assert segundo == CONTINUAR_ENCARGO and segundo.sin_eco is True and segundo.id == c.ultimo['id']
    c.t.turno_empieza(segundo)
    assert c.drenar()[-1]['payload'] == {'input_id': c.ultimo['id']}
    await cerrar(c)
    assert codigo(c.enviar('input', {'blocks': [{'type': 'text', 'text': 'x'}], 'alfalfa': {'continue': True}})) \
        == 'invalid_message'
    c.entrada('normal')
    assert (await asyncio.wait_for(c.t.next_message(), 1)).sin_eco is False
    c.comprobar()


def test_sin_la_extension_el_bloque_alfalfa_del_input_no_cuenta():
    c = Cliente(extensiones=())
    c.saludar()
    c.entrada('hola', alfalfa={'echo': False})
    assert c.t.inbox[0].sin_eco is False


# -- trabajo que pide permisos fuera de un turno (M17) ----------------------------------------
async def test_guardar_una_prueba_del_navegador_abre_una_ejecucion_y_su_permiso_se_puede_aprobar():
    c = Cliente()
    c.saludar()

    async def exportar(ident, ruta):
        await c.t.approval('.', False)(dict(argv=['write', ruta], purpose='Guardar la prueba', motivo='edicion'))
        return 'tests/e2e/' + ruta
    c.t.exportar_prueba = exportar
    run, = c.enviar('input', {'blocks': [{'type': 'structured', 'schema': 'alfalfa.export_browser_test',
                                         'data': {'id': 'f' * 32, 'path': 'login.spec.ts'}}]})
    entrada = c.ultimo['id']
    await reposo()
    inicio, peticion = c.drenar()
    assert run['type'] == 'run_started' and run['payload'] == {'input_id': entrada}
    assert inicio['payload']['alfalfa'] == {'synthetic': True}     # la abrió el permiso: no es una herramienta
    assert peticion['type'] == 'approval_request' and peticion['payload']['run_id'] == run['id']
    c.enviar('approval_result', {'decision': 'approved', 'scope': 'once'}, reply_to=peticion['id'])
    await asyncio.wait_for(asyncio.shield(c.t.exportacion), 1)
    await reposo()
    final = c.drenar()
    assert [m['type'] for m in final][-1] == 'run_finished'
    assert final[-1]['payload'] == {'run_id': run['id'], 'outcome': 'success'}
    assert 'Prueba guardada en tests/e2e/login.spec.ts' in json.dumps(final, ensure_ascii=False)
    assert not c.t._ocupado
    c.comprobar()


async def test_la_cola_de_encargos_es_una_ejecucion_y_un_mensaje_la_corta_como_en_v2():
    c = Cliente()
    c.saludar()
    fin = asyncio.Event()

    class Cola:
        worker = None
        activa = property(lambda self: self.worker is not None and not self.worker.done())

    async def trabajar():
        await c.t.approval('.', False)(dict(argv=['make'], purpose='Encargo «uno»: compilar', motivo='modo'))
        await fin.wait()
    recibidas = []

    def encargos(valor):
        recibidas.append(valor)
        if valor['accion'] == 'iniciar':
            c.t.cola_encargos.worker = asyncio.ensure_future(trabajar())
    c.t.cola_encargos, c.t.encargos = Cola(), encargos
    c.enviar('input', {'blocks': [{'type': 'structured', 'schema': 'alfalfa.jobs_command',
                                   'data': {'action': 'start'}}]})
    await reposo()
    assert recibidas == [{'tipo': 'encargos', 'accion': 'iniciar'}]
    run = c.del_agente('run_started')[-1]
    peticion = c.del_agente('approval_request')[-1]
    assert peticion['payload']['run_id'] == run['id']
    c.enviar('approval_result', {'decision': 'approved', 'scope': 'once'}, reply_to=peticion['id'])
    # Un mensaje durante la cola no espera a que acabe: llega al motor, que la para y abre el turno.
    c.entrada('para y mira esto')
    assert c.t.inbox == ['para y mira esto'] and not c.t._cola
    c.t.cola_encargos.worker.cancel()
    await asyncio.gather(c.t.cola_encargos.worker, return_exceptions=True)
    c.t.turno_empieza(await asyncio.wait_for(c.t.next_message(), 1))
    salida = c.drenar()
    assert [m['type'] for m in salida][-2:] == ['run_finished', 'run_started']
    assert salida[-2]['payload'] == {'run_id': run['id'], 'outcome': 'interrupted'}
    await cerrar(c)
    c.comprobar()


def test_un_bloque_structured_que_no_es_un_trabajo_se_rechaza():
    c = Cliente()
    c.saludar()
    for bloques in ([{'type': 'structured', 'schema': 'alfalfa.compact', 'data': {}}],
                    [{'type': 'structured', 'schema': 'alfalfa.jobs_command', 'data': {'action': 'volar'}}]):
        assert codigo(c.enviar('input', {'blocks': bloques})) == 'invalid_message'
    assert not c.t._ocupado


# -- lo que llega con la ejecución en cierre no se pierde (M18) --------------------------------
async def test_en_una_ejecucion_en_cierre_los_eventos_de_v2_salen_enteros_en_la_extension():
    c = Cliente()
    run = await abrir(c)
    tarea, peticion = await pedir(c, motivo='modo')
    c.t.emit('permiso_resuelto', id=c.t.t.aprobacion(peticion['id']).v2id, aprobado=False, motivo='Turno interrumpido')
    tarea.cancel()
    c.drenar()
    c.t.emit('evento', rol='agent', texto='Me cortaron', hora=4.0, turno='t3')
    avisos = [m['payload'] for m in c.drenar() + herramienta(c, 'read_file', {'path': 'a'})
              + fin_de_herramienta(c, 'read_file', {'content': 'x'})]
    assert avisos[0] == {'role': 'agent', 'text': 'Me cortaron', 'time': 4.0, 'turn': 't3', 'run_id': run}
    assert [a['role'] for a in avisos] == ['agent', 'tool', 'tool']
    assert avisos[1]['tool']['phase'] == 'start' and avisos[2]['tool']['phase'] == 'end'
    assert all(m['type'] in ('alfalfa.notice', 'approval_result', 'tool_end') for m in c.del_agente()[-3:])
    # El fin de una orden que se cortó con el turno anterior llega en el siguiente: tampoco se pierde.
    await cerrar(c, 'interrupted')
    c.entrada('sigue')
    c.t.turno_empieza(await asyncio.wait_for(c.t.next_message(), 1))
    c.drenar()
    tardio, = fin_de_herramienta(c, 'run_command', {'error': 'El proceso se interrumpió durante esta herramienta.'})
    assert tardio['type'] == 'alfalfa.notice' and tardio['payload']['role'] == 'tool'
    await cerrar(c, 'interrupted')
    c.comprobar()



# -- con el motor de verdad --------------------------------------------------------------------
# Inferencia sustituida: contesta con el último mensaje del usuario. Los argumentos van tal cual a `main`.
MODELO = """
import sys
from unittest.mock import patch
from alfalfa.terminal import main
from alfalfa.proveedores.nativo.runtime import NativeRuntime
async def inference(self, client, endpoint, payload, **kwargs):
    if endpoint == '/api/show':
        return {'capabilities': ['tools']}
    return dict(message=dict(role='assistant', content='Hecho: ' + payload['messages'][-1]['content'][-40:],
                             tool_calls=[]), done=True, done_reason='stop', prompt_eval_count=1, eval_count=1)
with patch.object(NativeRuntime, 'request', inference):
    sys.exit(main(sys.argv[1:]))
"""


def sesion(tmp_path, entradas, *argumentos):
    """`alfalfa --pacai` de verdad: el `hello` y cada entrada tras el `run_finished` (o el `error`) de la anterior;
    luego EOF. Devuelve la transcripción de los dos lados."""
    # HOME propio: el motor no mira la configuración de Codex ni de Claude Code de quien corre la suite.
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), ALFALFA_HOME=str(tmp_path / 'home'),
               HOME=str(tmp_path / 'casa'))
    proc = subprocess.Popen([sys.executable, '-c', MODELO, '--pacai', *argumentos], cwd=tmp_path, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    log, seq = [], 0
    vigia = threading.Timer(60, proc.kill)               # un motor que no contesta no cuelga la suite
    vigia.start()

    def enviar(tipo, payload):
        nonlocal seq
        seq += 1
        m = dict(pacai='0.3', id='c_%08d' % seq, type=tipo, session='s_prueba_0123456789abcdef', sender='client',
                 seq=seq, time='2026-11-16T10:00:00Z', payload=payload)
        log.append(m)
        proc.stdin.write(json.dumps(m) + '\n')
        proc.stdin.flush()
    try:
        enviar('hello', {'versions': ['0.3'], 'profiles': ['work'], 'extensions': ['alfalfa']})
        pendientes = list(entradas)
        for linea in proc.stdout:
            m = json.loads(linea)
            log.append(m)
            listo = m['type'] in ('session_started', 'run_finished') or (m['type'] == 'error' and m.get('payload',
                                                                                  {}).get('alfalfa'))
            if listo and pendientes:
                enviar('input', pendientes.pop(0))
            elif listo:
                proc.stdin.close()
        proc.wait(timeout=30)
    finally:
        vigia.cancel()
        if proc.poll() is None:
            proc.kill()
    return log


def test_el_encargo_inicial_sin_eco_y_los_siguientes_con_su_eco(tmp_path):
    log = sesion(tmp_path, [{'blocks': [{'type': 'text', 'text': 'Primero'}], 'alfalfa': {'echo': False}},
                            {'blocks': [{'type': 'text', 'text': 'Segundo'}]}])
    assert problemas(log) == []
    ecos = [m['payload'] for m in log if m['type'] == 'alfalfa.user_message']
    assert [e['text'] for e in ecos] == ['Segundo']
    segundo = [m for m in log if m['type'] == 'input'][1]
    assert ecos[0]['input_id'] == segundo['id'] and ecos[0]['turn']
    cierres = [m['payload'] for m in log if m['type'] == 'alfalfa.closing']
    assert [c['status'] for c in cierres] == ['completed', 'completed'] and all(c['exit_code'] == 0 for c in cierres)
    task = json.loads(next((tmp_path / 'home').rglob('task.json')).read_text())
    assert task['objective'] == 'Primero'


def test_la_imagen_de_un_bloque_file_llega_al_turno_con_su_etiqueta(tmp_path):
    imagen = tmp_path / 'captura.png'
    imagen.write_bytes(b'png')
    log = sesion(tmp_path, [{'blocks': [{'type': 'text', 'text': 'Mira'}, bloque(imagen, 'image/png')]}])
    assert problemas(log) == []
    eco = next(m for m in log if m['type'] == 'alfalfa.user_message')['payload']
    assert eco['text'] == '<image name=[Image #1] path="' + str(imagen.resolve()) + '"></image>\nMira'


def test_pacai_abre_la_sesion_de_una_cola_de_encargos(tmp_path):
    log = sesion(tmp_path, [], '--cola-encargos')
    assert [m['type'] for m in log if m['sender'] == 'agent'][:2] == ['hello', 'session_started']
    assert log[-1]['type'] == 'close' and problemas(log) == []
    task = json.loads(next((tmp_path / 'home').rglob('task.json')).read_text())
    assert task['objective'] == 'Cola de encargos'


# -- un turno que el motor abre mientras se cierra el anterior ----------------------------------
async def test_el_turno_siguiente_no_empieza_hasta_que_se_cierra_el_anterior():
    """«Enviar ahora» o un `steer` que el modelo no llegó a leer: el motor tiene ya el mensaje y abre el turno
    siguiente en cuanto cierra el anterior, mientras el transporte aún calcula su diff."""
    c = Cliente()
    run = await abrir(c, 'uno')
    c.entrada('dos', mode='steer', run_id=run, alfalfa={'redirect': False})
    segundo = c.ultimo['id']
    c.t.emit('turno_terminado', estado='cortado', resumen='', codigo_salida=1, tokens=1)
    assert c.t._cerrando is not None and not c.t._cerrando.done()
    c.t.turno_empieza(await asyncio.wait_for(c.t.next_message(), 1))       # el motor no espera
    await c.t.antes_del_turno()
    c.t.emit('evento', rol='agent', texto='Sigo con dos', hora=1.0)
    tipos_ = [(m['type'], m['payload'].get('run_id') or m['payload'].get('input_id')) for m in c.drenar()
              if m['type'] in ('run_started', 'run_finished', 'message_start')]
    nuevo = c.t.t.raiz
    assert tipos_ == [('run_finished', run), ('run_started', segundo), ('message_start', nuevo)]
    assert nuevo != run and c.t._ocupado
    await cerrar(c)
    c.comprobar()


async def test_los_argumentos_de_una_herramienta_mcp_van_tal_cual():
    """Son del servidor MCP: una clave como `ruta` o `texto`, o un valor como `ahorro`, no es un nombre de v2."""
    c = Cliente()
    await abrir(c)
    mcp = {'servidor': 'notas', 'herramienta': 'guardar', 'nombre': 'mcp__notas__guardar', 'llamada': 'k1',
           'argumentos': {'ruta': 'a.md', 'texto': 'hola', 'modo': 'ahorro', 'estado': {'fase': 'inicio'}}}
    tarea, peticion = await pedir(c, motivo='mcp', mcp=mcp)
    extra = peticion['payload']['alfalfa']['mcp']
    assert extra == {'server': 'notas', 'tool': 'guardar', 'name': 'mcp__notas__guardar', 'call': 'k1',
                     'arguments': mcp['argumentos']}
    assert peticion['payload']['reason'] == 'external_tool'
    tarea.cancel()
    await cerrar(c)
