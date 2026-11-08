"""Hooks compatibles con Claude Code en AlfalfaCode.

Los hooks son procesos reales (hook_prueba.py) lanzados por /bin/sh dentro del
sandbox Seatbelt real; sólo se sustituye la inferencia (NativeRuntime.request).
El registro de cada hook es la medida de sus ejecuciones reales.
"""
import asyncio
import io
import json
import os
from pathlib import Path
import shlex
import socket
import sys
import time
from unittest.mock import patch
from uuid import uuid4

import pytest

from alfalfa.hooks import motor as motor_hooks, nombres
from alfalfa.hooks.motor import (Ejecucion, Filtro, Manejador, MotorHooks, Resultado, interpretar,
                                   leer_fuente)
from alfalfa.proveedores.nativo.herramientas import COMPLEMENTOS_TOOLS, ON_REQUEST_TOOLS, TOOLS
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.stdio import Transport
from alfalfa.terminal import TerminalManager

HOOK = str(Path(__file__).with_name('hook_prueba.py'))
SERVIDOR_MCP = str(Path(__file__).with_name('mcp_servidor_prueba.py'))
macos = pytest.mark.skipif(sys.platform != 'darwin', reason='los hooks corren en Seatbelt, sólo en macOS')


def orden(registro, modo, argumento=None):
    partes = [sys.executable, HOOK, str(registro), modo] + ([argumento] if argumento is not None else [])
    return ' '.join(shlex.quote(p) for p in partes)


def grupo(comando, matcher=None, **extra):
    fila = {'hooks': [dict(type='command', command=comando, **extra)]}
    if matcher is not None:
        fila['matcher'] = matcher
    return fila


def leer(registro):
    return [json.loads(line) for line in Path(registro).read_text().splitlines()] if Path(registro).exists() else []


def filas(salida):
    return [json.loads(line) for line in salida.getvalue().splitlines()]


class Modelo:
    """Guion de respuestas; registra el payload real de cada /api/chat."""

    def __init__(self, pasos):
        self.pasos, self.payloads = list(pasos), []

    def request(self):
        modelo = self

        async def request(rt, client, endpoint, payload, **kwargs):
            if endpoint == '/api/show':
                return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
            modelo.payloads.append(json.loads(json.dumps(payload)))
            paso = modelo.pasos.pop(0) if modelo.pasos else None
            mensaje = paso(payload) if callable(paso) else (paso or {'role': 'assistant', 'content': 'Hecho.'})
            return dict(message=mensaje, done=True, done_reason='stop', prompt_eval_count=900, eval_count=10)
        return request


def llamada(nombre, argumentos, ident=None):
    call = {'function': {'name': nombre, 'arguments': argumentos}}
    if ident:
        call['id'] = ident
    return {'role': 'assistant', 'content': '', 'tool_calls': [call]}


class Sesion:
    """TerminalManager real con Transport real, hooks del usuario y un cliente que responde permisos."""

    def __init__(self, tmp_path, monkeypatch, hooks, *, respuesta=True, servidores=None, resume=None,
                 objetivo='Prueba de hooks.'):
        self.root = tmp_path / 'project'
        self.root.mkdir(exist_ok=True)
        monkeypatch.chdir(self.root)
        self.home = tmp_path / 'alfalfa'
        self.home.mkdir(exist_ok=True)
        if hooks is not None:
            (self.home / 'hooks.json').write_text(json.dumps(hooks))
        if servidores:
            (self.home / 'mcp.json').write_text(json.dumps({'servidores': servidores}))
        self.salida = io.StringIO()
        self.transport = Transport(self.salida)
        self.respuesta = respuesta
        self.m = TerminalManager(objetivo, home=self.home, transport=self.transport, resume=resume)

    async def _responder(self):
        vistos = set()
        while True:
            for ident in list(self.transport.pending):
                if ident not in vistos:
                    vistos.add(ident)
                    self.transport.receive(json.dumps(dict(tipo='permiso', id=ident, aprobado=self.respuesta)))
            await asyncio.sleep(0.01)

    def correr(self, pasos, prompt='Prueba de hooks.', cerrar=True):
        modelo = Modelo(pasos)

        async def run():
            cliente = asyncio.create_task(self._responder())
            try:
                with patch.object(NativeRuntime, 'request', modelo.request()):
                    return await self.m.run(prompt)
            finally:
                cliente.cancel()
                await asyncio.gather(cliente, return_exceptions=True)
                if cerrar:
                    await self.m.close_mcp()
        codigo = asyncio.run(asyncio.wait_for(run(), 120))
        if cerrar:
            self.m.close()
        return codigo, modelo

    def eventos(self, tipo):
        return [r for r in filas(self.salida) if r['tipo'] == tipo]

    def textos(self):
        return [r['texto'] for r in self.eventos('evento')]


def herramienta(payload, indice=-1):
    """Contenido JSON del último mensaje de herramienta que vio el modelo."""
    mensajes = [m for m in payload['messages'] if m['role'] == 'tool']
    return json.loads(mensajes[indice]['content'])


# ---------------------------------------------------------------- equivalencias

def test_tabla_de_equivalencias_e_inventario_completo():
    assert nombres.EQUIVALENCIAS['run_command'] == 'Bash'
    assert nombres.EQUIVALENCIAS['read_file'] == 'Read'
    assert nombres.EQUIVALENCIAS['edit_file'] == 'Edit'
    assert nombres.EQUIVALENCIAS['write_file'] == 'Write'
    # Inventario: toda herramienta nativa tiene equivalencia o un motivo para no tenerla.
    nativas = ({t['function']['name'] for t in TOOLS + ON_REQUEST_TOOLS + COMPLEMENTOS_TOOLS}
               | {'buscar_herramientas', 'apply_patch'})
    sin_inventariar = nativas - set(nombres.EQUIVALENCIAS) - set(nombres.SIN_EQUIVALENTE)
    assert not sin_inventariar
    assert not set(nombres.EQUIVALENCIAS) & set(nombres.SIN_EQUIVALENTE)
    # El despacho conserva el nombre nativo; el filtro compara alias y nativo.
    assert nombres.nombre_hook('run_command') == 'Bash'
    assert nombres.entradas_de_filtro('run_command') == ['Bash', 'run_command']
    assert nombres.entradas_de_filtro('search') == ['search']
    assert nombres.entradas_de_filtro('mcp__s__t') == ['mcp__s__t']
    # Como Codex (hook_names.rs): apply_patch también se elige con Edit y Write.
    assert nombres.entradas_de_filtro('apply_patch') == ['apply_patch', 'Edit', 'Write']
    assert nombres.nombre_hook('apply_patch') == 'apply_patch'


def test_adaptacion_de_entradas_a_claude(tmp_path):
    raiz = tmp_path.resolve()
    assert nombres.a_claude('run_command', {'argv': ['git', 'log', '-n', '1'], 'purpose': 'ver', 'timeout_seconds': 30},
                            raiz) == {'command': 'git log -n 1', 'description': 'ver', 'timeout': 30000}
    assert nombres.a_claude('run_command', {'argv': ['echo', 'a b'], 'purpose': 'p'}, raiz)['command'] == "echo 'a b'"
    assert nombres.a_claude('read_file', {'path': 'src/a.py', 'offset': 3}, raiz) == {
        'file_path': str(raiz / 'src/a.py'), 'offset': 3}
    assert nombres.a_claude('edit_file', {'path': 'a', 'old': 'x', 'new': 'y', 'expected_sha256': 'h'}, raiz) == {
        'file_path': str(raiz / 'a'), 'old_string': 'x', 'new_string': 'y', 'replace_all': False}
    assert nombres.a_claude('write_file', {'path': 'a', 'content': 'c', 'expected_sha256': 'NEW'}, raiz) == {
        'file_path': str(raiz / 'a'), 'content': 'c'}
    assert nombres.a_claude('search', {'query': 'q'}, raiz) == {'query': 'q'}


def test_vuelta_exacta_y_conservacion_de_campos_nativos(tmp_path):
    raiz = tmp_path.resolve()
    original = {'argv': ['echo', 'a'], 'purpose': 'p', 'cwd': 'server', 'needs_network': False}
    vuelta = nombres.desde_claude('run_command', original, {'command': "echo 'hola mundo' x", 'timeout': 2500}, raiz)
    assert vuelta == {'argv': ['echo', 'hola mundo', 'x'], 'purpose': 'p', 'cwd': 'server',
                      'needs_network': False, 'timeout_seconds': 3}
    vuelta = nombres.desde_claude('edit_file', {'path': 'a', 'old': 'x', 'new': 'y', 'expected_sha256': 'h'},
                                  {'file_path': str(raiz / 'b'), 'old_string': '1', 'new_string': '2'}, raiz)
    assert vuelta == {'path': 'b', 'old': '1', 'new': '2', 'expected_sha256': 'h'}
    assert nombres.desde_claude('read_file', {'path': 'a', 'limit': 5}, {'file_path': 'c'}, raiz) == {'path': 'c'}
    assert nombres.desde_claude('mcp__s__t', {'a': 1}, {'a': 2}, raiz) == {'a': 2}


@pytest.mark.parametrize('orden_shell', [
    'ls | wc -l', 'echo a > b', 'rm -rf $HOME', 'echo `id`', 'ls *.py', 'a && b', 'a; b', 'echo "$X"',
    'cat ~/.ssh/id_rsa', '(cd x)', 'echo {a,b}', 'echo "a\\"b"', "echo 'abierta", 'echo a\nrm x', '   ', 'a #c'])
def test_ordenes_de_shell_no_representables_se_rechazan(tmp_path, orden_shell):
    with pytest.raises(nombres.NoRepresentable):
        nombres.desde_claude('run_command', {'argv': ['true'], 'purpose': 'p'}, {'command': orden_shell}, tmp_path)


@pytest.mark.parametrize('nativo,nueva', [
    ('run_command', {'command': 'ls', 'run_in_background': True}),
    ('run_command', {'command': 'ls', 'sandbox': False}),
    ('run_command', {'command': 'ls', 'timeout': 'x'}),
    ('edit_file', {'file_path': 'a', 'old_string': 'x', 'new_string': 'y', 'replace_all': True}),
    ('edit_file', {'file_path': '/etc/hosts', 'old_string': 'x', 'new_string': 'y'}),
    ('write_file', {'file_path': '../fuera.txt', 'content': 'x'}),
    ('write_file', {'file_path': 'a', 'content': 3}),
    ('read_file', 'no es objeto'),
])
def test_conversiones_no_representables_se_rechazan(tmp_path, nativo, nueva):
    with pytest.raises(nombres.NoRepresentable):
        nombres.desde_claude(nativo, {'path': 'a', 'argv': ['true'], 'purpose': 'p'}, nueva, tmp_path / 'p')


# ---------------------------------------------------------------- configuración

def test_filtros_exacto_lista_regex_y_todo():
    assert Filtro('').coincide(['x']) and Filtro('*').coincide(['x'])
    assert Filtro('Bash').coincide(['Bash', 'run_command']) and not Filtro('Bash').coincide(['Read', 'read_file'])
    assert Filtro('Edit|Write').coincide(['Write', 'write_file'])
    assert Filtro('mcp__.*').coincide(['mcp__s__t']) and not Filtro('mcp__.*').coincide(['buscar_herramientas'])
    # Como Regex::is_match de Rust: busca en cualquier posición.
    assert Filtro('run_').coincide(['run_command']) is False  # sólo [A-Za-z0-9_|]: lista exacta
    assert Filtro('run_.*').coincide(['Bash', 'run_command'])


def test_lectura_de_hooks_json_y_diagnosticos(tmp_path):
    archivo = tmp_path / 'hooks.json'
    archivo.write_text(json.dumps({'description': 'd', 'otro': 1, 'hooks': {
        'PreToolUse': [grupo('echo a', 'Bash', timeout=5), grupo('echo b', '(', timeout=5),
                       {'matcher': 'Bash', 'hooks': [{'type': 'prompt', 'prompt': 'x'}]},
                       grupo('echo c', 'Read', timeout=9999), grupo('echo d', 'Read', timeout=0, async_=True)],
        'Stop': [grupo('echo e', 'Bash')],
        'Notification': [grupo('echo f')], 'PermissionRequest': [grupo('echo g')],
        'Inventado': [grupo('echo h')], 'PostToolUse': 'no es lista',
    }}).replace('async_', 'async'))
    manejadores, diagnosticos = leer_fuente(archivo, 'usuario')
    assert [(m.evento, m.comando, m.tiempo) for m in manejadores] == [
        ('PreToolUse', 'echo a', 5), ('PreToolUse', 'echo c', 600), ('PreToolUse', 'echo d', 60), ('Stop', 'echo e', 60)]
    codigos = [d.codigo for d in diagnosticos]
    for codigo in ('HOOK-CAMPO', 'HOOK-FILTRO', 'HOOK-TIPO', 'HOOK-PLAZO', 'HOOK-SIN-EQUIVALENTE', 'HOOK-EVENTO',
                   'HOOK-FORMATO'):
        assert codigo in codigos, codigo
    textos = ' '.join(d.texto for d in diagnosticos)
    assert 'Notification no tiene equivalente' in textos and 'PermissionRequest no tiene equivalente' in textos
    assert 'async corre en serie' in textos and 'Stop no filtra' in textos
    # Archivo roto: diagnóstico y ningún hook, sin excepción.
    archivo.write_text('{')
    assert leer_fuente(archivo, 'usuario')[0] == []
    assert leer_fuente(archivo, 'usuario')[1][0].codigo == 'HOOK-ARCHIVO'


def test_la_carpeta_de_trabajo_no_carga_sus_hooks(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    for ruta in ('.claude/settings.json', 'hooks/hooks.json', '.codex/hooks.json'):
        (root / ruta).parent.mkdir(parents=True, exist_ok=True)
        (root / ruta).write_text(json.dumps({'hooks': {'Stop': [grupo('touch hecho')]}}))
    s = Sesion(tmp_path, monkeypatch, None)
    try:
        assert s.m.hooks is None
        assert s.m.runtime().hooks is None
    finally:
        s.m.close()


def test_solo_lectura_y_agentes_del_integrador_no_tienen_hooks(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    root.mkdir()
    monkeypatch.chdir(root)
    home = tmp_path / 'alfalfa'
    home.mkdir()
    (home / 'hooks.json').write_text(json.dumps({'hooks': {'Stop': [grupo('touch hecho')]}}))
    m = TerminalManager('x', home=home, read_only=True)
    try:
        assert m.hooks is None and m.runtime().hooks is None
        # NativeRuntime sin el parámetro (agentes del integrador) no tiene hooks.
        assert NativeRuntime(m, dict(m.task)).hooks is None
    finally:
        m.close()


# ---------------------------------------------------------------- interpretación

def ejecucion(evento, codigo=0, stdout='', stderr='', error=None):
    return Ejecucion(Manejador(evento, Filtro(''), 'x', 60, 'usuario', 'hooks.json'), codigo, stdout, stderr, error)


def resultado(evento, **kwargs):
    r = Resultado()
    interpretar(evento, ejecucion(evento, **kwargs), r)
    return r


def pre(salida):
    return resultado('PreToolUse', stdout=json.dumps(salida))


def test_pre_tool_use_decisiones():
    r = pre({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                    'permissionDecisionReason': 'no toques eso', 'additionalContext': 'ctx'}})
    assert r.bloquear and r.motivo == 'no toques eso' and r.contextos == ['ctx']
    r = pre({'decision': 'block', 'reason': 'legado'})
    assert r.bloquear and r.motivo == 'legado'
    r = pre({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow',
                                    'updatedInput': {'command': 'ls'}}})
    assert not r.bloquear and r.entrada == {'command': 'ls'} and 'no concede permisos' in r.avisos[0]
    # allow sin updatedInput: no concede nada, sólo avisa; la herramienta sigue su curso.
    r = pre({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow'}})
    assert not r.bloquear and r.entrada is None and r.avisos
    r = pre({'decision': 'approve'})
    assert not r.bloquear and r.avisos
    r = resultado('PreToolUse', codigo=2, stderr='motivo por stderr\n')
    assert r.bloquear and r.motivo == 'motivo por stderr'


@pytest.mark.parametrize('kwargs', [
    dict(codigo=1, stderr='roto'), dict(codigo=3), dict(codigo=None, error='El hook no terminó en 1 s y se detuvo.'),
    dict(stdout='{"decision":'), dict(stdout='[1]'), dict(codigo=2, stderr='  '),
    dict(stdout=json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'ask'}})),
    dict(stdout=json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny'}})),
    dict(stdout=json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'updatedInput': {}}})),
    dict(stdout=json.dumps({'hookSpecificOutput': {'hookEventName': 'Stop'}})),
    dict(stdout=json.dumps({'continue': False})), dict(stdout=json.dumps({'decision': 'block'})),
    dict(stdout=json.dumps({'reason': 'sin decision'})), dict(stdout=json.dumps({'systemMessage': 3})),
])
def test_un_fallo_de_pre_tool_use_bloquea_la_herramienta(kwargs):
    r = resultado('PreToolUse', **kwargs)
    assert r.bloquear and r.motivo and r.entrada is None


def test_otros_eventos_codigos_y_salidas():
    r = resultado('PostToolUse', codigo=2, stderr='revisa el formato')
    assert not r.bloquear and r.contextos == ['revisa el formato']
    r = resultado('PostToolUse', stdout=json.dumps({'decision': 'block', 'reason': 'mal',
                                                     'hookSpecificOutput': {'hookEventName': 'PostToolUse',
                                                                            'additionalContext': 'más'}}))
    assert r.contextos == ['mal', 'más'] and not r.bloquear
    r = resultado('PostToolUse', stdout=json.dumps({'continue': False, 'stopReason': 'basta'}))
    assert r.detener and r.motivo_detener == 'basta'
    r = resultado('PostToolUse', codigo=1, stderr='roto')
    assert not r.bloquear and not r.detener and any('roto' in a for a in r.avisos)
    r = resultado('UserPromptSubmit', stdout='contexto en texto plano\n')
    assert r.contextos == ['contexto en texto plano']
    r = resultado('UserPromptSubmit', codigo=2, stderr='no se envía')
    assert r.bloquear and r.motivo == 'no se envía'
    r = resultado('Stop', stdout=json.dumps({'decision': 'block', 'reason': 'falta probar'}))
    assert r.bloquear and r.motivo == 'falta probar'
    r = resultado('SessionStart', stdout=json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart',
                                                                             'additionalContext': 'x' * 20000}}))
    assert r.contextos == ['x' * motor_hooks.MAX_CONTEXTO] and r.avisos
    r = resultado('Stop', stdout=json.dumps({'raro': 1}))
    assert 'sin equivalente' in r.avisos[0]
    r = resultado('PostToolUse', stdout=json.dumps({'hookSpecificOutput': {'hookEventName': 'PostToolUse',
                                                                            'updatedMCPToolOutput': {}}}))
    assert any('updatedMCPToolOutput' in a for a in r.avisos)


# ---------------------------------------------------------------- sesiones reales

@macos
def test_pre_tool_use_bash_bloquea_run_command_sin_efectos(tmp_path, monkeypatch):
    registro = tmp_path / 'registro.jsonl'
    s = Sesion(tmp_path, monkeypatch, {'hooks': {'PreToolUse': [
        grupo(orden(registro, 'bloquear', 'Prohibido crear efecto.txt'), 'Bash')]}})
    codigo, modelo = s.correr([llamada('run_command', {'argv': ['touch', 'efecto.txt'], 'purpose': 'crear'},
                                       'call-1')])
    assert not (s.root / 'efecto.txt').exists()
    fila, = leer(registro)
    entrada = fila['entrada']
    assert entrada['hook_event_name'] == 'PreToolUse' and entrada['tool_name'] == 'Bash'
    assert entrada['tool_input'] == {'command': 'touch efecto.txt', 'description': 'crear'}
    assert entrada['alfalfa_tool_name'] == 'run_command' and entrada['tool_use_id'] == 'call-1'
    assert entrada['session_id'] == s.m.task['id'] and entrada['cwd'] == str(s.root.resolve())
    assert entrada['transcript_path'].endswith(s.m.task['id'] + '.jsonl') and entrada['turn_id']
    assert entrada['permission_mode'] == 'default' and entrada['model'] == 'deepseek-v4.1-flash:cloud'
    visto = herramienta(modelo.payloads[1])
    assert 'Prohibido crear efecto.txt' in visto['error'] and 'No se ejecutó' in visto['error']
    assert not s.eventos('permiso')
    assert any('PreToolUse bloqueó run_command' in t for t in s.textos())


@macos
def test_filtros_sin_coincidencia_y_coincidencias_multiples_sin_duplicar(tmp_path, monkeypatch):
    a, b, c = (tmp_path / n for n in ('a.jsonl', 'b.jsonl', 'c.jsonl'))
    s = Sesion(tmp_path, monkeypatch, {'hooks': {'PreToolUse': [
        grupo(orden(a, 'nada'), 'Read'),                 # no coincide con run_command
        grupo(orden(b, 'nada'), 'Bash'),
        grupo(orden(b, 'nada'), 'run_command|Bash'),     # la misma orden: una sola vez
        grupo(orden(c, 'nada'), 'run_.*'),               # otra orden que también coincide
    ]}})
    codigo, _ = s.correr([llamada('run_command', {'argv': ['true'], 'purpose': 'nada'})])
    assert leer(a) == [] and len(leer(b)) == 1 and len(leer(c)) == 1


@macos
def test_orden_frecuencia_y_correlacion_de_los_cinco_eventos(tmp_path, monkeypatch):
    registro = tmp_path / 'registro.jsonl'
    eventos = ('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop')
    s = Sesion(tmp_path, monkeypatch, {'hooks': {e: [grupo(orden(registro, 'nada'))] for e in eventos}})
    (s.root / 'leeme.txt').write_text('hola\n')
    s.correr([llamada('read_file', {'path': 'leeme.txt'}, 'call-r'),
              llamada('run_command', {'argv': ['true'], 'purpose': 'p'}, 'call-b')], cerrar=False)
    filas_ = [f['entrada'] for f in leer(registro)]
    assert [f['hook_event_name'] for f in filas_] == ['SessionStart', 'UserPromptSubmit', 'PreToolUse',
                                                       'PostToolUse', 'PreToolUse', 'PostToolUse', 'Stop']
    assert filas_[0]['source'] == 'startup' and filas_[1]['prompt'] == 'Prueba de hooks.'
    assert len({f['session_id'] for f in filas_}) == 1 and len({f['turn_id'] for f in filas_}) == 1
    assert [f['tool_use_id'] for f in filas_[2:6]] == ['call-r', 'call-r', 'call-b', 'call-b']
    assert filas_[2]['tool_name'] == 'Read' and filas_[2]['tool_input'] == {'file_path': str(s.root.resolve() / 'leeme.txt')}
    assert filas_[3]['tool_response']['text'] == 'hola\n' and filas_[5]['tool_response']['exit_code'] == 0
    assert filas_[6]['stop_hook_active'] is False and filas_[6]['last_assistant_message'] == 'Hecho.'
    # Segundo turno de la misma sesión: sin SessionStart y con otro turn_id.
    s.correr([], prompt='Otro mensaje.')
    nuevas = [f['entrada'] for f in leer(registro)][7:]
    assert [f['hook_event_name'] for f in nuevas] == ['UserPromptSubmit', 'Stop']
    assert nuevas[0]['turn_id'] != filas_[0]['turn_id'] and nuevas[0]['session_id'] == filas_[0]['session_id']
    # La sesión guarda la evidencia de cada ejecución.
    estado = json.loads((s.m.directory / 'native' / s.m.task['id'] / 'session.json').read_text())
    assert [h['evento'] for h in estado['hooks']] == [f['hook_event_name'] for f in filas_ + nuevas]


@macos
def test_reanudar_dispara_session_start_resume(tmp_path, monkeypatch):
    registro = tmp_path / 'registro.jsonl'
    hooks = {'hooks': {'SessionStart': [grupo(orden(registro, 'nada'), 'startup|resume')]}}
    s = Sesion(tmp_path, monkeypatch, hooks)
    s.correr([{'role': 'assistant', 'content': ''}, {'role': 'assistant', 'content': ''},
              {'role': 'assistant', 'content': ''}])  # sin respuesta: la sesión no termina bien
    ident = s.m.task['id']
    s2 = Sesion(tmp_path, monkeypatch, hooks, resume=ident)
    s2.correr([])
    assert [f['entrada']['source'] for f in leer(registro)] == ['startup', 'resume']


@macos
def test_user_prompt_submit_bloquea_sin_llamar_al_modelo_y_session_start_da_contexto(tmp_path, monkeypatch):
    registro = tmp_path / 'registro.jsonl'
    s = Sesion(tmp_path, monkeypatch, {'hooks': {
        'UserPromptSubmit': [grupo(orden(registro, 'bloquear', 'Ese mensaje no se envía'))]}})
    codigo, modelo = s.correr([])
    assert modelo.payloads == [] and s.m.task['status'] == 'blocked' and codigo == 1
    assert 'Ese mensaje no se envía' in s.m.task['summary']
    (tmp_path / 'otra').mkdir()
    s = Sesion(tmp_path / 'otra', monkeypatch, {'hooks': {
        'SessionStart': [grupo(orden(registro, 'texto', 'Rama de trabajo: hooks'))],
        'UserPromptSubmit': [grupo(orden(registro, 'json', json.dumps({'hookSpecificOutput': {
            'hookEventName': 'UserPromptSubmit', 'additionalContext': 'Usa pytest -q'}})))]}})
    codigo, modelo = s.correr([])
    mensajes = modelo.payloads[0]['messages']
    assert mensajes[-2] == {'role': 'user', 'content': 'Prueba de hooks.'}
    assert mensajes[-1]['content'].startswith('Contexto añadido por los hooks que instaló el usuario')
    assert 'Rama de trabajo: hooks' in mensajes[-1]['content'] and 'Usa pytest -q' in mensajes[-1]['content']
    assert 'Hay hooks que instaló el usuario' in mensajes[0]['content']


@macos
def test_post_tool_use_da_contexto_y_continue_false_detiene_el_turno(tmp_path, monkeypatch):
    registro = tmp_path / 'registro.jsonl'
    s = Sesion(tmp_path, monkeypatch, {'hooks': {'PostToolUse': [
        grupo(orden(registro, 'json', json.dumps({'decision': 'block', 'reason': 'Formatea con ruff'})), 'Read'),
        grupo(orden(registro, 'json', json.dumps({'continue': False, 'stopReason': 'Basta por hoy'})), 'Bash')]}})
    (s.root / 'a.txt').write_text('a')
    codigo, modelo = s.correr([llamada('read_file', {'path': 'a.txt'}),
                               llamada('run_command', {'argv': ['true'], 'purpose': 'p'}),
                               llamada('run_command', {'argv': ['touch', 'nunca'], 'purpose': 'p'})])
    assert herramienta(modelo.payloads[1])['hooks_contexto'] == ['Formatea con ruff']
    assert len(modelo.payloads) == 2 and not (s.root / 'nunca').exists()
    assert s.m.task['status'] == 'blocked' and 'Basta por hoy' in s.m.task['summary']


@macos
def test_stop_pide_seguir_con_proteccion_contra_bucles(tmp_path, monkeypatch):
    registro = tmp_path / 'registro.jsonl'
    s = Sesion(tmp_path, monkeypatch, {'hooks': {'Stop': [
        grupo(orden(registro, 'json', json.dumps({'decision': 'block', 'reason': 'Ejecuta las pruebas'})))]}})
    codigo, modelo = s.correr([])
    activos = [f['entrada']['stop_hook_active'] for f in leer(registro)]
    assert activos == [False, True, True, True]
    assert len(modelo.payloads) == 4
    assert modelo.payloads[1]['messages'][-1]['content'].endswith('Ejecuta las pruebas')
    assert any('para no entrar en bucle' in t for t in s.textos())


# ---------------------------------------------------------------- reescritura y permisos

def reescribir(comando):
    return json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow',
                                              'updatedInput': {'command': comando}}})


@macos
@pytest.mark.parametrize('respuesta', [False, True])
def test_entrada_reescrita_se_valida_y_autoriza_de_nuevo(tmp_path, monkeypatch, respuesta):
    registro = tmp_path / 'registro.jsonl'
    s = Sesion(tmp_path, monkeypatch, {'hooks': {'PreToolUse': [
        grupo(orden(registro, 'json', reescribir('rm -f borrar.txt')), 'Bash')]}}, respuesta=respuesta)
    (s.root / 'borrar.txt').write_text('x')
    codigo, modelo = s.correr([llamada('run_command', {'argv': ['echo', 'inofensivo'], 'purpose': 'p'})])
    # La orden original no pedía permiso; la reescrita es peligrosa y sí lo pide,
    # con el argv final: lo aprobado es exactamente lo que se ejecuta.
    permiso, = s.eventos('permiso')
    assert permiso['argv'] == ['rm', '-f', 'borrar.txt']
    assert (s.root / 'borrar.txt').exists() is (not respuesta)
    visto = herramienta(modelo.payloads[1])
    assert visto['hooks_entrada_final']['argv'] == ['rm', '-f', 'borrar.txt']
    if not respuesta:
        assert 'rechazado' in visto['error']


@macos
def test_una_reescritura_hacia_una_orden_prohibida_por_las_reglas_no_se_ejecuta(tmp_path, monkeypatch):
    registro = tmp_path / 'registro.jsonl'
    (tmp_path / 'alfalfa').mkdir()
    (tmp_path / 'alfalfa' / 'reglas.toml').write_text(
        '[[regla]]\npatron = ["touch", "prohibido"]\ndecision = "prohibida"\n')
    s = Sesion(tmp_path, monkeypatch, {'hooks': {'PreToolUse': [
        grupo(orden(registro, 'json', reescribir('touch prohibido')), 'Bash')]}})
    codigo, modelo = s.correr([llamada('run_command', {'argv': ['touch', 'permitido'], 'purpose': 'p'})])
    assert not (s.root / 'prohibido').exists() and not (s.root / 'permitido').exists()
    assert 'prohibida por una regla del usuario' in herramienta(modelo.payloads[1])['error']


@macos
def test_reescritura_con_shell_se_rechaza_sin_ejecutar(tmp_path, monkeypatch):
    registro = tmp_path / 'registro.jsonl'
    s = Sesion(tmp_path, monkeypatch, {'hooks': {'PreToolUse': [
        grupo(orden(registro, 'json', reescribir('touch a && touch b')), 'Bash')]}})
    codigo, modelo = s.correr([llamada('run_command', {'argv': ['touch', 'c'], 'purpose': 'p'})])
    assert not any((s.root / n).exists() for n in 'abc')
    assert 'no representable' in herramienta(modelo.payloads[1])['error'] and not s.eventos('permiso')


@macos
@pytest.mark.parametrize('respuesta', [False, True])
def test_allow_de_un_hook_no_sustituye_el_permiso_mcp(tmp_path, monkeypatch, respuesta):
    registro = tmp_path / 'registro.jsonl'
    permitir = json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow'}})
    s = Sesion(tmp_path, monkeypatch, {'hooks': {
        'PreToolUse': [grupo(orden(registro, 'json', permitir), 'mcp__.*')],
        'PostToolUse': [grupo(orden(registro, 'nada'), 'mcp__.*')]}},
        servidores={'prueba': dict(orden=sys.executable, argumentos=[SERVIDOR_MCP], tiempo_inicio=60)},
        respuesta=respuesta)
    codigo, modelo = s.correr([
        llamada('buscar_herramientas', {'consulta': 'contador'}),
        llamada('buscar_herramientas', {'consulta': 'select:mcp__prueba__contar'}),
        llamada('mcp__prueba__contar', {'nota': 'hook'}, 'call-mcp')])
    contador = s.root / 'contador.txt'
    assert (contador.exists() and int(contador.read_text()) == 1) is respuesta
    permiso, = s.eventos('permiso')  # el allow del hook no evitó preguntar
    assert permiso['mcp']['nombre'] == 'mcp__prueba__contar'
    entradas = [f['entrada'] for f in leer(registro)]
    # Buscar y cargar esquemas no dispara los hooks de herramientas MCP.
    assert [(e['hook_event_name'], e['tool_name'], e['tool_use_id']) for e in entradas] == [
        ('PreToolUse', 'mcp__prueba__contar', 'call-mcp'), ('PostToolUse', 'mcp__prueba__contar', 'call-mcp')]
    assert entradas[0]['tool_input'] == {'nota': 'hook'}
    assert any('no concede permisos' in t for t in s.textos())


# ---------------------------------------------------------------- entorno y sandbox

CENTINELAS = {'OLLAMA_API_KEY': 'CENTINELA-OLLAMA-5e4d', 'OTRO_SECRETO': 'CENTINELA-OTRO-8c7b',
              'OLLAMA_API_KEY': 'CENTINELA-OLLAMA2-3a2f'}


@macos
def test_entorno_minimo_sin_secretos_y_variables_declaradas(tmp_path, monkeypatch):
    for clave, valor in CENTINELAS.items():
        monkeypatch.setenv(clave, valor)
    registro, volcado = tmp_path / 'registro.jsonl', tmp_path / 'entorno.json'
    s = Sesion(tmp_path, monkeypatch, {'entorno': {'MI_VARIABLE': 'declarada', 'OLLAMA_X': 'no', 'PATH': '/x'},
                                       'hooks': {'Stop': [grupo(orden(registro, 'entorno', str(volcado)))]}})
    s.correr([])
    entorno = json.loads(volcado.read_text())
    texto = json.dumps(entorno)
    assert not any(v in texto for v in CENTINELAS.values())
    assert entorno['MI_VARIABLE'] == 'declarada' and 'OLLAMA_X' not in entorno
    assert entorno['CLAUDE_PROJECT_DIR'] == str(s.root.resolve()) and entorno['PATH'] == os.environ['PATH']
    permitidas = {'PATH', 'HOME', 'TMPDIR', 'LANG', 'CLAUDE_PROJECT_DIR', 'MI_VARIABLE',
                  'PWD', 'SHLVL', '_', '__CF_USER_TEXT_ENCODING', 'LC_CTYPE'}
    assert set(entorno) <= permitidas, set(entorno) - permitidas
    assert any('OLLAMA_X' in t for t in s.textos())


@macos
def test_variables_del_complemento(tmp_path):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    volcado = tmp_path / 'entorno.json'
    complemento = tmp_path / 'complemento'
    motor = MotorHooks.desde_fuentes([dict(origen='complemento:demo', archivo='hooks.json', raiz_complemento=complemento,
                                           datos_complemento=tmp_path / 'datos',
                                           datos={'hooks': {'Stop': [grupo(orden(tmp_path / 'r', 'entorno', str(volcado)))]}})],
                                     raiz=raiz)
    asyncio.run(motor.disparar('Stop', {}))
    entorno = json.loads(volcado.read_text())
    assert entorno['CLAUDE_PLUGIN_ROOT'] == entorno['PLUGIN_ROOT'] == str(complemento)
    assert entorno['CLAUDE_PLUGIN_DATA'] == str(tmp_path / 'datos')


@macos
def test_sandbox_impide_escribir_fuera_proteger_git_y_usar_la_red(tmp_path, monkeypatch):
    fuera = Path.home() / ('.alfalfa-hook-prueba-' + uuid4().hex[:8])
    servidor = socket.socket()
    servidor.bind(('127.0.0.1', 0))
    servidor.listen(1)
    puerto = servidor.getsockname()[1]
    registro = tmp_path / 'registro.jsonl'
    root = tmp_path / 'project'
    (root / '.git').mkdir(parents=True)
    rutas = ','.join([str(fuera), str(root / '.git' / 'config'), str(root / 'dentro.txt')])
    s = Sesion(tmp_path, monkeypatch, {'hooks': {'Stop': [grupo(orden(registro, 'escribir', rutas)),
                                                          grupo(orden(registro, 'red', str(puerto)))]}})
    try:
        s.correr([])
    finally:
        servidor.close()
        if fuera.exists():
            fuera.unlink()
            pytest.fail('El hook escribió fuera de la carpeta de trabajo')
    escrito = json.loads(next(t for t in s.textos() if t.startswith('Hooks · {')).removeprefix('Hooks · '))
    assert escrito[str(root / 'dentro.txt')] == 'escrito'
    assert 'Operation not permitted' in escrito[str(fuera)]
    assert 'Operation not permitted' in escrito[str(root / '.git' / 'config')]
    assert any(t == 'Hooks · red=bloqueado: PermissionError' or t.startswith('Hooks · red=bloqueado')
               for t in s.textos())


# ---------------------------------------------------------------- límites

def motor_de(tmp_path, evento, comando, **extra):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir(exist_ok=True)
    from alfalfa.seatbelt import SeatbeltSandbox
    return MotorHooks.desde_fuentes([dict(origen='usuario', archivo='hooks.json',
                                          datos={'hooks': {evento: [grupo(comando, **extra)]}})],
                                    raiz=raiz, sandbox=lambda r: SeatbeltSandbox(r, network=False))


@macos
def test_plazo_salida_grande_y_codigos_bloquean_pre_tool_use(tmp_path):
    registro = tmp_path / 'r.jsonl'
    casos = [(orden(registro, 'dormir', '30'), dict(timeout=1), 'no terminó en 1 s'),
             (orden(registro, 'grande'), {}, 'supera 64 KB'),
             (orden(registro, 'salir', '1'), {}, 'código 1'),
             (orden(registro, 'json', '{roto'), {}, 'JSON inválido'),
             ('/no/existe/hook', {}, 'código 127')]
    for comando, extra, esperado in casos:
        motor = motor_de(tmp_path, 'PreToolUse', comando, **extra)
        inicio = time.monotonic()
        r = asyncio.run(motor.disparar('PreToolUse', {'tool_name': 'Bash'}, ['Bash']))
        assert r.bloquear and esperado in r.motivo and 'no se ejecutó' in r.motivo, (comando, r.motivo)
        assert time.monotonic() - inicio < 20


@macos
def test_cancelar_detiene_el_hook_y_su_grupo(tmp_path):
    registro = tmp_path / 'r.jsonl'
    motor = motor_de(tmp_path, 'Stop', orden(registro, 'dormir', '60'))

    async def run():
        tarea = asyncio.create_task(motor.disparar('Stop', {}))
        while not leer(registro):
            await asyncio.sleep(0.05)
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea
    asyncio.run(asyncio.wait_for(run(), 30))
    pid = leer(registro)[0]['pid']
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail('el hook siguió vivo tras cancelar')


def test_sin_sandbox_disponible_el_hook_no_se_lanza(tmp_path):
    registro = tmp_path / 'r.jsonl'

    class Roto:
        async def prepare(self, argv):
            raise RuntimeError('sin sandbox')
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    motor = MotorHooks.desde_fuentes([dict(origen='usuario', archivo='hooks.json', datos={'hooks': {
        'PreToolUse': [grupo(orden(registro, 'nada'))]}})], raiz=raiz, sandbox=lambda r: Roto())
    r = asyncio.run(motor.disparar('PreToolUse', {}, ['Bash']))
    assert r.bloquear and 'sandbox' in r.motivo and not registro.exists()


# ---------------------------------------------------------------- complementos

def complemento(raiz, nombre='demo-hooks', hooks=None, inline=None):
    (raiz / '.claude-plugin').mkdir(parents=True)
    manifiesto = {'name': nombre, 'version': '1.0.0'}
    if inline is not None:
        manifiesto['hooks'] = inline
    (raiz / '.claude-plugin' / 'plugin.json').write_text(json.dumps(manifiesto))
    if hooks is not None:
        (raiz / 'hooks').mkdir()
        (raiz / 'hooks' / 'hooks.json').write_text(json.dumps(hooks))
    return raiz


def autorizar(home, raices, politicas=None):
    home.mkdir(parents=True, exist_ok=True)
    (home / 'complementos.json').write_text(json.dumps({'raices': [{'raiz': str(r)} for r in raices],
                                                        'politicas': politicas or {}}))


@macos
def test_hooks_de_un_complemento_autorizado_bloquean_con_su_raiz(tmp_path, monkeypatch):
    registro, volcado = tmp_path / 'registro.jsonl', tmp_path / 'entorno.json'
    plug = complemento(tmp_path / 'plug', hooks={'hooks': {
        'PreToolUse': [grupo(orden(registro, 'bloquear', 'El complemento no deja tocar eso'), 'Bash')],
        'Stop': [grupo(orden(registro, 'entorno', str(volcado)))]}})
    autorizar(tmp_path / 'alfalfa', [plug], {'demo-hooks': {'hooks': {'entorno': {'NIVEL': 'alto'}}}})
    s = Sesion(tmp_path, monkeypatch, None)
    codigo, modelo = s.correr([llamada('run_command', {'argv': ['touch', 'efecto.txt'], 'purpose': 'p'})])
    assert not (s.root / 'efecto.txt').exists()
    assert 'El complemento no deja tocar eso' in herramienta(modelo.payloads[1])['error']
    entorno = json.loads(volcado.read_text())
    assert entorno['CLAUDE_PLUGIN_ROOT'] == entorno['PLUGIN_ROOT'] == str(plug.resolve())
    assert entorno['NIVEL'] == 'alto'
    estado = json.loads((s.m.directory / 'native' / s.m.task['id'] / 'session.json').read_text())
    assert {h['origen'] for h in estado['hooks']} == {'complemento:demo-hooks'}
    # El turno del hook es el de la sesión (arnes/sesiones).
    assert {f['entrada']['turn_id'] for f in leer(registro)} == {s.m.task['turnos'][-1]['id']}


@macos
def test_hooks_inline_del_manifiesto_y_politica_que_los_apaga(tmp_path, monkeypatch):
    registro = tmp_path / 'registro.jsonl'
    plug = complemento(tmp_path / 'plug', inline={'hooks': {'Stop': [grupo(orden(registro, 'nada'))]}})
    autorizar(tmp_path / 'alfalfa', [plug])
    s = Sesion(tmp_path, monkeypatch, None)
    s.correr([])
    assert len(leer(registro)) == 1
    autorizar(tmp_path / 'alfalfa', [plug], {'demo-hooks': {'hooks': {'activo': False}}})
    s = Sesion(tmp_path, monkeypatch, None)
    assert s.m.hooks is None
    s.correr([])
    assert len(leer(registro)) == 1


def test_hooks_de_complementos_no_autorizados_no_se_cargan(tmp_path, monkeypatch):
    registro = tmp_path / 'registro.jsonl'
    hooks = {'hooks': {'Stop': [grupo(orden(registro, 'nada'))]}}
    complemento(tmp_path / 'project' / 'plugin-local', hooks=hooks)
    complemento(tmp_path / 'claude-plugins' / 'demo', hooks=hooks)
    monkeypatch.setenv('HOME', str(tmp_path))
    autorizar(tmp_path / 'alfalfa', [])
    s = Sesion(tmp_path, monkeypatch, None)
    try:
        assert s.m.hooks is None and s.m.complementos.fuentes_hooks() == []
    finally:
        s.m.close()


def test_el_listado_de_complementos_muestra_sus_hooks(tmp_path, monkeypatch, capsys):
    plug = complemento(tmp_path / 'plug', hooks={'hooks': {'Stop': [grupo('true')]}})
    autorizar(tmp_path / 'alfalfa', [plug])
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path / 'alfalfa'))
    monkeypatch.chdir(tmp_path)
    from alfalfa.terminal import main
    assert main(['--complementos', '--json']) == 0
    salida = json.loads(capsys.readouterr().out)
    fila, = salida['complementos']
    assert fila['capacidades']['hooks'] == ['hooks/hooks.json'] and 'hooks' not in fila['ignoradas']


def test_licencia_y_procedencia():
    import re
    carpeta = Path(motor_hooks.__file__).resolve().parent
    assert re.search(r'Copyright\s2025\sOpenAI', (carpeta / 'NOTICE').read_text())
    assert 'Apache License' in (carpeta / 'LICENSE').read_text()
    assert 'faeb379646bdbad016e11f7c9ca91113cad4bc9d' in (carpeta / 'PROCEDENCIA.md').read_text()
