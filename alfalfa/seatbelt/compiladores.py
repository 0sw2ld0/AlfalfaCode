"""Ajustes para que las herramientas de compilación funcionen dentro del sandbox.

SwiftPM abre su propio sandbox (sandbox-exec) para compilar el manifiesto y
ejecutar las macros y los plugins, y macOS no deja crear un sandbox dentro de
otro: `sandbox_apply: Operation not permitted`. Dentro del sandbox de AlfalfaCode
se le pasa `--disable-sandbox`; el de AlfalfaCode sigue rodeando la orden, así que
no se pierde aislamiento, sólo se evita el anidado.

La caché de módulos de Clang y Swift vive en `~/.cache/clang`, fuera de lo que el
sandbox deja escribir; se lleva a `$TMPDIR` con variables de entorno, que también
llegan a los scripts que compilan por dentro.

Un script (`sh "Abrir app.command"`, `make`, `./build.sh`) que llama a `swift
build` por dentro no pasa por aquí: para eso, las órdenes del sandbox llevan
delante en el PATH unos envoltorios de `swift`, `swift-build`, `swift-run`,
`swift-test`, `swift-package` y `xcrun` que añaden `--disable-sandbox` a SwiftPM
y lanzan el programa de verdad. Sólo existen en el PATH de las órdenes que corren
en el sandbox; sin sandbox, SwiftPM conserva el suyo. Se reescriben antes de cada
orden, así que una orden anterior no puede dejarlos cambiados.

Sólo se aplica a órdenes que corren dentro del sandbox. La orden que ve el
usuario en un permiso sigue siendo la que pidió el modelo.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import tempfile

SUBORDENES_SWIFT = ('build', 'run', 'test', 'package')
BINARIOS_SWIFT = ('swift-build', 'swift-run', 'swift-test', 'swift-package')
SIN_SANDBOX = '--disable-sandbox'


def carpeta_de_cache():
    return Path(tempfile.gettempdir()) / 'alfalfa-cache-de-modulos'


def entorno():
    """Variables para que Clang y SwiftPM escriban su caché donde el sandbox deja."""
    cache = str(carpeta_de_cache())
    return {'CLANG_MODULE_CACHE_PATH': cache, 'SWIFTPM_MODULECACHE_OVERRIDE': cache}


def _swiftpm(argv):
    """Índice donde va `--disable-sandbox`, o None si la orden no es de SwiftPM."""
    if not argv:
        return None
    nombre = os.path.basename(argv[0])
    if nombre in BINARIOS_SWIFT:
        return 1
    if nombre == 'swift' and len(argv) > 1 and argv[1] in SUBORDENES_SWIFT:
        return 2
    return None


def dentro_del_sandbox(argv):
    """La orden tal como se lanza dentro del sandbox: si es de SwiftPM, sin su propio sandbox."""
    argv = list(argv)
    indice = _swiftpm(argv)
    if indice is not None and SIN_SANDBOX not in argv:
        argv = argv[:indice] + [SIN_SANDBOX] + argv[indice:]
    return argv


def carpeta_de_envoltorios():
    return Path(tempfile.gettempdir()) / 'alfalfa-swiftpm-sin-sandbox'


def _envoltorio_swift(real):
    """`swift build|run|test|package …` → con `--disable-sandbox` tras la subórden."""
    return f"""#!/bin/sh
# AlfalfaCode: dentro de su sandbox, SwiftPM no puede abrir el suyo (sandbox_apply).
# Sólo está en el PATH de las órdenes que corren en el sandbox de AlfalfaCode.
real={shlex.quote(real)}
case "$1" in
    build|run|test|package)
        for argumento in "$@"; do
            [ "$argumento" = {SIN_SANDBOX} ] && exec "$real" "$@"
        done
        subOrden=$1
        shift
        exec "$real" "$subOrden" {SIN_SANDBOX} "$@" ;;
esac
exec "$real" "$@"
"""


def _envoltorio_binario(real):
    """`swift-build …` → `swift-build --disable-sandbox …`."""
    return f"""#!/bin/sh
# AlfalfaCode: dentro de su sandbox, SwiftPM no puede abrir el suyo (sandbox_apply).
real={shlex.quote(real)}
for argumento in "$@"; do
    [ "$argumento" = {SIN_SANDBOX} ] && exec "$real" "$@"
done
exec "$real" {SIN_SANDBOX} "$@"
"""


def _envoltorio_xcrun(real):
    """`xcrun swift build …` y `xcrun swift-build …` pasan por los envoltorios de al lado."""
    nombres = '|'.join(('swift',) + BINARIOS_SWIFT)
    return f"""#!/bin/sh
# AlfalfaCode: `xcrun swift build` dentro del sandbox, sin el sandbox anidado de SwiftPM.
real={shlex.quote(real)}
case "$1" in
    {nombres})
        herramienta=$1
        shift
        exec "$(dirname "$0")/$herramienta" "$@" ;;
esac
exec "$real" "$@"
"""


def envoltorios(path):
    """Escribe los envoltorios para el PATH dado y devuelve su carpeta, o None si no hay swift.

    La carpeta lleva el hash de su contenido: dos sesiones con toolchains distintos no
    se pisan los envoltorios."""
    base = carpeta_de_envoltorios()
    otros = os.pathsep.join(d for d in (path or '').split(os.pathsep)
                            if d and Path(d).parent != base)
    contenidos = {}
    for nombre in ('swift',) + BINARIOS_SWIFT + ('xcrun',):
        real = shutil.which(nombre, path=otros)
        if not real:
            continue
        if nombre == 'swift':
            contenidos[nombre] = _envoltorio_swift(real)
        elif nombre == 'xcrun':
            contenidos[nombre] = _envoltorio_xcrun(real)
        else:
            contenidos[nombre] = _envoltorio_binario(real)
    if 'swift' not in contenidos and 'xcrun' not in contenidos:
        return None
    huella = hashlib.sha256(json.dumps(contenidos, sort_keys=True).encode()).hexdigest()[:12]
    carpeta = base / huella
    carpeta.mkdir(mode=0o700, parents=True, exist_ok=True)
    for nombre, texto in contenidos.items():
        ruta = carpeta / nombre
        try:
            actual = ruta.read_text() if ruta.is_file() and not ruta.is_symlink() else None
        except OSError:
            actual = None
        if actual != texto or not os.access(ruta, os.X_OK):
            temporal = carpeta / ('.' + nombre + '.' + str(os.getpid()))
            temporal.write_text(texto)
            temporal.chmod(0o700)
            os.replace(temporal, ruta)
    return carpeta


def entorno_del_sandbox(env):
    """Añade al entorno de una orden en el sandbox la caché de módulos en $TMPDIR y, delante
    en el PATH, los envoltorios de SwiftPM para los scripts que compilan por dentro."""
    variables = entorno()
    carpeta_de_cache().mkdir(parents=True, exist_ok=True)
    for clave, valor in variables.items():
        env.setdefault(clave, valor)
    try:
        carpeta = envoltorios(env.get('PATH', os.defpath))
    except OSError:
        carpeta = None  # Sin envoltorios, una orden directa de SwiftPM sigue valiendo.
    if carpeta is not None:
        base = carpeta_de_envoltorios()
        resto = [d for d in env.get('PATH', os.defpath).split(os.pathsep) if d and Path(d).parent != base]
        env['PATH'] = os.pathsep.join([str(carpeta)] + resto)
    return env
