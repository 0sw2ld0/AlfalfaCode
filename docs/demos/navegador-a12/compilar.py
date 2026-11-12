"""Construye únicamente el bundle de depuración del ensayo A12, sin ejecutarlo ni firmarlo."""
import argparse
import platform
import plistlib
import subprocess
from pathlib import Path
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path, help='Ruta nueva para LecturasA12Demo.app')
    bundle = parser.parse_args().bundle.resolve()
    if bundle.suffix != '.app' or bundle.exists():
        raise ValueError('Indica un bundle .app nuevo en tu carpeta de ensayo.')
    carpeta = Path(__file__).resolve().parent
    package = carpeta.parents[2] / 'alfalfa-mac'
    subprocess.run(['swift', 'build', '--target', 'AlfalfaKit'], cwd=package, check=True)
    build = Path(subprocess.check_output(['swift', 'build', '--show-bin-path'], cwd=package, text=True).strip())
    objects = sorted((build / 'AlfalfaKit.build').glob('*.swift.o'))
    if not objects:
        raise ValueError('No se encontraron los objetos de AlfalfaKit de esta compilación.')
    ejecutable = bundle / 'Contents/MacOS/LecturasA12Demo'
    ejecutable.parent.mkdir(parents=True)
    subprocess.run(['swiftc', '-parse-as-library', '-target', platform.machine() + '-apple-macos14.0',
                    '-module-name', 'LecturasA12Demo', '-I', str(build / 'Modules'), str(carpeta / 'Demo.swift'),
                    *map(str, objects), '-o', str(ejecutable)], check=True)
    with (bundle / 'Contents/Info.plist').open('wb') as info:
        plistlib.dump({'CFBundleExecutable': ejecutable.name, 'CFBundleName': 'Lecturas A12',
                       'CFBundlePackageType': 'APPL', 'LSMinimumSystemVersion': '14.0',
                       'CFBundleIdentifier': 'com.alfalfa.debug.a12.lecturas.' + uuid4().hex[:8]}, info)
    print(ejecutable)


if __name__ == '__main__':
    main()
