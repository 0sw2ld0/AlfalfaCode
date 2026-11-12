"""Una sesión nueva que falla al arrancar no deja una carpeta «ilegible» en la barra lateral."""
import os

import pytest

from alfalfa.stdio import sessions
from alfalfa.terminal import TerminalManager


def _carpetas(home):
    base = home / 'sesiones'
    return sorted(p.name for p in base.iterdir()) if base.is_dir() else []


def test_sesion_nueva_que_falla_no_deja_carpeta(tmp_path, monkeypatch):
    (tmp_path / 'p').mkdir()
    monkeypatch.chdir(tmp_path / 'p')
    home = tmp_path / 'home'
    with pytest.raises(ValueError, match='Modo desconocido'):
        TerminalManager('x', home=home, mode='libre')
    assert _carpetas(home) == []


def test_reanudar_y_fallar_no_borra_la_sesion(tmp_path, monkeypatch):
    (tmp_path / 'p').mkdir()
    monkeypatch.chdir(tmp_path / 'p')
    home = tmp_path / 'home'
    m = TerminalManager('x', home=home)
    ident = m.task['id']
    m.task['status'] = 'interrupted'
    m.save()
    m.close()
    with pytest.raises(ValueError, match='Modo desconocido'):
        TerminalManager(None, home=home, resume=ident, mode='libre')
    assert _carpetas(home) == [ident]
    assert (home / 'sesiones' / ident / 'task.json').is_file()


def test_carpeta_con_solo_lock_no_sale_en_la_lista(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setenv('ALFALFA_HOME', str(home))
    vacia = home / 'sesiones' / 'abcdef0123456789'
    vacia.mkdir(parents=True)
    os.close(os.open(vacia / 'lock', os.O_CREAT | os.O_RDWR, 0o600))
    rota = home / 'sesiones' / '0123456789abcdef'
    rota.mkdir()
    (rota / 'task.json').write_text('{no es json')
    filas = sessions()
    assert [f['id'] for f in filas] == ['0123456789abcdef']
    assert filas[0]['estado'] == 'corrupta'
