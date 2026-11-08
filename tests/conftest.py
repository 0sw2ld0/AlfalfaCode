import os

# La suite no debe heredar el entorno de quien la corre: una sesión de
# AlfalfaCode exporta su hogar, su Ollama y sus referencias.
for _nombre in [n for n in os.environ if n.startswith(('ALFALFA_', 'OLLAMA_', 'OPENAI_'))]:
    del os.environ[_nombre]


import pytest  # noqa: E402  (después de limpiar el entorno)


@pytest.fixture(autouse=True)
def _sin_llavero_ni_proveedores_del_usuario(tmp_path_factory, monkeypatch):
    """Ni el Llavero real ni el proveedores.json de quien corre la suite entran."""
    from alfalfa.proveedores import configuracion, llavero
    monkeypatch.setattr(llavero, '_almacen', {})
    monkeypatch.setattr(llavero, '_leidos', {})
    monkeypatch.setattr(configuracion, '_casa', tmp_path_factory.mktemp('alfalfa'))


@pytest.fixture(autouse=True)
def _sin_habilidades_del_usuario(tmp_path_factory, monkeypatch):
    """Las habilidades sueltas de ~/.claude/skills y ~/.codex/skills de quien corre la suite no entran."""
    from alfalfa.complementos import cargador
    monkeypatch.setattr(cargador, 'CASA', tmp_path_factory.mktemp('casa'))
