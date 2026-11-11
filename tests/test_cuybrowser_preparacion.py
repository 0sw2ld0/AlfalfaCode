import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from alfalfa import cuybrowser_preparacion, cuybrowser_proceso
from alfalfa.terminal import main


def test_resuelve_ayudante_de_la_distribucion_y_path_explicito(tmp_path, monkeypatch):
    root = tmp_path / 'proyecto'
    root.mkdir()
    helper = tmp_path / 'AlfalfaCode.app/Contents/Helpers/cuybrowser'
    external = tmp_path / 'bin/cuybrowser'
    monkeypatch.setattr(cuybrowser_proceso.empaquetado, 'ayudante', lambda _: helper)
    monkeypatch.setattr(cuybrowser_proceso.shutil, 'which', lambda _: str(external))
    assert cuybrowser_proceso.ejecutable_cuybrowser(root) == (str(helper.resolve()), 'incluido')
    assert cuybrowser_proceso.ejecutable_cuybrowser(root, origen='path') == (str(external.resolve()), 'path')


def test_entorno_separa_perfiles_y_comparte_binario_chromium(tmp_path, monkeypatch):
    home = tmp_path / 'alfalfa'
    primero, segundo = home / 'cuybrowser/uno', home / 'cuybrowser/dos'
    monkeypatch.setenv('BUN_OPTIONS', '--preload=ajeno.js')
    monkeypatch.setenv('NODE_OPTIONS', '--require=ajeno.js')
    monkeypatch.delenv('PLAYWRIGHT_BROWSERS_PATH', raising=False)
    cache = str(home / 'chromium-cuybrowser')
    for profile in (primero, segundo):
        environment = cuybrowser_proceso.entorno_cuybrowser(home, profile)
        assert environment['CUYBROWSER_HOME'] == str(profile)
        assert environment['PLAYWRIGHT_BROWSERS_PATH'] == cache
        assert not {'BUN_OPTIONS', 'NODE_OPTIONS'} & environment.keys()
    anterior = primero / 'browsers'
    anterior.mkdir(parents=True)
    assert cuybrowser_proceso.entorno_cuybrowser(home, primero)['PLAYWRIGHT_BROWSERS_PATH'] == str(anterior)
    Path(cache).mkdir(mode=0o700)
    assert cuybrowser_proceso.entorno_cuybrowser(home, primero)['PLAYWRIGHT_BROWSERS_PATH'] == cache


def test_cli_descarga_solo_con_confirmacion_y_si_falta(tmp_path, monkeypatch, capsys):
    home = tmp_path / 'alfalfa'
    root = tmp_path / 'proyecto'
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setenv('ALFALFA_HOME', str(home))
    monkeypatch.delenv('PLAYWRIGHT_BROWSERS_PATH', raising=False)
    monkeypatch.setattr(cuybrowser_proceso.empaquetado, 'ayudante', lambda _: tmp_path / 'Helpers/cuybrowser')
    calls = []
    installed = False

    def run(argv, **kwargs):
        nonlocal installed
        calls.append(argv[1:])
        assert kwargs['cwd'] == '/'
        assert kwargs['env']['PLAYWRIGHT_BROWSERS_PATH'] == str(home / 'chromium-cuybrowser')
        if argv[1] == 'install-browser':
            assert argv[2:] == ['--yes']
            installed = True
            return subprocess.CompletedProcess(argv, 0)
        if not installed:
            return subprocess.CompletedProcess(argv, 1, '', cuybrowser_preparacion.SIN_CHROMIUM)
        return subprocess.CompletedProcess(argv, 0, '* Chromium de Playwright: /ruta/propia', '')

    monkeypatch.setattr(cuybrowser_preparacion.subprocess, 'run', run)
    assert main(['--descargar-chromium', '--json']) == 1
    assert json.loads(capsys.readouterr().out)['estado'] == 'sin_chromium'
    assert calls == [['browsers']] and not home.exists()
    assert main(['--descargar-chromium', '--confirmar-descarga-chromium', '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['version'] == 2
    assert result['estado'] == 'disponible' and result['navegador'] == 'Chromium de Playwright'
    assert calls == [['browsers'], ['browsers'], ['install-browser', '--yes'], ['browsers']]
    assert main(['--descargar-chromium', '--confirmar-descarga-chromium', '--json']) == 0
    assert calls.count(['install-browser', '--yes']) == 1


def test_preferencia_cli_solo_se_aplica_despues_de_elegir(tmp_path, monkeypatch, capsys):
    home = tmp_path / 'alfalfa'
    monkeypatch.setenv('ALFALFA_HOME', str(home))
    args = SimpleNamespace(motor_navegador=None, origen_cuybrowser=None)
    cuybrowser_preparacion.preparar_terminal(args, home)
    assert args.motor_navegador is None and not home.exists()
    assert main(['--cuybrowser-cli', 'activar']) == 0
    assert 'activado' in capsys.readouterr().out
    cuybrowser_preparacion.preparar_terminal(args, home)
    assert args.motor_navegador == 'cuybrowser'
    assert main(['--cuybrowser-cli', 'desactivar']) == 0
    args.motor_navegador = None
    cuybrowser_preparacion.preparar_terminal(args, home)
    assert args.motor_navegador is None


def test_app_no_sondea_path_sin_elegirlo(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'proyecto'
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(cuybrowser_proceso.empaquetado, 'ayudante', lambda _: None)
    monkeypatch.setattr(cuybrowser_proceso.shutil, 'which', lambda _: str(tmp_path / 'bin/cuybrowser'))

    def inesperado(*args, **kwargs):
        raise AssertionError('La consulta de la app no debe ejecutar CuyBrowser de PATH sin selección.')

    monkeypatch.setattr(cuybrowser_preparacion.subprocess, 'run', inesperado)
    assert main(['--cuybrowser-estado', '--cuybrowser-solo-incluido', '--json']) == 1
    assert json.loads(capsys.readouterr().out)['estado'] == 'sin_cuybrowser'
