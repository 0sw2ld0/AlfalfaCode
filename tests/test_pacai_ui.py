"""Perfil `ui` de PACAI en AlfalfaCode: negociación, pantallas del modelo, acciones del cliente y el prompt."""
import asyncio
import dataclasses

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


NODO = '{"id":"root","component":"Text","props":{"text":"Hola"}}'


def test_la_valla_vale_aunque_no_empiece_la_linea():
    # GPT-6 Luna escribe a veces una frase (o su razonamiento) y abre el bloque en la misma línea.
    texto = 'Se actualiza la rejilla.```pacai-ui view=tablero placement=latest\n' + NODO + '\n```\nListo.'
    assert partir(texto) == [('text', 'Se actualiza la rejilla.\n'),
                             ('ui', {'view': 'tablero', 'placement': 'latest'}, [NODO]),
                             ('text', 'Listo.')]
    # Sólo se encuentra el bloque: su contenido es el mismo que con la valla al principio de la línea.
    assert partir(texto)[1] == partir('```pacai-ui view=tablero placement=latest\n' + NODO + '\n```')[0]


def test_una_valla_citada_o_dentro_de_otro_codigo_sigue_siendo_texto():
    citas = [
        'Usa ````pacai-ui view=x` para dibujar.\n' + NODO + '\n',            # citada entre comillas invertidas
        'Escribe ```pacai-ui view=x y después los nodos.\n' + NODO + '\n',   # la frase sigue tras la valla
        '```markdown\nEjemplo:```pacai-ui view=x\n' + NODO + '\n```\n',      # dentro de otro bloque de código
    ]
    for texto in citas:
        assert [t[0] for t in partir(texto)] == ['text'], texto
        assert ''.join(t[1] for t in partir(texto)) == texto


async def test_el_motor_dibuja_una_pantalla_abierta_a_media_linea():
    c = Cliente(perfiles=('work', 'ui'))
    c.hello['render'] = {'ui': {'version': '1'}}
    await abrir(c, 'Enséñame un saludo')
    c.t.emit('evento', rol='agent', hora=1.0, texto='Aquí está:```pacai-ui view=saludo\n' + NODO + '\n```\n')
    mensajes = c.drenar()
    datos = [b['data'] for m in mensajes for b in m['payload'].get('blocks', []) if b.get('type') == 'structured']
    textos = [b['text'] for m in mensajes if m['type'] == 'message_delta' for b in m['payload']['blocks']
              if b['type'] == 'text']
    assert datos and datos[0]['nodes'][0]['id'] == 'root'
    assert ''.join(textos).strip() == 'Aquí está:' and not any('pacai-ui' in t for t in textos)
    await cerrar(c)
    c.comprobar()


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


FORMULARIO_SIN_ROOT = [
    '{"id":"formulario","component":"Form","props":{"submit_label":"Preparar solicitud"},'
    '"children":["inicio","motivo"]}',
    '{"id":"inicio","component":"Input","props":{"label":"Inicio","name":"inicio"}}',
    '{"id":"motivo","component":"Input","props":{"label":"Motivo","name":"motivo"}}',
]


def test_una_vista_nueva_sin_root_se_envuelve_en_un_stack_y_luego_se_puede_cambiar():
    # La primera línea no es un contenedor: un Stack nuevo envuelve los nodos de primer nivel.
    vistas = Vistas(Catalogo())
    titulo = '{"id":"titulo","component":"Text","props":{"text":"Vacaciones","style":"title"}}'
    data, errores = vistas.aplicar({'view': 'vacaciones'}, [titulo] + FORMULARIO_SIN_ROOT)
    assert errores == [] and errores_de('update', data) == []
    assert data['nodes'][-1] == {'id': 'root', 'component': 'Stack', 'props': {'direction': 'vertical'},
                                 'children': ['titulo', 'formulario']}
    assert vistas.reparaciones == ['Faltaba root: un Stack vertical envuelve titulo, formulario.']
    foto = vistas.foto('vacaciones')
    assert [n['id'] for n in foto['nodes']].count('root') == 1
    data, errores = vistas.aplicar({'view': 'vacaciones'}, [
        '{"id":"formulario","component":"Form","props":{"submit_label":"Enviar"},"children":["inicio"]}'])
    assert errores == [] and [n['id'] for n in data['nodes']] == ['formulario'] and vistas.reparaciones == []
    assert vistas.vistas['vacaciones'].nodos['root']['children'] == ['titulo', 'formulario']


def test_una_vista_nueva_cuya_primera_linea_es_un_contenedor_suelto_lo_usa_de_root():
    vistas = Vistas(Catalogo())
    data, errores = vistas.aplicar({'view': 'vacaciones'}, FORMULARIO_SIN_ROOT)
    assert errores == [] and errores_de('update', data) == []
    assert [n['id'] for n in data['nodes']] == ['root', 'inicio', 'motivo'] and data['nodes'][0]['component'] == 'Form'
    assert vistas.reparaciones == ['Faltaba root: formulario va primero, no cuelga de nadie y es un contenedor; '
                                   'pasa a llamarse root.']
    assert list(vistas.vistas['vacaciones'].nodos) == ['root', 'inicio', 'motivo']
    # Si la primera línea no valió, no se adivina: un Stack envuelve lo que sí valió.
    vistas = Vistas(Catalogo())
    data, errores = vistas.aplicar({'view': 'v'}, ['{"id":"fila","component":"Stack","props":{"x":1}}',
                                                   FORMULARIO_SIN_ROOT[0], *FORMULARIO_SIN_ROOT[1:]])
    assert errores and data['nodes'][-1]['component'] == 'Stack' and data['nodes'][-1]['children'] == ['formulario']


def test_varios_nodos_sueltos_de_una_vista_nueva_acaban_en_root_en_orden():
    vistas = Vistas(Catalogo())
    vistas.aplicar({'view': 'v'}, ['{"id":"b","component":"Text","props":{"text":"B"}}',
                                   '{"id":"c","component":"Card","props":{"title":"C"},"children":["d"]}',
                                   '{"id":"a","component":"Text","props":{"text":"A"}}',
                                   '{"id":"d","component":"Text","props":{"text":"D"}}'])
    assert vistas.vistas['v'].nodos['root']['children'] == ['b', 'c', 'a']


def test_un_root_rechazado_en_una_vista_nueva_no_se_sustituye_por_otro():
    vistas = Vistas(Catalogo())
    data, errores = vistas.aplicar({'view': 'v'}, [
        '{"id":"root","component":"Stack","props":{"children":["t"]}}',
        '{"id":"t","component":"Text","props":{"text":"hola"}}'])
    assert len(errores) == 1 and 'root' not in vistas.vistas['v'].nodos and vistas.reparaciones == []


def test_un_root_que_no_es_json_tampoco_se_sustituye():
    vistas = Vistas(Catalogo())
    data, errores = vistas.aplicar({'view': 'v'}, [
        '{"id": "root","component":"Stack","props":{"direction":"grid"},"children":["t"]',
        '{"id":"t","component":"Text","props":{"text":"hola"}}'])
    assert errores == ['Línea 1: no es JSON.'] and 'root' not in vistas.vistas['v'].nodos
    assert vistas.reparaciones == []


def test_tras_un_primer_bloque_sin_nodos_validos_la_vista_sigue_siendo_nueva():
    vistas = Vistas(Catalogo())
    assert vistas.aplicar({'view': 'v'}, ['no es json', '{"state":{"x":1}}'])[0] is not None
    data, errores = vistas.aplicar({'view': 'v'}, ['{"id":"t","component":"Text","props":{"text":"hola"}}'])
    assert errores == [] and vistas.vistas['v'].nodos['root']['children'] == ['t']


def test_los_hijos_de_un_nodo_rechazado_no_suben_al_root_nuevo():
    vistas = Vistas(Catalogo())
    vistas.aplicar({'view': 'v'}, [
        '{"id":"titulo","component":"Text","props":{"text":"Pedir"}}',
        '{"id":"formulario","component":"Form","props":{"inventada":1},"children":["nombre"]}',
        '{"id":"nombre","component":"Input","props":{"label":"Nombre","name":"nombre"}}'])
    assert vistas.vistas['v'].nodos['root']['children'] == ['titulo']


def test_si_no_cabe_el_root_nuevo_se_dice_por_que():
    vistas = Vistas(dataclasses.replace(Catalogo(), max_nodos=2))
    data, errores = vistas.aplicar({'view': 'v'}, ['{"id":"a","component":"Text","props":{"text":"A"}}',
                                                   '{"id":"b","component":"Text","props":{"text":"B"}}'])
    assert any('ya tiene 2 nodos' in e for e in errores) and 'root' not in vistas.vistas['v'].nodos


def test_un_cambio_sin_root_sobre_una_vista_que_existe_no_se_repara():
    vistas = Vistas(Catalogo())
    vistas.aplicar({'view': 'v'}, ['{"id":"root","component":"Stack","children":["t"]}',
                                   '{"id":"t","component":"Text","props":{"text":"hola"}}'])
    data, errores = vistas.aplicar({'view': 'v'}, ['{"id":"suelto","component":"Text","props":{"text":"S"}}'])
    assert errores == [] and [n['id'] for n in data['nodes']] == ['suelto'] and vistas.reparaciones == []
    assert vistas.vistas['v'].nodos['root']['children'] == ['t']


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


def test_los_identificadores_con_tildes_y_enes_se_pasan_a_ascii_y_el_texto_no():
    vistas = Vistas(Catalogo())
    data, errores = vistas.aplicar({'view': 'Lista_Tareas'}, [
        '{"id":"root","component":"Stack","children":["añadir","campo_descripción"]}',
        '{"id":"añadir","component":"Button","props":{"label":"Añadir tarea"},'
        '"events":{"press":[{"set":{"núm":1}},{"send":"Añadir_Tarea","with":["descripción"]}]}}',
        '{"id":"campo_descripción","component":"Input","props":{"name":"descripción","label":"Descripción"}}',
        '{"state":{"descripción":""}}',
    ])
    assert errores == []
    assert data['view'] == 'lista_tareas'
    nodos = {n['id']: n for n in data['nodes']}
    assert nodos['root']['children'] == ['anadir', 'campo_descripcion']
    assert nodos['anadir']['props']['label'] == 'Añadir tarea'
    assert nodos['anadir']['events']['press'] == [{'set': {'num': 1}},
                                                  {'send': 'anadir_tarea', 'with': ['descripcion']}]
    assert nodos['campo_descripcion']['props'] == {'name': 'descripcion', 'label': 'Descripción'}
    assert data['state'] == {'descripcion': ''}
    assert errores_de('update', data) == []


def test_un_set_de_su_propio_campo_en_change_se_quita():
    vistas = Vistas(Catalogo())
    data, errores = vistas.aplicar({'view': 'v'}, [
        '{"id":"a","component":"Input","props":{"name":"titulo"},"events":{"change":{"set":{"titulo":"$value"}}}}',
        '{"id":"b","component":"Input","props":{"name":"otro"},'
        '"events":{"change":[{"set":{"otro":"$value","filtro":true}}]}}',
    ])
    assert errores == []
    nodos = {n['id']: n for n in data['nodes']}
    assert 'events' not in nodos['a']
    assert nodos['b']['events'] == {'change': [{'set': {'filtro': True}}]}


def test_un_alt_dentro_del_nodo_y_las_claves_de_mas_no_tiran_el_nodo():
    vistas = Vistas(Catalogo())
    data, errores = vistas.aplicar({'view': 'v'}, [
        '{"id":"root","component":"Stack","alt":"Tareas de hoy","style":"x","children":[]}'])
    assert errores == []
    assert data['nodes'] == [{'id': 'root', 'component': 'Stack', 'children': []}]
    assert vistas.foto('v')['alt'] == 'Tareas de hoy'


def test_con_buscador_el_manual_dice_que_busque_antes_de_dibujar():
    cat = Catalogo()
    assert 'web_search' in cat.manual(web=True)
    assert 'web_search' not in cat.manual()
    c = Cliente(perfiles=('work', 'ui'))
    c.saludar()
    assert 'web_search' in c.t.manual_ui(web=True) and 'web_search' not in c.t.manual_ui()


# -- ejemplos por componente en el prompt -----------------------------------------------------------------
def _cuymercado():
    import json
    from pathlib import Path
    ruta = Path(__file__).with_name('conformidad') / 'catalogo-cuymercado.json'
    return json.loads(ruta.read_text(encoding='utf-8'))


def test_cada_componente_base_trae_un_ejemplo_que_pasa_el_catalogo():
    from alfalfa.pacai.ui import BASE, EJEMPLOS_BASE
    cat = Catalogo()
    assert set(EJEMPLOS_BASE) == set(BASE)
    for nodo in cat.ejemplos():
        assert cat.errores_del_nodo(nodo) == [], nodo
    ejemplos = [n for n in cat.ejemplos()]
    # Los fallos vistos en el piloto: options como objetos, los kind de Input y leer el estado con {"$": …}.
    assert all(isinstance(o, dict) for n in ejemplos if n['component'] == 'Select' for o in n['props']['options'])
    assert {n['props'].get('kind') for n in ejemplos if n['component'] == 'Input'} >= {'email', 'multiline',
                                                                                        'number', 'date'}
    assert any(n['props'].get('text') == {'$': 'moneda'} for n in ejemplos if n['component'] == 'Text')


def test_un_select_puede_ser_obligatorio():
    cat = Catalogo()
    opciones = [{'value': 'a'}, {'value': 'b'}]
    assert cat.errores_del_nodo({'id': 's', 'component': 'Select', 'props': {'name': 'x', 'options': opciones,
                                                                           'required': True}}) == []
    assert cat.errores_del_nodo({'id': 's', 'component': 'Select', 'props': {'name': 'x', 'options': opciones,
                                                                           'required': 'sí'}})
    assert 'required' in cat.manual().split('- Select:')[1].splitlines()[0]
    assert any(n['component'] == 'Select' and n['props'].get('required') is True for n in cat.ejemplos())


def test_el_manual_lleva_los_ejemplos_del_catalogo_y_los_de_la_app():
    declaracion = _cuymercado()
    assert errores_de('declaration', declaracion) == []
    cat = Catalogo.de(declaracion)
    manual = cat.manual()
    assert 'Ejemplos, un nodo por línea' in manual
    assert ('{"id":"filtros","component":"Filters","props":{"period":{"$":"periodo"},"family":{"$":"familia"}}}'
            in manual)
    assert '"options":[{"value":"pen","label":"Soles"}' in manual
    assert 'Ejemplos' not in cat.manual(ejemplos=False)
    # Un ejemplo de la app que no pasa su propio esquema no entra en el prompt.
    declaracion['components']['Stat']['example'] = [{'id': 'mal', 'component': 'Stat', 'props': {'label': 'X'}}]
    assert '"id":"mal"' not in Catalogo.de(declaracion).manual()
    declaracion['components']['Stat']['example'] = []
    assert errores_de('declaration', declaracion)               # minItems: 1


async def test_al_reanudar_la_sesion_las_pantallas_siguen_su_revision(tmp_path, monkeypatch):
    # Antes, las vistas vivían sólo en memoria: el agente reanudado volvía a mandar la misma vista con rev 1 y el
    # cliente, que ya iba por la 2, la daba por vieja.
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path))
    (tmp_path / 'sesiones' / 's1').mkdir(parents=True)

    def sesion_ui():
        c = Cliente(perfiles=('work', 'ui'))
        c.hello['render'] = {'ui': {'version': '1'}}
        c.t.emit('sesion', id='s1', cwd=str(tmp_path), modelo='m', esfuerzo='low', modo='auto')
        return c

    c = sesion_ui()
    await abrir(c, 'Enséñame los tickets')
    c.t.emit('evento', rol='agent', texto=PANTALLA, hora=1.0)
    (_, primera), = estructurados(c.drenar(), 'message_end')
    await cerrar(c)
    guardadas = tmp_path / 'sesiones' / 's1' / 'pacai-vistas.json'
    assert guardadas.exists() and oct(guardadas.stat().st_mode & 0o777) == '0o600'

    # Otro proceso reanuda la sesión: una acción sobre la pantalla de antes no está desfasada...
    c = sesion_ui()
    c.saludar()
    c.enviar('input', {'blocks': [
        {'type': 'text', 'text': 'Crear ticket'},
        {'type': 'structured', 'schema': 'pacai.ui.action/1', 'data': {
            'view': 'tickets', 'rev': primera['rev'], 'node': 'nuevo', 'event': 'press', 'name': 'crear_ticket',
            'values': {}}}]})
    entregado = await asyncio.wait_for(c.t.next_message(), 1)
    texto = getattr(entregado, 'texto', None) or getattr(entregado, 'text', None) or str(entregado)
    assert '[Acción en la pantalla]' in texto and 'desfasada' not in texto
    c.t.turno_empieza(entregado)
    c.drenar()
    # ... y la misma vista sigue por la revisión siguiente, con los nodos que ya tenía.
    c.t.emit('evento', rol='agent', texto='```pacai-ui view=tickets\n'
             '{"id":"titulo","component":"Text","props":{"text":"3 tickets abiertos","style":"title"}}\n```', hora=2.0)
    mensajes = c.drenar()
    (_, patch), = estructurados(mensajes, 'message_delta')
    (_, foto), = estructurados(mensajes, 'message_end')
    assert patch['rev'] == primera['rev'] + 1 and foto['rev'] == primera['rev'] + 2
    assert {n['id'] for n in foto['nodes']} == {n['id'] for n in primera['nodes']}
    await cerrar(c)
    c.comprobar()


def test_las_vistas_guardadas_se_recuperan_sin_lo_que_el_catalogo_no_admite():
    base = Catalogo.de({'version': '1'})
    vistas = Vistas(base)
    vistas.aplicar({'view': 'tickets'}, PANTALLA.split('```')[1].splitlines()[1:])
    vistas.foto('tickets')
    datos = vistas.a_datos()
    solo_texto = Catalogo.de({'version': '1', 'catalog': 'none', 'components': {
        'Stack': {'props': {'type': 'object'}, 'children': True}, 'Text': {'props': {'type': 'object'}}}})
    otra = Vistas(solo_texto)
    otra.cargar(datos)
    assert otra.rev('tickets') == vistas.rev('tickets')
    assert set(otra.vistas['tickets'].nodos) == {'root', 'titulo'}          # el Button no está en este catálogo
    for malo in (None, {'version': 2}, {'version': 1, 'vistas': {'../x': {'rev': 3}, 'y': {'rev': -1}}}):
        nada = Vistas(base)
        nada.cargar(malo)
        assert nada.vistas == {}


def test_la_vista_enriquecida_apagada_quita_el_manual_y_la_sesion_lo_anuncia():
    c = Cliente(perfiles=('work', 'ui'))
    c.saludar(render={'ui': {'version': '1'}})
    c.t.emit('sesion', id='s1', cwd='/x', modelo='m', esfuerzo='low', modo='auto')
    sesion, = [m for m in c.drenar() if m['type'] == 'alfalfa.session']
    assert sesion['payload']['rich_view'] is True
    assert 'pacai-ui' in c.t.manual_ui()
    assert c.enviar('alfalfa.set_rich_view', {'active': False}) == []
    assert c.t.manual_ui() == '' and c.t.manual_ui(web=True) == ''
    c.t.emit('sesion', id='s1', cwd='/x', modelo='m', esfuerzo='low', modo='auto')
    assert [m for m in c.drenar() if m['type'] == 'alfalfa.session'][0]['payload']['rich_view'] is False
    c.enviar('alfalfa.set_rich_view', {'active': True})
    assert 'pacai-ui' in c.t.manual_ui()
    error, = c.enviar('alfalfa.set_rich_view', {'active': 'si'})
    assert error['type'] == 'error' and error['payload']['code'] == 'invalid_message'
    c.comprobar()


def test_sin_ui_la_sesion_no_anuncia_la_vista_enriquecida():
    c = Cliente()
    c.saludar()
    c.t.emit('sesion', id='s1', cwd='/x', modelo='m', esfuerzo='low', modo='auto')
    sesion, = [m for m in c.drenar() if m['type'] == 'alfalfa.session']
    assert 'rich_view' not in sesion['payload']
