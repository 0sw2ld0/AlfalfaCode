import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from alfalfa.permisos.modos import AprobacionConModo
from alfalfa.proveedores.nativo import imagenes
from alfalfa.proveedores.nativo.herramientas import TOOLS, WorkspaceTools

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+'
                       'kX2kAAAAASUVORK5CYII=')
ARGS = {'prompt': 'Un zorro en la nieve', 'path': 'zorro.png'}


@pytest.fixture
def tools(tmp_path, monkeypatch):
    monkeypatch.setattr(imagenes.ajustes, 'openai_clave', lambda: 'clave-ficticia')
    monkeypatch.setattr(imagenes.contenido_externo, 'secretos', lambda runtime: ())
    rt = SimpleNamespace(task={'cwd': str(tmp_path), 'modo': 'omitir'}, manager=SimpleNamespace(),
                         state={}, save=lambda: None, excluded_tools=(), tool_specs=TOOLS, image_provider='openai')
    rt.manager.task = rt.task
    rt.approve = AprobacionConModo(AsyncMock(return_value='una_vez'), rt.manager)
    return WorkspaceTools(rt)


def respuesta(status=200):
    return httpx.Response(status, json={'data': [{'b64_json': base64.b64encode(PNG).decode()}],
                                        'usage': {'input_tokens': 20, 'output_tokens': 100, 'total_tokens': 120}})


@pytest.mark.asyncio
async def test_generacion_aprueba_cada_envio_y_reporta_costo(tools, monkeypatch):
    peticiones = []
    aprobaciones = tools.runtime.approve.interior

    def atender(request):
        assert aprobaciones.await_count == len(peticiones) + 1
        assert str(request.url) == imagenes.URL
        assert request.headers['authorization'] == 'Bearer clave-ficticia'
        assert json.loads(request.content) == {'model': 'gpt-image-1.5', 'prompt': ARGS['prompt'], 'n': 1,
                                               'output_format': 'png', 'size': '1024x1024', 'quality': 'medium'}
        peticiones.append(request)
        return respuesta()

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(atender))
    monkeypatch.setattr(imagenes.httpx, 'AsyncClient', lambda **kwargs: cliente)
    informe = await tools.call('generate_image', ARGS)
    assert (tools.root / ARGS['path']).read_bytes() == PNG
    assert informe['width'] == informe['height'] == 1
    assert informe['estimated_cost']['estimated_total'] == '0.034100'
    assert informe['usage']['total_tokens'] == 120
    assert tools.runtime.state['changes'][0]['after'] == informe['sha256']
    permiso = aprobaciones.await_args.args[0]
    assert 'USD 0.034' in permiso['purpose'] and permiso['argv'][-1] == ARGS['prompt']
    assert not {'prefijo', 'para_sesion'} & permiso.keys()
    monkeypatch.setattr(imagenes, 'solicitar', AsyncMock(return_value=respuesta().json()))
    await tools.call('generate_image', dict(ARGS, path='otro.png'))
    assert aprobaciones.await_count == 2


@pytest.mark.asyncio
async def test_rechazo_y_cancelacion_no_envian(tools, monkeypatch):
    enviar = AsyncMock()
    monkeypatch.setattr(imagenes, 'solicitar', enviar)
    tools.runtime.approve.interior.return_value = False
    with pytest.raises(ValueError, match='rechazada'):
        await tools.call('generate_image', ARGS)
    listo = asyncio.Event()

    async def esperar(_):
        listo.set()
        await asyncio.Future()

    tools.runtime.approve.interior.side_effect = esperar
    llamada = asyncio.create_task(tools.call('generate_image', ARGS))
    await listo.wait()
    llamada.cancel()
    with pytest.raises(asyncio.CancelledError):
        await llamada
    enviar.assert_not_awaited()
    assert not (tools.root / ARGS['path']).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('args', [dict(ARGS, path='../escape.png'), dict(ARGS, path='existe.png'),
                                 dict(ARGS, path='enlace.png'), dict(ARGS, quality='auto')])
async def test_destinos_y_opciones_invalidas(tools, args):
    (tools.root / 'existe.png').write_bytes(b'original')
    (tools.root / 'enlace.png').symlink_to(tools.root / 'existe.png')
    with pytest.raises(ValueError):
        await tools.call('generate_image', args)
    tools.runtime.approve.interior.assert_not_awaited()
    assert (tools.root / 'existe.png').read_bytes() == b'original'


@pytest.mark.asyncio
@pytest.mark.parametrize('cambio', [{'modo': 'plan'}, {'review': True}, {'subagente_de': 'raiz'}])
async def test_sesiones_restringidas(tools, cambio):
    tools.runtime.task.update(cambio)
    with pytest.raises(ValueError, match='deshabilitada'):
        await tools.call('generate_image', ARGS)
    tools.runtime.approve.interior.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallo_de_red_no_se_reintenta(tools, monkeypatch):
    peticiones = []

    def atender(request):
        peticiones.append(request)
        raise httpx.ReadTimeout('timeout simulado', request=request)

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(atender))
    monkeypatch.setattr(imagenes.httpx, 'AsyncClient', lambda **kwargs: cliente)
    with pytest.raises(ValueError, match='pudo cobrarla; no se reintentó'):
        await tools.call('generate_image', ARGS)
    assert len(peticiones) == 1
    assert not (tools.root / ARGS['path']).exists()


@pytest.mark.asyncio
async def test_sin_clave_no_pide_pago(tools, monkeypatch):
    monkeypatch.setattr(imagenes.ajustes, 'openai_clave', lambda: None)
    with pytest.raises(ValueError, match='Configura la clave'):
        await tools.call('generate_image', ARGS)
    tools.runtime.approve.interior.assert_not_awaited()


async def test_codex_usa_el_plan_y_devuelve_las_dimensiones_reales(tools, monkeypatch, tmp_path):
    from alfalfa.proveedores.nativo import imagenes_codex

    binary = tmp_path / 'codex'
    monkeypatch.setattr(imagenes_codex, 'ejecutable', lambda: binary)
    imagenes.configurar(codex=('gpt-6.2-sol', 'low'))
    no_api = AsyncMock(side_effect=AssertionError('No se usa la API de imágenes'))
    monkeypatch.setattr(imagenes, 'solicitar', no_api)

    def no_key():
        raise AssertionError('No se consulta la clave de la API')

    monkeypatch.setattr(imagenes.ajustes, 'openai_clave', no_key)
    generate = AsyncMock(return_value=PNG)
    monkeypatch.setattr(imagenes_codex, 'generar', generate)
    tools.runtime.image_provider = 'codex'
    args = ARGS
    approval = tools.runtime.approve.interior
    approval.return_value = False
    with pytest.raises(ValueError, match='rechazada'):
        await tools.call('generate_image', args)
    generate.assert_not_awaited()

    approval.return_value = 'una_vez'
    output = await tools.call('generate_image', args)
    generate.assert_awaited_once_with(binary, ARGS['prompt'], '1024x1024', 'medium', model='gpt-6.2-sol', effort='low')
    no_api.assert_not_awaited()
    assert 'plan de ChatGPT' in approval.await_args.args[0]['purpose']
    assert 'Modelo de Codex: gpt-6.2-sol. Esfuerzo: low.' in approval.await_args.args[0]['purpose']
    assert output['provider'] == 'codex' and 'estimated_cost' not in output
    assert output['requested_size'] == '1024x1024'
    assert (output['width'], output['height']) == (1, 1)
    assert (tools.root / ARGS['path']).read_bytes() == PNG


async def test_codex_rechaza_autenticacion_por_api():
    from alfalfa.proveedores.nativo import imagenes_codex

    session = imagenes_codex._Sesion(None)
    session.pedir = AsyncMock(side_effect=[{}, {'account': {'type': 'apiKey'}}])
    session.enviar = AsyncMock()
    with pytest.raises(ValueError, match='Inicia sesión con ChatGPT'):
        await session.generar(None, '/tmp', 'sprout', '1024x1024', 'low')
    assert [call.args[0] for call in session.pedir.await_args_list] == ['initialize', 'account/read']


async def test_codex_solo_envia_los_ajustes_del_encargo_de_imagen(tmp_path):
    from alfalfa.proveedores.nativo import imagenes_codex

    folder = tmp_path / 'generated_images' / 'thread1'
    folder.mkdir(parents=True)
    image = folder / 'image.png'
    image.write_bytes(PNG)
    for options in ({'model': 'gpt-6.2-sol', 'effort': 'low'}, {}, {'effort': 'high'}, {'model': 'gpt-6-luna'}):
        session = imagenes_codex._Sesion(None)
        session.pedir = AsyncMock(side_effect=[
            {}, {'account': {'type': 'chatgpt'}}, {'thread': {'id': 'thread1'}}, {'turn': {'id': 'turn1'}},
        ])
        session.enviar = AsyncMock()
        session.recibir = AsyncMock(side_effect=[
            {'method': 'item/completed', 'params': {'threadId': 'thread1', 'turnId': 'turn1',
                                                   'item': {'type': 'imageGeneration', 'savedPath': str(image)}}},
            {'method': 'turn/completed', 'params': {'threadId': 'thread1',
                                                   'turn': {'id': 'turn1', 'status': 'completed'}}},
        ])
        assert await session.generar(tmp_path, '/tmp', 'sprout', '1024x1024', 'low', **options) == PNG
        calls = session.pedir.await_args_list
        assert [call.args[0] for call in calls] == ['initialize', 'account/read', 'thread/start', 'turn/start']
        thread, turn = calls[2].kwargs, calls[3].kwargs
        assert thread['ephemeral'] is True
        assert ('model' in thread) == ('model' in options)
        assert ('effort' in turn) == ('effort' in options)
        assert thread.get('model') == options.get('model')
        assert turn.get('effort') == options.get('effort')
        assert 'effort' not in thread and 'model' not in turn


def test_codex_solo_acepta_archivos_de_la_sesion(tmp_path):
    from alfalfa.proveedores.nativo import imagenes_codex

    folder = tmp_path / 'generated_images' / 'thread1'
    folder.mkdir(parents=True)
    image = folder / 'image.png'
    image.write_bytes(PNG)
    assert imagenes_codex._artefacto(tmp_path, 'thread1', str(image)) == PNG
    with pytest.raises(ValueError, match='fuera de la carpeta'):
        imagenes_codex._artefacto(tmp_path, 'other-thread', str(image))
    outside = tmp_path / 'outside.png'
    outside.write_bytes(PNG)
    link = folder / 'link.png'
    link.symlink_to(outside)
    with pytest.raises(ValueError, match='No se pudo leer'):
        imagenes_codex._artefacto(tmp_path, 'thread1', str(link))


def test_preferencia_de_imagen_preserva_otros_ajustes_y_no_recurre_a_la_api(monkeypatch):
    from alfalfa.proveedores import configuracion
    from alfalfa.proveedores.nativo import imagenes_codex

    configuracion.escribir({'por_defecto': 'ollama'})
    monkeypatch.setattr(imagenes.ajustes, 'openai_clave', lambda: 'clave-ficticia')
    assert imagenes.configurar() == {'preferencia': None, 'proveedor': 'none'}
    monkeypatch.setattr(imagenes_codex, 'conectado', lambda: True)
    assert imagenes.configurar() == {'preferencia': None, 'proveedor': 'codex'}
    assert imagenes.configurar('none')['proveedor'] == 'none'
    assert imagenes.configurar()['proveedor'] == 'none'
    assert imagenes.configurar('openai')['proveedor'] == 'openai'
    assert configuracion.por_defecto() == 'ollama'
    monkeypatch.setattr(imagenes.ajustes, 'openai_clave', lambda: None)
    assert imagenes.configurar() == {'preferencia': 'openai', 'proveedor': 'none'}
    assert imagenes.configurar('codex')['proveedor'] == 'codex'
    monkeypatch.setattr(imagenes_codex, 'conectado', lambda: False)
    assert imagenes.configurar() == {'preferencia': 'codex', 'proveedor': 'none'}


def test_los_ajustes_de_imagen_se_guardan_y_se_borran_sin_cambiar_otros():
    from alfalfa.proveedores import configuracion

    existente = {'por_defecto': 'ollama', 'proveedor_imagen': 'none', 'ollama': {'url': 'http://localhost:11434'}}
    configuracion.escribir(existente)
    imagenes.configurar(codex=('gpt-6.2-sol', 'low'))
    configurado = dict(existente, modelo_codex_imagen='gpt-6.2-sol', esfuerzo_codex_imagen='low')
    assert configuracion.leer() == configurado
    assert imagenes.configurar()['esfuerzo_codex_imagen'] == 'low'
    with pytest.raises(ValueError, match='Esfuerzo de Codex desconocido'):
        imagenes.configurar(codex=('gpt-6-luna', 'invalido'))
    assert configuracion.leer() == configurado
    imagenes.configurar(codex=('-', '-'))
    assert configuracion.leer() == existente


async def test_sin_proveedor_no_pide_permiso_ni_escribe(tools):
    tools.runtime.image_provider = 'none'
    with pytest.raises(ValueError, match='herramienta para generar imágenes'):
        await tools.call('generate_image', ARGS)
    tools.runtime.approve.interior.assert_not_awaited()
    assert not (tools.root / ARGS['path']).exists()
