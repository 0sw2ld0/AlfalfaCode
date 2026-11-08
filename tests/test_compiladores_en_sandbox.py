"""SwiftPM y la caché de módulos dentro del sandbox de AlfalfaCode."""
from alfalfa.seatbelt import compiladores
from alfalfa.seatbelt.compiladores import dentro_del_sandbox, entorno_del_sandbox


def test_swiftpm_corre_sin_su_propio_sandbox():
    assert dentro_del_sandbox(['swift', 'build', '-c', 'release']) == ['swift', 'build', '--disable-sandbox', '-c', 'release']
    assert dentro_del_sandbox(['swift', 'run', 'App', '--', 'x']) == ['swift', 'run', '--disable-sandbox', 'App', '--', 'x']
    assert dentro_del_sandbox(['/usr/bin/swift-build']) == ['/usr/bin/swift-build', '--disable-sandbox']
    assert dentro_del_sandbox(['swift', 'test', '--disable-sandbox']).count('--disable-sandbox') == 1
    assert dentro_del_sandbox(['swift', '--version']) == ['swift', '--version']
    assert dentro_del_sandbox(['tools/abrir.sh']) == ['tools/abrir.sh']


def test_la_cache_va_a_tmpdir_sin_pisar_la_elegida(tmp_path):
    cache = str(compiladores.carpeta_de_cache())
    # Sin swift ni xcrun en el PATH no hay envoltorios: sólo la caché.
    vacio = str(tmp_path)
    assert entorno_del_sandbox({'PATH': vacio}) == {'CLANG_MODULE_CACHE_PATH': cache,
                                                   'SWIFTPM_MODULECACHE_OVERRIDE': cache, 'PATH': vacio}
    assert entorno_del_sandbox({'CLANG_MODULE_CACHE_PATH': '/tmp/mia', 'PATH': vacio})['CLANG_MODULE_CACHE_PATH'] == '/tmp/mia'
