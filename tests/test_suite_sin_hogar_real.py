"""La suite no deja nada en el ~/.alfalfa de verdad: el fixture `_nunca_el_hogar_real` hace fallar a la prueba que
lo intente. Para comprobarlo sin tocar el hogar real, se le hace creer que un hogar temporal es el de verdad."""
import pytest

import tests.casa as casa
from alfalfa.terminal import TerminalManager


def test_un_terminal_sin_hogar_propio_no_escribe_en_el_hogar_real(tmp_path, monkeypatch):
    falso = (tmp_path / 'casa' / '.alfalfa').resolve()
    monkeypatch.setattr(casa, 'hogares_reales', lambda: {falso}, raising=False)
    monkeypatch.setenv('ALFALFA_HOME', str(falso))   # lo que haría el hogar por defecto: ~/.alfalfa
    with pytest.raises(AssertionError, match='hogar real de AlfalfaCode'):
        TerminalManager('prueba', model='ollama/demo', cwd=tmp_path, reglas_equipo=None).close()
    assert not (falso / 'sesiones').exists()


def test_con_un_hogar_temporal_sigue_funcionando(tmp_path):
    manager = TerminalManager('prueba', model='ollama/demo', cwd=tmp_path, home=tmp_path / 'hogar', reglas_equipo=None)
    try:
        assert manager.directory.is_relative_to(tmp_path / 'hogar')
    finally:
        manager.close()
