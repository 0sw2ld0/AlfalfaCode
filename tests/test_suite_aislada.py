"""La suite no depende de la casa de quien la corre, ni escribe en ella.

Corre unas pruebas en un pytest hijo con una casa falsa que trae lo que alguien puede tener de verdad:
un `~/.claude/agents/explorador.md` propio y un `~/.gitconfig` que firma los commits con un gpg que
falla y llama `desarrollo` a la rama inicial. Antes:
- el explorador del usuario tapaba al integrado y fallaba una prueba de subagentes (M23);
- el primer `git commit` de una docena de archivos fallaba con «gpg failed to sign the data» (M24);
- las de npm dejaban ~/.npm y una de la primera vez creaba ~/.alfalfa/sesiones (M25).
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
PRUEBAS = [
    'tests/test_subagentes.py::test_spawn_agent_espera_el_informe_como_agent_de_claude',
    'tests/test_alfalfa_sesiones.py::test_metadatos_de_la_sesion',
    'tests/test_aprendizaje_verificacion_final.py::test_npm_prefijo_inspecciona_carpeta_real',
    'tests/test_aprendizaje_verificacion_final.py::test_npm_workspaces_no_acredita_script_de_raiz_no_ejecutado',
    'tests/test_primera_vez.py::test_un_encargo_sin_modelo_elegido_lo_pide',
]


def listado(carpeta):
    return sorted(str(p.relative_to(carpeta)) for p in carpeta.rglob('*'))


@pytest.mark.skipif(sys.platform != 'darwin', reason='las de npm y subagentes usan Seatbelt')
def test_con_la_casa_de_otra_persona_pasan_y_no_la_tocan(tmp_path):
    casa = tmp_path / 'casa'
    (casa / '.claude' / 'agents').mkdir(parents=True)
    (casa / '.claude' / 'agents' / 'explorador.md').write_text(
        '---\nname: explorador\ndescription: Mi explorador personal\ntools: Bash, Write\n---\nHaz lo que quieras.\n')
    (casa / '.gitconfig').write_text('[user]\n\tname = Otra\n\temail = otra@example.invalid\n'
                                     '[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = /usr/bin/false\n'
                                     '[init]\n\tdefaultBranch = desarrollo\n')
    antes = listado(casa)
    temporales = tmp_path / 'pytest'
    temporales.mkdir()
    # Sólo lo imprescindible: nada de GIT_*, ALFALFA_* ni el GIT_CONFIG_GLOBAL de esta misma suite.
    entorno = {k: os.environ[k] for k in ('PATH', 'TMPDIR', 'LANG', 'DEVELOPER_DIR') if k in os.environ}
    entorno.update(HOME=str(casa), PYTHONPATH=str(RAIZ), PYTHONDONTWRITEBYTECODE='1',
                   PYTEST_DEBUG_TEMPROOT=str(temporales))
    hijo = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *PRUEBAS],
                          cwd=RAIZ, env=entorno, capture_output=True, text=True, timeout=600)
    assert hijo.returncode == 0, hijo.stdout[-4000:] + hijo.stderr[-2000:]
    assert listado(casa) == antes
