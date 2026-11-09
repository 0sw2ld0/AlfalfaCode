"""Buscar e instalar versiones nuevas con un repositorio git local como GitHub."""
import io
import json
import subprocess

import pytest

from alfalfa import actualizacion as act
from alfalfa.terminal import main


def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def origen(tmp_path):
    """Un repositorio «publicado» con v0.1.0 y v0.2.0, y un clon en v0.1.0."""
    pub = tmp_path / 'pub'
    pub.mkdir()
    git('init', '-q', '-b', 'main', cwd=pub)
    git('config', 'user.email', 'p@p', cwd=pub)
    git('config', 'user.name', 'p', cwd=pub)
    (pub / 'pyproject.toml').write_text('[project]\nname="x"\n')
    (pub / 'alfalfa').mkdir()
    (pub / 'alfalfa' / 'a.py').write_text('uno\n')
    git('add', '.', cwd=pub)
    git('commit', '-qm', 'uno', cwd=pub)
    git('tag', 'v0.1.0', cwd=pub)
    clon = tmp_path / 'clon'
    git('clone', '-q', str(pub), str(clon), cwd=tmp_path)
    git('config', 'user.email', 'c@c', cwd=clon)
    git('config', 'user.name', 'c', cwd=clon)
    (pub / 'alfalfa-mac').mkdir()
    (pub / 'alfalfa-mac' / 'App.swift').write_text('// dos\n')
    (pub / 'alfalfa' / 'a.py').write_text('dos\n')
    git('add', '.', cwd=pub)
    git('commit', '-qm', 'dos', cwd=pub)
    git('tag', 'v0.2.0', cwd=pub)
    git('tag', 'prueba-no-es-version', cwd=pub)
    return pub, clon


@pytest.fixture
def pips(monkeypatch):
    llamadas = []
    monkeypatch.setattr(act, '_pip', lambda python, *args: llamadas.append(args))
    return llamadas


def test_las_versiones_se_comparan_como_numeros():
    assert act.clave('v0.10.0') > act.clave('0.9.9')
    assert act.clave('prueba') is None


def test_reconoce_el_clon_y_el_paquete_instalado(origen, tmp_path):
    _, clon = origen
    assert act.raiz_del_clon(clon / 'alfalfa') == clon
    assert act.raiz_del_clon(tmp_path / 'site-packages' / 'alfalfa') is None


def test_busca_la_etiqueta_mas_alta_del_origen(origen):
    _, clon = origen
    info = act.buscar(clon, instalada='0.1.0')
    assert (info['ultima'], info['hay_nueva'], info['instalacion']) == ('0.2.0', True, 'clon')
    assert 'Hay una versión nueva: 0.2.0' in act.texto(dict(info, actualizado=False))
    assert act.buscar(clon, instalada='0.2.0')['hay_nueva'] is False


def test_actualiza_el_clon_y_avisa_si_cambio_la_app_de_mac(origen, pips):
    _, clon = origen
    r = act.actualizar(clon, instalada='0.1.0', python='py')
    assert r['actualizado'] and r['app_mac_cambio']
    assert (clon / 'alfalfa' / 'a.py').read_text() == 'dos\n'
    assert pips == [('-e', str(clon))]
    assert 'swift build' in act.texto(r)


def test_sin_version_nueva_no_toca_nada(origen, pips):
    _, clon = origen
    r = act.actualizar(clon, instalada='0.2.0')
    assert r['actualizado'] is False and pips == []


def test_no_pisa_cambios_sin_guardar(origen, pips):
    _, clon = origen
    (clon / 'alfalfa' / 'a.py').write_text('mío\n')
    with pytest.raises(act.ErrorDeActualizacion, match='cambios sin guardar'):
        act.actualizar(clon, instalada='0.1.0')
    assert (clon / 'alfalfa' / 'a.py').read_text() == 'mío\n' and pips == []


def test_no_mezcla_commits_propios(origen, pips):
    _, clon = origen
    (clon / 'b.py').write_text('mío\n')
    git('add', '.', cwd=clon)
    git('commit', '-qm', 'mío', cwd=clon)
    with pytest.raises(act.ErrorDeActualizacion, match='actualízala a mano'):
        act.actualizar(clon, instalada='0.1.0')
    assert pips == []


def test_el_paquete_se_reinstala_con_pip_en_la_etiqueta(origen, pips, monkeypatch):
    pub, _ = origen
    monkeypatch.setenv('ALFALFA_REPO_URL', str(pub))
    monkeypatch.setattr(act, 'raiz_del_clon', lambda paquete=None: None)
    r = act.actualizar(instalada='0.1.0', python='py')
    assert r['actualizado'] and r['instalacion'] == 'paquete'
    assert pips == [('--upgrade', f'alfalfa @ git+{pub}@v0.2.0')]


def test_sin_acceso_al_repositorio_se_explica(monkeypatch, tmp_path):
    monkeypatch.setenv('ALFALFA_REPO_URL', str(tmp_path / 'no-existe'))
    monkeypatch.setattr(act, 'raiz_del_clon', lambda paquete=None: None)
    salida = io.StringIO()
    assert act.orden(False, True, salida) == 1
    mensaje = json.loads(salida.getvalue())
    assert mensaje['tipo'] == 'error' and 'No se pudo consultar' in mensaje['texto']


def test_la_orden_emite_json_para_la_app(origen, monkeypatch, capsys):
    pub, _ = origen
    monkeypatch.setenv('ALFALFA_REPO_URL', str(pub))
    monkeypatch.setattr(act, 'raiz_del_clon', lambda paquete=None: None)
    monkeypatch.setattr(act, 'buscar', lambda raiz=None, instalada='0.1.0', f=act.buscar: f(raiz, instalada))
    assert main(['--buscar-actualizacion', '--json']) == 0
    mensaje = json.loads(capsys.readouterr().out)
    assert (mensaje['tipo'], mensaje['ultima'], mensaje['hay_nueva']) == ('actualizacion', '0.2.0', True)


def test_version_dice_la_del_paquete(capsys):
    from alfalfa import __version__
    with pytest.raises(SystemExit):
        main(['--version'])
    assert capsys.readouterr().out.strip() == 'AlfalfaCode ' + __version__


def test_pip_usa_uv_si_el_entorno_no_trae_pip(monkeypatch):
    ordenes = []

    def ejecutar(orden, **kwargs):
        ordenes.append(orden)
        return subprocess.CompletedProcess(orden, 1 if orden[1:3] == ['-m', 'pip'] else 0, '', '')
    monkeypatch.setattr(act.subprocess, 'run', ejecutar)
    monkeypatch.setattr(act.shutil, 'which', lambda nombre: '/bin/uv' if nombre == 'uv' else None)

    act._pip('/v/bin/python', '-e', '/repo')

    assert ordenes[-1] == ['/bin/uv', 'pip', 'install', '--quiet', '--python', '/v/bin/python', '-e', '/repo']


def test_pip_del_entorno_si_lo_trae(monkeypatch):
    ordenes = []

    def ejecutar(orden, **kwargs):
        ordenes.append(orden)
        return subprocess.CompletedProcess(orden, 0, '', '')
    monkeypatch.setattr(act.subprocess, 'run', ejecutar)
    monkeypatch.setattr(act.shutil, 'which', lambda nombre: '/bin/uv')

    act._pip('/v/bin/python', '-e', '/repo')

    assert ordenes[-1] == ['/v/bin/python', '-m', 'pip', 'install', '--quiet', '-e', '/repo']


def test_instalar_dependencias_reinstala_el_clon_en_su_sitio(origen, pips, tmp_path):
    _, clon = origen

    act.instalar_dependencias(clon)

    assert pips == [('-e', str(clon))]
    with pytest.raises(act.ErrorDeActualizacion, match='no está en un clon'):
        act.instalar_dependencias(False)


# -- AlfalfaCode.app ------------------------------------------------------------

class _Respuesta:
    def __init__(self, datos, estado=200):
        self.datos, self.estado = datos, estado

    def raise_for_status(self):
        if self.estado != 200:
            raise RuntimeError(f'HTTP {self.estado}')

    def json(self):
        return self.datos


def _github(etiquetas, vistas=None, estado=200):
    def get(url, **kw):
        if vistas is not None:
            vistas.append(url)
        return _Respuesta([{'name': e} for e in etiquetas], estado)
    return get


def test_la_app_busca_la_version_por_https_sin_git(monkeypatch):
    vistas = []
    monkeypatch.setattr(act, '_git', lambda *a, **k: pytest.fail('dentro de la app no se usa git'))
    falsa = _github(['v0.9.0', 'v0.10.0', 'beta'], vistas)
    monkeypatch.setattr(act, 'ultima_version_https', lambda url, f=act.ultima_version_https: f(url, get=falsa))
    monkeypatch.delenv('ALFALFA_REPO_URL', raising=False)
    info = act.buscar(instalada='0.9.5', app=True)
    assert vistas == ['https://api.github.com/repos/0sw2ld0/AlfalfaCode/tags']
    assert (info['ultima'], info['hay_nueva'], info['instalacion']) == ('0.10.0', True, 'app')
    assert info['descarga'].startswith('https://github.com/0sw2ld0/AlfalfaCode/releases')
    assert 'Descárgala' in act.texto(dict(info, actualizado=False))


def test_la_app_no_se_reinstala_sola(monkeypatch, pips):
    monkeypatch.setattr(act, 'ultima_version_https', lambda url: 'v9.0.0')
    monkeypatch.setattr(act, '_git', lambda *a, **k: pytest.fail('dentro de la app no se usa git'))
    with pytest.raises(act.ErrorDeActualizacion, match='arrástrala a Aplicaciones'):
        act.actualizar(instalada='0.1.0', app=True)
    assert pips == []
    # Al día: no hay nada que descargar ni error.
    monkeypatch.setattr(act, 'ultima_version_https', lambda url: 'v0.1.0')
    assert act.actualizar(instalada='0.1.0', app=True)['actualizado'] is False


def test_la_app_explica_si_github_no_responde():
    with pytest.raises(act.ErrorDeActualizacion, match='No se pudo consultar'):
        act.ultima_version_https('https://github.com/0sw2ld0/AlfalfaCode.git', get=_github([], estado=503))
    with pytest.raises(act.ErrorDeActualizacion, match='github.com'):
        act.ultima_version_https('https://ejemplo.com/repo.git', get=_github([]))
