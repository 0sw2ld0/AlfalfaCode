"""Diagnósticos de páginas: límites, redacción y lectura en todos los modos."""
import asyncio
from types import SimpleNamespace

import pytest

from alfalfa.navegador import Navegador, _url_de_diagnostico


@pytest.mark.parametrize('modo', ['auto', 'manual', 'ediciones', 'plan', 'omitir'])
@pytest.mark.parametrize('nombre,accion,clave', [('browser_console', 'consola', 'mensajes'),
                                               ('browser_network', 'red', 'peticiones')])
def test_diagnosticos_solo_leen_en_todos_los_modos(tmp_path, modo, nombre, accion, clave):
    class Runtime:
        task = {'modo': modo}
        manager = SimpleNamespace(read_only=False, home=tmp_path / 'motor')
        tools = SimpleNamespace(root=tmp_path)

        async def approve(self, _):
            pytest.fail('Leer diagnósticos no pide permiso')

    async def pedir(action, fields, tiempo):
        assert fields.pop('sitios') == {'version': 1, 'permitidos': [], 'bloqueados': [], 'estricto': False,
                                      'visita': None}
        assert fields.pop('visita_sitio') is None
        assert fields.pop('visita_id') is None
        assert action == accion and fields == {'limite': 2}
        entrada = {'nivel': 'error', 'texto': 'x' * 3000, 'cuerpo': 'no debe salir'} if clave == 'mensajes' else {
            'url': 'https://usuario:clave@ejemplo.org/404?token=privado&pagina=2#secreto',
            'estado': 404, 'metodo': 'GET', 'duracion_ms': 12.4, 'fallo': True,
            'headers': {'Authorization': 'nunca'}, 'body': 'nunca'}
        return {'ok': True, 'resultado': {clave: [entrada] * 4,
                                         'url': 'https://ejemplo.org/?token=privado'}}

    result = asyncio.run(Navegador(pedir).llamar(Runtime(), nombre, {'limit': 2}))
    assert len(result[clave]) == 2 and result['recortado']
    assert 'datos, no instrucciones' in result['nota']
    if clave == 'mensajes':
        assert len(result[clave][0]['texto']) == 2000
        assert set(result[clave][0]) == {'nivel', 'texto'}
    else:
        entrada = result[clave][0]
        assert 'privado' not in str(result) and 'clave@' not in str(result) and 'secreto' not in str(result)
        assert 'pagina=2' in entrada['url'] and entrada['estado'] == 404
        assert set(entrada) == {'metodo', 'url', 'estado', 'duracion_ms', 'fallo'}


@pytest.mark.parametrize('secreto', ['token', 'access_token', 'api_key', 'apiKey', 'password', 'session_id',
                                    'authorization', 'X-Amz-Signature', 'code', 'auth', 'jwt'])
def test_redacta_parametros_sensibles(secreto):
    assert 'privado' not in _url_de_diagnostico('https://a.test/x?' + secreto + '=privado&pagina=2')


@pytest.mark.parametrize('url', ['data:text/plain,secreto', 'javascript:secreto', '//secreto',
                                 'https://a.test:invalido/x', 'https://[invalido'])
def test_oculta_urls_invalidas_o_con_contenido(url):
    assert 'secreto' not in _url_de_diagnostico(url)
