"""`apply_patch` es todo o nada también al escribir, y las ediciones dejan lo que se pidió.

Antes:
- si fallaba la escritura de un archivo del parche, los anteriores quedaban cambiados y el error
  no lo decía;
- un bloque que solo añade tras `@@ def uno():` iba al final del archivo, dentro de otra función;
- un «Move to» de un guion perdía el bit de ejecución;
- `edit_file` en un archivo CRLF con `old`/`new` escritos en LF dejaba finales mezclados.
"""
import asyncio
import os
import stat

import pytest

from alfalfa.edicion_tolerante.reemplazo import tolerant_edit
from alfalfa.terminal import TerminalManager

sin_root = pytest.mark.skipif(os.geteuid() == 0, reason='root escribe en carpetas sin permiso')


@pytest.fixture
def espacio(tmp_path, monkeypatch):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    monkeypatch.chdir(raiz)
    m = TerminalManager('Edita.', home=tmp_path / 'alfalfa')
    rt = m.runtime()

    def llamar(nombre, **args):
        return asyncio.run(rt.tools.call(nombre, args))

    yield raiz, rt, llamar
    m.close()


# ---------------------------------------------------------------- todo o nada al escribir
@sin_root
def test_si_falla_un_archivo_se_deshacen_los_anteriores(espacio):
    raiz, rt, llamar = espacio
    (raiz / 'a.txt').write_text('alfa\n')
    (raiz / 'b.txt').write_text('beta\n')
    (raiz / 'bloqueada').mkdir()
    (raiz / 'bloqueada' / 'c.txt').write_text('gamma\n')
    (raiz / 'bloqueada').chmod(0o555)
    parche = ('*** Begin Patch\n*** Update File: a.txt\n@@\n-alfa\n+ALFA\n'
              '*** Add File: nuevo/dentro/n.txt\n+hola\n'
              '*** Update File: b.txt\n@@\n-beta\n+BETA\n'
              '*** Update File: bloqueada/c.txt\n@@\n-gamma\n+GAMMA\n*** End Patch')
    try:
        with pytest.raises(ValueError) as fallo:
            llamar('apply_patch', input=parche)
    finally:
        (raiz / 'bloqueada').chmod(0o755)
    texto = str(fallo.value)
    assert 'bloqueada/c.txt' in texto and 'Se deshicieron los 3 archivos' in texto
    assert (raiz / 'a.txt').read_text() == 'alfa\n' and (raiz / 'b.txt').read_text() == 'beta\n'
    assert (raiz / 'bloqueada' / 'c.txt').read_text() == 'gamma\n'
    assert not (raiz / 'nuevo').exists(), 'ni el archivo nuevo ni sus carpetas'
    assert not rt.state.get('changes'), 'nada apuntado como cambiado'


def test_si_la_carpeta_de_un_archivo_nuevo_es_un_archivo_no_queda_nada(espacio):
    raiz, _, llamar = espacio
    (raiz / 'd.txt').write_text('delta\n')
    (raiz / 'notas').write_text('soy un archivo\n')
    parche = ('*** Begin Patch\n*** Update File: d.txt\n@@\n-delta\n+DELTA\n'
              '*** Add File: notas/nuevo.txt\n+hola\n*** End Patch')
    with pytest.raises(ValueError, match='no se aplicó'):
        llamar('apply_patch', input=parche)
    assert (raiz / 'd.txt').read_text() == 'delta\n'


def test_un_parche_que_va_bien_sigue_igual(espacio):
    raiz, rt, llamar = espacio
    (raiz / 'a.txt').write_text('alfa\n')
    r = llamar('apply_patch', input='*** Begin Patch\n*** Update File: a.txt\n@@\n-alfa\n+ALFA\n'
                                    '*** Add File: b/c.txt\n+hola\n*** End Patch')
    assert r['summary'] == 'Parche aplicado: M a.txt, A b/c.txt.'
    assert (raiz / 'a.txt').read_text() == 'ALFA\n' and (raiz / 'b' / 'c.txt').read_text() == 'hola\n'
    assert len(rt.state['changes']) == 2


# ---------------------------------------------------------------- dónde va lo añadido
def test_solo_anadir_tras_un_contexto_va_detras_de_esa_linea(espacio):
    raiz, _, llamar = espacio
    (raiz / 'mod.py').write_text('def uno():\n    return 1\n\n\ndef dos():\n    return 2\n')
    llamar('apply_patch', input='*** Begin Patch\n*** Update File: mod.py\n@@ def uno():\n'
                                '+    print("entra en uno")\n*** End Patch')
    assert (raiz / 'mod.py').read_text() == ('def uno():\n    print("entra en uno")\n    return 1\n\n\n'
                                             'def dos():\n    return 2\n')


def test_solo_anadir_sin_contexto_sigue_yendo_al_final(espacio):
    raiz, _, llamar = espacio
    (raiz / 'notas.txt').write_text('uno\ndos\n')
    llamar('apply_patch', input='*** Begin Patch\n*** Update File: notas.txt\n@@\n+tres\n*** End Patch')
    assert (raiz / 'notas.txt').read_text() == 'uno\ndos\ntres\n'


# ---------------------------------------------------------------- permisos al mover
def test_mover_un_guion_conserva_el_bit_de_ejecucion(espacio):
    raiz, _, llamar = espacio
    (raiz / 'correr.sh').write_text('#!/bin/sh\necho hola\n')
    (raiz / 'correr.sh').chmod(0o755)
    llamar('apply_patch', input='*** Begin Patch\n*** Update File: correr.sh\n*** Move to: bin/correr.sh\n'
                                '@@\n-echo hola\n+echo adios\n*** End Patch')
    destino = raiz / 'bin' / 'correr.sh'
    assert not (raiz / 'correr.sh').exists() and destino.read_text() == '#!/bin/sh\necho adios\n'
    assert stat.S_IMODE(destino.stat().st_mode) == 0o755


# ---------------------------------------------------------------- CRLF en edit_file
def test_edit_file_en_un_archivo_crlf_no_mezcla_finales(espacio):
    raiz, _, llamar = espacio
    (raiz / 'win.txt').write_bytes(b'uno\r\ndos\r\ntres\r\ncuatro\r\n')
    sha = llamar('read_file', path='win.txt')['sha256']
    llamar('edit_file', path='win.txt', old='dos\ntres', new='DOS\nTRES', expected_sha256=sha)
    assert (raiz / 'win.txt').read_bytes() == b'uno\r\nDOS\r\nTRES\r\ncuatro\r\n'


@pytest.mark.parametrize('texto, old, new, esperado', [
    ('uno\r\ndos\r\n', 'dos', 'DOS\nmás', 'uno\r\nDOS\r\nmás\r\n'),
    ('uno\r\ndos\r\n', 'dos\n', 'DOS\r\n', 'uno\r\nDOS\r\n'),
    ('uno\r\ndos\r\n', 'dos', 'DOS', 'uno\r\nDOS\r\n'),
    # LF y mezclados se dejan como estaban.
    ('uno\ndos\n', 'dos', 'DOS\nmás', 'uno\nDOS\nmás\n'),
    ('uno\r\ndos\ntres\r\n', 'dos', 'DOS\nmás', 'uno\r\nDOS\nmás\ntres\r\n'),
])
def test_finales_de_linea_del_bloque_nuevo(texto, old, new, esperado):
    assert tolerant_edit(texto, old, new) == esperado
