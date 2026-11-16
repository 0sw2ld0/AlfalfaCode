"""La integración continua de Linux: el workflow carga el mismo perfil de AppArmor para bwrap que explica
docs/USO.md, así la guía se comprueba en cada PR."""
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
WORKFLOW = RAIZ / '.github' / 'workflows' / 'linux.yml'


def _perfil(texto):
    """Las líneas del perfil que se escribe con `tee … <<'EOF'`, sin la sangría."""
    hallado = re.search(r"apparmor\.d/bwrap >/dev/null <<'EOF'\n(.*?)\n\s*EOF\n", texto, re.S)
    assert hallado, 'no está el perfil de bwrap'
    return [linea.strip() for linea in hallado.group(1).splitlines()]


def test_el_workflow_carga_el_perfil_de_la_guia_y_corre_toda_la_suite():
    workflow = WORKFLOW.read_text()
    guia = (RAIZ / 'docs' / 'USO.md').read_text().split('### Linux: el sandbox', 1)[1]
    assert _perfil(workflow) == _perfil(guia)
    assert 'profile bwrap /usr/bin/bwrap flags=(unconfined) {' in _perfil(workflow)
    assert 'runs-on: ubuntu-latest' in workflow and 'contents: read' in workflow
    assert 'ruff check .' in workflow and re.search(r'python -m pytest -q -p no:cacheprovider\s*$', workflow)
