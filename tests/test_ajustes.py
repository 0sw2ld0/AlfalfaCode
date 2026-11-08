"""Lo que una aplicación integradora cambia en `alfalfa.ajustes`, y sus valores propios."""
import asyncio
import os

import pytest

from alfalfa import ajustes
from alfalfa.permisos import ordenes
from alfalfa.proveedores.nativo import web
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.terminal import AGENT_TONE, INSTRUCTIONS, TerminalManager


@pytest.fixture
def m(tmp_path, monkeypatch):
    (tmp_path / 'project').mkdir()
    monkeypatch.chdir(tmp_path / 'project')
    manager = TerminalManager('Encargo.', home=tmp_path / 'alfalfa')
    yield manager
    manager.close()


def test_sin_integrador_usa_las_instrucciones_de_alfalfacode(m):
    rt = NativeRuntime(m, m.task)
    assert rt.instructions == AGENT_TONE + INSTRUCTIONS


def test_el_integrador_da_sus_instrucciones_y_su_tono(m, monkeypatch):
    monkeypatch.setattr(ajustes, 'instrucciones_por_defecto', lambda: ('Reglas propias.', 'Tono propio. '))
    assert NativeRuntime(m, m.task).instructions == 'Tono propio. Reglas propias.'
    # Un tono explícito gana al registrado.
    assert NativeRuntime(m, m.task, agent_tone='Otro. ').instructions == 'Otro. Reglas propias.'


def test_sin_integrador_no_hay_repo_propio_ni_referencias(monkeypatch):
    monkeypatch.delenv('ALFALFA_REFERENCIAS', raising=False)
    assert ordenes._repo_del_integrador() is None
    assert ordenes._venv_del_integrador() is None
    assert ordenes.carpetas_de_referencia() == []


def test_el_repo_del_integrador_da_su_venv_y_sus_referencias(tmp_path, monkeypatch):
    monkeypatch.delenv('ALFALFA_REFERENCIAS', raising=False)
    repo = tmp_path / 'a' / 'b' / 'repo'
    (repo.parent / 'referencias').mkdir(parents=True)
    repo.mkdir()
    monkeypatch.setattr(ajustes, 'repo_del_integrador', lambda: str(repo))
    assert ordenes._venv_del_integrador() == repo / '.venv' / 'bin'
    assert ordenes.carpetas_de_referencia() == [os.path.realpath(repo.parent / 'referencias')]


def test_la_web_sin_cuysearch_configurado_se_explica(monkeypatch):
    monkeypatch.delenv('ALFALFA_CUYSEARCH_URL', raising=False)
    with pytest.raises(ValueError, match='no hay URL de CuySearch'):
        asyncio.run(web.buscar('alfalfa'))


def test_la_web_usa_la_url_la_clave_y_el_aviso_del_integrador(monkeypatch):
    import httpx
    vistas, avisos = [], []

    def manejador(peticion):
        vistas.append((str(peticion.url), peticion.headers.get('X-CuySearch-Key')))
        return httpx.Response(200, json={'results': []})

    real = httpx.AsyncClient
    monkeypatch.setattr(web.httpx, 'AsyncClient',
                        lambda *a, **k: real(*a, **dict(k, transport=httpx.MockTransport(manejador))))

    async def antes(base):
        avisos.append(base)
    monkeypatch.setattr(ajustes, 'cuysearch_url', lambda: 'http://buscador.prueba/')
    monkeypatch.setattr(ajustes, 'cuysearch_clave', lambda: 'clave-de-prueba')
    monkeypatch.setattr(ajustes, 'antes_de_cuysearch', antes)
    assert asyncio.run(web.buscar('alfalfa')) == {'query': 'alfalfa', 'results': []}
    assert vistas == [('http://buscador.prueba/v1/search', 'clave-de-prueba')]
    assert avisos == ['http://buscador.prueba']
