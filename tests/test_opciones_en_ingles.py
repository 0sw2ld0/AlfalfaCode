"""Las opciones de `alfalfa` se llaman en inglés y su nombre en español de antes sigue valiendo."""
import argparse
import json
import pathlib
import re

import pytest

from alfalfa.terminal import parser_de_la_orden

RAIZ = pathlib.Path(__file__).resolve().parent.parent
# Las que ya se llamaban igual en los dos idiomas.
SIN_CAMBIO = {'--stdio', '--pacai', '--json', '--auto', '--cuybrowser-cli', '--version', '--help'}


def _pares():
    pares = []
    for accion in parser_de_la_orden()._actions:
        largas = [o for o in accion.option_strings if o.startswith('--')]
        if len(largas) >= 2:
            pares.append((largas[0], largas[1], accion))
    return pares


def _valor(accion):
    """Un valor que la opción acepte, para comparar lo que deja cada nombre."""
    if accion.nargs == 0:
        return []
    if accion.nargs in ('?', '*'):
        return []
    if accion.nargs == argparse.REMAINDER:
        return ['git', 'status']
    if accion.choices:
        return [str(next(c for c in accion.choices if c != accion.default))]
    if accion.type is json.loads:
        return ['[]']
    if accion.type is not None:
        return ['3']
    return ['x'] * (accion.nargs if isinstance(accion.nargs, int) else 1)


def test_cada_opcion_tiene_nombre_en_ingles_y_su_alias_en_espanol():
    pares = _pares()
    assert len(pares) == 99
    todas = {o for a in parser_de_la_orden()._actions for o in a.option_strings if o.startswith('--')}
    sueltas = {o for a in parser_de_la_orden()._actions
               if len([x for x in a.option_strings if x.startswith('--')]) == 1
               for o in a.option_strings if o.startswith('--')}
    assert sueltas == SIN_CAMBIO, 'Una opción nueva necesita su nombre en inglés (y, si lo tenía, el de español)'
    assert len(todas) == len(SIN_CAMBIO) + 2 * len(pares)
    for ingles, espanol, accion in pares:
        assert re.fullmatch(r'--[a-z][a-z0-9-]*', ingles)
        # El destino es el de siempre: stdio.py, encargos.py… leen args.solo_lectura y compañía.
        assert accion.dest == espanol[2:].replace('-', '_'), ingles


@pytest.mark.parametrize('ingles,espanol,accion', _pares(), ids=lambda v: v if isinstance(v, str) else '')
def test_el_alias_en_espanol_deja_lo_mismo_que_el_nombre_en_ingles(ingles, espanol, accion):
    valor = _valor(accion)
    con_ingles = parser_de_la_orden().parse_args([ingles, *valor])
    con_espanol = parser_de_la_orden().parse_args([espanol, *valor])
    assert vars(con_espanol) == vars(con_ingles)
    assert vars(con_ingles) != vars(parser_de_la_orden().parse_args([]))


def test_lo_que_usan_las_apps_sigue_valiendo():
    # CuyMercado Chat, la app de Mac y la extensión de VS Code arrancan el motor con los nombres en español.
    viejo = parser_de_la_orden().parse_args(['--pacai', '--solo-lectura', '--auto', '--modelo', 'openai/gpt-6',
                                             '--reanudar', 'abc', '--esfuerzo', 'high', '--modo', 'plan'])
    nuevo = parser_de_la_orden().parse_args(['--pacai', '--read-only', '--auto', '--model', 'openai/gpt-6',
                                             '--resume', 'abc', '--effort', 'high', '--permission-mode', 'plan'])
    assert vars(viejo) == vars(nuevo)
    assert viejo.solo_lectura and viejo.modelo == 'openai/gpt-6' and viejo.reanudar == 'abc' and viejo.modo == 'plan'
    assert parser_de_la_orden().parse_args(['--eliminar-sesion', 'abc', '--json']).eliminar_sesion == 'abc'


def test_la_ayuda_sale_en_ingles():
    ayuda = parser_de_la_orden().format_help()
    for ingles, espanol, accion in _pares():
        if accion.help == argparse.SUPPRESS:
            continue
        assert re.search(r'(?m)^  ' + re.escape(ingles) + r'(?![\w-])', ayuda), ingles
        assert not re.search(r'(?m)^  ' + re.escape(espanol) + r'(?![\w-])', ayuda), espanol
        assert not re.search(r'(?<![\w-])' + re.escape(espanol) + r'(?![\w-])', accion.help or ''), espanol
    assert 'docs/USO.md' in ayuda and '--reanudar' in ayuda


def test_uso_md_trae_la_tabla_entera():
    uso = (RAIZ / 'docs' / 'USO.md').read_text()
    for ingles, espanol, _ in _pares():
        assert f'| `{ingles}` | `{espanol}` |' in uso
