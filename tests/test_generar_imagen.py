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
                         state={}, save=lambda: None, excluded_tools=(), tool_specs=TOOLS)
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
