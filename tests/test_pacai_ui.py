"""Perfil `ui` de PACAI en AlfalfaCode: negociación, pantallas del modelo, acciones del cliente y el prompt."""
import asyncio

from alfalfa.pacai.ui import Catalogo, Vistas, accion_para_el_modelo, errores_de, partir
from tests.pacai_comun import Cliente, abrir, cerrar

PANTALLA = '''Tienes 2 tickets abiertos:

```pacai-ui view=tickets
{"id":"root","component":"Stack","children":["titulo","nuevo"]}
{"id":"titulo","component":"Text","props":{"text":"2 tickets abiertos","style":"title"}}
{"id":"nuevo","component":"Button","props":{"label":"Crear"},"events":{"press":{"send":"crear_ticket"}}}
{"state":{"priority":"medium"}}
```

¿Creo otro?'''


def estructurados(mensajes, tipo=None):
    return [(m['type'], b['data']) for m in mensajes if tipo is None or m['type'] == tipo
            for b in m['payload'].get('blocks', []) if b.get('type') == 'structured']


# -- piezas sueltas -----------------------------------------------------------------------------------
def test_partir_separa_texto_y_vallas():
    trozos = partir(PANTALLA)
    assert [t[0] for t in trozos] == ['text', 'ui', 'text']
    assert trozos[1][1] == {'view': 'tickets'} and len(trozos[1][2]) == 4
    # Una valla sin cerrar (respuesta cortada) también cuenta.
    assert partir('```pacai-ui view=a placement=latest\n{"id":"root","component":"Stack"}')[0][:2] == \
        ('ui', {'view': 'a', 'placement': 'latest'})
    # Otras vallas son texto.
    assert partir('```python\nprint(1)\n```') == [('text', '```python\nprint(1)\n```')]


def test_vistas_aplica_patch_rechaza_lo_invalido_y_hace_la_foto():
    vistas = Vistas(Catalogo())
    data, errores = vistas.aplicar({'view': 'v'}, [
        '{"id":"root","component":"Stack","children":["t"]}',
        '{"id":"t","component":"Text","props":{"text":"hola"}}',
        '{"id":"x","component":"Inventado"}',
        '{"id":"b","component":"Button","props":{"label":"A"},"events":{"press":{"open_url":"http://x"}}}',
        'no es json',
    ])
    assert [n['id'] for n in data['nodes']] == ['root', 't'] and data['rev'] == 1 and data['op'] == 'patch'
    assert len(errores) == 3 and 'Inventado' in errores[0]
    assert errores_de('update', data) == []
    data, _ = vistas.aplicar({'view': 'v'}, ['{"id":"t","component":"Text","props":{"text":"adiós"}}',
                                             '{"remove":["sobra"]}'])
    assert data['rev'] == 2 and data['remove'] == ['sobra']
    foto = vistas.foto('v')
    assert foto['op'] == 'replace' and foto['rev'] == 3 and 'adiós' in foto['alt']
    assert errores_de('update', foto) == []


def test_el_catalogo_del_cliente_manda():
    propio = {'props': {'type': 'object', 'properties': {'title': {'type': 'string'}}, 'required': ['title'],
                        'additionalProperties': False}, 'events': ['press']}
    cat = Catalogo.de({'version': '1', 'catalog': 'none', 'components': {'Ticket': propio}, 'actions': ['set']})
    assert cat.errores_del_nodo({'id': 't', 'component': 'Ticket', 'props': {'title': 'Uno'}}) == []
    assert cat.errores_del_nodo({'id': 't', 'component': 'Ticket', 'props': {}})          # falta title
    assert cat.errores_del_nodo({'id': 't', 'component': 'Text', 'props': {'text': 'x'}})  # sin catálogo base
    assert cat.errores_del_nodo({'id': 't', 'component': 'Ticket', 'props': {'title': 'Uno'},
                                 'events': {'press': {'send': 'abrir'}}})                   # send no declarado
    manual = cat.manual()
    assert 'Ticket' in manual and 'Stack' not in manual.split('Componentes:')[1]


def test_la_accion_llega_al_modelo_como_texto_y_avisa_si_esta_desfasada():
    data = {'view': 'v', 'rev': 1, 'node': 'b', 'event': 'press', 'name': 'crear', 'values': {'t': 'x'}}
    texto = accion_para_el_modelo(['Crear «x»'], data, 3)
    assert texto.startswith('Crear «x»') and '"name": "crear"' in texto and 'desfasada' in texto
    assert 'desfasada' not in accion_para_el_modelo([], data, 1)


# -- extremo a extremo ----------------------------------------------------------------------------------
def test_el_agente_ofrece_ui_y_se_negocia():
    c = Cliente(perfiles=('work', 'ui'))
    c.saludar(render={'ui': {'version': '1'}})
    hola, empezada = c.del_agente()[:2]
    assert 'ui' in hola['payload']['profiles'] and 'ui' in empezada['payload']['profiles']
    assert 'Pantallas en el chat' in c.t.manual_ui()
    sin = Cliente()
    sin.saludar()
    assert 'ui' not in sin.del_agente('session_started')[0]['payload']['profiles'] and sin.t.manual_ui() == ''


def test_una_declaracion_invalida_deja_la_sesion_sin_ui():
    c = Cliente(perfiles=('work', 'ui'))
    respuesta = c.saludar(render={'ui': {'version': '1', 'components': {'Button': {'props': {}}}}})
    assert any(m['type'] == 'error' and 'render.ui' in m['payload']['message'] for m in respuesta)
    assert 'ui' not in c.del_agente('session_started')[0]['payload']['profiles']
    assert c.t.manual_ui() == ''


async def test_el_modelo_dibuja_y_el_usuario_responde_con_una_accion():
    c = Cliente(perfiles=('work', 'ui'))
    c.hello['render'] = {'ui': {'version': '1'}}
    run = await abrir(c, 'Enséñame los tickets')
    c.t.emit('evento', rol='agent', texto=PANTALLA, hora=1.0)
    mensajes = c.drenar()
    textos = [b['text'] for m in mensajes if m['type'] == 'message_delta' for b in m['payload']['blocks']
              if b['type'] == 'text']
    assert textos[0].startswith('Tienes 2') and textos[-1].strip() == '¿Creo otro?'
    assert not any('pacai-ui' in t for t in textos)
    (_, patch), = estructurados(mensajes, 'message_delta')
    (_, foto), = estructurados(mensajes, 'message_end')
    assert patch['op'] == 'patch' and patch['state'] == {'priority': 'medium'} and len(patch['nodes']) == 3
    assert foto['op'] == 'replace' and foto['rev'] == patch['rev'] + 1 and foto['alt']
    await cerrar(c)
    assert c.del_agente('run_finished')[-1]['payload']['run_id'] == run

    # El usuario pulsa «Crear»: el modelo lo recibe como un mensaje de texto.
    c.enviar('input', {'blocks': [
        {'type': 'text', 'text': 'Crear ticket'},
        {'type': 'structured', 'schema': 'pacai.ui.action/1', 'data': {
            'view': 'tickets', 'rev': foto['rev'], 'node': 'nuevo', 'event': 'press', 'name': 'crear_ticket',
            'values': {'priority': 'medium'}}}]})
    entregado = await asyncio.wait_for(c.t.next_message(), 1)
    texto = getattr(entregado, 'texto', None) or getattr(entregado, 'text', None) or str(entregado)
    assert 'Crear ticket' in texto and '[Acción en la pantalla]' in texto and 'desfasada' not in texto
    c.comprobar()


async def test_sin_ui_las_vallas_son_texto_y_las_acciones_no_valen():
    c = Cliente()
    await abrir(c)
    c.t.emit('evento', rol='agent', texto=PANTALLA, hora=1.0)
    mensajes = c.drenar()
    assert estructurados(mensajes) == []
    assert any('pacai-ui' in b['text'] for m in mensajes if m['type'] == 'message_delta'
               for b in m['payload']['blocks'])
    await cerrar(c)
    respuesta = c.enviar('input', {'blocks': [{'type': 'structured', 'schema': 'pacai.ui.action/1', 'data': {
        'view': 'tickets', 'rev': 1, 'node': 'nuevo', 'event': 'press', 'name': 'crear', 'values': {}}}]})
    assert respuesta[-1]['type'] == 'error' and respuesta[-1]['payload']['code'] == 'not_negotiated'


async def test_los_nodos_rechazados_se_cuentan_con_la_extension():
    c = Cliente(perfiles=('work', 'ui'))
    await abrir(c)
    c.t.emit('evento', rol='agent', texto='```pacai-ui view=v\n{"id":"root","component":"Marquee"}\n```', hora=1.0)
    mensajes = c.drenar()
    rechazo, = [m for m in mensajes if m['type'] == 'alfalfa.ui_rejected']
    assert rechazo['payload']['view'] == 'v' and 'Marquee' in rechazo['payload']['errors'][0]
    assert estructurados(mensajes) == []
    await cerrar(c)
    c.comprobar()


async def test_una_accion_mal_formada_es_invalid_message():
    c = Cliente(perfiles=('work', 'ui'))
    await abrir(c)
    respuesta = c.enviar('input', {'blocks': [{'type': 'structured', 'schema': 'pacai.ui.action/1',
                                               'data': {'view': 'v'}}]})
    assert respuesta[-1]['payload']['code'] == 'invalid_message'


# -- la demo web (docs/demos/pacai-ui) ---------------------------------------------------------------------
def test_el_agente_simulado_de_la_demo_habla_pacai_valido():
    import json
    import subprocess
    import sys
    import threading
    from pathlib import Path

    from tests.pacai_comun import SESION, problemas

    def sobre(seq, tipo, payload):
        return {'pacai': '0.3', 'id': 'c_%08d' % seq, 'type': tipo, 'session': SESION, 'sender': 'client',
                'seq': seq, 'time': '2026-11-24T10:00:00Z', 'payload': payload}

    accion = {'view': 'tickets', 'rev': 2, 'node': 'nuevo', 'event': 'submit', 'name': 'crear_ticket',
              'values': {'title': 'Nuevo', 'priority': 'high'}}
    entrada = [sobre(1, 'hello', {'versions': ['0.3'], 'profiles': ['work', 'ui'], 'extensions': ['alfalfa'],
                                  'render': {'ui': {'version': '1'}}}),
               sobre(2, 'input', {'blocks': [{'type': 'text', 'text': 'tickets'}]}),
               sobre(3, 'input', {'blocks': [{'type': 'text', 'text': 'Crear'},
                                             {'type': 'structured', 'schema': 'pacai.ui.action/1', 'data': accion}]})]
    script = Path(__file__).resolve().parent.parent / 'docs' / 'demos' / 'pacai-ui' / 'agente_simulado.py'
    # EOF equivale a `close`: la entrada sigue abierta hasta que terminan las dos ejecuciones.
    proceso = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True)
    reloj = threading.Timer(20, proceso.kill)              # si se atasca, readline devuelve '' y la prueba falla
    reloj.start()
    try:
        proceso.stdin.write(''.join(json.dumps(m) + '\n' for m in entrada))
        proceso.stdin.flush()
        del_agente = []
        while sum(m['type'] == 'run_finished' for m in del_agente) < 2:
            linea = proceso.stdout.readline()
            assert linea, 'el agente simulado se cerró antes de tiempo'
            del_agente.append(json.loads(linea))
        proceso.stdin.close()
        del_agente += [json.loads(linea) for linea in proceso.stdout if linea.strip()]
        proceso.wait(10)
    finally:
        reloj.cancel()
        proceso.kill()
    # El cliente lo mandó todo al empezar (encadenado tras su `hello`); el agente contestó después.
    assert problemas(entrada + del_agente) == []
    tablas = [b['data'] for m in del_agente for b in m['payload'].get('blocks', [])
              if b.get('schema') == 'pacai.ui/1' and m['type'] == 'message_end']
    assert len(tablas) == 2
    filas = next(n for n in tablas[1]['nodes'] if n['id'] == 'tabla')['props']['rows']
    assert filas[-1] == {'id': 'T-21', 'title': 'Nuevo', 'priority': 'Alta'}
    assert all(errores_de('update', t) == [] for t in tablas)
