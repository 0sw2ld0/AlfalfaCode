"""Un `proveedores.json` que no se entiende no se sobrescribe.

Antes se leía como vacío: con una coma de más (editado a mano), `alfalfa --alcance-busqueda todas`
lo dejaba en `{"busqueda_sesiones": {"alcance": "todas"}}` y se perdían el proveedor por defecto,
las URL y los modelos elegidos.
"""
import pytest

from alfalfa import busqueda_sesiones, terminal
from alfalfa.proveedores import configuracion
from alfalfa.proveedores.nativo import web

ROTO = ('{"por_defecto": "openrouter",\n "openrouter": {"guardada": true, "url": "https://openrouter.ai/api/v1"},\n'
        ' "alfalfa": {"url": "https://alfalfa.example.com"},\n}\n')  # coma de más


@pytest.fixture
def roto():
    destino = configuracion.ruta()
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(ROTO)
    return destino


@pytest.mark.parametrize('cambio', [
    lambda: busqueda_sesiones.fijar_alcance('todas'),
    lambda: configuracion.cambiar(None, 'por_defecto', 'ollama'),
    lambda: configuracion.cambiar('ollama', 'url', 'http://127.0.0.1:11434'),
    lambda: web.configurar('apagado'),
], ids=['alcance', 'por_defecto', 'url', 'buscador'])
def test_un_cambio_no_pisa_un_archivo_que_no_se_entiende(roto, cambio):
    with pytest.raises(ValueError, match='no se entiende'):
        cambio()
    assert roto.read_text() == ROTO


def test_la_orden_lo_dice_y_lo_deja_como_estaba(roto, capsys):
    assert terminal.main(['--alcance-busqueda', 'todas']) == 1
    salida = capsys.readouterr()
    assert 'no se entiende' in salida.err + salida.out and 'arréglalo a mano o bórralo' in salida.err + salida.out
    assert roto.read_text() == ROTO


def test_tampoco_si_no_es_un_objeto(roto):
    roto.write_text('["ollama"]\n')
    with pytest.raises(ValueError, match='no es un objeto'):
        configuracion.cambiar(None, 'por_defecto', 'ollama')
    assert roto.read_text() == '["ollama"]\n'


def test_leer_para_ensenar_sigue_tolerando(roto):
    assert configuracion.leer() == {} and configuracion.por_defecto() == 'ollama'
    assert busqueda_sesiones.alcance() == 'carpeta'


def test_sin_archivo_se_crea_como_siempre():
    destino = configuracion.ruta()
    assert not destino.exists()
    configuracion.cambiar(None, 'por_defecto', 'ollama')
    busqueda_sesiones.fijar_alcance('todas')
    assert configuracion.leer() == {'por_defecto': 'ollama', 'busqueda_sesiones': {'alcance': 'todas'}}
