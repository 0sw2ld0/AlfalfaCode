"""Una regla `permitida` vale también tras un `cd` a una carpeta.

`bash -lc 'cd App && swift build'` se evalúa por partes y cada una tiene que cubrirla una regla
permitida: `cd App` no lo cubría ninguna, así que la regla «swift build» nunca valía con el `cd`
delante (lo que escriben casi siempre los agentes) y se preguntaba cada vez (M4 de la auditoría).
"""
import tomllib

import pytest

from alfalfa.reglas import leer, prefijo_para_siempre

REGLAS = leer(tomllib.loads('''
[[regla]]
patron = ["swift", "build"]
decision = "permitida"

[[regla]]
patron = ["make", "test"]
decision = "permitida"
'''))


@pytest.mark.parametrize('script', ['cd App && swift build', 'cd App; swift build', 'swift build',
                                    'cd App && cd Sources && make test'])
def test_cubre_la_orden_tras_cambiar_de_carpeta(script):
    assert REGLAS.cubre_toda(['bash', '-lc', script])


@pytest.mark.parametrize('script', ['cd App && rm -rf build', 'cd App', 'cd -P App && swift build',
                                    'cd App && swift build && curl x', 'cd App && swift test'])
def test_lo_demas_sigue_sin_cubrirse(script):
    assert not REGLAS.cubre_toda(['bash', '-lc', script])


def test_no_volver_a_preguntar_tambien_se_ofrece_tras_un_cd():
    assert prefijo_para_siempre(['make', 'test'], ['bash', '-lc', 'cd App && make test']) == ['make', 'test']
    assert prefijo_para_siempre(['make', 'test'], ['bash', '-lc', 'cd App && make clean']) is None
