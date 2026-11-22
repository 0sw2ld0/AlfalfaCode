"""instalar.sh, botella.sh y actualizar-tap.sh con una release falsa: un DMG pequeño de verdad
(hdiutil) con una app firmada ad hoc, y `curl`, `codesign -dv` y `spctl` falsos en el PATH para
hacer de GitHub, del equipo y de la notarización. Nunca salen a internet ni tocan el HOME real."""
import hashlib
import json
import os
import plistlib
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
CUYBROWSER = RAIZ / 'distribucion' / 'cuybrowser.sh'
TAP = RAIZ / 'distribucion' / 'actualizar-tap.sh'
VERSION = '9.9.9'
VERSION_CUY = '0.8.0'
BASE = f'https://github.com/0sw2ld0/AlfalfaCode/releases/download/v{VERSION}'

CURL = '''#!/bin/sh
# curl falso: -o DESTINO URL. Sirve FALSO_JSON y FALSO_DMG; lo demás, 404.
destino=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in -o) destino=$2; shift ;; http*|file*) url=$1 ;; esac
  shift
done
[ -n "$FALSO_REGISTRO" ] && echo "$url" >> "$FALSO_REGISTRO"
case "$url" in
  */version-intel.json) [ -z "$FALSO_SIN_INTEL" ] || exit 22; cp "$FALSO_JSON" "$destino" ;;
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
# Un Mac con Intel: uname -m y hw.optional.arm64 como los da uno de verdad.
UNAME_INTEL = '#!/bin/sh\n[ "$1" = -m ] && { echo x86_64; exit 0; }\nexec /usr/bin/uname "$@"\n'
SYSCTL_INTEL = '''#!/bin/sh
for a in "$@"; do [ "$a" = hw.optional.arm64 ] && { echo 0; exit 0; }; done
exec /usr/sbin/sysctl "$@"
'''
ROSETTA = sys.platform == 'darwin' and subprocess.run(['/usr/bin/arch', '-x86_64', '/usr/bin/true'],
                                                      capture_output=True).returncode == 0
HDIUTIL_OCUPADO = '''#!/bin/sh
# hdiutil de verdad, salvo «detach» sin -force: el disco está ocupado, como bajo carga.
if [ "$1" = detach ] && ! printf '%s\\n' "$@" | grep -qx -- -force; then
  echo 'hdiutil: detach failed - Resource busy' >&2
  exit 16
fi
exec /usr/bin/hdiutil "$@"
'''


def _imagenes_bajo(carpeta):
    """Las imágenes conectadas cuyo DMG estaba dentro de `carpeta`."""
    salida = subprocess.run(['hdiutil', 'info', '-plist'], capture_output=True, check=True).stdout
    raiz = str(Path(carpeta).resolve())
    return [i for i in plistlib.loads(salida).get('images', []) if i.get('image-path', '').startswith(raiz)]


@pytest.fixture(autouse=True)
def sin_imagenes_colgadas(tmp_path):
    """Una prueba que falla o se interrumpe no deja su DMG conectado para las suites siguientes."""
    yield
    for imagen in _imagenes_bajo(tmp_path):
        disco = min((e['dev-entry'] for e in imagen.get('system-entities', [])), key=len)
        subprocess.run(['hdiutil', 'detach', '-force', disco], capture_output=True)


def _ejecutable(ruta, texto):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(texto)
    os.chmod(ruta, 0o755)


def _compilar(destino, texto, arq=None):
    """Un ejecutable Mach-O de verdad (de este procesador, o de `arq`)."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    fuente = f'#include <stdio.h>\nint main(void) {{ puts("{texto}"); }}\n'
    subprocess.run(['/usr/bin/clang', *(['-arch', arq] if arq else []), '-x', 'c', '-', '-o', str(destino)],
                   check=True, input=fuente, text=True, capture_output=True)


def _crear_release(base, version_cuy=None, version_ejecutable=None, arq=None, *, anterior=False):
    """DMG firmado ad hoc con un motor mínimo y, si corresponde, un ayudante Mach-O. `arq`
    («x86_64» o «arm64») fija el procesador del ejecutable de la app y de CuyBrowser."""
    app = base / 'dmg' / 'AlfalfaCode.app' / 'Contents'
    _compilar(app / 'MacOS' / 'AlfalfaCode', 'app', arq)
    _ejecutable(app / 'Resources' / 'motor' / 'alfalfa', f'#!/bin/sh\necho "AlfalfaCode {VERSION}"\n')
    _ejecutable(app / 'Resources' / 'motor' / 'python' / 'bin' / 'python3', '#!/bin/sh\nexit 0\n')
    _ejecutable(app / 'Helpers' / 'alfalfa-firma-abc', '#!/bin/sh\nexit 0\n')
    nombre = 'cuybrowser' if anterior else 'alfalfabrowser'
    clave = 'AlfalfaCuyBrowserVersion' if anterior else 'AlfalfaBrowserVersion'
    if version_ejecutable:
        _compilar(app / 'Helpers' / nombre, version_ejecutable, arq)
    clave_cuy = f'<key>{clave}</key><string>{version_cuy}</string>' if version_cuy else ''
    (app / 'Info.plist').write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>com.cuycoders.alfalfacode</string>
<key>CFBundleShortVersionString</key><string>{VERSION}</string>
<key>CFBundleExecutable</key><string>AlfalfaCode</string>
<key>CFBundlePackageType</key><string>APPL</string>
{clave_cuy}
</dict></plist>
''')
    subprocess.run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', str(app.parent)], check=True,
                   capture_output=True)
    dmg = base / f'AlfalfaCode-{VERSION}.dmg'
    subprocess.run(['hdiutil', 'create', '-quiet', '-volname', 'Prueba', '-srcfolder', str(base / 'dmg'),
                    '-format', 'UDZO', str(dmg)], check=True)
    return dmg, hashlib.sha256(dmg.read_bytes()).hexdigest()


@pytest.fixture(scope='module')
def release(tmp_path_factory):
    return _crear_release(tmp_path_factory.mktemp('release'), VERSION_CUY, VERSION_CUY)


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
    assert f'AlfalfaBrowser {VERSION_CUY} incluido' in r.stdout
    raiz = home / '.local' / 'share' / 'alfalfa'
    assert (raiz / 'instalacion').read_text() == f'curl\n{VERSION}\n'
    assert (raiz / 'Helpers' / 'alfalfa-firma-abc').exists()
    cuy = raiz / 'Helpers' / 'alfalfabrowser'
    subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(cuy)], check=True)
    salida = subprocess.run([str(home / '.local' / 'bin' / 'alfalfa'), '--version'], capture_output=True, text=True)
    assert salida.stdout.strip() == f'AlfalfaCode {VERSION}'
    assert 'export PATH="$HOME/.local/bin:$PATH"' in (home / '.zshrc').read_text()
    # Otra vez: sustituye la suya sin quejarse y sin repetir la línea del PATH.
    assert _instalar(env).returncode == 0
    assert (home / '.zshrc').read_text().count('AlfalfaCode (instalar.sh)') == 1
    assert not list((home / '.local' / 'share').glob('.alfalfa-*'))


def test_no_deja_el_dmg_conectado_si_el_disco_esta_ocupado(entorno, tmp_path):
    env, _, _ = entorno
    _ejecutable(tmp_path / 'falsos' / 'hdiutil', HDIUTIL_OCUPADO)
    r = _instalar(env)
    assert r.returncode == 0, r.stderr
    assert _imagenes_bajo(tmp_path) == []


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


@pytest.mark.parametrize('version_ejecutable', [None, '0.5.0'])
def test_rechaza_un_dmg_con_cuybrowser_incompleto(entorno, tmp_path, version_ejecutable):
    env, home, escribir = entorno
    dmg, huella = _crear_release(tmp_path / 'incompleta', VERSION_CUY, version_ejecutable)
    escribir(sha256=huella)
    r = _instalar(env, FALSO_DMG=str(dmg))
    assert r.returncode != 0 and f'AlfalfaBrowser {VERSION_CUY}' in r.stderr
    assert not (home / '.local').exists()
    salida = tmp_path / 'bottle'
    r = subprocess.run([str(BOTELLA), str(dmg), VERSION, str(salida)], capture_output=True, text=True,
                       env={**env, 'BOTELLA_SIN_EQUIPO': '1'}, timeout=120)
    assert r.returncode != 0 and f'AlfalfaBrowser {VERSION_CUY}' in r.stderr
    assert not salida.exists()


def test_dmg_anterior_sigue_instalando_sin_cuybrowser(entorno, tmp_path):
    env, home, escribir = entorno
    dmg, huella = _crear_release(tmp_path / 'anterior')
    escribir(sha256=huella, url=f'file://{dmg}')
    r = _instalar(env, ALFALFA_FEED=f'file://{env["FALSO_JSON"]}', ALFALFA_SIN_PATH='1')
    assert r.returncode == 0, r.stderr
    ayudantes = home / '.local' / 'share' / 'alfalfa' / 'Helpers'
    assert (ayudantes / 'alfalfa-firma-abc').is_file()
    assert not (ayudantes / 'cuybrowser').exists()
    assert not (ayudantes / 'alfalfabrowser').exists()


def test_dmg_anterior_con_cuybrowser_sigue_instalando(entorno, tmp_path):
    env, home, escribir = entorno
    dmg, huella = _crear_release(tmp_path / 'anterior', '0.7.1', '0.7.1', anterior=True)
    escribir(sha256=huella, url=f'file://{dmg}')
    r = _instalar(env, ALFALFA_FEED=f'file://{env["FALSO_JSON"]}', ALFALFA_SIN_PATH='1')
    assert r.returncode == 0, r.stderr
    assert 'CuyBrowser 0.7.1 incluido' in r.stdout
    assert (home / '.local' / 'share' / 'alfalfa' / 'Helpers' / 'cuybrowser').is_file()


def test_no_usa_un_binario_cuybrowser_con_otra_huella(tmp_path):
    bin_ = tmp_path / 'bin'
    _ejecutable(bin_ / 'curl', '''#!/bin/sh
while [ "$1" != "-o" ]; do shift; done
printf 'descarga dañada' > "$2"
''')
    destino = tmp_path / 'cache' / 'cuybrowser'
    r = subprocess.run(['bash', '-ec', '. "$1"; cuybrowser_preparar "$2"', 'bash',
                        str(CUYBROWSER), str(destino)], capture_output=True, text=True,
                       env={**os.environ, 'PATH': f'{bin_}:/usr/bin:/bin'}, timeout=10)
    assert r.returncode != 0 and 'huella SHA-256 fijada' in r.stderr
    assert not list(destino.parent.iterdir())


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


def _como_intel(env, tmp_path):
    """El mismo entorno, pero en un Mac con Intel, y con registro de lo que se baja."""
    bin_ = Path(env['PATH'].split(':')[0])
    _ejecutable(bin_ / 'uname', UNAME_INTEL)
    _ejecutable(bin_ / 'sysctl', SYSCTL_INTEL)
    registro = tmp_path / 'bajado.txt'
    return {**env, 'FALSO_REGISTRO': str(registro)}, registro


def test_en_apple_silicon_lee_version_json(entorno, tmp_path):
    env, _, _ = entorno
    registro = tmp_path / 'bajado.txt'
    assert _instalar(env, FALSO_REGISTRO=str(registro)).returncode == 0
    assert registro.read_text().splitlines()[0].endswith('/releases/latest/download/version.json')


def test_en_intel_lee_version_intel_y_no_instala_el_dmg_de_apple_silicon(entorno, tmp_path):
    env, home, _ = entorno
    env, registro = _como_intel(env, tmp_path)
    r = _instalar(env)
    assert registro.read_text().splitlines()[0].endswith('/releases/latest/download/version-intel.json')
    assert r.returncode != 0 and 'para otro procesador' in r.stderr and 'x86_64' in r.stderr
    assert not (home / '.local' / 'share' / 'alfalfa').exists()


def test_en_intel_avisa_si_la_release_aun_no_trae_la_version_de_intel(entorno, tmp_path):
    env, _, _ = entorno
    env, _ = _como_intel(env, tmp_path)
    r = _instalar(env, FALSO_SIN_INTEL='1')
    assert r.returncode != 0 and 'todavía no trae la versión para Mac con Intel' in r.stderr


@pytest.mark.skipif(not ROSETTA, reason='hace falta Rosetta para arrancar el CuyBrowser de Intel')
def test_en_intel_instala_el_dmg_de_intel(entorno, tmp_path):
    env, home, escribir = entorno
    dmg, huella = _crear_release(tmp_path / 'intel', VERSION_CUY, VERSION_CUY, arq='x86_64')
    escribir(sha256=huella, url=f'{BASE}/AlfalfaCode-{VERSION}-intel.dmg')
    env, _ = _como_intel(env, tmp_path)
    r = _instalar(env, FALSO_DMG=str(dmg))
    assert r.returncode == 0, r.stderr
    assert (home / '.local' / 'share' / 'alfalfa' / 'instalacion').read_text() == f'curl\n{VERSION}\n'


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
        assert f'alfalfa/{VERSION}/libexec/Helpers/alfalfabrowser' in nombres
        assert tar.extractfile(f'alfalfa/{VERSION}/libexec/instalacion').read() == f'brew\n{VERSION}\n'.encode()
        assert not any('/._' in n for n in nombres)
    # Sin BOTELLA_SIN_EQUIPO exige el equipo (esta app va firmada ad hoc).
    r = subprocess.run([str(BOTELLA), str(dmg), VERSION, str(tmp_path / 'otra')], capture_output=True, text=True)
    assert r.returncode != 0 and 'equipo FZ8TW9AWLE' in r.stderr


@pytest.mark.skipif(not ROSETTA, reason='hace falta Rosetta para arrancar el CuyBrowser de Intel')
def test_botella_de_intel(tmp_path, release):
    dmg, _ = _crear_release(tmp_path / 'intel', VERSION_CUY, VERSION_CUY, arq='x86_64')
    env = {**os.environ, 'BOTELLA_SIN_EQUIPO': '1', 'BOTELLA_ETIQUETA': 'sonoma'}
    r = subprocess.run([str(BOTELLA), str(dmg), VERSION, str(tmp_path / 'b')], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / 'b' / f'alfalfa-{VERSION}.sonoma.bottle.tar.gz').exists()
    # El DMG de Apple Silicon no sirve para el bottle de Intel.
    r = subprocess.run([str(BOTELLA), str(release[0]), VERSION, str(tmp_path / 'c')], capture_output=True,
                       text=True, env=env)
    assert r.returncode != 0 and 'no es para x86_64' in r.stderr


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
