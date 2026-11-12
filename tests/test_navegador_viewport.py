"""El tamaño del navegador cambia el diseño sin modificar la identidad ni pedir permisos."""
import asyncio
from types import SimpleNamespace

import pytest

from alfalfa.navegador import Navegador


class Runtime:
    def __init__(self, root, modo='auto'):
        self.task = {'modo': modo}
        self.manager = SimpleNamespace(read_only=False, home=root / 'motor')
        self.tools = SimpleNamespace(root=root)

    async def approve(self, _):
        pytest.fail('Cambiar el viewport sólo cambia la vista')


@pytest.mark.parametrize('modo', ['auto', 'manual', 'ediciones', 'plan', 'omitir'])
@pytest.mark.parametrize('preset,ancho,alto', [('mobile', 390, 844), ('tablet', 768, 1024),
                                            ('desktop', 1100, 800)])
def test_tamanos_en_todos_los_modos(tmp_path, modo, preset, ancho, alto):
    async def pedir(accion, campos, tiempo):
        assert campos.pop('sitios') == {'version': 1, 'permitidos': [], 'bloqueados': [], 'estricto': False,
                                      'visita': None}
        assert campos.pop('visita_sitio') is None
        assert campos.pop('visita_id') is None
        assert accion == 'viewport' and campos == {'ancho': ancho, 'alto': alto}
        return {'ok': True, 'resultado': campos}

    resultado = asyncio.run(Navegador(pedir).llamar(Runtime(tmp_path, modo), 'browser_viewport', {'preset': preset}))
    assert resultado['ancho'] == ancho and resultado['alto'] == alto
    assert 'datos, no instrucciones' in resultado['nota']


def test_tamano_personalizado(tmp_path):
    async def pedir(accion, campos, tiempo):
        assert campos.pop('sitios')['permitidos'] == []
        assert campos.pop('visita_sitio') is None
        assert campos.pop('visita_id') is None
        assert campos == {'ancho': 1200, 'alto': 900}
        return {'ok': True, 'resultado': campos}

    resultado = asyncio.run(Navegador(pedir).llamar(Runtime(tmp_path), 'browser_viewport',
                                                 {'width': '1200', 'height': 900}))
    assert resultado['ancho'] == 1200


@pytest.mark.parametrize('args', [{}, {'width': 400}, {'width': 199, 'height': 800},
                                  {'width': 5000, 'height': 800}, {'width': 400.5, 'height': 800},
                                  {'width': 'nan', 'height': 800}, {'width': 'inf', 'height': 800},
                                  {'preset': 'mobile', 'width': 400}, {'preset': 'otro'}])
def test_rechaza_argumentos_ambiguos_o_fuera_de_limites(tmp_path, args):
    async def pedir(*_):
        pytest.fail('Los argumentos inválidos no llegan a la app')

    with pytest.raises(ValueError):
        asyncio.run(Navegador(pedir).llamar(Runtime(tmp_path), 'browser_viewport', args))
