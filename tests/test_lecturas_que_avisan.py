"""`read_file`, `search` y `list_files` dicen lo que no pudieron leer.

Antes:
- `read_file` cortaba a 16 000 caracteres por el medio de una línea sin decir hasta cuál llegó;
  el modelo seguía con offset=201 (la descripción decía «continue with offset») y nunca veía el hueco;
- `search` con más de 5000 archivos delante devolvía `matches: [], truncated: False`, y se saltaba
  en silencio los archivos de más de 1 MB y los que no son UTF-8;
- `list_files` decía `truncated: False` aunque el recorrido se quedara en 5000 entradas.
"""
import asyncio
from types import SimpleNamespace

import pytest

from alfalfa.proveedores.nativo.herramientas import MAX_LECTURA, ON_REQUEST_TOOLS, WorkspaceTools


@pytest.fixture
def raiz(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def llamar(raiz, nombre, **args):
    tools = WorkspaceTools(SimpleNamespace(task={'cwd': str(raiz)}, manager=SimpleNamespace(home=None),
                                           excluded_tools=(), tool_specs=ON_REQUEST_TOOLS))
    return asyncio.run(tools.call(nombre, args))


# ---------------------------------------------------------------- read_file
def test_read_file_corta_por_lineas_y_dice_donde_seguir(raiz):
    (raiz / 'largo.txt').write_text(''.join('linea %03d ' % i + 'x' * 110 + '\n' for i in range(1, 401)))
    leidas, offset = [], 1
    while True:
        r = llamar(raiz, 'read_file', path='largo.txt', offset=offset)
        assert len(r['text']) <= MAX_LECTURA
        assert r['text'].endswith('\n'), 'nunca media línea'
        leidas += [int(linea[6:9]) for linea in r['text'].splitlines()]
        assert r['end_line'] == leidas[-1]
        if not r['truncated']:
            assert 'next_offset' not in r
            break
        offset = r['next_offset']
    assert leidas == list(range(1, 401)), 'siguiendo next_offset se leen todas, sin huecos'


def test_read_file_sin_recorte_sigue_igual(raiz):
    (raiz / 'corto.txt').write_text('uno\ndos\ntres\n')
    r = llamar(raiz, 'read_file', path='corto.txt')
    assert r['text'] == 'uno\ndos\ntres\n' and r['truncated'] is False
    assert r['start_line'] == 1 and r['end_line'] == 3 and r['total_lines'] == 3


def test_read_file_con_limit_dice_la_siguiente_linea(raiz):
    (raiz / 'corto.txt').write_text(''.join('%d\n' % i for i in range(1, 11)))
    r = llamar(raiz, 'read_file', path='corto.txt', offset=3, limit=4)
    assert r['text'] == '3\n4\n5\n6\n' and r['end_line'] == 6 and r['next_offset'] == 7


def test_una_linea_enorme_se_marca_cortada(raiz):
    (raiz / 'una.min.js').write_text('a' * (MAX_LECTURA * 2) + '\nfin\n')
    r = llamar(raiz, 'read_file', path='una.min.js')
    assert len(r['text']) == MAX_LECTURA and r['line_cut'] == 1 and r['next_offset'] == 2


# ---------------------------------------------------------------- search
def llenar(carpeta, cuantos):
    carpeta.mkdir()
    for i in range(cuantos):
        (carpeta / ('a%05d.txt' % i)).write_text('nada\n')


def test_search_con_mas_de_5000_archivos_no_dice_que_no_hay(raiz):
    llenar(raiz / 'a', 5001)
    (raiz / 'z').mkdir()
    (raiz / 'z' / 'aguja.txt').write_text('AGUJA\n')  # detrás de las 5000 primeras
    r = llamar(raiz, 'search', query='AGUJA')
    assert r['matches'] == [] and r['truncated'] is True and '5000' in r['reason']


def test_list_files_con_mas_de_5000_entradas_dice_truncated(raiz):
    llenar(raiz / 'a', 5001)
    r = llamar(raiz, 'list_files')
    assert r['truncated'] is True and 'reason' in r


def test_search_encuentra_en_archivos_que_no_son_utf8(raiz):
    (raiz / 'viejo.txt').write_bytes('canción AGUJA año\n'.encode('latin-1'))
    r = llamar(raiz, 'search', query='AGUJA')
    assert [m['path'] for m in r['matches']] == ['viejo.txt'] and r['not_utf8'] == ['viejo.txt']


def test_search_dice_que_archivos_grandes_no_miro(raiz):
    (raiz / 'grande.log').write_text('x' * (1024 * 1024 + 10) + '\nAGUJA\n')
    (raiz / 'chico.txt').write_text('nada\n')
    r = llamar(raiz, 'search', query='AGUJA')
    assert r['matches'] == [] and r['skipped_large'] == ['grande.log'] and r['skipped_large_count'] == 1
    assert '1 MB' in r['reason']


def test_search_normal_sigue_igual(raiz):
    (raiz / 'a.py').write_text('x = 1\nAGUJA = 2\n')
    r = llamar(raiz, 'search', query='AGUJA')
    assert r == {'matches': [{'path': 'a.py', 'line': 2, 'text': 'AGUJA = 2'}], 'truncated': False}


def test_search_salta_binarios_sin_ruido(raiz):
    (raiz / 'dato.bin').write_bytes(b'\0AGUJA\0')
    r = llamar(raiz, 'search', query='AGUJA')
    assert r == {'matches': [], 'truncated': False}
