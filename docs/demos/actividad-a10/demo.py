"""Genera la demo de A10 con el candidato CB-5 y la vista SwiftUI de esta rama."""
import argparse
import hashlib
import json
import plistlib
import shlex
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


def privada(path):
    path.mkdir(mode=0o700)
    return path


def ejecutar(args, root, env, log, timeout):
    with log.open('w') as output:
        subprocess.run(args, cwd=root, env=env, stdout=output, stderr=subprocess.STDOUT, check=True, timeout=timeout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cuy', type=Path, required=True, help='Worktree del PR CB-5 compilado con npm run build')
    parser.add_argument('--node', type=Path, required=True, help='Ejecutable de Node 22')
    parser.add_argument('--swift-bin', type=Path, required=True, help='Carpeta debug con Modules y AlfalfaKit.build')
    parser.add_argument('--destino', type=Path, required=True, help='Carpeta nueva de evidencia de ensayo')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    target, cuy, node = args.destino.resolve(), args.cuy.resolve(), args.node.resolve()
    cli = cuy / 'dist/adapters/cli.js'
    if not cli.is_file() or not subprocess.check_output([node, '--version'], text=True).startswith('v22.'):
        parser.error('Compila CB-5 y usa Node 22.')
    privada(target)
    home, temporary, tools = (privada(target / name) for name in ('home', 'tmp', 'bin'))
    wrapper = tools / 'cuybrowser'
    wrapper.write_text('#!/bin/sh\nexec ' + shlex.join([str(node), str(cli)]) + ' "$@"\n')
    wrapper.chmod(0o700)
    env = {'HOME': str(home), 'CFFIXED_USER_HOME': str(home), 'TMPDIR': str(temporary) + '/',
           'PATH': str(tools) + ':/usr/bin:/bin:/usr/sbin:/sbin', 'ALFALFA_A10_DEMO_DIR': str(target),
           'PRUEBA_CUYBROWSER_ACTIVIDAD_REAL': '1', 'PRUEBA_A10_DESTINO': str(target)}
    ejecutar([root / '.venv/bin/python', '-m', 'pytest', '-q', 'tests/test_navegador_actividad_cuy_real.py'],
             root, env, target / 'cuy-real.log', 120)

    bundle = privada(target / 'ActividadA10.app')
    contents = privada(bundle / 'Contents')
    executable = privada(contents / 'MacOS') / 'ActividadA10'
    (contents / 'Info.plist').write_bytes(plistlib.dumps({
        'CFBundleExecutable': 'ActividadA10', 'CFBundleIdentifier': 'io.alfalfa.fase3.a10.fixture',
        'CFBundleName': 'Actividad A10', 'CFBundlePackageType': 'APPL', 'LSMinimumSystemVersion': '14.0',
        'NSHighResolutionCapable': True,
    }))
    built = args.swift_bin.resolve()
    objects = sorted((built / 'AlfalfaKit.build').glob('*.swift.o'))
    if not objects:
        parser.error('Primero compila y prueba AlfalfaKit en el worktree.')
    app_sources = root / 'alfalfa-mac/Sources/AlfalfaCode'
    sources = [app_sources / 'Theme.swift', app_sources / 'Session/BrowserActivityView.swift',
               Path(__file__).with_name('Demo.swift').resolve()]
    ejecutar(['xcrun', 'swiftc', '-parse-as-library', '-target', 'arm64-apple-macos14.0', '-g', '-Onone',
              '-I', built / 'Modules', *objects, *sources, '-o', executable], root, env, target / 'swiftc.log', 120)
    ejecutar([executable], root, env, target / 'app.log', 25)

    path = target / 'alfalfa/sesiones/ensayo-a10/native/ensayo-a10/actividad-navegador.json'
    data = json.loads(path.read_text())
    port = urlsplit(data['eventos'][0]['origen']).port
    with socket.socket() as probe:
        probe.settimeout(0.5)
        assert probe.connect_ex(('127.0.0.1', port)) != 0, 'El servidor de ensayo sigue abierto.'
    assert (target / 'panel.png').stat().st_size > 1000
    (target / 'cierre.json').write_text(json.dumps({
        'servidor_cerrado': True, 'puerto': port, 'app_propia_terminada': True,
        'datos_personales': False, 'captura_global': False,
        'cuy_head': subprocess.check_output(['git', '-C', cuy, 'rev-parse', 'HEAD'], text=True).strip(),
        'archivos_ui': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
    }, ensure_ascii=False, indent=2) + '\n')
    print('Demo verificada:', target)


if __name__ == '__main__':
    main()
