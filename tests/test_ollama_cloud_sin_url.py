"""Con la clave de Ollama Cloud pero sin Ollama en este equipo (un servidor): el fallo de conexión dice que
falta apuntar Ollama a la nube, en vez de «revisa el servicio de Ollama»."""
from alfalfa import proveedores


def test_con_clave_y_ollama_local_ausente_dice_como_apuntar_a_la_nube(monkeypatch):
    monkeypatch.setenv('OLLAMA_API_KEY', 'clave-de-prueba-1234')
    accion = proveedores.obtener('ollama').accion({'cause': 'network'}, 'All connection attempts failed')
    assert accion and 'http://127.0.0.1:11434' in accion
    assert 'alfalfa --fijar-url ollama https://ollama.com' in accion


def test_sin_clave_queda_el_aviso_de_siempre(monkeypatch):
    monkeypatch.delenv('OLLAMA_API_KEY', raising=False)
    assert proveedores.obtener('ollama').accion({'cause': 'network'}, '') is None


def test_ya_en_la_nube_no_se_sugiere(monkeypatch):
    monkeypatch.setenv('OLLAMA_API_KEY', 'clave-de-prueba-1234')
    monkeypatch.setenv('ALFALFA_OLLAMA_URL', 'https://ollama.com')
    assert proveedores.obtener('ollama').accion({'cause': 'network'}, '') is None


def test_otro_fallo_no_cambia(monkeypatch):
    monkeypatch.setenv('OLLAMA_API_KEY', 'clave-de-prueba-1234')
    assert proveedores.obtener('ollama').accion({'cause': 'timeout'}, '') is None


def test_la_guia_de_linux_lo_dice():
    from pathlib import Path
    guia = (Path(__file__).resolve().parents[1] / 'docs' / 'USO.md').read_text().split('### Linux: claves', 1)[1]
    guia = guia.split('\n### ', 1)[0]
    assert 'alfalfa --fijar-url ollama https://ollama.com' in guia
