"""Las pruebas del sandbox no suponen que $HOME está fuera de él.

Usaban `Path.home()` como «un sitio fuera del sandbox». Con un HOME temporal (para aislar la suite, o
el que exporta una sesión) la casa cuelga de $TMPDIR, donde el sandbox sí deja escribir, y diez
pruebas fallaban: entre ellas las únicas del flujo «el sandbox lo impide → tarjeta para reintentar sin
sandbox». Ahora usan la casa de la base de usuarios (`tests/casa.py`).
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.casa import bajo_temporales, base_fuera_del_sandbox, casa_real

RAIZ = Path(__file__).resolve().parents[1]
PRUEBAS = [
    'tests/test_alfalfa_bajo_demanda.py::test_real_network_is_asked_before_enabling_and_keeps_write_restrictions',
    'tests/test_alfalfa_bajo_demanda.py::test_real_approved_dangerous_command_stays_sandboxed_without_network',
    'tests/test_alfalfa_bajo_demanda.py::test_real_denied_write_keeps_result_and_asks_explicit_retry',
    'tests/test_alfalfa_seatbelt.py::test_real_run_command_writes_only_inside_allowed_roots',
    'tests/test_alfalfa_seatbelt.py::test_command_args_follow_codex_shape',
    'tests/test_alfalfa_hooks.py::test_sandbox_impide_escribir_fuera_proteger_git_y_usar_la_red',
    'tests/test_contenido_externo.py::test_lo_demas_de_fuera_sigue_preguntando',
    'tests/test_contenido_externo.py::test_un_enlace_en_tmp_que_apunta_al_hogar_sigue_preguntando',
    'tests/test_permitir_en_sesion.py::test_un_reintento_sin_sandbox_que_funciona_no_deja_el_turno_bloqueado',
]


def test_la_casa_real_no_depende_de_home(monkeypatch, tmp_path):
    antes = casa_real()
    monkeypatch.setenv('HOME', str(tmp_path))
    assert casa_real() == antes and bajo_temporales(tmp_path)


@pytest.mark.skipif(sys.platform != 'darwin', reason='usa Seatbelt')
@pytest.mark.skipif(base_fuera_del_sandbox() is None, reason='la casa real también cuelga de un temporal')
def test_con_un_home_temporal_siguen_probando_lo_mismo(tmp_path):
    # Bajo /private/tmp, como un HOME aislado: el sandbox deja escribir aquí. (Bajo el propio $TMPDIR, el
    # arnés protege los antecesores de su carpeta de datos y el sandbox ya no escribe en $TMPDIR mismo.)
    casa = Path(tempfile.mkdtemp(prefix='alfalfa-casa-', dir='/private/tmp'))
    (casa / 'Library' / 'Caches').mkdir(parents=True)
    temporales = tmp_path / 'pytest'
    temporales.mkdir()
    entorno = {k: os.environ[k] for k in ('PATH', 'TMPDIR', 'LANG', 'DEVELOPER_DIR') if k in os.environ}
    entorno.update(HOME=str(casa), PYTHONPATH=str(RAIZ), PYTHONDONTWRITEBYTECODE='1',
                   PYTEST_DEBUG_TEMPROOT=str(temporales))
    hijo = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *PRUEBAS],
                          cwd=RAIZ, env=entorno, capture_output=True, text=True, timeout=600)
    shutil.rmtree(casa, ignore_errors=True)
    assert hijo.returncode == 0, hijo.stdout[-4000:] + hijo.stderr[-2000:]
    assert ' skipped' not in hijo.stdout.splitlines()[-1], hijo.stdout[-500:]
