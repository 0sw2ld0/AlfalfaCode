"""El linter que el proyecto ya tiene configurado, sólo sobre los archivos tocados.

ruff (`ruff.toml`, `.ruff.toml` o `[tool.ruff]` en `pyproject.toml`), ESLint (su
configuración o `eslintConfig` en `package.json`) y SwiftLint (`.swiftlint.yml`).
Cada archivo se pasa por la entrada estándar: así también se puede pasar el
contenido de antes de la edición y quedarse sólo con lo nuevo.

ruff también sirve de respaldo para Python cuando no hay pyright ni basedpyright,
aunque el proyecto no lo configure (con sus reglas por defecto).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
from pathlib import Path

from .. import procesos

MAX_SALIDA = 4 * 1024 * 1024
CONFIG_ESLINT = ('eslint.config.js', 'eslint.config.mjs', 'eslint.config.cjs', 'eslint.config.ts',
                 'eslint.config.mts', 'eslint.config.cts', '.eslintrc', '.eslintrc.js', '.eslintrc.cjs',
                 '.eslintrc.json', '.eslintrc.yml', '.eslintrc.yaml')
CONFIG_SWIFTLINT = ('.swiftlint.yml', '.swiftlint.yaml')
CONFIG_RUFF = ('ruff.toml', '.ruff.toml')
# Familias de ruff que son errores de verdad (como `flake8 --select=E9,F63,F7,F82`): sintaxis,
# comparaciones imposibles, sentencias fuera de sitio y nombres sin definir.
ERRORES_RUFF = ('E9', 'F63', 'F7', 'F82')


class LinterFallido(Exception):
    pass


def _subiendo(desde, raiz, prueba):
    raiz, carpeta = Path(raiz), Path(desde)
    while True:
        if prueba(carpeta):
            return carpeta
        if carpeta == raiz or raiz not in carpeta.parents:
            return None
        carpeta = carpeta.parent


def _leer(ruta):
    try:
        return ruta.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return ''


def config_ruff(carpeta):
    return (any((carpeta / n).is_file() for n in CONFIG_RUFF)
            or '[tool.ruff' in _leer(carpeta / 'pyproject.toml'))


def config_eslint(carpeta):
    if any((carpeta / n).is_file() for n in CONFIG_ESLINT):
        return True
    try:
        return 'eslintConfig' in json.loads(_leer(carpeta / 'package.json') or '{}')
    except ValueError:
        return False


def config_swiftlint(carpeta):
    return any((carpeta / n).is_file() for n in CONFIG_SWIFTLINT)


async def correr(argv, cwd, entrada, envolver, env):
    """stdout de la orden (en el sandbox), con `entrada` por stdin. Si la cancelan, se mata su grupo.

    Sin sandbox no se lanza: `envolver` lanza SandboxUnavailable y el linter cuenta como fallido."""
    argv = await envolver(argv)
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(cwd), env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, start_new_session=True)
    try:
        salida, error = await proc.communicate(entrada.encode())
    finally:
        if proc.returncode is None:
            procesos.senal_grupo(proc.pid, signal.SIGKILL)
    if len(salida) > MAX_SALIDA:
        raise LinterFallido('salida demasiado grande')
    return salida, error, proc.returncode


def _json(salida, error, codigo, validos):
    """La salida JSON del linter. Un código que no es de «sin avisos» o «con avisos» es un linter roto
    (configuración inválida, regla inexistente…), aunque no escriba nada: nunca cuenta como limpio."""
    detalle = (error or salida or b'').decode(errors='replace').strip().splitlines()
    motivo = 'código ' + str(codigo) + (': ' + detalle[-1][:200] if detalle else '')
    if codigo not in validos:
        raise LinterFallido(motivo)
    try:
        return json.loads(salida or b'null')
    except ValueError:
        raise LinterFallido(motivo) from None


class Linter:
    # Códigos de salida de «sin avisos» y «con avisos»; cualquier otro es que el linter falló.
    VALIDOS = (0, 1)

    def __init__(self, nombre, programa, cwd, env, archivos, *, del_proyecto=False):
        self.nombre = nombre
        self.programa = programa
        self.cwd = cwd
        self.env = env
        self.archivos = archivos
        # El programa es del propio proyecto (node_modules/.bin): ejecutarlo es ejecutar código del repo.
        self.del_proyecto = del_proyecto
        # Carga código del proyecto aunque el programa sea global (la configuración JS de ESLint).
        self.carga_codigo = False

    def fila(self, rel, linea, columna, error, mensaje, codigo):
        return {'path': rel, 'linea': int(linea or 1), 'columna': int(columna or 1),
                'gravedad': 'error' if error else 'aviso', 'mensaje': ' '.join(str(mensaje or '').split())[:300],
                'fuente': self.nombre, 'codigo': str(codigo)[:60] if codigo else None}


class Ruff(Linter):
    async def lint(self, rel, texto, envolver):
        salida, error, codigo = await correr(
            [self.programa, 'check', '--no-cache', '--output-format', 'json', '--force-exclude',
             '--stdin-filename', rel, '-'], self.cwd, texto, envolver, self.env)
        filas = []
        for d in _json(salida, error, codigo, self.VALIDOS) or []:
            if not isinstance(d, dict):
                continue
            lugar = d.get('location') or {}
            codigo_regla = d.get('code')
            mensaje = d.get('message')
            if codigo_regla == 'invalid-syntax':
                codigo_regla = None
                if not str(mensaje or '').startswith('SyntaxError'):
                    mensaje = 'SyntaxError: ' + str(mensaje or '')
            error_de_verdad = (codigo_regla is None or str(codigo_regla).startswith(ERRORES_RUFF)
                               or str(mensaje or '').startswith('SyntaxError'))
            filas.append(self.fila(rel, lugar.get('row'), lugar.get('column'), error_de_verdad, mensaje,
                                   codigo_regla))
        return filas


class ESLint(Linter):
    # 0 sin problemas, 1 con problemas, 2 error de configuración o del propio ESLint.
    VALIDOS = (0, 1)

    async def lint(self, rel, texto, envolver):
        salida, error, codigo = await correr(
            [self.programa, '--format', 'json', '--stdin', '--stdin-filename', str(self.raiz / rel)],
            self.cwd, texto, envolver, self.env)
        filas = []
        for archivo in _json(salida, error, codigo, self.VALIDOS) or []:
            for d in (archivo or {}).get('messages') or []:
                if not isinstance(d, dict) or (d.get('ruleId') is None and 'ignored' in str(d.get('message'))):
                    continue
                filas.append(self.fila(rel, d.get('line'), d.get('column'), d.get('severity') == 2 or d.get('fatal'),
                                       d.get('message'), d.get('ruleId')))
        return filas


class SwiftLint(Linter):
    # 0 sin problemas, 2 con alguna violación de gravedad error; 1 y los demás, que SwiftLint falló.
    VALIDOS = (0, 2)

    async def lint(self, rel, texto, envolver):
        salida, error, codigo = await correr(
            [self.programa, 'lint', '--use-stdin', '--quiet', '--no-cache', '--reporter', 'json'],
            self.cwd, texto, envolver, self.env)
        filas = []
        for d in _json(salida, error, codigo, self.VALIDOS) or []:
            if isinstance(d, dict):
                filas.append(self.fila(rel, d.get('line'), d.get('character'),
                                       str(d.get('severity')).lower() == 'error', d.get('reason'), d.get('rule_id')))
        return filas


def _programa(nombre, env, *carpetas, raiz=None):
    """(ruta, del_proyecto): el del PATH antes que el de node_modules/.bin del proyecto. Uno del PATH que
    en realidad está dentro de `raiz` (un .venv o node_modules del repo en el PATH) también es del proyecto."""
    from . import dentro_de
    global_ = shutil.which(nombre, path=(env or os.environ).get('PATH'))
    if global_:
        return global_, dentro_de(global_, raiz)
    for carpeta in carpetas:
        candidato = Path(carpeta) / nombre
        if candidato.is_file() and os.access(candidato, os.X_OK):
            return str(candidato), True
    return None, False


def del_proyecto(archivos, raiz, env, *, python_sin_servidor=False):
    """{nombre: Linter} con los archivos que le tocan a cada uno."""
    from . import JAVASCRIPT, PYTHON, SWIFT, _bins_de_node
    raiz = Path(raiz)
    hallados = {}
    python = [a for a in archivos if a['ruta'].suffix.lower() in PYTHON]
    if python:
        ruff, propio = _programa('ruff', env, raiz=raiz)
        configurado = any(_subiendo(a['ruta'].parent, raiz, config_ruff) for a in python)
        if ruff and (configurado or python_sin_servidor):
            hallados['ruff'] = Ruff('ruff', ruff, raiz, env, python, del_proyecto=propio)
    swift = [a for a in archivos if a['ruta'].suffix.lower() in SWIFT]
    if swift:
        carpeta = _subiendo(swift[0]['ruta'].parent, raiz, config_swiftlint)
        programa, propio = _programa('swiftlint', env, raiz=raiz) if carpeta else (None, False)
        if programa:
            hallados['swiftlint'] = SwiftLint('swiftlint', programa, carpeta, env, swift, del_proyecto=propio)
    javascript = [a for a in archivos if a['ruta'].suffix.lower() in JAVASCRIPT]
    if javascript:
        carpeta = _subiendo(javascript[0]['ruta'].parent, raiz, config_eslint)
        programa, propio = (_programa('eslint', env, *_bins_de_node(javascript[0]['ruta'].parent, raiz), raiz=raiz)
                            if carpeta else (None, False))
        if programa:
            linter = ESLint('eslint', programa, carpeta, env, javascript, del_proyecto=propio)
            linter.raiz = raiz
            linter.carga_codigo = True  # su configuración es JavaScript del proyecto
            hallados['eslint'] = linter
    return hallados
