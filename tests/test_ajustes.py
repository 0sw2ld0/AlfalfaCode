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


def test_el_integrador_pone_sus_nombres_en_el_manual_y_los_motivos(m, monkeypatch):
    from alfalfa.proveedores.nativo.runtime import manual_del_harness
    assert 'runtime nativo de AlfalfaCode' in manual_del_harness('glm') and '{runtime}' not in manual_del_harness('glm')
    assert 'las concede el arnés solo' in manual_del_harness('glm')
    assert ordenes.safe_command('git merge otra', str(m.task['cwd']))[1].endswith('lo decide el usuario, no el agente')
    monkeypatch.setattr(ajustes, 'nombres', lambda: {'runtime': 'MiApp', 'concede': 'MiApp sola', 'decide': 'MiApp',
                                                     'integrador': 'miapp'})
    assert 'runtime nativo de MiApp' in manual_del_harness('glm') \
        and 'las concede MiApp sola' in manual_del_harness('glm')
    assert 'runtime nativo de MiApp.' in NativeRuntime.default_engine_text('low')
    assert 'lo decide MiApp' in ordenes.safe_command('git merge otra', str(m.task['cwd']))[1]


def test_el_integrador_añade_ramas_de_avance_y_ordenes_propias(tmp_path, monkeypatch):
    espacio = str(tmp_path)
    assert not ordenes.safe_command('git merge --ff-only app/integracion', espacio)[0]
    assert not ordenes.safe_command('miherramienta --auditar salida', espacio)[0]
    monkeypatch.setattr(ajustes, 'ramas_de_avance', lambda: ('app/integracion',))

    def propia(programa, argumentos, espacio):
        if programa == 'miherramienta' and argumentos[:1] == ['--auditar'] \
                and not ordenes.fuera_del_espacio(argumentos[1], espacio):
            return 'auditoría propia del integrador'
        return None
    monkeypatch.setattr(ajustes, 'orden_permitida', propia)
    assert ordenes.safe_command('git merge --ff-only app/integracion', espacio)[0]
    assert not ordenes.safe_command('git merge app/integracion', espacio)[0]
    assert ordenes.safe_command('miherramienta --auditar salida', espacio)[0]
    assert not ordenes.safe_command('miherramienta --auditar /etc/x', espacio)[0]
