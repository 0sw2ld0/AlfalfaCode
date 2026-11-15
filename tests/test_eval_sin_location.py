"""`browser_eval` sin permiso no puede navegar a una URL `javascript:` ni tocar `location`.

`location` no se puede redefinir en el mundo de sólo lectura de la app, así que
`location['assign']('javascript:void(document.forms[0].submit())')` pasaba el filtro del motor
(sólo miraba `location.assign(`) y ejecutaba código en la página: el formulario se enviaba sin
tarjeta. Ahora cualquier uso de `location` que no sea leer una de sus partes, y cualquier
`javascript:` del script, piden permiso.
"""
import pytest

from alfalfa.navegador import parece_que_cambia


@pytest.mark.parametrize('script', [
    "location['assign']('javascript:void(document.forms[0].submit())')",
    "location['replace']('https://otra.org')",
    'window.location.assign("https://otra.org")',
    'document.location.href = "https://otra.org"',
    'return location',
    'return String(location)',
    'const l = location; l.reload()',
    "window['location']['assign']('x')",
    "return '\\x6aavascript:alert(1)'",
    "return 'JavaScript:alert(1)'",
    "return '\\u006aavascript:1'",
    "a.href = 'javascript:1'",
])
def test_lo_que_navega_o_toca_location_pide_permiso(script):
    assert parece_que_cambia(script)


@pytest.mark.parametrize('script', [
    'return location.href',
    'return window.location.pathname',
    'return [location.host, location.search, location.hash]',
    'const origen = location.origin; return origen',
    'return document.title',
])
def test_leer_las_partes_de_location_no_pide_permiso(script):
    assert not parece_que_cambia(script)
