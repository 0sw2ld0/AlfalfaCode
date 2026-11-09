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
    """stdout de la orden (en el sandbox), con `entrada` por stdin. Si la cancelan, se mata su grupo."""
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


def _json(salida, error, codigo):
    try:
        return json.loads(salida or b'null')
    except ValueError:
        detalle = (error or salida or b'').decode(errors='replace').strip().splitlines()
        raise LinterFallido('código ' + str(codigo) + (': ' + detalle[-1][:200] if detalle else '')) from None


class Linter:
    def __init__(self, nombre, programa, cwd, env, archivos):
        self.nombre = nombre
        self.programa = programa
        self.cwd = cwd
        self.env = env
        self.archivos = archivos

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
        for d in _json(salida, error, codigo) or []:
            if not isinstance(d, dict):
                continue
            lugar = d.get('location') or {}
            sintaxis = d.get('code') in (None, 'E999') or str(d.get('message', '')).startswith('SyntaxError')
            filas.append(self.fila(rel, lugar.get('row'), lugar.get('column'), sintaxis, d.get('message'),
                                   d.get('code')))
        return filas


class ESLint(Linter):
    async def lint(self, rel, texto, envolver):
        salida, error, codigo = await correr(
            [self.programa, '--format', 'json', '--stdin', '--stdin-filename', str(self.raiz / rel)],
            self.cwd, texto, envolver, self.env)
        filas = []
        for archivo in _json(salida, error, codigo) or []:
            for d in (archivo or {}).get('messages') or []:
                if not isinstance(d, dict) or (d.get('ruleId') is None and 'ignored' in str(d.get('message'))):
                    continue
                filas.append(self.fila(rel, d.get('line'), d.get('column'), d.get('severity') == 2 or d.get('fatal'),
                                       d.get('message'), d.get('ruleId')))
        return filas


class SwiftLint(Linter):
    async def lint(self, rel, texto, envolver):
        salida, error, codigo = await correr(
            [self.programa, 'lint', '--use-stdin', '--quiet', '--no-cache', '--reporter', 'json'],
            self.cwd, texto, envolver, self.env)
        filas = []
        for d in _json(salida, error, codigo) or []:
            if isinstance(d, dict):
                filas.append(self.fila(rel, d.get('line'), d.get('character'),
                                       str(d.get('severity')).lower() == 'error', d.get('reason'), d.get('rule_id')))
        return filas


def _programa(nombre, env, *carpetas):
    for carpeta in carpetas:
        candidato = Path(carpeta) / nombre
        if candidato.is_file() and os.access(candidato, os.X_OK):
            return str(candidato)
    return shutil.which(nombre, path=(env or os.environ).get('PATH'))


def del_proyecto(archivos, raiz, env, con_sandbox, *, python_sin_servidor=False):
    """{nombre: Linter} con los archivos que le tocan a cada uno."""
    from . import JAVASCRIPT, PYTHON, SWIFT, _bins_de_node
    raiz = Path(raiz)
    hallados = {}
    python = [a for a in archivos if a['ruta'].suffix.lower() in PYTHON]
    if python:
        ruff = _programa('ruff', env)
        configurado = any(_subiendo(a['ruta'].parent, raiz, config_ruff) for a in python)
        if ruff and (configurado or python_sin_servidor):
            hallados['ruff'] = Ruff('ruff', ruff, raiz, env, python)
    swift = [a for a in archivos if a['ruta'].suffix.lower() in SWIFT]
    if swift:
        carpeta = _subiendo(swift[0]['ruta'].parent, raiz, config_swiftlint)
        programa = _programa('swiftlint', env) if carpeta else None
        if programa:
            hallados['swiftlint'] = SwiftLint('swiftlint', programa, carpeta, env, swift)
    javascript = [a for a in archivos if a['ruta'].suffix.lower() in JAVASCRIPT]
    if javascript and con_sandbox:  # la configuración de ESLint es código del proyecto
        carpeta = _subiendo(javascript[0]['ruta'].parent, raiz, config_eslint)
        programa = _programa('eslint', env, *_bins_de_node(javascript[0]['ruta'].parent, raiz)) if carpeta else None
        if programa:
            linter = ESLint('eslint', programa, carpeta, env, javascript)
            linter.raiz = raiz
            hallados['eslint'] = linter
    return hallados
