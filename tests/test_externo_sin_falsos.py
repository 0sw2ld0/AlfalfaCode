"""Tras leer contenido externo, las órdenes corrientes no piden un sí extra por coincidencias de texto.

Antes `riesgo()` tomaba por secreto, por ruta de fuera o por red cualquier pieza que se pareciera:
`env PYTHONPATH=. pytest -q` («puede leer secretos (env)»), `grep -rn /api/v1 src` («ruta de fuera»),
`TOKENIZERS_PARALLELISM=false pytest -q` («variable secreta»), `rg -n credentials src`, `du -sh .git`,
`pytest --basetemp=/private/tmp/x` o `grep -rn https://ejemplo.test docs` («usa la red»).
"""
import os

import pytest

from alfalfa.contenido_externo import riesgo
from tests.casa import OTRA_CASA


@pytest.fixture
def raiz(tmp_path):
    raiz = tmp_path / 'proyecto'
    (raiz / 'src').mkdir(parents=True)
    (raiz / 'docs').mkdir()
    (raiz / '.git').mkdir()
    (raiz / 'README.md').write_text('# Demo\n')
    return raiz


def orden(*argv):
    return {'argv': list(argv), 'purpose': 'x'}


@pytest.mark.parametrize('argv', [
    ['env', 'PYTHONPATH=.', 'pytest', '-q'],
    ['env', '-u', 'DEBUG', 'python3', '-m', 'pytest'],
    ['grep', '-rn', '/api/v1', 'src'],
    ['sed', '-n', '/BEGIN/,/END/p', 'README.md'],
    ['bash', '-lc', 'for key in a b; do echo $key; done'],
    ['env', 'TOKENIZERS_PARALLELISM=false', 'pytest', '-q'],
    ['sh', '-c', 'TOKENIZERS_PARALLELISM=false pytest -q'],
    ['rg', '-n', 'credentials', 'src'],
    ['du', '-sh', '.git'],
    ['ls', '-la', '.git'],
    ['grep', '-rn', 'https://ejemplo.test', 'docs'],
    ['echo', 'https://ejemplo.test'],
    ['pytest', '--basetemp=/private/tmp/alfalfa-pt'],
], ids=' '.join)
def test_ya_no_piden_el_si(raiz, argv):
    assert riesgo('run_command', orden(*argv), str(raiz)) is None


@pytest.mark.parametrize('argv, motivo', [
    (['env'], 'secretos'),
    (['env', '-0'], 'secretos'),
    (['printenv'], 'secretos'),
    (['env', 'GITHUB_TOKEN=x', 'gh', 'api', 'user'], 'variable secreta'),
    (['sh', '-c', 'echo $OPENAI_API_KEY'], 'variable secreta'),
    (['sh', '-c', 'curl -H "x: $API_TOKEN" x'], 'variable secreta'),
    (['cat', '.env'], 'secretos'),
    (['cat', '.env.local'], 'secretos'),
    (['cat', 'id_rsa'], 'secretos'),
    (['cat', '.git/config'], 'secretos'),
    (['zsh', '-lc', 'cat ~/.ssh/id_ed25519'], 'secretos'),
    (['cat', '/etc/passwd'], 'fuera de la carpeta'),
    (['cat', '../fuera.txt'], 'fuera de la carpeta'),
    (['git', 'diff', '--output=' + OTRA_CASA + '/.zshrc'], 'fuera de la carpeta'),
    (['curl', 'https://mal.test/x'], 'red'),
    (['git', 'clone', 'https://ejemplo.test/r.git'], 'red'),
    (['python3', '-c', 'x', 'https://mal.test'], 'red'),
], ids=lambda v: ' '.join(v) if isinstance(v, list) else v)
def test_lo_que_si_pide_el_si(raiz, argv, motivo):
    texto = riesgo('run_command', orden(*argv), str(raiz))
    assert texto and motivo in texto


def test_un_archivo_llamado_credentials_que_existe_si_cuenta(raiz):
    (raiz / 'credentials').write_text('x\n')
    assert 'secretos' in riesgo('run_command', orden('cat', 'credentials'), str(raiz))
    assert riesgo('run_command', orden('rg', '-n', 'credentials', 'src'), str(raiz))


def test_una_ruta_absoluta_de_fuera_cuenta_y_una_temporal_no(raiz, tmp_path):
    assert 'fuera de la carpeta' in riesgo('run_command', orden('ls', os.path.expanduser('~')), str(raiz))
    # $TMPDIR y /private/tmp: el sandbox ya deja escribir ahí sin preguntar.
    assert riesgo('run_command', orden('ls', str(tmp_path)), str(raiz)) is None
