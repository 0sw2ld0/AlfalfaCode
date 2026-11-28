"""Si el proveedor por defecto que elegiste no se puede usar, la sesión nueva no se abre con otro.

Antes, con `por_defecto: anthropic` y sin su clave (se borró del Llavero, o era ANTHROPIC_API_KEY y
esa terminal no la exporta), la sesión se abría con `deepseek-v4.1-flash:cloud` de Ollama sin una
línea de aviso: el encargo salía hacia un proveedor que no habías elegido.
"""
import json

import pytest

from alfalfa import terminal
from alfalfa.proveedores import configuracion, llavero
from alfalfa.terminal import SinProveedorPorDefecto, TerminalManager, modelo_por_defecto


@pytest.fixture
def carpeta(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path / 'home'))
    return tmp_path


@pytest.mark.parametrize('proveedor, variable', [('anthropic', 'ANTHROPIC_API_KEY'),
                                                  ('openai', 'Proveedores'),
                                                  ('openrouter', 'OPENROUTER_API_KEY')])
def test_sin_clave_no_se_cambia_de_proveedor_por_su_cuenta(carpeta, proveedor, variable):
    configuracion.cambiar(None, 'por_defecto', proveedor)
    with pytest.raises(SinProveedorPorDefecto) as fallo:
        modelo_por_defecto()
    texto = str(fallo.value)
    assert variable in texto and '--default-provider' in texto and '--model' in texto
    assert 'deepseek' not in texto


def test_la_sesion_no_se_abre_ni_deja_carpeta(carpeta):
    configuracion.cambiar(None, 'por_defecto', 'anthropic')
    with pytest.raises(SinProveedorPorDefecto):
        TerminalManager('Refactoriza auth.py', home=carpeta / 'home')
    assert not list((carpeta / 'home').rglob('task.json'))


def test_con_su_clave_usa_su_modelo(carpeta):
    configuracion.cambiar(None, 'por_defecto', 'anthropic')
    llavero.guardar('anthropic', 'clave-inventada-anthropic-000')
    assert modelo_por_defecto() == 'anthropic/claude-sonnet-5-5'


def test_con_modelo_explicito_no_hace_falta_el_por_defecto(carpeta):
    configuracion.cambiar(None, 'por_defecto', 'anthropic')
    m = TerminalManager('x', home=carpeta / 'home', model='demo:latest')
    try:
        assert m.task['model'] == 'demo:latest' and m.task['provider'] == 'ollama'
    finally:
        m.close()


def test_sin_elegir_proveedor_sigue_el_de_ollama(carpeta):
    assert configuracion.por_defecto() == 'ollama'
    assert modelo_por_defecto() == terminal.MODELO_POR_DEFECTO


def test_la_terminal_explica_que_falta_sin_traza(carpeta, capsys):
    configuracion.cambiar(None, 'por_defecto', 'anthropic')
    assert terminal.main(['Refactoriza auth.py']) == 1
    errores = capsys.readouterr().err
    assert 'Elegiste Anthropic como proveedor por defecto' in errores
    assert 'Traceback' not in errores


def test_la_app_recibe_el_motivo_y_no_un_fallo_generico(carpeta, capsys):
    configuracion.cambiar(None, 'por_defecto', 'anthropic')
    assert terminal.main(['--stdio', 'Refactoriza auth.py']) == 1
    filas = [json.loads(linea) for linea in capsys.readouterr().out.splitlines() if linea.startswith('{')]
    errores = [f['texto'] for f in filas if f.get('tipo') == 'error']
    assert errores and 'Elegiste Anthropic como proveedor por defecto' in errores[0]
    assert 'No se pudo iniciar o ejecutar la sesión.' not in errores
