"""web_search y read_web con la librería CuySearch, el selector de buscador y el servicio remoto."""
import asyncio
import io
import json
from contextlib import redirect_stdout

import cuysearch
import pytest
from cuysearch.motores import Motor, MotorBloqueado
from cuysearch.tipos import Resultado

from alfalfa import ajustes, terminal
from alfalfa.proveedores.nativo import web


class MotorFalso(Motor):
    def __init__(self, nombre, resultados=(), error=None):
        super().__init__()
        self.nombre = nombre
        self.resultados = list(resultados)
        self.error = error

    async def buscar(self, cliente, consulta):
        if self.error:
            raise self.error
        return [Resultado(titulo=t, url=u, fragmento='f ' * 400, motor=self.nombre, fecha='2026-11-01')
                for t, u in self.resultados]


@pytest.fixture
def con_motores(monkeypatch):
    """Sustituye los motores reales por los que se den, conservando lo que elige `_buscador`."""
    elegidos = {}

    def usar(*motores):
        real = cuysearch.Buscador

        def falso(nombres, config):
            elegidos.update(nombres=nombres, config=config)
            return real(list(motores))
        monkeypatch.setattr(cuysearch, 'Buscador', falso)
        return elegidos
    return usar


def test_el_modo_sale_del_entorno_o_de_proveedores_json(monkeypatch):
    assert web.modo() == 'auto'
    web.configurar('searxng')
    assert web.modo() == 'searxng'
    monkeypatch.setenv('ALFALFA_BUSCADOR', 'apagado')
    assert web.modo() == 'apagado'
    monkeypatch.setenv('ALFALFA_BUSCADOR', 'google')
    assert web.modo() == 'auto'


def test_configurar_valida_el_modo_y_la_url_de_searxng():
    assert web.searxng_url() == 'http://127.0.0.1:8888'
    assert web.configurar(url='http://127.0.0.1:9999/')['searxng_url'] == 'http://127.0.0.1:9999'
    with pytest.raises(ValueError, match='HTTPS'):
        web.configurar(url='http://buscador.ejemplo.org')
    with pytest.raises(ValueError, match='desconocido'):
        web.configurar('google')
    assert web.configurar(url='-')['searxng_url'] == 'http://127.0.0.1:8888'


def test_apagado_quita_las_herramientas_al_modelo(monkeypatch):
    assert terminal.excluidas() == ()
    monkeypatch.setenv('ALFALFA_BUSCADOR', 'apagado')
    assert terminal.excluidas() == ('web_search', 'read_web')
    with pytest.raises(ValueError, match='apagada'):
        asyncio.run(web.buscar('alfalfa'))


def test_sin_libreria_tampoco_se_ofrecen(monkeypatch):
    monkeypatch.setattr(web, 'libreria_disponible', lambda: False)
    assert terminal.excluidas() == ('web_search', 'read_web')
    monkeypatch.setattr(ajustes, 'cuysearch_url', lambda: 'http://127.0.0.1:8090')
    assert terminal.excluidas() == ()


def test_buscar_con_la_libreria_devuelve_el_formato_de_siempre(con_motores):
    con_motores(MotorFalso('caido', error=MotorBloqueado('caido limitó la petición (HTTP 429)')),
                MotorFalso('bueno', [('Docs', 'https://docs.ejemplo.org/a'), ('Fuera', 'https://otro.org/')]))

    salida = asyncio.run(web.buscar('  hola   mundo ', ['ejemplo.org']))

    assert salida['query'] == 'hola mundo'
    assert salida['engine'] == 'bueno'
    assert [r['url'] for r in salida['results']] == ['https://docs.ejemplo.org/a']
    assert len(salida['results'][0]['snippet']) == web.MAX_FRAGMENTO
    assert salida['results'][0]['published'] == '2026-11-01'
    assert salida['warnings'] == ['caido: caido limitó la petición (HTTP 429)']


def test_ollama_usa_la_clave_del_usuario_y_si_falta_lo_avisa(con_motores, monkeypatch):
    web.configurar('ollama')
    elegidos = con_motores(MotorFalso('m', [('A', 'https://ejemplo.org/')]))
    salida = asyncio.run(web.buscar('hola'))
    assert elegidos['nombres'] == 'auto'
    assert 'falta la clave de Ollama' in salida['warnings'][0]

    web._buscadores.clear()
    monkeypatch.setattr(ajustes, 'ollama_clave', lambda: 'clave-de-prueba')
    asyncio.run(web.buscar('hola'))
    assert elegidos['nombres'][0] == 'ollama'
    assert dict(elegidos['config'].claves) == {'ollama': 'clave-de-prueba'}
    assert 'clave-de-prueba' not in json.dumps(web.estado())


def test_searxng_usa_su_url_y_despues_los_gratuitos(con_motores):
    web.configurar('searxng', 'http://127.0.0.1:9999')
    elegidos = con_motores(MotorFalso('m', [('A', 'https://ejemplo.org/')]))

    asyncio.run(web.buscar('hola'))

    assert elegidos['nombres'][:2] == ['searxng', 'duckduckgo']
    assert elegidos['config'].searxng_url == 'http://127.0.0.1:9999'


def test_si_ningun_motor_responde_el_agente_sabe_que_marcar(con_motores):
    con_motores(MotorFalso('caido', error=MotorBloqueado('bloqueado')))

    with pytest.raises(ValueError, match='Ningún motor respondió.*«sin verificar»'):
        asyncio.run(web.buscar('hola'))


def test_read_web_rechaza_destinos_locales_con_la_libreria():
    for url in ('http://localhost:8080/', 'http://127.0.0.1/', 'http://169.254.169.254/latest/meta-data'):
        with pytest.raises(ValueError, match='No se pudo leer'):
            asyncio.run(web.leer(url))
    with pytest.raises(ValueError, match='sólo acepta'):
        asyncio.run(web.leer('file:///etc/passwd'))


def test_read_web_con_la_libreria(monkeypatch):
    async def leer(self, url):
        return cuysearch.Pagina(url=url + 'final', titulo='T', texto='x' * (web.MAX_PAGINA + 5))
    monkeypatch.setattr(cuysearch.Buscador, 'leer', leer)

    salida = asyncio.run(web.leer('https://ejemplo.org/'))

    assert salida['url'] == 'https://ejemplo.org/final' and salida['title'] == 'T'
    assert len(salida['text']) == web.MAX_PAGINA and salida['truncated'] is True


@pytest.mark.parametrize('argv,modo', [(['--buscador', '--json'], 'auto'),
                                       (['--buscador', 'searxng', '--json'], 'searxng'),
                                       (['--fijar-searxng', 'http://127.0.0.1:7777', '--json'], 'auto')])
def test_la_orden_buscador_para_la_app(argv, modo):
    salida = io.StringIO()
    with redirect_stdout(salida):
        assert terminal.main(argv) == 0
    datos = json.loads(salida.getvalue())
    assert datos['version'] == 2 and datos['tipo'] == 'buscador'
    assert datos['modo'] == modo and datos['libreria'] is True and datos['remoto'] is False
    if argv[0] == '--fijar-searxng':
        assert datos['searxng_url'] == 'http://127.0.0.1:7777'
