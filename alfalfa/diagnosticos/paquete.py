"""Los ajustes de compilación de un paquete SwiftPM, sacados dentro del sandbox.

sourcekit-lsp corre fuera del sandbox (su sourcekitd es un servicio XPC que
Seatbelt bloquea), así que nunca debe evaluar un `Package.swift`: es código, y
con `dlsym`, `@_silgen_name` u otras vías puede pedir cualquier opción al
compilador sin escribir su nombre (cargar un programa del repositorio como
plugin de macros, por ejemplo). Ninguna lista negra sobre el texto lo cierra.

Por eso AlfalfaCode:

1. Evalúa el manifiesto DENTRO del sandbox con `swift package describe --type
   json` (sin red, en un temporal) y sólo se queda con datos: los objetivos de
   Swift, sus carpetas y fuentes dentro del paquete y las dependencias entre
   ellos. Ninguna opción del manifiesto llega al compilador.
2. Compila en el sandbox, con `swiftc -emit-module` y opciones fijas, los
   módulos de los objetivos de los que dependen otros, y los copia a su caché.
3. Escribe un `compile_commands.json` con opciones fijas (`-module-name`,
   `-sdk`, `-target`, `-swift-version`, `-parse-as-library`, `-I`/`-F` de su
   caché y de la plataforma) en `~/Library/Caches/alfalfa/sourcekit/…`, fuera
   del proyecto y de lo que el sandbox de las órdenes escribe.

sourcekit-lsp se lanza en modo base de datos de compilación con esa carpeta
como raíz, y sólo recibe archivos que estén en la base: uno que no estuviera
(el propio Package.swift) le haría buscar un espacio de trabajo y abriría
SwiftPM. Si algo falla, no hay diagnósticos de Swift para ese paquete; nunca
se cae al modo SwiftPM.

No se cargan macros ni plugins del paquete, ni dependencias de otros paquetes:
los diagnósticos de ese código quedan incompletos.
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import procesos

NOMBRE_DE_MODULO = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,200}$')
MAX_DESCRIBIR = 60.0
MAX_MODULO = 60.0
MAX_ARCHIVOS = 5000
COPIABLES = ('.swiftmodule', '.swiftdoc', '.swiftsourceinfo', '.abi.json')
OMITIDAS = ('.build', '.swiftpm', 'node_modules')


class PaqueteNoPreparable(Exception):
    """No se pudieron sacar los ajustes de forma segura: ese paquete se queda sin diagnósticos de Swift."""


@functools.lru_cache(maxsize=1)
def herramientas():
    """swift, swiftc, el SDK, la plataforma y el triple del sistema, preguntados a xcrun (nunca al proyecto)."""
    def xcrun(*args):
        salida = subprocess.run(['/usr/bin/xcrun', *args], capture_output=True, text=True, timeout=20, cwd='/')
        if salida.returncode != 0 or not salida.stdout.strip():
            raise PaqueteNoPreparable('xcrun ' + ' '.join(args) + ' falló')
        return salida.stdout.strip()
    try:
        swiftc = xcrun('--find', 'swiftc')
        info = json.loads(subprocess.run([swiftc, '-print-target-info'], capture_output=True, text=True,
                                         timeout=20, cwd='/').stdout)
        return {'swift': xcrun('--find', 'swift'), 'swiftc': swiftc, 'sdk': xcrun('--show-sdk-path'),
                'plataforma': xcrun('--show-sdk-platform-path'), 'triple': info['target']['triple']}
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise PaqueteNoPreparable('no se encontró la cadena de Swift: ' + (str(exc) or type(exc).__name__)) from None


async def herramientas_sin_bloquear():
    """`herramientas()` en un hilo: la primera vez llama a xcrun y swiftc, y con la máquina cargada eso
    bloquearía el bucle de eventos y el plazo de la edición no podría saltar."""
    return await asyncio.to_thread(herramientas)


def _dentro(ruta, paquete):
    real, base = os.path.realpath(ruta), os.path.realpath(paquete)
    return real == base or real.startswith(base.rstrip(os.sep) + os.sep)


def objetivos(descripcion, paquete):
    """Los objetivos de Swift de la descripción, sólo con datos comprobados.

    Cada uno: nombre de módulo, tipo, fuentes (rutas absolutas dentro del paquete,
    también tras resolver enlaces) y los objetivos del paquete de los que depende.
    Uno con dependencias de otros paquetes o con fuentes fuera del paquete se descarta.
    """
    if not isinstance(descripcion, dict) or not isinstance(descripcion.get('targets'), list):
        raise PaqueteNoPreparable('la descripción del paquete no se entiende')
    paquete = Path(paquete)
    hallados = {}
    for objetivo in descripcion['targets']:
        if not isinstance(objetivo, dict) or objetivo.get('module_type') != 'SwiftTarget':
            continue
        nombre = objetivo.get('c99name')
        tipo = objetivo.get('type')
        carpeta = objetivo.get('path')
        if (not isinstance(nombre, str) or not NOMBRE_DE_MODULO.match(nombre)
                or tipo not in ('library', 'executable', 'test', 'snippet')
                or not isinstance(carpeta, str) or objetivo.get('product_dependencies')):
            continue
        carpeta = paquete / carpeta
        if not _dentro(carpeta, paquete):
            continue
        fuentes = []
        for fuente in objetivo.get('sources') or []:
            ruta = carpeta / str(fuente)
            if not str(fuente).endswith('.swift') or not _dentro(ruta, paquete) or not ruta.is_file():
                fuentes = None
                break
            fuentes.append(str(ruta))
        if not fuentes:
            continue
        dependencias = [d for d in objetivo.get('target_dependencies') or [] if isinstance(d, str)]
        hallados[objetivo.get('name')] = {'nombre': nombre, 'tipo': tipo, 'carpeta': str(carpeta),
                                          'fuentes': sorted(fuentes), 'dependencias': dependencias}
    # Sólo dependencias entre objetivos de Swift que quedaron.
    for objetivo in hallados.values():
        objetivo['dependencias'] = [hallados[d]['nombre'] for d in objetivo['dependencias'] if d in hallados]
    return list(hallados.values())


def version_de_swift(descripcion):
    texto = str((descripcion or {}).get('tools_version') or '5')
    return '6' if texto.split('.')[0].isdigit() and int(texto.split('.')[0]) >= 6 else '5'


def archivos_de_objetivos(objetivos_):
    """Los .swift que hay en las carpetas de los objetivos (para saber si apareció o se quitó uno)."""
    hallados = []
    for objetivo in objetivos_:
        for raiz, carpetas, nombres in os.walk(objetivo['carpeta']):
            carpetas[:] = [c for c in carpetas if not c.startswith('.') and c not in OMITIDAS]
            hallados += [os.path.join(raiz, n) for n in nombres if n.endswith('.swift')]
            if len(hallados) > MAX_ARCHIVOS:
                return sorted(hallados)
    return sorted(hallados)


def firma(rutas, con_contenido=False):
    resumen = hashlib.sha256()
    for ruta in rutas:
        resumen.update(ruta.encode() + b'\0')
        if con_contenido:
            try:
                estado = os.stat(ruta)
            except OSError:
                continue
            resumen.update(str((estado.st_size, estado.st_mtime_ns)).encode())
    return resumen.hexdigest()


def dependencias_de_otros(objetivos_):
    """Los objetivos de los que depende alguno: sus módulos hay que compilarlos."""
    nombres = {d for o in objetivos_ for d in o['dependencias']}
    return [o for o in objetivos_ if o['nombre'] in nombres]


def orden_de_compilacion(objetivos_):
    """Los necesarios, cada uno después de los suyos. Un ciclo se corta (ese módulo faltará)."""
    por_nombre = {o['nombre']: o for o in objetivos_}
    necesarios, hecho, orden = dependencias_de_otros(objetivos_), set(), []

    def visitar(objetivo, camino):
        if objetivo['nombre'] in hecho or objetivo['nombre'] in camino:
            return
        for dependencia in objetivo['dependencias']:
            if dependencia in por_nombre:
                visitar(por_nombre[dependencia], camino | {objetivo['nombre']})
        hecho.add(objetivo['nombre'])
        orden.append(objetivo)
    for objetivo in necesarios:
        visitar(objetivo, frozenset())
    return orden


def opciones_fijas(objetivo, herramientas_, version, modulos):
    """Las únicas opciones que ve el compilador de sourcekit-lsp: ninguna sale del manifiesto."""
    opciones = ['-module-name', objetivo['nombre'], '-sdk', herramientas_['sdk'], '-target',
                herramientas_['triple'], '-swift-version', version, '-I', str(modulos)]
    if not objetivo.get('guion') and not any(os.path.basename(f) == 'main.swift' for f in objetivo['fuentes']):
        opciones.append('-parse-as-library')
    if objetivo['tipo'] == 'test':
        plataforma = Path(herramientas_['plataforma']) / 'Developer'
        opciones += ['-F', str(plataforma / 'Library' / 'Frameworks'), '-I', str(plataforma / 'usr' / 'lib')]
    return opciones


def escribir_base(destino, objetivos_, herramientas_, version, modulos):
    """compile_commands.json en `destino`; devuelve el conjunto de archivos que cubre."""
    filas, cubiertos = [], set()
    for objetivo in objetivos_:
        argumentos = [herramientas_['swiftc'], *opciones_fijas(objetivo, herramientas_, version, modulos),
                      *objetivo['fuentes']]
        for fuente in objetivo['fuentes']:
            filas.append({'directory': os.path.dirname(fuente), 'file': fuente, 'arguments': argumentos})
            cubiertos.add(os.path.realpath(fuente))
    Path(destino).mkdir(mode=0o700, parents=True, exist_ok=True)
    temporal = Path(destino) / 'compile_commands.json.tmp'
    temporal.write_text(json.dumps(filas, ensure_ascii=False), encoding='utf-8')
    os.replace(temporal, Path(destino) / 'compile_commands.json')
    return cubiertos


async def en_el_sandbox(argv, sandbox, env, cwd, plazo, temporal):
    """stdout de `argv` en el sandbox, sin red y con sus temporales en `temporal`; si falla, la excepción."""
    from ..seatbelt.compiladores import dentro_del_sandbox
    orden = await sandbox.envolver(dentro_del_sandbox(argv))
    proc = await asyncio.create_subprocess_exec(
        *orden, cwd=str(cwd), env=dict(env, TMPDIR=str(temporal) + os.sep), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
    try:
        salida, error = await asyncio.wait_for(proc.communicate(), plazo)
    finally:
        if proc.returncode is None:
            procesos.senal_grupo(proc.pid, 9)
            await asyncio.gather(proc.wait(), return_exceptions=True)
    if proc.returncode != 0:
        ultima = (error or b'').decode(errors='replace').strip().splitlines()
        raise PaqueteNoPreparable('código ' + str(proc.returncode) + (': ' + ultima[-1][:200] if ultima else ''))
    return salida


async def describir(paquete, sandbox, env):
    """El manifiesto evaluado por SwiftPM en el sandbox, como datos (sin opciones de compilación)."""
    h = await herramientas_sin_bloquear()
    with tempfile.TemporaryDirectory(prefix='alfalfa-paquete-', ignore_cleanup_errors=True) as temporal:
        salida = await en_el_sandbox([h['swift'], 'package', '--package-path', str(paquete), '--scratch-path',
                                      os.path.join(temporal, 'compilacion'), 'describe', '--type', 'json'],
                                     sandbox, env, paquete, MAX_DESCRIBIR, temporal)
    try:
        return json.loads(salida)
    except ValueError:
        raise PaqueteNoPreparable('swift package describe no dio JSON') from None


async def compilar_modulos(objetivos_, destino, sandbox, env, version):
    """Los módulos de los objetivos de los que dependen otros, compilados en el sandbox con opciones fijas
    y copiados a `destino`. Devuelve los que faltan (no compilaron)."""
    h = await herramientas_sin_bloquear()
    Path(destino).mkdir(mode=0o700, parents=True, exist_ok=True)
    faltan = []
    with tempfile.TemporaryDirectory(prefix='alfalfa-paquete-', ignore_cleanup_errors=True) as temporal:
        salida = Path(temporal) / 'modulos'
        salida.mkdir()
        for objetivo in orden_de_compilacion(objetivos_):
            modulo = str(salida / (objetivo['nombre'] + '.swiftmodule'))
            argv = [h['swiftc'], '-emit-module', '-emit-module-path', modulo, '-enable-testing',
                    *opciones_fijas(dict(objetivo, tipo='library'), h, version, salida), *objetivo['fuentes']]
            if '-parse-as-library' not in argv:
                argv.insert(2, '-parse-as-library')
            try:
                await en_el_sandbox(argv, sandbox, env, objetivo['carpeta'], MAX_MODULO, temporal)
            except (PaqueteNoPreparable, asyncio.TimeoutError, OSError):
                faltan.append(objetivo['nombre'])
        for archivo in salida.iterdir():
            # Sólo archivos normales con su extensión: nada de enlaces que lleven a otro sitio.
            if archivo.is_file() and not archivo.is_symlink() and archivo.name.endswith(COPIABLES):
                shutil.copyfile(archivo, Path(destino) / archivo.name)
    return faltan
