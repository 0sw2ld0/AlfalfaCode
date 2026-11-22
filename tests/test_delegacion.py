import asyncio
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from alfalfa import delegacion, proveedores
from alfalfa.permisos.modos import AprobacionConModo

AGENT = """
import json
import os
import sys

assert sys.argv[1:] == ['acp', '--sandbox', '--include-default-customizations=false']
assert 'OPENAI_API_KEY' not in os.environ
assert not os.listdir()

def read(method):
    frame = json.loads(input())
    assert frame['method'] == method
    return frame

def send(**frame):
    print(json.dumps(dict(jsonrpc='2.0', **frame)), flush=True)

def reply(request, **result):
    send(id=request['id'], result=result)

reply(read('initialize'), protocolVersion=1)
new = read('session/new')
assert new['params']['mcpServers'] == []
reply(new, sessionId='child', modes={
    'currentModeId': 'default',
    'availableModes': [{'id': 'plan'}],
})
mode = read('session/set_mode')
assert mode['params'] == {'sessionId': 'child', 'modeId': 'plan'}
reply(mode)
prompt = read('session/prompt')
send(id='fs', method='fs/write_text_file')
assert json.loads(input())['error']['code'] == -32601
send(id='permission', method='session/request_permission', params={
    'options': [{'kind': 'allow_once', 'optionId': 'yes'}, {'kind': 'reject_once', 'optionId': 'no'}],
})
assert json.loads(input())['result']['outcome'] == {'outcome': 'selected', 'optionId': 'no'}
if prompt['params']['prompt'][0]['text'].endswith('\\n\\nwait'):
    print(json.dumps(dict(jsonrpc='2.0', method='test/waiting')), flush=True)
    sys.stdin.read()
for session, text in [('other', 'ignore'), ('child', 'analysis')]:
    send(method='session/update', params={'sessionId': session, 'update': {
        'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': text},
    }})
reply(prompt, stopReason='end_turn')
"""


@pytest.fixture
def fake_agy(tmp_path):
    path = tmp_path / 'agy'
    path.write_text('#!' + sys.executable + '\n' + AGENT)
    path.chmod(0o700)
    return path


async def test_delegar_pide_permiso_incluso_en_omitir(monkeypatch, fake_agy):
    monkeypatch.setattr(delegacion, 'ejecutable', lambda: fake_agy)
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-secret-never-passed-to-agy')
    manager = SimpleNamespace(task={'modo': 'omitir'}, event=AsyncMock())
    approval = AsyncMock(side_effect=ValueError('denegado'))
    runtime = SimpleNamespace(approve=AprobacionConModo(approval, manager))
    from alfalfa import contenido_externo
    monkeypatch.setattr(contenido_externo, 'secretos', lambda _: ['replace-this-secret'])
    launch = AsyncMock(wraps=delegacion.antigravity)
    monkeypatch.setattr(delegacion, 'antigravity', launch)
    with pytest.raises(ValueError, match='denegado'):
        await delegacion.delegar(runtime, 'before replace-this-secret')
    request = approval.call_args.args[0]
    assert request['motivo'] == 'delegacion_antigravity'
    assert 'replace-this-secret' not in json.dumps(request)
    launch.assert_not_awaited()

    approval.side_effect = None
    assert await delegacion.delegar(runtime, 'review this') == {'text': 'analysis'}
    launch.assert_awaited_once_with(fake_agy, 'review this')


async def test_delegacion_sin_modo_plan_no_envia_el_encargo(monkeypatch):
    connection = delegacion._ACP(None)
    request = AsyncMock(side_effect=[
        {'protocolVersion': 1},
        {'sessionId': 'child', 'modes': {'currentModeId': 'default', 'availableModes': []}},
    ])
    monkeypatch.setattr(connection, 'request', request)
    with pytest.raises(ValueError, match='no ofreció el modo Plan'):
        await connection.run('/tmp', 'private task')
    assert [call.args[0] for call in request.await_args_list] == ['initialize', 'session/new']


async def test_delegacion_cancelada_cierra_el_proceso(monkeypatch, fake_agy):
    waiting = asyncio.Event()
    launch = asyncio.create_subprocess_exec
    processes = []

    async def track(*args, **kwargs):
        process = await launch(*args, **kwargs)
        processes.append(process)
        return process

    receive = delegacion._ACP.receive

    async def notify(self, message):
        if message.get('method') == 'test/waiting':
            waiting.set()
        await receive(self, message)

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', track)
    monkeypatch.setattr(delegacion._ACP, 'receive', notify)
    task = asyncio.create_task(delegacion.antigravity(fake_agy, 'wait'))
    try:
        await asyncio.wait_for(waiting.wait(), 5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(processes) == 1
    assert processes[0].returncode is not None


async def test_modelo_alfalfa_recibe_solo_la_consulta_y_el_modelo_elegido(monkeypatch):
    def answer(request):
        assert request.url.path == '/api/chat'
        data = json.loads(request.content)
        assert data['model'] == 'fixed-model'
        assert 'tools' not in data
        assert data['messages'][-1] == {'role': 'user', 'content': 'review this'}
        assert data['options']['num_predict'] == 2048
        return httpx.Response(200, json={'message': {'content': 'reviewed'}})

    provider = SimpleNamespace(configurado=lambda: True, secretos=lambda: [], cliente=lambda timeout: httpx.AsyncClient(
        base_url='https://model.test', transport=httpx.MockTransport(answer), timeout=timeout,
    ))
    def resolve(model):
        assert model == 'example/fixed-model'
        return provider, 'fixed-model'

    monkeypatch.setattr(proveedores, 'para_modelo', resolve)
    assert await delegacion.consultar('example/fixed-model', 'review this') == 'reviewed'


async def test_servidor_mcp_anuncia_solo_delegacion_y_limita_consultas(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ, PYTHONPATH=os.getcwd(), ALFALFA_HOME=str(tmp_path / 'config'))
    worker = '''
from alfalfa import delegacion, terminal

async def consult(model, message):
    return model + ': ' + message

delegacion.consultar = consult
raise SystemExit(terminal.main())
'''
    command = StdioServerParameters(command=sys.executable, args=[
        '-c', worker, '--delegacion-mcp', 'example/fixed-model',
    ], cwd=tmp_path, env=env)
    async with asyncio.timeout(10), stdio_client(command) as (read, write), ClientSession(read, write) as client:
        await client.initialize()
        tools = (await client.list_tools()).tools
        assert [tool.name for tool in tools] == ['delegate_alfalfa']
        assert set(tools[0].inputSchema['properties']) == {'message'}
        for _ in range(3):
            result = await client.call_tool('delegate_alfalfa', {'message': 'review'})
            assert not result.isError
            assert result.content[0].text == 'example/fixed-model: review'
        result = await client.call_tool('delegate_alfalfa', {'message': 'too many'})
        assert result.isError
        assert 'tres consultas' in result.content[0].text
