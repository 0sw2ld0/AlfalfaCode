"""La CLI suelta: instalar.sh (curl) y la fórmula de Homebrew copian el motor de la app fuera de ella."""
import os
import subprocess
from pathlib import Path

import pytest

from alfalfa import actualizacion as act
from alfalfa import empaquetado

RAIZ = Path(__file__).resolve().parents[1]
INSTALADOR = RAIZ / 'alfalfa' / 'instalar.sh'


def _suelta(tmp_path, quien='curl', version='0.99.0'):
    """La forma que dejan instalar.sh y la fórmula: <raiz>/{Resources/motor, Helpers, instalacion}."""
    raiz = tmp_path / 'share' / 'alfalfa'
    paquete = raiz / 'Resources' / 'motor' / 'python' / 'lib' / 'python3.13' / 'site-packages' / 'alfalfa'
    paquete.mkdir(parents=True)
    (raiz / 'Helpers').mkdir()
    (raiz / 'instalacion').write_text(f'{quien}\n{version}\n')
    return raiz, paquete


def test_reconoce_la_cli_de_curl_y_la_de_brew(tmp_path):
    for quien in ('curl', 'brew'):
        raiz, paquete = _suelta(tmp_path / quien, quien)
        assert empaquetado.contenidos(paquete) == raiz
        assert empaquetado.instalacion(paquete) == quien
        assert not empaquetado.en_la_app(paquete)
        assert empaquetado.empaquetado(paquete)


def test_sin_marca_o_con_una_rara_no_es_una_instalacion(tmp_path):
    raiz, paquete = _suelta(tmp_path)
    (raiz / 'instalacion').write_text('pip\n')
    assert empaquetado.instalacion(paquete) is None
    (raiz / 'instalacion').unlink()
    assert empaquetado.contenidos(paquete) is None
    assert not empaquetado.empaquetado(paquete)


def test_la_app_sigue_siendo_la_app(tmp_path):
    paquete = (tmp_path / 'AlfalfaCode.app' / 'Contents' / 'Resources' / 'motor' / 'python' / 'lib'
               / 'python3.13' / 'site-packages' / 'alfalfa')
    paquete.mkdir(parents=True)
    assert empaquetado.instalacion(paquete) == 'app'
    assert empaquetado.en_la_app(paquete)


def test_la_cli_suelta_usa_sus_ayudantes_compilados(tmp_path):
    raiz, paquete = _suelta(tmp_path)
    ayudante = raiz / 'Helpers' / 'alfalfa-firma-abc'
    ayudante.write_text('#!/bin/sh\n')
    os.chmod(ayudante, 0o755)
    assert empaquetado.ayudante('alfalfa-firma-abc', paquete) == ayudante


@pytest.fixture
def ultima(monkeypatch):
    monkeypatch.setattr(act, 'ultima_version_https', lambda url: 'v0.3.0')


def test_brew_dice_que_se_actualiza_con_brew(ultima):
    info = act.buscar(instalada='0.2.0', instalacion='brew')
    assert info['hay_nueva'] and info['instalacion'] == 'brew' and info['orden'] == 'brew upgrade alfalfa'
    assert '`brew upgrade alfalfa`' in act.texto(dict(info, actualizado=False))
    with pytest.raises(act.ErrorDeActualizacion, match='brew upgrade alfalfa'):
        act.actualizar(instalada='0.2.0', instalacion='brew')


def test_curl_vuelve_a_pasar_instalar_sh_en_su_prefijo(ultima, tmp_path, monkeypatch):
    raiz, _ = _suelta(tmp_path / 'prefijo')
    monkeypatch.setattr(empaquetado, 'contenidos', lambda paquete=None: raiz)
    marca = tmp_path / 'llamado'
    guion = tmp_path / 'instalar.sh'
    guion.write_text(f'#!/bin/sh\necho "$ALFALFA_PREFIJO" > "{marca}"\n')
    r = act.actualizar(instalada='0.2.0', instalacion='curl', instalador=guion)
    assert r['actualizado'] and r['ultima'] == '0.3.0'
    assert marca.read_text().strip() == str(tmp_path / 'prefijo')


def test_curl_explica_si_el_instalador_falla(ultima, tmp_path, monkeypatch):
    raiz, _ = _suelta(tmp_path)
    monkeypatch.setattr(empaquetado, 'contenidos', lambda paquete=None: raiz)
    guion = tmp_path / 'instalar.sh'
    guion.write_text('#!/bin/sh\necho "✗ AlfalfaCode: la huella del DMG no coincide" >&2\nexit 1\n')
    with pytest.raises(act.ErrorDeActualizacion, match='huella del DMG'):
        act.actualizar(instalada='0.2.0', instalacion='curl', instalador=guion)


def test_curl_sin_version_nueva_no_reinstala(ultima, tmp_path):
    guion = tmp_path / 'no-se-llama.sh'
    r = act.actualizar(instalada='0.3.0', instalacion='curl', instalador=guion)
    assert not r['actualizado']


def test_instalar_sh_viaja_en_el_paquete():
    assert INSTALADOR.is_file()
    assert 'alfalfa' in (RAIZ / 'pyproject.toml').read_text().split('[tool.setuptools.package-data]')[1]
    assert subprocess.run(['sh', '-n', str(INSTALADOR)]).returncode == 0


def _sh(home, *args, **env):
    return subprocess.run(['sh', str(INSTALADOR), *args], capture_output=True, text=True,
                          env={'HOME': str(home), 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'SHELL': '/bin/zsh', **env})


def test_desinstalar_quita_lo_suyo_y_deja_lo_demas(tmp_path):
    home = tmp_path / 'home'
    raiz = home / '.local' / 'share' / 'alfalfa'
    (raiz / 'Resources').mkdir(parents=True)
    (raiz / 'instalacion').write_text('curl\n0.99.0\n')
    bin_ = home / '.local' / 'bin'
    bin_.mkdir(parents=True)
    (bin_ / 'alfalfa').write_text('#!/bin/sh\n# AlfalfaCode: lo dejó instalar.sh (https://cuycoders.dev/alfalfa)\n')
    (bin_ / 'otra').write_text('mía\n')
    (home / '.alfalfa').mkdir()
    (home / '.zshrc').write_text('alias ll=ls\n\n# AlfalfaCode (instalar.sh)\nexport PATH="$HOME/.local/bin:$PATH"\n'
                                 'export EDITOR=vim\n')
    r = _sh(home, '--desinstalar')
    assert r.returncode == 0, r.stderr
    assert not raiz.exists() and not (bin_ / 'alfalfa').exists()
    assert (bin_ / 'otra').exists() and (home / '.alfalfa').exists()
    assert (home / '.zshrc').read_text() == 'alias ll=ls\nexport EDITOR=vim\n'


def test_desinstalar_no_toca_lo_que_no_instalo(tmp_path):
    home = tmp_path / 'home'
    raiz = home / '.local' / 'share' / 'alfalfa'
    raiz.mkdir(parents=True)
    (raiz / 'algo').write_text('de otro\n')
    r = _sh(home, '--desinstalar')
    assert r.returncode != 0 and 'no se toca' in r.stderr
    assert (raiz / 'algo').exists()


def test_no_pisa_un_alfalfa_ajeno(tmp_path):
    home = tmp_path / 'home'
    (home / '.local' / 'bin').mkdir(parents=True)
    (home / '.local' / 'bin' / 'alfalfa').write_text('#!/bin/sh\necho pip\n')
    r = _sh(home)
    assert r.returncode != 0 and 'no instaló este guion' in r.stderr


def test_rechaza_un_feed_que_no_es_https_ni_file(tmp_path):
    r = _sh(tmp_path, ALFALFA_FEED='http://example.com/version.json')
    assert r.returncode != 0 and 'https:// o file://' in r.stderr


def test_rechaza_una_huella_que_no_coincide(tmp_path):
    dmg = tmp_path / 'AlfalfaCode-9.9.9.dmg'
    dmg.write_bytes(b'no es un dmg')
    feed = tmp_path / 'version.json'
    feed.write_text('{"version": "9.9.9", "url": "file://%s", "sha256": "%s"}' % (dmg, '0' * 64))
    r = _sh(tmp_path / 'home', ALFALFA_FEED='file://' + str(feed))
    assert r.returncode != 0 and 'huella del DMG no coincide' in r.stderr
    assert not (tmp_path / 'home' / '.local' / 'share' / 'alfalfa').exists()


def test_con_feed_local_solo_vale_un_dmg_local(tmp_path):
    feed = tmp_path / 'version.json'
    feed.write_text('{"version": "9.9.9", "url": "https://evil.example/x.dmg", "sha256": "%s"}' % ('0' * 64))
    r = _sh(tmp_path / 'home', ALFALFA_FEED='file://' + str(feed))
    assert r.returncode != 0 and 'sólo vale un DMG file://' in r.stderr
