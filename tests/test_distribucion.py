"""instalar.sh, botella.sh y actualizar-tap.sh con una release falsa: un DMG pequeño de verdad
(hdiutil) con una app firmada ad hoc, y `curl`, `codesign -dv` y `spctl` falsos en el PATH para
hacer de GitHub, del equipo y de la notarización. Nunca salen a internet ni tocan el HOME real."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'darwin' or not shutil.which('hdiutil'),
                                reason='hdiutil y codesign son de macOS')

RAIZ = Path(__file__).resolve().parents[1]
INSTALADOR = RAIZ / 'alfalfa' / 'instalar.sh'
BOTELLA = RAIZ / 'distribucion' / 'botella.sh'
TAP = RAIZ / 'distribucion' / 'actualizar-tap.sh'
VERSION = '9.9.9'
BASE = f'https://github.com/0sw2ld0/AlfalfaCode/releases/download/v{VERSION}'

CURL = '''#!/bin/sh
# curl falso: -o DESTINO URL. Sirve FALSO_JSON y FALSO_DMG; lo demás, 404.
destino=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in -o) destino=$2; shift ;; http*|file*) url=$1 ;; esac
  shift
done
case "$url" in
  */version.json) cp "$FALSO_JSON" "$destino" ;;
  *.dmg)
    # FALSO_SENAL: la terminal se cierra (HUP) o el usuario pulsa Ctrl-C (INT) a mitad de la descarga.
    if [ -n "$FALSO_SENAL" ]; then kill -"$FALSO_SENAL" "$PPID"; sleep 1; fi
    cp "$FALSO_DMG" "$destino" ;;
  *) exit 22 ;;
esac
'''
CODESIGN = '''#!/bin/sh
# codesign falso sólo para -dv (el equipo); lo demás, el de verdad.
for a in "$@"; do
  if [ "$a" = "-dv" ]; then echo "TeamIdentifier=${FALSO_EQUIPO:-FZ8TW9AWLE}" >&2; exit 0; fi
done
exec /usr/bin/codesign "$@"
'''
SPCTL = '#!/bin/sh\nexit ${FALSO_SPCTL:-0}\n'


def _ejecutable(ruta, texto):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(texto)
    os.chmod(ruta, 0o755)


@pytest.fixture(scope='module')
def release(tmp_path_factory):
    """(dmg, huella): AlfalfaCode.app 9.9.9 firmada ad hoc, con un motor que dice su versión."""
    base = tmp_path_factory.mktemp('release')
    app = base / 'dmg' / 'AlfalfaCode.app' / 'Contents'
    _ejecutable(app / 'MacOS' / 'AlfalfaCode', '#!/bin/sh\nexit 0\n')
    _ejecutable(app / 'Resources' / 'motor' / 'alfalfa', f'#!/bin/sh\necho "AlfalfaCode {VERSION}"\n')
    _ejecutable(app / 'Resources' / 'motor' / 'python' / 'bin' / 'python3', '#!/bin/sh\nexit 0\n')
    _ejecutable(app / 'Helpers' / 'alfalfa-firma-abc', '#!/bin/sh\nexit 0\n')
    (app / 'Info.plist').write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>com.cuycoders.alfalfacode</string>
<key>CFBundleShortVersionString</key><string>{VERSION}</string>
<key>CFBundleExecutable</key><string>AlfalfaCode</string>
<key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>
''')
    subprocess.run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', str(app.parent)], check=True,
                   capture_output=True)
    dmg = base / f'AlfalfaCode-{VERSION}.dmg'
    subprocess.run(['hdiutil', 'create', '-quiet', '-volname', 'Prueba', '-srcfolder', str(base / 'dmg'),
                    '-format', 'UDZO', str(dmg)], check=True)
    return dmg, hashlib.sha256(dmg.read_bytes()).hexdigest()


@pytest.fixture
def entorno(tmp_path, release):
    """Un HOME vacío y el PATH con las herramientas falsas delante. `json(**cambios)` escribe el
    version.json que «publica» GitHub."""
    dmg, huella = release
    bin_ = tmp_path / 'falsos'
    _ejecutable(bin_ / 'curl', CURL)
    _ejecutable(bin_ / 'codesign', CODESIGN)
    _ejecutable(bin_ / 'spctl', SPCTL)
    home = tmp_path / 'home'
    home.mkdir()
    feed = tmp_path / 'version.json'

    def escribir(**cambios):
        feed.write_text(json.dumps({'version': VERSION, 'url': f'{BASE}/AlfalfaCode-{VERSION}.dmg',
                                    'sha256': huella, **cambios}))
    escribir()
    env = {'HOME': str(home), 'SHELL': '/bin/zsh', 'PATH': f'{bin_}:/usr/bin:/bin:/usr/sbin:/sbin',
           'FALSO_JSON': str(feed), 'FALSO_DMG': str(dmg), 'TMPDIR': str(tmp_path)}
    return env, home, escribir


def _instalar(env, *args, **extra):
    return subprocess.run(['sh', str(INSTALADOR), *args], capture_output=True, text=True,
                          env={**env, **extra}, timeout=120)


def test_instala_desde_la_release_de_github_y_reinstala(entorno):
    env, home, _ = entorno
    r = _instalar(env)
    assert r.returncode == 0, r.stderr
    assert 'notarizada' in r.stdout
    raiz = home / '.local' / 'share' / 'alfalfa'
    assert (raiz / 'instalacion').read_text() == f'curl\n{VERSION}\n'
    assert (raiz / 'Helpers' / 'alfalfa-firma-abc').exists()
    salida = subprocess.run([str(home / '.local' / 'bin' / 'alfalfa'), '--version'], capture_output=True, text=True)
    assert salida.stdout.strip() == f'AlfalfaCode {VERSION}'
    assert 'export PATH="$HOME/.local/bin:$PATH"' in (home / '.zshrc').read_text()
    # Otra vez: sustituye la suya sin quejarse y sin repetir la línea del PATH.
    assert _instalar(env).returncode == 0
    assert (home / '.zshrc').read_text().count('AlfalfaCode (instalar.sh)') == 1
    assert not list((home / '.local' / 'share').glob('.alfalfa-*'))


@pytest.mark.parametrize('senal,codigo', [('INT', 130), ('HUP', 129), ('TERM', 143)])
def test_una_senal_limpia_y_para(entorno, tmp_path, senal, codigo):
    env, home, _ = entorno
    r = _instalar(env, FALSO_SENAL=senal)
    assert r.returncode == codigo, (r.stdout, r.stderr)
    assert not list(tmp_path.glob('alfalfa-instalar.*'))
    assert not (home / '.local' / 'share' / 'alfalfa').exists()
    assert '✓ AlfalfaCode' not in r.stdout


def test_no_borra_una_carpeta_que_no_instalo(entorno):
    env, home, _ = entorno
    ajena = home / '.local' / 'share' / 'alfalfa'
    ajena.mkdir(parents=True)
    (ajena / 'importante.txt').write_text('mío\n')
    r = _instalar(env)
    assert r.returncode != 0 and 'no la instaló este guion' in r.stderr
    assert (ajena / 'importante.txt').read_text() == 'mío\n'
    # Con otra marca tampoco.
    (ajena / 'instalacion').write_text('brew\n1.0\n')
    assert _instalar(env).returncode != 0
    assert (ajena / 'importante.txt').exists()


def test_rechaza_un_dmg_fuera_de_las_releases(entorno):
    env, home, escribir = entorno
    escribir(url='https://evil.example/AlfalfaCode.dmg')
    r = _instalar(env)
    assert r.returncode != 0 and 'apunta fuera de las releases' in r.stderr
    escribir(url=f'https://github.com/otro/AlfalfaCode/releases/download/v{VERSION}/AlfalfaCode-{VERSION}.dmg')
    assert 'apunta fuera de las releases' in _instalar(env).stderr
    assert not (home / '.local').exists()


def test_rechaza_una_huella_o_una_version_rara(entorno):
    env, _, escribir = entorno
    escribir(sha256='0' * 64)
    assert 'huella del DMG no coincide' in _instalar(env).stderr
    escribir(sha256='no-es-hex')
    assert 'huella SHA-256 válida' in _instalar(env).stderr
    escribir(version='9.9.9; rm -rf /')
    assert 'versión rara' in _instalar(env).stderr


def test_rechaza_otro_equipo(entorno):
    env, home, _ = entorno
    r = _instalar(env, FALSO_EQUIPO='OTROEQUIPO')
    assert r.returncode != 0 and 'no está firmada por el equipo FZ8TW9AWLE' in r.stderr
    assert not (home / '.local' / 'share' / 'alfalfa').exists()


def test_rechaza_lo_que_apple_no_notarizo(entorno):
    env, home, _ = entorno
    r = _instalar(env, FALSO_SPCTL='3')
    assert r.returncode != 0 and 'notarizada' in r.stderr
    assert not (home / '.local' / 'share' / 'alfalfa').exists()


def test_rechaza_otra_version_dentro_del_dmg(entorno):
    env, _, escribir = entorno
    escribir(version='9.9.8')
    assert 'no es la versión 9.9.8' in _instalar(env).stderr


def test_una_firma_rota_no_pasa(entorno, tmp_path, release):
    env, _, escribir = entorno
    # La misma app, cambiada después de firmarla.
    origen = tmp_path / 'rota'
    subprocess.run(['hdiutil', 'attach', '-quiet', '-nobrowse', '-readonly', '-mountpoint', str(tmp_path / 'v'),
                    str(release[0])], check=True)
    try:
        shutil.copytree(tmp_path / 'v' / 'AlfalfaCode.app', origen / 'AlfalfaCode.app', symlinks=True)
    finally:
        subprocess.run(['hdiutil', 'detach', '-quiet', str(tmp_path / 'v')], check=True)
    (origen / 'AlfalfaCode.app' / 'Contents' / 'Resources' / 'motor' / 'alfalfa').write_text('#!/bin/sh\necho mal\n')
    dmg = tmp_path / 'rota.dmg'
    subprocess.run(['hdiutil', 'create', '-quiet', '-srcfolder', str(origen), '-format', 'UDZO', str(dmg)], check=True)
    escribir(sha256=hashlib.sha256(dmg.read_bytes()).hexdigest())
    r = _instalar(env, FALSO_DMG=str(dmg))
    assert r.returncode != 0 and 'firma de la app no es válida' in r.stderr


def test_botella_tiene_la_forma_de_la_formula(release, tmp_path):
    dmg, _ = release
    r = subprocess.run([str(BOTELLA), str(dmg), VERSION, str(tmp_path)], capture_output=True, text=True,
                       env={**os.environ, 'BOTELLA_SIN_EQUIPO': '1'})
    assert r.returncode == 0, r.stderr
    botella = tmp_path / f'alfalfa-{VERSION}.arm64_sonoma.bottle.tar.gz'
    assert r.stdout.strip() == hashlib.sha256(botella.read_bytes()).hexdigest()
    with tarfile.open(botella) as tar:
        nombres = set(tar.getnames())
        assert f'alfalfa/{VERSION}/bin/alfalfa' in nombres
        assert f'alfalfa/{VERSION}/libexec/Resources/motor/alfalfa' in nombres
        assert f'alfalfa/{VERSION}/libexec/Helpers/alfalfa-firma-abc' in nombres
        assert tar.extractfile(f'alfalfa/{VERSION}/libexec/instalacion').read() == f'brew\n{VERSION}\n'.encode()
        assert not any('/._' in n for n in nombres)
    # Sin BOTELLA_SIN_EQUIPO exige el equipo (esta app va firmada ad hoc).
    r = subprocess.run([str(BOTELLA), str(dmg), VERSION, str(tmp_path / 'otra')], capture_output=True, text=True)
    assert r.returncode != 0 and 'equipo FZ8TW9AWLE' in r.stderr


def _tap(entorno, destino, **extra):
    env, _, _ = entorno
    return subprocess.run([str(TAP), VERSION, str(destino)], capture_output=True, text=True,
                          env={**env, 'BOTELLA_SIN_EQUIPO': '1', 'TRABAJO': str(destino.parent / 'trabajo'), **extra},
                          timeout=120)


def test_actualizar_tap_pone_version_url_y_huellas(entorno, release, tmp_path):
    dmg, huella = release
    env, _, _ = entorno
    destino = tmp_path / 'tap'
    r = _tap(entorno, destino, VERSION_JSON=env['FALSO_JSON'], DMG=str(dmg))
    assert r.returncode == 0, r.stderr
    cask = (destino / 'Casks' / 'alfalfacode.rb').read_text()
    formula = (destino / 'Formula' / 'alfalfa.rb').read_text()
    botella = hashlib.sha256((tmp_path / 'trabajo' / f'alfalfa-{VERSION}.arm64_sonoma.bottle.tar.gz')
                             .read_bytes()).hexdigest()
    assert f'version "{VERSION}"' in cask and f'sha256 "{huella}"' in cask
    assert f'url "{BASE}/AlfalfaCode-{VERSION}.dmg"' in formula and f'sha256 "{huella}"' in formula
    assert f'root_url "{BASE}"' in formula and f'arm64_sonoma: "{botella}"' in formula
    assert (destino / 'README.md').exists()
    # Las plantillas del repositorio no cambian.
    assert VERSION not in (RAIZ / 'distribucion' / 'homebrew' / 'Formula' / 'alfalfa.rb').read_text()


def test_actualizar_tap_no_deja_un_tap_sin_bottle_publicado(entorno, tmp_path):
    # La release «de GitHub» (curl falso) no trae el bottle y no se pidió --subir-botella.
    destino = tmp_path / 'tap'
    r = _tap(entorno, destino)
    assert r.returncode != 0 and 'no trae alfalfa-9.9.9.arm64_sonoma.bottle.tar.gz' in r.stderr
    assert not (destino / 'Formula' / 'alfalfa.rb').exists()


def test_actualizar_tap_rechaza_un_dmg_con_otra_huella(entorno, tmp_path):
    env, _, escribir = entorno
    escribir(sha256='0' * 64)
    r = _tap(entorno, tmp_path / 'tap')
    assert r.returncode != 0 and 'huella del DMG' in r.stderr


@pytest.mark.skipif(not shutil.which('ruby'), reason='sin ruby')
def test_la_formula_y_el_cask_son_ruby_valido():
    for ruta in ('Formula/alfalfa.rb', 'Casks/alfalfacode.rb'):
        r = subprocess.run(['ruby', '-c', str(RAIZ / 'distribucion' / 'homebrew' / ruta)], capture_output=True,
                           text=True)
        assert r.returncode == 0, r.stderr
    formula = (RAIZ / 'distribucion' / 'homebrew' / 'Formula' / 'alfalfa.rb').read_text()
    # Formula#system no acepta opciones con nombre: `out:` llegaría a codesign como argumento.
    assert 'out:' not in formula
