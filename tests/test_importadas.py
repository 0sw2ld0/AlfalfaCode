"""Proyectos de OpenCode, Codex y Claude Code en AlfalfaCode sin tocar nada (alfalfa/importadas).

Todo corre en un HOME y un proyecto de demo dentro de tmp_path: nunca se leen las
configuraciones (~/.codex, ~/.config/opencode, ~/.claude) de quien corre la suite.
"""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from alfalfa import agents_md, importadas, subagentes
from alfalfa.importadas import Importadas, formatos, traduccion
from alfalfa.importadas import orden as orden_importar
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from tests.test_alfalfa_hooks import Sesion, llamada
from tests.test_subagentes import Guion, nombres_de_herramientas

SERVIDOR = str(Path(__file__).with_name('mcp_servidor_prueba.py'))
RAIZ = Path(__file__).resolve().parents[1]
SECRETO = 'clave-de-demo-que-no-se-copia-123'
macos = pytest.mark.skipif(sys.platform != 'darwin', reason='stdio corre en Seatbelt, sólo en macOS')


@pytest.fixture
def casa(tmp_path, monkeypatch):
    casa = tmp_path / 'casa'
    (casa / '.codex').mkdir(parents=True)
    (casa / '.config' / 'opencode').mkdir(parents=True)
    monkeypatch.setattr(importadas, 'CASA', casa)
    monkeypatch.setenv('DEMO_TOKEN', SECRETO)
    return casa


@pytest.fixture
def demo(tmp_path, casa):
    """El proyecto de demo: AGENTS.md, opencode.json (un MCP, un agente y un comando) y .codex/config.toml."""
    raiz = tmp_path / 'project'
    (raiz / '.git').mkdir(parents=True)
    (raiz / 'AGENTS.md').write_text('# Guía\nUsa pytest.\n')
    (raiz / 'opencode.json').write_text('''{
  "$schema": "https://opencode.ai/config.json",
  // OpenCode admite comentarios y comas finales (JSONC)
  "mcp": {
    "eco": {"type": "local", "command": [%s, %s], "environment": {"TOKEN": "{env:DEMO_TOKEN}"},
            "enabled": true, "timeout": 60000},
  },
  "agent": {"revisor": {"description": "Revisa cambios sin editarlos", "mode": "subagent",
                        "prompt": "Revisa con lupa y no edites.", "permission": {"edit": "deny", "bash": "deny"}}},
  "command": {"probar": {"template": "Ejecuta las pruebas de $ARGUMENTS y resume // sin miedo.",
                         "description": "Corre pruebas"}}
}''' % (json.dumps(sys.executable), json.dumps(SERVIDOR)))
    (raiz / '.codex').mkdir()
    (raiz / '.codex' / 'config.toml').write_text(
        'developer_instructions = "Responde en español."\n\n[mcp_servers.git_tools]\ncommand = "uvx"\n'
        'args = ["mcp-server-git"]\nenv_vars = ["DEMO_TOKEN"]\nenabled_tools = ["log"]\ntool_timeout_sec = 45\n')
    return raiz


def elemento(imp, tipo, nombre):
    return next(e for e in imp.elementos if e.tipo == tipo and e.nombre == nombre)


# ------------------------------------------------------------------ formatos

def test_jsonc_sin_comentarios_ni_comas_finales_y_cadenas_intactas(tmp_path):
    ruta = tmp_path / 'x.jsonc'
    ruta.write_text('{\n // uno\n "url": "https://a.b/c", /* dos */ "t": "a // b, ]",\n "l": [1, 2,],\n}')
    assert formatos.leer_jsonc(ruta) == {'url': 'https://a.b/c', 't': 'a // b, ]', 'l': [1, 2]}
    ruta.write_text('{"a": }')
    with pytest.raises(formatos.Invalido, match='línea 1'):
        formatos.leer_jsonc(ruta)


def test_cabecera_con_mapa_anidado_lista_y_bloque():
    datos, cuerpo = formatos.cabecera('---\ndescription: "Hola: mundo"\nmode: subagent\ntools:\n  bash: false\n'
                                      '  write: true\nlista:\n  - a\n  - b\nprompt: |\n  uno\n  dos\n---\nCuerpo\n')
    assert datos == {'description': 'Hola: mundo', 'mode': 'subagent', 'tools': {'bash': False, 'write': True},
                     'lista': ['a', 'b'], 'prompt': 'uno\ndos'}
    assert cuerpo == 'Cuerpo'
    assert formatos.cabecera('sin cabecera') == ({}, 'sin cabecera')


# ------------------------------------------------------------------ servidores MCP

def test_mcp_de_opencode_v1_v2_codex_y_claude(tmp_path, casa, monkeypatch):
    monkeypatch.setenv('FIGMA_TOKEN', 'figma-token-largo')
    raiz = tmp_path / 'p'
    (raiz / '.git').mkdir(parents=True)
    (raiz / '.opencode').mkdir()
    (raiz / '.opencode' / 'opencode.jsonc').write_text(json.dumps({'mcp': {'servers': {
        'v2': {'type': 'remote', 'url': 'https://mcp.example.com/mcp',
               'headers': {'Authorization': 'Bearer {env:DEMO_TOKEN}'},
               'disabled': False, 'timeout': {'startup': 5000, 'execution': 9999999}},
        'apagado': {'type': 'local', 'command': ['x'], 'disabled': True}}}}))
    (raiz / '.mcp.json').write_text(json.dumps({'mcpServers': {
        'claude': {'command': '${HERRAMIENTA:-npx}', 'args': ['-y', 'srv'], 'env': {'K': '${DEMO_TOKEN}'}},
        'viejo': {'type': 'sse', 'url': 'https://x.y/sse'}}}))
    (casa / '.codex' / 'config.toml').write_text(
        '[mcp_servers.figma]\nurl = "https://mcp.figma.com/mcp"\nbearer_token_env_var = "FIGMA_TOKEN"\n'
        'http_headers = { "X-Region" = "us" }\n')
    imp = Importadas(raiz, tmp_path / 'alfalfa')
    v2 = elemento(imp, 'mcp', 'v2').valor
    assert v2.cabeceras == {'Authorization': 'Bearer ' + SECRETO}
    assert (v2.tiempo_inicio, v2.tiempo_herramienta) == (5.0, 3600.0)  # se recorta al máximo
    assert elemento(imp, 'mcp', 'apagado').estado == 'desactivado'
    claude = elemento(imp, 'mcp', 'claude')
    assert claude.valor.orden == 'npx' and claude.valor.entorno == {'K': SECRETO}
    assert claude.valor.cwd == str(raiz.resolve()) and claude.herramienta == 'Claude Code'
    assert elemento(imp, 'mcp', 'viejo').estado == 'invalido'
    figma = elemento(imp, 'mcp', 'figma')
    assert figma.ambito == 'usuario' and figma.valor.cabeceras == {'X-Region': 'us',
                                                                     'Authorization': 'Bearer figma-token-largo'}
    # La lista nunca enseña argumentos, entorno ni cabeceras.
    assert SECRETO not in json.dumps(imp.informe()) and 'figma-token-largo' not in json.dumps(imp.informe())


@pytest.mark.parametrize('referencia', ['{env:OPENAI_API_KEY}', '{env:NO_EXISTE}', '{file:~/.ssh/id_rsa}'])
def test_variables_prohibidas_ausentes_o_archivos_invalidan_el_servidor(tmp_path, casa, monkeypatch, referencia):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-del-usuario')
    (tmp_path / 'opencode.json').write_text(json.dumps({'mcp': {'x': {
        'type': 'local', 'command': ['srv'], 'environment': {'A': referencia}}}}))
    x = elemento(Importadas(tmp_path, tmp_path / 'alfalfa'), 'mcp', 'x')
    assert x.estado == 'invalido' and x.valor is None
    assert 'sk-del-usuario' not in x.problema


def test_precedencia_proyecto_cercano_y_dot_opencode_y_duplicados(tmp_path, casa):
    raiz = tmp_path / 'p'
    sub = raiz / 'sub'
    (raiz / '.git').mkdir(parents=True)
    (sub / '.opencode').mkdir(parents=True)

    def servidor(nombre):
        return json.dumps({'mcp': {'docs': {'type': 'local', 'command': [nombre]}}})
    (raiz / 'opencode.json').write_text(servidor('raiz'))
    (sub / 'opencode.json').write_text(servidor('suelto'))
    (sub / '.opencode' / 'opencode.json').write_text(servidor('punto'))
    (casa / '.config' / 'opencode' / 'opencode.json').write_text(servidor('usuario'))
    imp = Importadas(sub, tmp_path / 'alfalfa')
    filas = [(e.valor.orden, e.estado) for e in imp.elementos if e.tipo == 'mcp']
    assert filas == [('punto', 'pendiente'), ('suelto', 'duplicado'), ('raiz', 'duplicado'),
                     ('usuario', 'duplicado')]
    imp.autorizar(imp.autorizables())
    # El mcp.json propio de AlfalfaCode gana: el importado con el mismo id no se registra.
    assert imp.servidores_mcp(reservados=['docs']) == []
    assert elemento(imp, 'mcp', 'docs').estado == 'duplicado'


# ------------------------------------------------------------------ autorización

def test_autorizar_una_vez_cambio_y_retirar_sin_copiar_claves(demo, tmp_path):
    hogar = tmp_path / 'alfalfa'
    imp = Importadas(demo, hogar)
    assert {(e.tipo, e.nombre) for e in imp.autorizables()} == {('mcp', 'eco'), ('mcp', 'git-tools'),
                                                                 ('agente', 'revisor')}
    assert imp.servidores_mcp() == [] and imp.agentes() == {}
    # Comandos e instrucciones no necesitan autorización.
    assert elemento(imp, 'comando', 'probar').estado == 'activo'
    imp.autorizar([elemento(imp, 'mcp', 'eco'), elemento(imp, 'agente', 'revisor')])
    guardado = (hogar / 'importadas.json').read_text()
    assert SECRETO not in guardado and 'TOKEN' not in guardado
    assert oct((hogar / 'importadas.json').stat().st_mode)[-3:] == '600'
    imp = Importadas(demo, hogar)
    assert [s.id for s in imp.servidores_mcp()] == ['eco'] and imp.servidores_mcp()[0].red is True
    assert set(imp.agentes()) == {'revisor'}
    # Otra definición (aquí, otro argumento) vuelve a preguntar.
    datos = (demo / 'opencode.json').read_text().replace('"enabled": true', '"enabled": true, "cwd": "."')
    (demo / 'opencode.json').write_text(datos)
    imp = Importadas(demo, hogar)
    assert elemento(imp, 'mcp', 'eco').estado == 'cambio' and imp.servidores_mcp() == []
    # El mismo nombre en otro proyecto no hereda la autorización.
    otro = tmp_path / 'otro'
    otro.mkdir()
    (otro / 'opencode.json').write_text((demo / 'opencode.json').read_text())
    assert elemento(Importadas(otro, hogar), 'agente', 'revisor').estado == 'pendiente'
    imp = Importadas(demo, hogar)
    imp.retirar([elemento(imp, 'agente', 'revisor')])
    assert Importadas(demo, hogar).agentes() == {}


def test_importadas_json_roto_no_autoriza_nada(demo, tmp_path):
    hogar = tmp_path / 'alfalfa'
    hogar.mkdir()
    (hogar / 'importadas.json').write_text('{roto')
    imp = Importadas(demo, hogar)
    assert imp.servidores_mcp() == [] and any('importadas.json inválido' in a for a in imp.avisos)


# ------------------------------------------------------------------ agentes y comandos

def test_agentes_de_opencode_en_md_y_json_como_subagentes(demo, tmp_path):
    carpeta = demo / '.opencode' / 'agent' / 'equipo'
    carpeta.mkdir(parents=True)
    (carpeta / 'doc.md').write_text('---\ndescription: Escribe documentación\nmodel: anthropic/claude-sonnet-5-5#high\n'
                                    'tools:\n  bash: false\n---\nEscribe documentación clara.\n')
    (demo / '.opencode' / 'agent' / 'oculto.md').write_text('---\ndescription: x\nhidden: true\n---\nx')
    hogar = tmp_path / 'alfalfa'
    imp = Importadas(demo, hogar)
    assert elemento(imp, 'agente', 'oculto').estado == 'desactivado'
    imp.autorizar(imp.autorizables())
    tipos, _ = subagentes.cargar_tipos(demo, hogar)
    revisor, doc = tipos['revisor'], tipos['equipo/doc']
    assert revisor['solo_lectura'] is True and revisor['excluidas'] == ['run_command']
    assert revisor['instrucciones'] == 'Revisa con lupa y no edites.' and 'opencode.json' in revisor['origen']
    assert doc['solo_lectura'] is False and doc['excluidas'] == ['run_command']
    assert doc['modelo'] is None and doc['esfuerzo'] == 'high'
    # Uno propio de AlfalfaCode con el mismo nombre gana.
    (demo / '.alfalfa' / 'agents').mkdir(parents=True)
    (demo / '.alfalfa' / 'agents' / 'revisor.md').write_text('---\ndescription: el propio\n---\nPropio.')
    assert subagentes.cargar_tipos(demo, hogar)[0]['revisor']['descripcion'] == 'el propio'


def test_plantillas_de_comandos():
    assert traduccion.expandir_plantilla('Revisa $ARGUMENTS.', 'src/a.py') == 'Revisa src/a.py.'
    assert traduccion.expandir_plantilla('Mira $1 y $2.', 'uno "dos tres" cuatro') == 'Mira uno y dos tres cuatro.'
    assert traduccion.expandir_plantilla('Mira $1, $2 y $3.', 'uno') == 'Mira uno,  y .'
    assert traduccion.expandir_plantilla('Sin marcadores.', 'extra') == 'Sin marcadores.\n\nextra'
    assert traduccion.expandir_plantilla('Commits: !`git log -$1`', '3') == \
        'Commits: (ejecuta `git log -3` y usa su salida)'


def test_comandos_de_opencode_y_claude_code(demo, casa, tmp_path):
    (demo / '.opencode' / 'command' / 'equipo').mkdir(parents=True)
    (demo / '.opencode' / 'command' / 'equipo' / 'resumen.md').write_text(
        '---\ndescription: Resume\nsubtask: true\n---\nResume $1 en $2 frases.')
    (demo / '.claude' / 'commands' / 'front').mkdir(parents=True)
    (demo / '.claude' / 'commands' / 'front' / 'componente.md').write_text('Crea el componente $ARGUMENTS.')
    imp = Importadas(demo, tmp_path / 'alfalfa')
    assert set(imp.comandos()) == {'probar', 'equipo/resumen', 'componente'}
    texto = imp.expandir('/probar unitarias')
    assert texto.startswith('Orden /probar (OpenCode, ') and texto.endswith(
        'Ejecuta las pruebas de unitarias y resume // sin miedo.')
    assert 'hazla con un subagente (spawn_agent)' in imp.expandir('/equipo/resumen README.md 3')
    assert imp.expandir('/componente Botón').endswith('Crea el componente Botón.')
    assert imp.expandir('/no-existe x') is None and imp.expandir('hola') is None


# ------------------------------------------------------------------ instrucciones

def test_instrucciones_en_orden_sin_duplicados(demo, casa, tmp_path):
    (casa / '.codex' / 'config.toml').write_text('developer_instructions = "Del usuario."\n')
    (demo / 'CLAUDE.md').write_text('@AGENTS.md\n')  # el CLAUDE.md habitual que sólo importa AGENTS.md
    (demo / 'server').mkdir()
    (demo / 'server' / 'AGENTS.md').write_text('Del servidor.')
    (demo / 'server' / 'CLAUDE.md').symlink_to(demo / 'server' / 'AGENTS.md')
    (demo / 'server' / 'CLAUDE.local.md').write_text('Del servidor.')  # mismo texto: no se repite
    (demo / '.claude').mkdir()
    (demo / '.claude' / 'CLAUDE.md').write_text('@AGENTS.md\nY usa ruff.')
    (demo / 'docs').mkdir()
    (demo / 'docs' / 'estilo.md').write_text('Frases cortas.')
    datos = json.loads(formatos.sin_comentarios((demo / 'opencode.json').read_text()))
    datos['instructions'] = ['docs/*.md', 'https://example.com/x.md', '../fuera.md']
    (tmp_path / 'fuera.md').write_text('no')
    (demo / 'opencode.json').write_text(json.dumps(datos))
    docs = agents_md.cargar(demo / 'server', tmp_path / 'alfalfa')
    nombres = [Path(str(r).split('#')[0]).name for r, _ in docs.proyecto]
    assert nombres == ['config.toml', 'config.toml', 'AGENTS.md', 'CLAUDE.md', 'AGENTS.md', 'estilo.md']
    assert [t.strip() for _, t in docs.proyecto] == ['Del usuario.', 'Responde en español.', '# Guía\nUsa pytest.',
                                                      'Y usa ruff.', 'Del servidor.', 'Frases cortas.']
    assert any('no se descargan' in a for a in docs.avisos)
    informe = Importadas(demo / 'server', tmp_path / 'alfalfa').informe()
    filas = {(f['nombre'], f['estado']) for f in informe['elementos'] if f['tipo'] == 'instrucciones'}
    assert ('CLAUDE.md', 'duplicado') in filas and ('CLAUDE.local.md', 'duplicado') in filas


def test_project_doc_fallback_filenames_de_codex(tmp_path, casa):
    (tmp_path / '.git').mkdir()
    (tmp_path / 'GUIA.md').write_text('Guía alternativa.')
    assert agents_md.cargar(tmp_path).texto() == ''
    (casa / '.codex' / 'config.toml').write_text('project_doc_fallback_filenames = ["GUIA.md"]\n')
    assert agents_md.cargar(tmp_path).texto() == 'Guía alternativa.'
    (tmp_path / 'AGENTS.md').write_text('Prevalece.')
    assert agents_md.cargar(tmp_path).texto() == 'Prevalece.'


# ------------------------------------------------------------------ orden --importar

def test_orden_importar_lista_autoriza_y_json(demo, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(demo)
    hogar = tmp_path / 'alfalfa'

    class Args:
        json, autorizar, retirar, sin_internet = False, [], [], False
    assert orden_importar.orden(Args, hogar) == 0
    salida = capsys.readouterr().out
    assert 'eco  [sin autorizar]  OpenCode · opencode.json' in salida
    assert 'alfalfa --importar --autorizar mcp:eco' in salida and SECRETO not in salida
    Args.autorizar, Args.sin_internet = ['mcp:eco', 'mcp:nada'], True
    assert orden_importar.orden(Args, hogar) == 1  # una clave desconocida es un error, la otra se autoriza
    assert 'mcp:nada' in capsys.readouterr().err
    Args.json, Args.autorizar, Args.sin_internet = True, [], False
    orden_importar.orden(Args, hogar)
    evento = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert evento['tipo'] == 'importadas'
    eco = next(f for f in evento['elementos'] if f['nombre'] == 'eco')
    assert eco['estado'] == 'autorizado' and eco['red'] is False and eco['lugar'] == 'opencode.json'


def test_cli_importar_de_verdad_con_home_de_demo(demo, casa, tmp_path):
    entorno = {k: v for k, v in os.environ.items() if not k.startswith('ALFALFA_')}
    entorno.update(HOME=str(casa), ALFALFA_HOME=str(tmp_path / 'alfalfa'), PYTHONPATH=str(RAIZ))
    salida = subprocess.run([sys.executable, '-m', 'alfalfa.terminal', '--importar', '--autorizar', 'todo', '--json'],
                            cwd=demo, env=entorno, capture_output=True, text=True, timeout=60)
    assert salida.returncode == 0, salida.stderr
    evento = json.loads(salida.stdout.splitlines()[-1])
    assert {f['nombre'] for f in evento['elementos'] if f['estado'] == 'autorizado'} == {'eco', 'git-tools', 'revisor'}
    sin_importar = subprocess.run([sys.executable, '-m', 'alfalfa.terminal', '--autorizar', 'todo'], cwd=demo,
                                  env=entorno, capture_output=True, text=True, timeout=60)
    assert sin_importar.returncode == 2 and 'sólo valen con --importar' in sin_importar.stderr


# ------------------------------------------------------------------ la sesión de verdad

def test_sesion_sin_autorizar_avisa_y_no_inicia_nada(demo, tmp_path, monkeypatch):
    sesion = Sesion(tmp_path, monkeypatch, None)
    assert sesion.m.mcp is None
    guion = Guion([], {})

    async def run():
        with patch.object(NativeRuntime, 'request', guion.request()):
            return await sesion.m.run('Hola.')
    asyncio.run(asyncio.wait_for(run(), 60))
    sesion.m.close()
    eventos = [json.loads(linea) for linea in sesion.salida.getvalue().splitlines()]
    aviso = [e['texto'] for e in eventos if e.get('rol') == 'system' and 'Importar ·' in e.get('texto', '')]
    assert len(aviso) == 1 and 'mcp eco (OpenCode)' in aviso[0] and 'agente revisor' in aviso[0]
    # Las instrucciones sí entran sin autorizar, como el AGENTS.md del repositorio.
    sistema = guion.payloads['principal'][0]['messages'][0]['content']
    assert 'Responde en español.' in sistema and 'Usa pytest.' in sistema


@macos
def test_proyecto_de_demo_funciona_tras_autorizarlo(demo, tmp_path, monkeypatch):
    """AGENTS.md, opencode.json (MCP, agente y comando) y .codex/config.toml, autorizados una vez."""
    imp = Importadas(demo, tmp_path / 'alfalfa')
    imp.autorizar([elemento(imp, 'mcp', 'eco'), elemento(imp, 'agente', 'revisor')])
    sesion = Sesion(tmp_path, monkeypatch, None)
    assert list(sesion.m.mcp.servidores) == ['eco']
    assert sesion.m.mcp.servidores['eco'].entorno == {'TOKEN': SECRETO}
    principal = [
        llamada('buscar_herramientas', {'consulta': 'select:mcp__eco__contar'}),
        llamada('mcp__eco__contar', {'nota': 'importado'}),
        llamada('spawn_agent', {'agent_type': 'revisor', 'message': 'Revisa el README.'}),
        {'role': 'assistant', 'content': 'Listo.'}]
    guion = Guion(principal, {'a1': [{'role': 'assistant', 'content': 'Revisado: todo bien.'}]})

    async def run():
        cliente = asyncio.create_task(sesion._responder())
        try:
            with patch.object(NativeRuntime, 'request', guion.request()):
                return await sesion.m.run('/probar unitarias')
        finally:
            cliente.cancel()
            await asyncio.gather(cliente, return_exceptions=True)
            await sesion.m.close_mcp()
    asyncio.run(asyncio.wait_for(run(), 120))
    sesion.m.close()
    # El comando de OpenCode llegó al modelo como su plantilla.
    usuario = [m['content'] for m in guion.payloads['principal'][0]['messages'] if m['role'] == 'user'][-1]
    assert 'Ejecuta las pruebas de unitarias y resume' in usuario
    # El MCP importado corrió de verdad (con permiso) y el agente importado trabajó de sólo lectura.
    assert (demo / 'contador.txt').read_text() == '1'
    payload = guion.payloads['a1'][0]
    assert 'Revisa con lupa y no edites.' in payload['messages'][0]['content']
    herramientas = nombres_de_herramientas(payload)
    assert 'run_command' not in herramientas and 'write_file' not in herramientas and 'read_file' in herramientas
    assert SECRETO not in sesion.salida.getvalue()
