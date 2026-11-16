"""De los eventos v2 del motor a mensajes PACAI: cada mensaje del alcance, validado contra el esquema y las reglas
de sesión. El motor se simula emitiendo sus eventos v2 reales (`herramienta` de `datos_de_inicio`/`datos_de_fin`)."""
import pytest

from alfalfa.pacai.traductor import archivos_del_diff, argumentos_de
from alfalfa.proveedores.nativo.descripciones import datos_de_inicio
from alfalfa.stdio import EnCola
from tests.pacai_comun import Cliente, abrir, cerrar, fin_de_herramienta, herramienta


# -- mensajes en flujo (§4.3) ----------------------------------------------
async def test_un_mensaje_del_agente_sale_en_inicio_delta_y_fin():
    c = Cliente()
    run = await abrir(c)
    c.t.emit('evento', rol='agent', texto='Listo, corregí el archivo.', hora=1.0)
    inicio, delta, fin = c.drenar()
    assert [inicio['type'], delta['type'], fin['type']] == ['message_start', 'message_delta', 'message_end']
    assert inicio['payload'] == {'run_id': run, 'role': 'assistant'}
    assert delta['payload'] == {'message_id': inicio['id'],
                                'blocks': [{'type': 'text', 'text': 'Listo, corregí el archivo.'}]}
    assert fin['payload'] == {'message_id': inicio['id']}
    await cerrar(c)
    c.comprobar()


async def test_un_texto_largo_se_reparte_en_varios_delta_y_se_concatena_en_orden():
    c = Cliente()
    await abrir(c)
    texto = ''.join(chr(97 + n % 26) for n in range(9500))
    c.t.emit('evento', rol='agent', texto=texto, hora=1.0)
    c.drenar()
    deltas = [m for m in c.log if m['type'] == 'message_delta']
    assert len(deltas) == 3
    assert ''.join(m['payload']['blocks'][0]['text'] for m in deltas) == texto
    await cerrar(c)
    c.comprobar()


async def test_el_eco_del_usuario_y_los_textos_vacios_no_salen():
    c = Cliente()
    await abrir(c)
    c.t.emit('evento', rol='user', texto='haz algo', hora=1.0)
    c.t.emit('evento', rol='agent', texto='', hora=1.0)
    assert not [m for m in c.drenar() if m['type'].startswith('message_')]
    await cerrar(c)


async def test_un_aviso_del_sistema_dentro_de_la_ejecucion_es_un_mensaje_system_y_fuera_una_notificacion():
    c = Cliente()
    c.saludar()
    c.t.emit('evento', rol='system', texto='Ya se está compactando la conversación.', hora=1.0)
    fuera = c.drenar()
    assert [m['type'] for m in fuera] == ['alfalfa.notice']
    assert fuera[0]['payload']['text'].startswith('Ya se está') and 'version' not in fuera[0]['payload']
    c.entrada()
    c.t.turno_empieza(EnCola('x', c.ultimo['id']))
    c.t.emit('evento', rol='system', texto='Hooks · aviso', hora=1.0)
    c.t.emit('evento', rol='system', texto='Conversación compactada', hora=1.0, compactacion={'antes': 9, 'despues': 3})
    c.drenar()
    assert c.log[-4]['payload']['role'] == 'system'
    assert c.log[-1]['type'] == 'alfalfa.notice' and c.log[-1]['payload']['compaction'] == {'before': 9, 'after': 3}
    await cerrar(c)
    c.comprobar()


# -- ejecuciones (§4.2) ----------------------------------------------------
@pytest.mark.parametrize('estado, resultado', [('completed', 'success'), ('blocked', 'blocked'), ('failed', 'failed'),
                                               ('cortado', 'interrupted'), ('interrupted', 'interrupted')])
async def test_el_estado_del_turno_es_el_resultado_de_la_ejecucion(estado, resultado):
    c = Cliente()
    run = await abrir(c)
    cierre = await cerrar(c, estado, resumen='motivo concreto')
    fin = cierre[-1]
    assert fin['type'] == 'run_finished' and fin['payload']['run_id'] == run and fin['payload']['outcome'] == resultado
    assert ('detail' in fin['payload']) == (resultado in ('failed', 'blocked'))
    if 'detail' in fin['payload']:
        assert fin['payload']['detail'] == 'motivo concreto'
    c.comprobar()


async def test_failed_sin_resumen_lleva_un_detalle_igualmente():
    c = Cliente()
    await abrir(c)
    cierre = await cerrar(c, 'failed', resumen='')
    assert cierre[-1]['payload']['detail']
    c.comprobar()


async def test_el_cierre_lleva_el_uso_del_turno_y_su_resumen_en_la_extension():
    c = Cliente()
    await abrir(c)
    cierre = await cerrar(c, coste_turno=0.0123, tokens_turno=321, turno='t1', cierre={'resumen': 'ok'})
    uso = next(m for m in cierre if m['type'] == 'usage')
    assert uso['payload']['cost'] == {'amount': 0.0123, 'currency': 'USD'}
    assert uso['payload']['alfalfa'] == {'turn_tokens': 321}
    extra = next(m for m in cierre if m['type'] == 'alfalfa.closing')
    assert extra['payload']['closing'] == {'summary': 'ok'} and extra['payload']['turn'] == 't1'
    c.comprobar()


# -- herramientas (§4.4) ---------------------------------------------------
def test_los_argumentos_salen_del_literal_que_escribe_el_motor():
    args = {'path': 'a.py', 'old': "it's", 'n': 3, 'ok': True, 'nada': None}
    assert argumentos_de('Inicio edit_file · ' + str(args), {}) == (args, False)


def test_si_el_literal_se_recorto_se_rehacen_los_datos_de_la_app():
    datos = datos_de_inicio('run_command', {'argv': ['git', 'status'], 'purpose': 'Ver', 'cwd': 'sub'})
    largo = 'Inicio run_command · ' + str({'argv': ['git', 'status'], 'x': 'y' * 2000})[:1000]
    assert argumentos_de(largo, datos) == ({'command': datos['orden'], 'cwd': 'sub'}, True)


def test_lo_que_no_es_json_no_se_cuela_en_los_argumentos():
    assert argumentos_de('Inicio t · ' + str({'a': float('nan')}), {'objetivo': 'x'}) == ({'target': 'x'}, True)
    assert argumentos_de('Inicio t · ' + str({1: 'a'}), {}) == ({'1': 'a'}, False)    # las claves de JSON son texto
    assert argumentos_de('Inicio t · [1, 2]', {}) == ({}, True)                        # no es un objeto
    assert argumentos_de('Inicio t · ' + str({'a': (1, 2)}), {}) == ({'a': [1, 2]}, False)


async def test_una_herramienta_del_agente_tiene_su_inicio_y_su_fin():
    c = Cliente()
    run = await abrir(c)
    inicio = herramienta(c, 'read_file', {'path': 'README.md'})
    assert [m['type'] for m in inicio] == ['tool_start']
    p = inicio[0]['payload']
    assert p['run_id'] == run and p['name'] == 'read_file' and p['side'] == 'agent'
    assert p['arguments'] == {'path': 'README.md'} and p['title']
    fin = fin_de_herramienta(c, 'read_file', {'content': 'hola'})
    assert fin[0]['type'] == 'tool_end' and fin[0]['payload']['tool_call_id'] == inicio[0]['id']
    assert fin[0]['payload']['status'] == 'success' and 'hola' in fin[0]['payload']['output'][0]['text']
    await cerrar(c)
    c.comprobar()


@pytest.mark.parametrize('resultado, estado', [
    ({'error': 'No existe'}, 'error'), ({'exit_code': 2, 'output': ''}, 'error'), ({'exit_code': 0}, 'success'),
    # `rg` sin coincidencias sale con 1 y el motor no lo cuenta como fallo.
    ({'exit_code': 1, 'classification': {'disposition': 'success'}}, 'success'),
    ({'exit_code': 0, 'classification': {'disposition': 'failure'}}, 'error')])
async def test_el_estado_de_la_herramienta_sigue_la_clasificacion_del_motor(resultado, estado):
    c = Cliente()
    await abrir(c)
    herramienta(c, 'run_command', {'argv': ['rg', 'x'], 'purpose': 'Buscar'})
    fin = fin_de_herramienta(c, 'run_command', resultado)
    assert fin[0]['payload']['status'] == estado
    if 'error' in resultado:
        assert fin[0]['payload']['summary'] == resultado['error']
    await cerrar(c)
    c.comprobar()


async def test_la_salida_enorme_de_una_herramienta_se_recorta_y_un_fin_sin_inicio_se_ignora():
    c = Cliente()
    await abrir(c)
    assert fin_de_herramienta(c, 'read_file', {'content': 'x'}) == []
    herramienta(c, 'read_file', {'path': 'a'})
    fin = fin_de_herramienta(c, 'read_file', {'content': 'y' * 200000})
    assert len(fin[0]['payload']['output'][0]['text']) == 60000
    await cerrar(c)
    c.comprobar()


async def test_una_herramienta_cortada_por_el_turno_se_cierra_cancelled_antes_del_run_finished():
    c = Cliente()
    await abrir(c)
    inicio = herramienta(c, 'run_command', {'argv': ['sleep', '9'], 'purpose': 'Esperar'})
    cierre = await cerrar(c, 'cortado')
    assert [m['type'] for m in cierre][-2:] == ['tool_end', 'run_finished']
    assert cierre[-2]['payload'] == {'tool_call_id': inicio[0]['id'], 'status': 'cancelled'}
    c.comprobar()


async def test_un_fin_solo_cierra_la_herramienta_del_mismo_nombre_y_el_mas_antiguo_primero():
    c = Cliente()
    await abrir(c)
    a = herramienta(c, 'read_file', {'path': 'a'})[0]
    b = herramienta(c, 'read_file', {'path': 'b'})[0]
    c1 = herramienta(c, 'search', {'query': 'x'})[0]
    assert fin_de_herramienta(c, 'read_file', {'content': '1'})[0]['payload']['tool_call_id'] == a['id']
    assert fin_de_herramienta(c, 'search', {'matches': []})[0]['payload']['tool_call_id'] == c1['id']
    assert fin_de_herramienta(c, 'read_file', {'content': '2'})[0]['payload']['tool_call_id'] == b['id']
    await cerrar(c)
    c.comprobar()


# -- perfil work (§7.3) ----------------------------------------------------
async def test_el_plan_es_plan_updated_con_el_perfil_work_y_una_extension_sin_el():
    c = Cliente()
    run = await abrir(c)
    pasos = [{'paso': 'Leer', 'estado': 'completed'}, {'paso': 'Editar', 'estado': 'in_progress'},
             {'paso': 'Probar', 'estado': 'rara'}]
    c.t.emit('plan', pasos=pasos, explicacion='Voy por partes')
    plan = c.drenar()[0]
    assert plan['type'] == 'plan_updated' and plan['payload']['run_id'] == run
    assert plan['payload']['steps'] == [{'text': 'Leer', 'status': 'completed'},
                                        {'text': 'Editar', 'status': 'in_progress'},
                                        {'text': 'Probar', 'status': 'pending'}]
    assert plan['payload']['alfalfa'] == {'explanation': 'Voy por partes'}
    await cerrar(c)
    c.comprobar()

    sin = Cliente(perfiles=())
    await abrir(sin)
    sin.t.emit('plan', pasos=pasos)
    assert [m['type'] for m in sin.drenar()] == ['alfalfa.plan']
    # Al reanudar, el plan llega antes de cualquier ejecución: nunca es un plan_updated.
    antes = Cliente()
    antes.saludar()
    antes.t.emit('plan', pasos=pasos)
    assert [m['type'] for m in antes.drenar()] == ['alfalfa.plan']


async def test_los_cambios_del_turno_salen_como_diff_updated():
    c = Cliente()
    run = await abrir(c)
    herramienta(c, 'edit_file', {'path': 'a.py'})
    fin_de_herramienta(c, 'edit_file', {'ok': True})
    parche = ('--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,3 @@\n-viejo\n+nuevo\n+otro\n contexto\n'
              '--- a/b.txt\n+++ b/b.txt\n@@ -1 +1 @@\n-uno\n+dos\n')
    c.t._diff_del_turno = lambda: parche
    cierre = await cerrar(c)
    diff = next(m for m in cierre if m['type'] == 'diff_updated')
    assert diff['payload']['run_id'] == run and diff['payload']['patch'] == parche
    assert diff['payload']['files'] == [{'path': 'a.py', 'added': 2, 'removed': 1},
                                        {'path': 'b.txt', 'added': 1, 'removed': 1}]
    c.comprobar()


async def test_sin_cambios_no_hay_diff_ni_se_calcula():
    c = Cliente()
    await abrir(c)

    def no_debe_llamarse():
        raise AssertionError('no hubo ediciones')
    c.t._diff_del_turno = no_debe_llamarse
    cierre = await cerrar(c)
    assert not [m for m in cierre if m['type'] == 'diff_updated']


def test_los_archivos_de_un_diff_unificado():
    assert archivos_del_diff('') == []
    # Una línea añadida que empieza por `++` no es la cabecera del archivo siguiente.
    assert archivos_del_diff('--- a/x\n+++ b/x\n@@ -1 +1,2 @@\n-tres\n+uno\n+++dos\n'
                             '--- a/y\n+++ b/y\n@@ -0,0 +1 @@\n+z\n\\ No newline at end of file\n'
                             ) == [{'path': 'x', 'added': 2, 'removed': 1}, {'path': 'y', 'added': 1, 'removed': 0}]


# -- uso, errores y cierre (§4.7, §4.8) ------------------------------------
async def test_el_contexto_es_usage_y_el_detalle_va_en_la_extension():
    c = Cliente()
    run = await abrir(c)
    c.t.emit('contexto', tokens=28400, limite=262144, desglose={'sistema': 10})
    uso, extra = c.drenar()
    assert uso['type'] == 'usage' and uso['payload'] == {'run_id': run, 'context_tokens': 28400,
                                                         'context_limit': 262144}
    assert extra['type'] == 'alfalfa.context' and extra['payload']['breakdown'] == {'system': 10}
    c.t.emit('contexto', tokens=1, limite=0)         # un límite desconocido no es un límite válido
    assert c.drenar()[0]['payload'] == {'run_id': run, 'context_tokens': 1}
    c.t.emit('uso_sesion', tokens=500, coste=0.5)
    gasto = c.drenar()[0]
    assert gasto['type'] == 'usage' and gasto['payload']['cost'] == {'amount': 0.5, 'currency': 'USD'}
    assert gasto['payload']['alfalfa'] == {'total_tokens': 500} and 'run_id' not in gasto['payload']
    await cerrar(c)
    c.comprobar()


async def test_el_usage_sin_ejecucion_se_refiere_a_la_sesion():
    c = Cliente()
    c.saludar()
    c.t.emit('contexto', tokens=7, limite=100)
    assert c.drenar()[0]['payload'] == {'context_tokens': 7, 'context_limit': 100}
    c.comprobar()


async def test_los_errores_del_motor_son_error_con_codigo_propio_o_del_nucleo():
    c = Cliente()
    c.saludar()
    c.t.emit('error', texto='No se pudo iniciar o ejecutar la sesión.')
    c.t.emit('error', texto='Línea demasiado larga.')
    c.t.emit('error', texto='Sin visión', motivo='sin_vision', rechazado={'texto': 'hola'})
    uno, dos, tres = c.drenar()
    assert uno['payload']['code'] == 'alfalfa.engine_error' and uno['payload']['message'].startswith('No se pudo')
    assert dos['payload']['code'] == 'message_too_large'
    assert tres['payload']['alfalfa'] == {'reason': 'sin_vision', 'rejected': {'text': 'hola'}}
    c.comprobar()


async def test_fin_cierra_lo_que_siga_abierto_y_manda_close():
    c = Cliente()
    run = await abrir(c)
    herramienta(c, 'run_command', {'argv': ['sleep', '9'], 'purpose': 'x'})
    c.t.emit('fin', estado='interrupted', resumen='Cierre forzado del motor.', codigo_salida=130, tokens=3)
    cierre = c.drenar()
    assert [m['type'] for m in cierre] == ['alfalfa.closing', 'tool_end', 'run_finished', 'close']
    fin = cierre[2]
    assert fin['payload'] == {'run_id': run, 'outcome': 'interrupted'}
    cierre_sesion = cierre[-1]
    assert cierre_sesion['payload']['reason'] == 'Cierre forzado del motor.'
    assert cierre_sesion['payload']['alfalfa']['exit_code'] == 130
    # Tras close se descarta todo.
    c.t.emit('evento', rol='agent', texto='tarde', hora=1.0)
    assert c.drenar() == []
    c.comprobar()


# -- extensiones (§8) ------------------------------------------------------
@pytest.mark.parametrize('tipo, extension', [
    ('presupuesto', 'alfalfa.budget'), ('procesos', 'alfalfa.processes'), ('despierto', 'alfalfa.awake'),
    ('cuota', 'alfalfa.quota'), ('preparando', 'alfalfa.preparing'), ('encargos', 'alfalfa.jobs'),
    ('revision_codigo', 'alfalfa.code_review'), ('aprendizaje_propuesto', 'alfalfa.learning_proposed'),
    ('quitado_de_cola', 'alfalfa.dequeued'), ('sesion', 'alfalfa.session'),
    ('algo_nuevo_de_v2', 'alfalfa.algo_nuevo_de_v2')])
async def test_lo_propio_de_alfalfa_va_como_extension_con_los_campos_de_v2(tipo, extension):
    c = Cliente()
    c.saludar()
    c.t.emit(tipo, valor=3, version=2)
    m = c.drenar()[0]
    assert m['type'] == extension and m['payload'] == {'valor': 3}
    c.comprobar()


async def test_sin_la_extension_negociada_no_sale_ninguna_extension():
    c = Cliente(extensiones=())
    run = await abrir(c)
    for tipo in ('presupuesto', 'procesos', 'sesion', 'cuota'):
        c.t.emit(tipo, valor=1)
    c.t.emit('contexto', tokens=5, limite=10, desglose={'a': 1})
    assert [m['type'] for m in c.drenar()] == ['usage']
    cierre = await cerrar(c, cierre={'resumen': 'x'}, turno='t')
    assert not [m for m in cierre if '.' in m['type']]
    assert cierre[-1]['payload'] == {'run_id': run, 'outcome': 'success'}
    c.comprobar()


# -- subejecuciones (§4.9) -------------------------------------------------
def subagentes(*filas):
    return [dict(id=i, nombre=n, tipo=t, estado=e, error=err, informe='') for i, n, t, e, err in filas]


async def test_dos_subagentes_en_paralelo_son_dos_subejecuciones_con_su_propio_run_id():
    c = Cliente()
    raiz = await abrir(c)
    c.t.emit('subagentes', subagentes=subagentes(('a1', 'Revisar A', 'explorador', 'trabajando', ''),
                                                  ('a2', 'Revisar B', 'general', 'trabajando', '')))
    inicios = [m for m in c.drenar() if m['type'] == 'run_started']
    assert [m['payload']['parent_run_id'] for m in inicios] == [raiz, raiz]
    assert inicios[0]['payload']['agent'] == {'name': 'explorador', 'description': 'Revisar A'}
    run1, run2 = inicios[0]['id'], inicios[1]['id']
    # Sus mensajes y herramientas se intercalan y cada uno lleva su run_id.
    c.t.emit('subagente_evento', agente='a1', rol='tool', texto="Inicio read_file · {'path': 'x'}", hora=1.0)
    c.t.emit('subagente_evento', agente='a2', rol='agent', texto='Encontré dos cosas', hora=1.0)
    c.t.emit('subagente_evento', agente='a1', rol='tool', texto='read_file\n{"content": "hola"}', hora=2.0)
    c.t.emit('subagente_evento', agente='a1', rol='agent', texto='Informe de A', hora=3.0)
    salida = c.drenar()
    tool = [m for m in salida if m['type'] == 'tool_start'][0]
    assert tool['payload']['run_id'] == run1 and tool['payload']['arguments'] == {'path': 'x'}
    assert [m['payload']['run_id'] for m in salida if m['type'] == 'message_start'] == [run2, run1]
    assert [m for m in salida if m['type'] == 'tool_end'][0]['payload']['tool_call_id'] == tool['id']
    c.t.emit('subagentes', subagentes=subagentes(('a1', 'Revisar A', 'explorador', 'terminado', ''),
                                                  ('a2', 'Revisar B', 'general', 'fallido', 'Se cayó')))
    cierres = [m for m in c.drenar() if m['type'] == 'run_finished']
    assert [(m['payload']['run_id'], m['payload']['outcome']) for m in cierres] == [(run1, 'success'),
                                                                                    (run2, 'failed')]
    assert cierres[1]['payload']['detail'] == 'Se cayó'
    await cerrar(c)
    c.comprobar()


async def test_cortar_el_turno_corta_a_los_subagentes_de_las_hojas_a_la_raiz():
    c = Cliente()
    raiz = await abrir(c)
    c.t.emit('subagentes', subagentes=subagentes(('a1', 'A', 'general', 'trabajando', '')))
    run1 = c.drenar()[0]['id']
    c.t.emit('subagente_evento', agente='a1', rol='tool', texto="Inicio run_command · {'argv': ['x']}", hora=1.0)
    herramienta(c, 'spawn_agent', {'message': 'haz A'})
    cierre = await cerrar(c, 'cortado')
    forma = [(m['type'], m['payload'].get('run_id')) for m in cierre if m['type'] in ('tool_end', 'run_finished')]
    assert forma[-1] == ('run_finished', raiz)
    assert ('run_finished', run1) in forma and forma.index(('run_finished', run1)) < forma.index(('run_finished', raiz))
    assert all(m['payload']['outcome'] == 'interrupted' for m in cierre if m['type'] == 'run_finished')
    c.comprobar()


async def test_un_subagente_que_vuelve_a_trabajar_abre_otra_invocacion():
    c = Cliente()
    await abrir(c)
    c.t.emit('subagentes', subagentes=subagentes(('a1', 'A', 'general', 'trabajando', '')))
    c.t.emit('subagentes', subagentes=subagentes(('a1', 'A', 'general', 'terminado', '')))
    c.t.emit('subagentes', subagentes=subagentes(('a1', 'A', 'general', 'trabajando', '')))
    c.t.emit('subagentes', subagentes=subagentes(('a1', 'A', 'general', 'cerrado', '')))
    runs = [m for m in c.drenar() if m['type'] == 'run_started']
    assert len(runs) == 2 and runs[0]['id'] != runs[1]['id']
    await cerrar(c)
    c.comprobar()


async def test_sin_ejecucion_raiz_no_nacen_subejecuciones():
    c = Cliente()
    c.saludar()
    c.t.emit('subagentes', subagentes=subagentes(('a1', 'A', 'general', 'trabajando', '')))
    assert [m['type'] for m in c.drenar()] == ['alfalfa.subagents']
    c.t.emit('subagente_evento', agente='a1', rol='agent', texto='hola', hora=1.0)
    assert c.drenar() == []


# -- ejecución en cierre (§4.2, 0.3) ---------------------------------------
async def test_una_ejecucion_en_cierre_no_empieza_trabajo_nuevo_pero_informa_y_termina():
    import asyncio
    c = Cliente(herramientas=('browser',))
    run = await abrir(c)
    herramienta(c, 'run_command', {'argv': ['rm', 'x'], 'purpose': 'p'})
    tarea = asyncio.create_task(c.t.approval('.', False)(dict(argv=['rm', 'x'], purpose='p')))
    await asyncio.sleep(0.02)
    c.t.stop()                                             # cancela la petición: la ejecución entra en cierre
    await asyncio.gather(tarea, return_exceptions=True)
    c.drenar()
    antes = len(c.log)
    c.t.emit('evento', rol='agent', texto='Sigo con otra cosa', hora=1.0)
    herramienta(c, 'read_file', {'path': 'a.py'})
    fin_de_herramienta(c, 'read_file', {'content': 'x'})
    otra = asyncio.create_task(c.t.approval('.', False)(dict(argv=['ls'], purpose='otra')))
    await asyncio.sleep(0.02)
    c.t.emit('subagentes', subagentes=[{'id': 'a1', 'nombre': 'B', 'tipo': 'general', 'estado': 'trabajando'}])
    c.t.emit('plan', pasos=[{'paso': 'x', 'estado': 'completed'}])
    c.drenar()
    nuevos = c.log[antes:]
    assert not {'message_start', 'tool_start', 'approval_request', 'run_started'} & set(m['type'] for m in nuevos)
    assert 'plan_updated' in [m['type'] for m in nuevos]       # informar sí vale
    with pytest.raises(ValueError):
        await asyncio.wait_for(otra, 1)                    # lo que pide permiso en cierre se niega
    await cerrar(c, 'interrupted')
    assert c.del_agente('run_finished')[-1]['payload'] == {'run_id': run, 'outcome': 'interrupted'}
    c.comprobar()


# -- subagentes lanzados por una herramienta (§4.9, 0.3) -------------------
def _fila(ident, estado):
    return {'id': ident, 'nombre': 'Revisor', 'tipo': 'explorador', 'estado': estado}


def _inicio(mensajes):
    return next(m for m in mensajes if m['type'] == 'tool_start')


async def test_la_subejecucion_lleva_el_tool_call_id_de_la_herramienta_que_la_lanzo():
    c = Cliente()
    run = await abrir(c)
    lanzar = _inicio(herramienta(c, 'spawn_agent', {'message': 'Revisa', 'background': True}))
    c.t.emit('subagentes', subagentes=[_fila('a1', 'trabajando')])
    sub = [m for m in c.drenar() if m['type'] == 'run_started'][0]
    assert sub['payload']['tool_call_id'] == lanzar['id'] and sub['payload']['parent_run_id'] == run
    # En segundo plano la herramienta vuelve ya con el id: cierra `success` y el subagente sigue.
    assert fin_de_herramienta(c, 'spawn_agent', {'agent': 'a1', 'status': 'trabajando'})[0]['payload']['status'] \
        == 'success'
    c.t.emit('subagente_evento', agente='a1', rol='agent', texto='Informe', hora=1.0)
    # Relanzarlo con `send_input` es otra invocación, y la lanza esa herramienta.
    c.t.emit('subagentes', subagentes=[_fila('a1', 'terminado')])
    otra = _inicio(herramienta(c, 'send_input', {'agent': 'a1', 'message': 'Sigue'}))
    c.t.emit('subagentes', subagentes=[_fila('a1', 'trabajando')])
    segunda = [m for m in c.drenar() if m['type'] == 'run_started'][0]
    assert segunda['payload']['tool_call_id'] == otra['id'] and segunda['id'] != sub['id']
    fin_de_herramienta(c, 'send_input', {'agent': 'a1'})
    c.t.emit('subagentes', subagentes=[_fila('a1', 'terminado')])
    # Uno al que el cliente escribe con `steer` (sin herramienta) no lleva tool_call_id.
    c.t.emit('subagentes', subagentes=[_fila('a1', 'trabajando')])
    tercera = [m for m in c.drenar() if m['type'] == 'run_started'][0]
    assert 'tool_call_id' not in tercera['payload']
    await cerrar(c)
    c.comprobar()


async def test_interrumpir_un_subagente_cancela_la_herramienta_que_lo_espera_y_la_madre_sigue():
    c = Cliente()
    run = await abrir(c)
    lanzar = _inicio(herramienta(c, 'spawn_agent', {'message': 'Revisa'}))
    c.t.emit('subagentes', subagentes=[_fila('a2', 'trabajando')])
    sub = [m for m in c.drenar() if m['type'] == 'run_started'][0]
    c.t.emit('subagentes', subagentes=[_fila('a2', 'cerrado')])
    cierre = c.drenar()
    cierres = [m['payload'] for m in cierre if m['type'] in ('run_finished', 'tool_end')]
    assert cierres == [{'run_id': sub['id'], 'outcome': 'interrupted'},
                       {'tool_call_id': lanzar['id'], 'status': 'cancelled'}]
    # El fin que el motor manda luego por esa herramienta ya no sale, y la madre sigue trabajando.
    assert fin_de_herramienta(c, 'spawn_agent', {'agent': 'a2', 'status': 'cerrado'}) == []
    c.t.emit('evento', rol='agent', texto='Sigo yo', hora=2.0)
    assert 'message_start' in [m['type'] for m in c.drenar()]
    await cerrar(c)
    assert c.del_agente('run_finished')[-1]['payload'] == {'run_id': run, 'outcome': 'success'}
    assert lanzar['id'] in [m['payload']['tool_call_id'] for m in c.del_agente('tool_end')]
    c.comprobar()
