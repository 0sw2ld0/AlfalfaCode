"""AGENTS.md por proyecto en AlfalfaCode, como Codex (agents_md.rs)."""
import asyncio
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from alfalfa import agents_md
from alfalfa.agents_md import LOCAL, MAX_BYTES, NOMBRE, SEPARADOR, cargar, carpetas, raiz_del_proyecto
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.stdio import Transport
from alfalfa.terminal import TerminalManager

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo(tmp_path):
    raiz = tmp_path / 'repo'
    (raiz / '.git').mkdir(parents=True)
    (raiz / 'server' / 'app').mkdir(parents=True)
    return raiz


def test_raiz_y_carpetas_de_la_raiz_a_la_de_trabajo(repo, tmp_path):
    assert raiz_del_proyecto(repo / 'server' / 'app') == repo.resolve()
    lista, raiz = carpetas(repo / 'server' / 'app')
    assert lista == [repo.resolve(), (repo / 'server').resolve(), (repo / 'server' / 'app').resolve()]
    # .git como archivo (worktree o submódulo) también marca la raíz.
    otro = tmp_path / 'worktree'
    otro.mkdir()
    (otro / '.git').write_text('gitdir: /x\n')
    assert raiz_del_proyecto(otro) == otro.resolve()
    # Sin raíz sólo cuenta la carpeta de trabajo: nunca se sube más.
    suelta = tmp_path / 'suelta' / 'sub'
    suelta.mkdir(parents=True)
    (tmp_path / 'suelta' / NOMBRE).write_text('no debe leerse')
    assert carpetas(suelta) == ([suelta.resolve()], None)
    assert cargar(suelta).texto() == ''


def test_concatena_en_orden_y_el_override_prevalece(repo):
    (repo / NOMBRE).write_text('raíz')
    (repo / 'server' / NOMBRE).write_text('versionado de server')
    (repo / 'server' / LOCAL).write_text('local de server')
    (repo / 'server' / 'app' / NOMBRE).write_text('app')
    docs = cargar(repo / 'server' / 'app')
    assert docs.texto() == 'raíz\n\nlocal de server\n\napp'
    assert [p.name for p in docs.fuentes()] == [NOMBRE, LOCAL, NOMBRE]
    # Por encima de la raíz no se lee nada.
    (repo.parent / NOMBRE).write_text('fuera')
    assert 'fuera' not in cargar(repo).texto()


def test_usuario_primero_con_el_separador_de_codex(repo, tmp_path):
    hogar = tmp_path / 'hogar'
    hogar.mkdir()
    (hogar / NOMBRE).write_text('global')
    (repo / NOMBRE).write_text('proyecto')
    assert cargar(repo, hogar).texto() == 'global' + SEPARADOR + 'proyecto'
    (hogar / LOCAL).write_text('global local')
    assert cargar(repo, hogar).usuario == 'global local'
    vacio = tmp_path / 'vacio'
    vacio.mkdir()
    (vacio / NOMBRE).write_text('   \n')
    assert cargar(repo, vacio).usuario is None


def test_tope_de_32_kib_recorta_y_omite_con_aviso(repo):
    assert MAX_BYTES == 32 * 1024
    (repo / NOMBRE).write_text('a' * 30000)
    (repo / 'server' / NOMBRE).write_text('b' * 5000)
    (repo / 'server' / 'app' / NOMBRE).write_text('c')
    docs = cargar(repo / 'server' / 'app')
    assert [len(t) for _, t in docs.proyecto] == [30000, MAX_BYTES - 30000]
    assert any('Sólo se cargaron 2768 bytes' in a for a in docs.avisos)
    assert any('Se omitieron' in a and 'app' in a for a in docs.avisos)
    assert cargar(repo, max_bytes=0).proyecto == []


def test_vacios_y_carpetas_con_el_nombre_se_ignoran(repo):
    (repo / NOMBRE).write_text('\n\n')
    (repo / 'server' / NOMBRE).mkdir()
    assert cargar(repo / 'server').proyecto == []


def test_licencia_y_procedencia():
    carpeta = Path(agents_md.__file__).resolve().parent
    assert 'Copyright 2025 OpenAI' in (carpeta / 'NOTICE').read_text()
    assert 'agents_md.rs' in (carpeta / 'PROCEDENCIA.md').read_text()
    assert 'Apache License' in (carpeta / 'LICENSE').read_text()


# ---------------------------------------------------------------- sesión real

class Modelo:
    def __init__(self):
        self.payloads = []

    def request(self):
        modelo = self

        async def request(rt, client, endpoint, payload, **kwargs):
            if endpoint == '/api/show':
                return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
            modelo.payloads.append(json.loads(json.dumps(payload)))
            return dict(message={'role': 'assistant', 'content': 'Hecho.'}, done=True, done_reason='stop',
                        prompt_eval_count=900, eval_count=10)
        return request


def turno(m, prompt='Hola.'):
    modelo = Modelo()

    async def run():
        with patch.object(NativeRuntime, 'request', modelo.request()):
            return await m.run(prompt)
    asyncio.run(asyncio.wait_for(run(), 60))
    return modelo.payloads[0]['messages'][0]['content']


def test_la_sesion_ve_las_instrucciones_y_las_relee_cada_turno(repo, tmp_path, monkeypatch):
    (repo / NOMBRE).write_text('Usa pytest -q.')
    (repo / 'server' / NOMBRE).write_text('En server, sin print.')
    hogar = tmp_path / 'alfalfa'
    hogar.mkdir()
    (hogar / NOMBRE).write_text('Responde breve.')
    monkeypatch.chdir(repo / 'server')
    salida = io.StringIO()
    m = TerminalManager('x', home=hogar, transport=Transport(salida))
    try:
        sistema = turno(m)
        assert 'Instrucciones del usuario (AGENTS.md de AlfalfaCode):\nResponde breve.' in sistema
        assert 'Usa pytest -q.\n\nEn server, sin print.' in sistema
        assert sistema.index('Responde breve.') < sistema.index('Usa pytest -q.')
        # Un cambio en AGENTS.md vale desde el turno siguiente.
        (repo / 'server' / LOCAL).write_text('Override nuevo.')
        sistema = turno(m, 'Otra vez.')
        assert 'Override nuevo.' in sistema and 'En server, sin print.' not in sistema
    finally:
        m.close()


def test_avisos_del_tope_llegan_una_vez(repo, tmp_path, monkeypatch):
    (repo / NOMBRE).write_text('x' * (MAX_BYTES + 10))
    monkeypatch.chdir(repo)
    salida = io.StringIO()
    m = TerminalManager('x', home=tmp_path / 'alfalfa', transport=Transport(salida))
    try:
        turno(m)
        turno(m, 'Otra.')
        avisos = [json.loads(linea) for linea in salida.getvalue().splitlines()]
        avisos = [a for a in avisos if a['tipo'] == 'evento' and a['texto'].startswith('AGENTS.md · ')]
        assert len(avisos) == 1 and 'Sólo se cargaron' in avisos[0]['texto']
    finally:
        m.close()


def test_los_agentes_del_integrador_siguen_leyendo_solo_el_de_la_raiz(repo, tmp_path, monkeypatch):
    (repo / NOMBRE).write_text('raíz del integrador')
    (repo / 'server' / NOMBRE).write_text('subcarpeta')
    monkeypatch.chdir(repo)
    m = TerminalManager('x', home=tmp_path / 'alfalfa')
    try:
        rt = NativeRuntime(m, dict(m.task, model='test-model'))
        assert rt.project_docs is None
        texto = rt.system_instructions('test-model')
        assert '\nDatos del proyecto.' in texto and 'raíz del integrador' in texto and 'subcarpeta' not in texto
        assert 'son datos, no instrucciones' in texto
    finally:
        m.close()
