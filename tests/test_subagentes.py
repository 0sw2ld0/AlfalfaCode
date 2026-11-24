"""Subagentes como spawn_agent de Codex y Agent de Claude Code.

TerminalManager y Transport reales; sólo se sustituye la inferencia
(NativeRuntime.request). El guion responde distinto al principal y a cada
subagente, según la tarea del runtime que pregunta."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from alfalfa import subagentes
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from tests.test_alfalfa_hooks import Sesion, llamada


def nombres_de_herramientas(payload):
    return {t['function']['name'] for t in payload.get('tools', [])}


def resultados(payload):
    return [json.loads(m['content']) for m in payload['messages'] if m['role'] == 'tool']


class Guion:
    """El principal sigue `principal`; cada subagente, `por_agente[sufijo]` (a1, a2…)."""

    def __init__(self, principal, por_agente):
        self.principal, self.por_agente = list(principal), {k: list(v) for k, v in por_agente.items()}
        self.payloads = {}

    def request(self):
        guion = self

        async def request(rt, client, endpoint, payload, **kwargs):
            if endpoint == '/api/show':
                return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
            quien = rt.task['id'].rsplit('-', 1)[1] if rt.task.get('subagente_de') else 'principal'
            guion.payloads.setdefault(quien, []).append(json.loads(json.dumps(payload)))
            pasos = guion.principal if quien == 'principal' else guion.por_agente.setdefault(quien, [])
            paso = pasos.pop(0) if pasos else {'role': 'assistant', 'content': 'Hecho.'}
            if callable(paso):
                paso = paso(payload)
                if asyncio.iscoroutine(paso):
                    paso = await paso
            return dict(message=paso, done=True, done_reason='stop', prompt_eval_count=900, eval_count=10)
        return request


def correr(sesion, guion, prompt='Usa subagentes.'):
    async def run():
        cliente = asyncio.create_task(sesion._responder())
        try:
            with patch.object(NativeRuntime, 'request', guion.request()):
                return await sesion.m.run(prompt)
        finally:
            cliente.cancel()
            await asyncio.gather(cliente, return_exceptions=True)
            await sesion.m.close_mcp()
    codigo = asyncio.run(asyncio.wait_for(run(), 60))
    sesion.m.close()
    return codigo


def test_spawn_agent_espera_el_informe_como_agent_de_claude(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, None)
    (s.root / 'notas.txt').write_text('La clave está en la línea dos.\nsegunda\n')
    guion = Guion(
        principal=[llamada('spawn_agent', {'message': 'Lee notas.txt y dime qué dice.', 'description': 'Leer notas',
                                           'agent_type': 'explorador'}),
                   {'role': 'assistant', 'content': 'El subagente dice que la clave está en la línea dos.'}],
        por_agente={'a1': [llamada('read_file', {'path': 'notas.txt'}),
                           {'role': 'assistant', 'content': 'notas.txt dice: «La clave está en la línea dos».'}]})
    assert correr(s, guion) == 0
    # El principal recibe el informe final del subagente como resultado de spawn_agent.
    informe = resultados(guion.payloads['principal'][1])[-1]
    assert informe['agent'] == 'a1' and informe['status'] == 'terminado' and informe['type'] == 'explorador'
    assert 'línea dos' in informe['result']
    # El explorador sólo lee, no lanza agentes ni cierra el turno del usuario.
    herramientas = nombres_de_herramientas(guion.payloads['a1'][0])
    assert {'read_file', 'search', 'list_files'} <= herramientas
    assert not herramientas & {'write_file', 'edit_file', 'run_command', 'apply_patch', 'spawn_agent', 'wrap_up',
                               'propose_session'}
    sistema = guion.payloads['a1'][0]['messages'][0]['content']
    assert 'Eres un subagente' in sistema and 'explorador de sólo lectura' in sistema
    # El principal sí tiene las herramientas de Codex, y el manual lista los tipos.
    principal = nombres_de_herramientas(guion.payloads['principal'][0])
    assert {'spawn_agent', 'wait_agent', 'send_input', 'close_agent', 'list_agents'} <= principal
    assert '- explorador:' in guion.payloads['principal'][0]['messages'][0]['content']
    # Sus pasos no entran en la conversación principal: van como subagente_evento.
    assert not any('read_file' in t for t in s.textos())
    pasos = s.eventos('subagente_evento')
    assert any(p['agente'] == 'a1' and p['rol'] == 'agent' and 'notas.txt dice' in p['texto'] for p in pasos)
    assert any(t.startswith('Subagente a1 (explorador') and 'empezó' in t for t in s.textos())
    assert any(t.startswith('Subagente a1 «Leer notas» terminado') for t in s.textos())
    lista = s.eventos('subagentes')[-1]['subagentes']
    assert lista[0]['id'] == 'a1' and lista[0]['estado'] == 'terminado' and lista[0]['pasos'] == 1
    registro = s.m.directory / 'subagentes' / 'a1.jsonl'
    assert 'notas.txt dice' in registro.read_text()


def test_en_segundo_plano_trabajan_a_la_vez_y_wait_agent_recoge_los_informes(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, None)
    empezados = set()
    ambos = asyncio.Event()

    def trabajar(nombre):
        async def paso(payload):
            empezados.add(nombre)
            if len(empezados) == 2:
                ambos.set()
            # Ninguno termina hasta que el otro empezó: sólo pasa si corren a la vez.
            await asyncio.wait_for(ambos.wait(), 10)
            return {'role': 'assistant', 'content': 'Informe de ' + nombre}
        return paso

    guion = Guion(
        principal=[{'role': 'assistant', 'content': '', 'tool_calls': [
                       {'function': {'name': 'spawn_agent', 'arguments': {
                           'message': 'Parte uno', 'description': 'Uno', 'background': True}}},
                       {'function': {'name': 'spawn_agent', 'arguments': {
                           'message': 'Parte dos', 'description': 'Dos', 'background': True}}}]},
                   llamada('wait_agent', {'timeout_seconds': 30}),
                   {'role': 'assistant', 'content': 'Junté los dos informes.'}],
        por_agente={'a1': [trabajar('a1')], 'a2': [trabajar('a2')]})
    assert correr(s, guion) == 0
    lanzados = resultados(guion.payloads['principal'][1])
    assert [r['status'] for r in lanzados] == ['trabajando', 'trabajando']
    esperado = resultados(guion.payloads['principal'][2])[-1]
    assert {a['agent']: a['result'] for a in esperado['agents']} == {'a1': 'Informe de a1', 'a2': 'Informe de a2'}


async def _hasta_que_acaben(sesion, paso):
    """Antes de dar `paso`, el principal espera a que todos sus subagentes hayan acabado."""
    for _ in range(500):
        agentes = sesion.m.subagentes.agentes.values()
        if agentes and not any(a.vivo for a in agentes):
            return paso
        await asyncio.sleep(0.01)
    raise AssertionError('los subagentes no acabaron')


def test_wait_agent_entrega_los_informes_de_los_que_ya_acabaron(tmp_path, monkeypatch):
    # Con Python 3.13 los subagentes solían acabar antes de que el principal
    # llamara a wait_agent, que respondía «No hay subagentes trabajando» y los
    # informes se perdían.
    s = Sesion(tmp_path, monkeypatch, None)
    guion = Guion(
        principal=[{'role': 'assistant', 'content': '', 'tool_calls': [
                       {'function': {'name': 'spawn_agent', 'arguments': {
                           'message': 'Parte uno', 'description': 'Uno', 'background': True}}},
                       {'function': {'name': 'spawn_agent', 'arguments': {
                           'message': 'Parte dos', 'description': 'Dos', 'background': True}}}]},
                   lambda payload: _hasta_que_acaben(s, llamada('wait_agent', {'timeout_seconds': 30})),
                   llamada('wait_agent', {'timeout_seconds': 30}),
                   {'role': 'assistant', 'content': 'Junté los dos informes.'}],
        por_agente={'a1': [{'role': 'assistant', 'content': 'Informe de a1'}],
                    'a2': [{'role': 'assistant', 'content': 'Informe de a2'}]})
    assert correr(s, guion) == 0
    recogido = resultados(guion.payloads['principal'][2])[-1]
    assert {a['agent']: a['result'] for a in recogido['agents']} == {'a1': 'Informe de a1', 'a2': 'Informe de a2'}
    # Ya entregados: la segunda espera no los repite.
    assert resultados(guion.payloads['principal'][3])[-1] == {'agents': [], 'note': 'No hay subagentes trabajando.'}
    # Ni se los vuelve a pasar al terminar el turno.
    assert len(guion.payloads['principal']) == 4


def test_al_terminar_el_turno_entrega_los_informes_que_nadie_recogio(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, None)
    guion = Guion(
        principal=[llamada('spawn_agent', {'message': 'Busca usos', 'background': True}),
                   lambda payload: _hasta_que_acaben(s, {'role': 'assistant', 'content': 'Reviso lo mío.'}),
                   {'role': 'assistant', 'content': 'Con el informe: hay tres usos.'}],
        por_agente={'a1': [{'role': 'assistant', 'content': 'Encontré tres usos.'}]})
    assert correr(s, guion) == 0
    assert len(guion.payloads['principal']) == 3
    aviso = guion.payloads['principal'][2]['messages'][-1]
    assert aviso['role'] == 'user' and 'Tus subagentes terminaron' in aviso['content']
    assert 'Encontré tres usos.' in aviso['content']
    # No había nadie trabajando: no dice que espera.
    assert not any(t.startswith('Esperando a que terminen los subagentes') for t in s.textos())


def test_el_turno_no_se_cierra_con_subagentes_trabajando(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, None)

    async def lento(payload):
        await asyncio.sleep(0.3)
        return {'role': 'assistant', 'content': 'Encontré tres usos.'}

    guion = Guion(
        principal=[llamada('spawn_agent', {'message': 'Busca usos', 'background': True}),
                   {'role': 'assistant', 'content': 'Mientras tanto, reviso lo mío.'},
                   {'role': 'assistant', 'content': 'Con el informe: hay tres usos.'}],
        por_agente={'a1': [lento]})
    assert correr(s, guion) == 0
    # Tras su primera respuesta final el principal recibe los informes y sigue.
    assert len(guion.payloads['principal']) == 3
    aviso = guion.payloads['principal'][2]['messages'][-1]
    assert aviso['role'] == 'user' and 'Tus subagentes terminaron' in aviso['content']
    assert 'Encontré tres usos.' in aviso['content']
    assert any(t.startswith('Esperando a que terminen los subagentes') for t in s.textos())


def test_un_subagente_no_lanza_otros(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, None)
    guion = Guion(
        principal=[llamada('spawn_agent', {'message': 'Intenta delegar'}), {'role': 'assistant', 'content': 'Ok.'}],
        por_agente={'a1': [llamada('spawn_agent', {'message': 'otro'}),
                           {'role': 'assistant', 'content': 'No pude delegar.'}]})
    assert correr(s, guion) == 0
    assert 'spawn_agent' not in nombres_de_herramientas(guion.payloads['a1'][0])
    rechazo = guion.payloads['a1'][1]['messages'][-1]['content']
    assert 'spawn_agent' in rechazo and ('deshabilitada' in rechazo or 'no configurada' in rechazo)
    assert len(s.m.subagentes.agentes) == 1


def test_tipos_del_usuario_con_el_formato_de_claude_code(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, None)
    carpeta = s.root / '.claude' / 'agents'
    carpeta.mkdir(parents=True)
    (carpeta / 'revisor.md').write_text('---\nname: revisor\ndescription: Revisa cambios con ojo crítico.\n'
                                        'tools: Read, Grep\nmodel: sonnet\n---\nBusca errores y dilos con su línea.\n')
    guion = Guion(
        principal=[llamada('spawn_agent', {'message': 'Revisa a.py', 'agent_type': 'revisor'}),
                   {'role': 'assistant', 'content': 'Revisado.'}],
        por_agente={'a1': [{'role': 'assistant', 'content': 'Sin errores.'}]})
    assert correr(s, guion) == 0
    assert '- revisor: Revisa cambios con ojo crítico.' in guion.payloads['principal'][0]['messages'][0]['content']
    assert nombres_de_herramientas(guion.payloads['a1'][0]) == {'read_file', 'search', 'update_plan'}
    assert 'Busca errores y dilos con su línea.' in guion.payloads['a1'][0]['messages'][0]['content']
    # «sonnet» no es un modelo de Ollama: hereda el de la sesión.
    assert guion.payloads['a1'][0]['model'] == guion.payloads['principal'][0]['model']


def test_tipo_desconocido_y_limite_de_agentes(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, None)
    monkeypatch.setattr(subagentes, 'MAX_VIVOS', 1)

    async def espera(payload):
        await asyncio.sleep(0.2)
        return {'role': 'assistant', 'content': 'listo'}

    guion = Guion(
        principal=[llamada('spawn_agent', {'message': 'x', 'agent_type': 'inventado'}),
                   llamada('spawn_agent', {'message': 'uno', 'background': True}),
                   llamada('spawn_agent', {'message': 'dos', 'background': True}),
                   {'role': 'assistant', 'content': 'fin'}, {'role': 'assistant', 'content': 'fin'}],
        por_agente={'a1': [espera]})
    correr(s, guion)
    textos = [m['content'] for p in guion.payloads['principal'] for m in p['messages'] if m['role'] == 'tool']
    assert any('No existe el tipo de agente «inventado»' in t and 'explorador' in t for t in textos)
    assert any('Ya hay 1 subagentes trabajando' in t for t in textos)


def test_los_permisos_dicen_que_subagente_pide(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, None)
    guion = Guion(
        principal=[llamada('spawn_agent', {'message': 'Borra build', 'description': 'Limpiar'}),
                   {'role': 'assistant', 'content': 'Ok.'}],
        por_agente={'a1': [llamada('run_command', {'argv': ['rm', '-rf', 'build'], 'purpose': 'Limpiar build'}),
                           {'role': 'assistant', 'content': 'Borrado.'}]})
    correr(s, guion)
    permisos = s.eventos('permiso')
    assert permisos and permisos[0]['proposito'].startswith('Subagente a1 «Limpiar»: ')


def test_send_input_da_otra_tarea_con_su_contexto_y_close_agent_lo_cierra(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, None)
    guion = Guion(
        principal=[llamada('spawn_agent', {'message': 'Cuenta hasta tres'}),
                   llamada('send_input', {'agent': 'a1', 'message': 'Ahora hasta cuatro'}),
                   llamada('close_agent', {'agent': 'a1'}),
                   llamada('send_input', {'agent': 'a1', 'message': 'otra'}),
                   {'role': 'assistant', 'content': 'Listo.'}],
        por_agente={'a1': [{'role': 'assistant', 'content': '1, 2, 3'},
                           {'role': 'assistant', 'content': '1, 2, 3, 4'}]})
    assert correr(s, guion) == 0
    segundo = guion.payloads['a1'][1]['messages']
    assert any(m['role'] == 'assistant' and m['content'] == '1, 2, 3' for m in segundo)  # conserva lo anterior
    tool = [json.loads(m['content']) for m in guion.payloads['principal'][2]['messages'] if m['role'] == 'tool']
    assert tool[-1]['result'] == '1, 2, 3, 4'
    cerrado = [json.loads(m['content']) for m in guion.payloads['principal'][3]['messages'] if m['role'] == 'tool']
    assert cerrado[-1] == {'agent': 'a1', 'previous_status': 'terminado', 'status': 'cerrado'}
    final = guion.payloads['principal'][4]['messages'][-1]['content']
    assert 'está cerrado' in final


def test_interrumpir_el_turno_detiene_a_los_subagentes(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, None)
    empezado = asyncio.Event()

    async def eterno(payload):
        empezado.set()
        await asyncio.sleep(3600)

    async def esperar_y_cortar(payload):
        await asyncio.sleep(3600)

    guion = Guion(principal=[llamada('spawn_agent', {'message': 'para siempre', 'background': True}),
                             esperar_y_cortar],
                  por_agente={'a1': [eterno]})

    async def run():
        with patch.object(NativeRuntime, 'request', guion.request()):
            turno = asyncio.create_task(s.m.run('x'))
            await asyncio.wait_for(empezado.wait(), 10)
            turno.cancel()
            return await turno
    assert asyncio.run(asyncio.wait_for(run(), 30)) == 130
    s.m.close()
    sub = s.m.subagentes.agentes['a1']
    assert sub.estado == 'interrumpido' and sub.trabajo.done()


def test_cabecera_y_equivalencias():
    datos, cuerpo = subagentes._cabecera('---\nname: x\ntools: [Read, Bash, Edit]\n---\nHola\n')
    assert datos == {'name': 'x', 'tools': '[Read, Bash, Edit]'} and cuerpo == 'Hola'
    assert subagentes._herramientas(datos['tools']) == ['read_file', 'run_command', 'edit_file']
    assert subagentes._cabecera('sin cabecera') == ({}, 'sin cabecera')


def test_lluvia_alterna_participantes_y_cierra_al_agotar_el_limite(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, None)
    s.m.task['lluvia_limite'] = 4
    s.m.read_only = True
    guion = Guion(
        principal=[
            llamada('spawn_agent', {'message': 'Propón dos ideas.', 'background': True}),
            llamada('spawn_agent', {'message': 'Evalúa la propuesta A.'}),
            llamada('send_input', {'agent': 'a1', 'message': 'Revisa tu propuesta con el contraste.'}),
            llamada('send_input', {'agent': 'a1', 'message': 'Formula el acuerdo final.'}),
            llamada('send_input', {'agent': 'a2', 'message': 'Intenta una quinta intervención.'}),
            {'role': 'assistant', 'content': 'Acuerdo: prototipo de la opción A.'},
        ],
        por_agente={
            'a1': [{'role': 'assistant', 'content': 'Propongo A o B.'},
                   {'role': 'assistant', 'content': 'A con la variante sugerida.'}],
            'a2': [{'role': 'assistant', 'content': 'Prefiero A con un ajuste.'},
                   {'role': 'assistant', 'content': 'De acuerdo con A.'}],
        },
    )

    assert correr(s, guion, '¿Cómo probamos el diseño?') == 0

    assert s.m.task['lluvia_usados'] == 4 and s.m.task['lluvia_cerrada'] is True
    assert [(a.nombre, a.turnos) for a in s.m.subagentes.agentes.values()] == [('Propuesta', 2), ('Contraste', 2)]
    assert 'última intervención' in guion.payloads['a2'][1]['messages'][-1]['content']
    assert not nombres_de_herramientas(guion.payloads['principal'][4])
    error = resultados(guion.payloads['principal'][5])[-1]
    assert 'no está disponible' in error['error']
    assert 'Acuerdo: prototipo de la opción A.' in s.textos()
    assert '**Contraste**\n\nDe acuerdo con A.' in s.textos()
    assert all(not nombres_de_herramientas(p) for p in guion.payloads['a1'] + guion.payloads['a2'])


def test_lluvia_puede_cerrar_pronto_si_ya_hay_acuerdo(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, None)
    s.m.task['lluvia_limite'] = 6
    s.m.read_only = True
    guion = Guion(
        principal=[
            llamada('spawn_agent', {'message': 'Propón una idea.'}),
            llamada('spawn_agent', {'message': 'Contrasta la idea.'}),
            {'role': 'assistant', 'content': 'Ya hay acuerdo.'},
        ],
        por_agente={
            'a1': [{'role': 'assistant', 'content': 'Probemos A.'}],
            'a2': [{'role': 'assistant', 'content': 'De acuerdo con A.'}],
        },
    )

    assert correr(s, guion) == 0
    assert s.m.task['lluvia_usados'] == 2 and s.m.task['lluvia_cerrada'] is True
    assert 'Ya hay acuerdo.' in s.textos()


def test_lluvia_acota_al_moderador_si_insiste_en_usar_herramientas_ajenas(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, None)
    s.m.task['lluvia_limite'] = 2
    guion = Guion([llamada('read_file', {'path': f'{i}.txt'}) for i in range(12)], {})

    assert correr(s, guion) != 0
    # Sin pasos para invitar, se le pide una síntesis sin herramientas en vez de cortar la sala.
    assert len(guion.payloads['principal']) == 6
    assert not nombres_de_herramientas(guion.payloads['principal'][-1])
    assert 'Cierra con una síntesis' in guion.payloads['principal'][-1]['messages'][-1]['content']
    assert 'La sala se interrumpió' not in s.m.task.get('summary', '')
    assert not s.m.subagentes.lista()


def test_lluvia_acepta_la_sintesis_si_el_moderador_insiste_en_cerrar(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, None)
    s.m.task['lluvia_limite'] = 6
    s.m.read_only = True
    guion = Guion(
        principal=[
            llamada('spawn_agent', {'message': 'Propón una idea.'}),
            {'role': 'assistant', 'content': 'Cierre 1.'},
            {'role': 'assistant', 'content': 'Cierre 2.'},
            {'role': 'assistant', 'content': 'Cierre 3: síntesis con un solo aporte.'},
        ],
        por_agente={'a1': [{'role': 'assistant', 'content': 'Probemos A.'}]},
    )

    assert correr(s, guion) == 0
    # Dos descartes avisados y la tercera respuesta se acepta, sin herramientas.
    assert 'Cierre 1.' not in s.textos() and 'Cierre 2.' not in s.textos()
    assert 'Cierre 3: síntesis con un solo aporte.' in s.textos()
    assert sum('quiso cerrar antes de que hablaran todos' in t for t in s.textos()) == 2
    assert not nombres_de_herramientas(guion.payloads['principal'][-1])
    assert 'No todos los participantes' in guion.payloads['principal'][-1]['messages'][-1]['content']
    assert s.m.task['lluvia_usados'] == 1 and s.m.task['lluvia_cerrada'] is True


def test_lluvia_devuelve_el_turno_de_un_participante_que_no_llega_a_existir(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, None)
    s.m.task['lluvia_limite'] = 4
    s.m.read_only = True
    s.m.task['lluvia_participantes'] = [
        {'model': 'modelo-uno', 'effort': 'low'},
        {'model': 'claude-code/opus', 'effort': 'low'},   # validar_modelo lo rechaza al lanzarlo
    ]
    guion = Guion(
        principal=[
            llamada('spawn_agent', {'message': 'Propón una idea.'}),
            llamada('spawn_agent', {'message': 'Contrasta la idea.'}),
            {'role': 'assistant', 'content': 'Cierre 1.'},
            {'role': 'assistant', 'content': 'Cierre 2.'},
            {'role': 'assistant', 'content': 'Síntesis con lo recibido.'},
        ],
        por_agente={'a1': [{'role': 'assistant', 'content': 'Probemos A.'}]},
    )

    assert correr(s, guion) == 0
    # El turno del que no pudo lanzarse no se gasta y la sala sigue cuadrada: el moderador
    # puede volver a invitar y, si insiste en cerrar, se acepta la síntesis.
    assert s.m.task['lluvia_usados'] == 1 and len(s.m.subagentes.lista()) == 1
    assert 'spawn_agent' in nombres_de_herramientas(guion.payloads['principal'][2])
    assert 'Síntesis con lo recibido.' in s.textos()
    assert s.m.task['lluvia_cerrada'] is True


def test_lluvia_respeta_modelos_esfuerzos_y_un_tercer_participante(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, None)
    s.m.task['lluvia_limite'] = 4
    s.m.task['lluvia_participantes'] = [
        {'model': 'modelo-uno', 'effort': 'low'},
        {'model': 'modelo-dos', 'effort': 'high'},
        {'model': 'modelo-tres', 'effort': 'medium'},
    ]
    (s.root / 'AGENTS.md').write_text('# Contenido del proyecto que no debe llegar a la sala')
    guion = Guion(
        principal=[
            llamada('spawn_agent', {'message': 'Propón la primera idea.', 'model': 'sustituto'}),
            llamada('spawn_agent', {'message': 'Contrasta la idea.'}),
            {'role': 'assistant', 'content': 'Cierre prematuro: los dos primeros están de acuerdo.'},
            llamada('spawn_agent', {'message': 'Evalúa la viabilidad.'}),
            {'role': 'assistant', 'content': 'Decisión registrada.'},
        ],
        por_agente={},
    )

    assert correr(s, guion) == 0

    assert [(a.nombre, a.task['model'], a.task['effort'], a.turnos)
            for a in s.m.subagentes.agentes.values()] == [
        ('Propuesta', 'modelo-uno', 'low', 1),
        ('Contraste', 'modelo-dos', 'high', 1),
        ('Participante 3', 'modelo-tres', 'medium', 1),
    ]
    assert 'Cierre prematuro' not in s.textos()
    assert 'Cierre prematuro' not in str(guion.payloads)
    assert 'participantes sin primer turno (1)' in guion.payloads['principal'][3]['messages'][-1]['content']
    assert 'Decisión registrada.' in s.textos()
    assert s.m.task['lluvia_usados'] == 3 and s.m.task['lluvia_cerrada'] is True
    assert 'que no debe llegar a la sala' not in str(guion.payloads)


def test_lluvia_comparte_solo_la_referencia_elegida_con_cada_participante(tmp_path, monkeypatch):
    from alfalfa.adjuntos import con_etiquetas

    s = Sesion(tmp_path, monkeypatch, None)
    s.m.task['lluvia_limite'] = 2
    s.m.read_only = True
    referencia = s.root / 'idea.md'
    referencia.write_text('# Propuesta elegida para la sesión\n')
    otro = s.root / 'privado.txt'
    otro.write_text('Contenido privado sin adjuntar.')
    guion = Guion(
        principal=[
            llamada('spawn_agent', {'message': con_etiquetas('Considera la idea.', [str(otro)])}),
            llamada('spawn_agent', {'message': 'Contrasta la propuesta.'}),
            {'role': 'assistant', 'content': 'Síntesis.'},
        ],
        por_agente={},
    )

    assert correr(s, guion, con_etiquetas('Evalúa esta idea.', [str(referencia)])) == 0

    for actor in ('principal', 'a1', 'a2'):
        mensajes = str(guion.payloads[actor][0]['messages'])
        assert 'Propuesta elegida para la sesión' in mensajes
        assert 'Contenido privado sin adjuntar' not in mensajes
        assert str(otro) not in mensajes



@pytest.mark.parametrize('approved', [True, False])
def test_prototype_only_offers_images_and_requires_approval(tmp_path, monkeypatch, approved):
    from alfalfa.proveedores.nativo import imagenes
    from tests.test_generar_imagen import PNG

    s = Sesion(tmp_path, monkeypatch, None, respuesta=approved, prototipo=True)
    monkeypatch.setattr(imagenes, 'configurar', lambda: {'proveedor': 'codex'})
    monkeypatch.setattr(imagenes.imagenes_codex, 'ejecutable', lambda: '/test/codex')
    generate = AsyncMock(return_value=PNG)
    monkeypatch.setattr(imagenes.imagenes_codex, 'generar', generate)

    def draft_prompt(payload):
        assert nombres_de_herramientas(payload) == {'generate_image'}
        assert 'Escribe tú el prompt visual' in payload['messages'][0]['content']
        assert 'Validador de pedidos' in payload['messages'][-1]['content']
        return llamada('write_file', {'path': 'unrelated.txt', 'content': 'No debe escribirse.'})

    visual_prompt = 'Pantalla del validador con una tabla de pedidos y una acción de aprobar.'
    script = Guion(
        principal=[draft_prompt,
                   llamada('generate_image', {'prompt': visual_prompt, 'path': 'mockup.png'}),
                   {'role': 'assistant', 'content': 'Terminé el encargo.'}],
        por_agente={},
    )
    correr(s, script, 'Idea original: Validador de pedidos. Síntesis: mostrar una tabla para aprobar.')
    assert not (s.root / 'unrelated.txt').exists()
    assert 'sólo puede generar imágenes' in resultados(script.payloads['principal'][1])[0]['error']
    [permission] = s.eventos('permiso')
    assert permission['motivo'] == 'generar_imagen'
    assert permission['argv'][-1] == visual_prompt
    if approved:
        assert (s.root / 'mockup.png').read_bytes() == PNG
        generate.assert_awaited_once()
    else:
        assert not (s.root / 'mockup.png').exists()
        generate.assert_not_awaited()


@pytest.mark.parametrize('modelo', ['claude-code/haiku', 'claude-code/sonnet', 'claude-code/opus'])
def test_lluvia_rechaza_el_cli_de_claude_code(modelo):
    from alfalfa.lluvia import validar

    participantes = [
        {'model': 'anthropic/claude-sonnet-5-5', 'effort': 'high'},
        {'model': modelo, 'effort': 'low'},
    ]
    with pytest.raises(ValueError, match='Elige un modelo del proveedor Anthropic'):
        validar(4, participantes)
