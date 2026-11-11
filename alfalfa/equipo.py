"""Restricciones compartidas de un proyecto, cargadas una vez al abrir la sesión."""
from __future__ import annotations

import hashlib
import os
import re
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import proveedores, reglas
from .permisos import modos
from .presupuesto import importe

CARPETA = '.alfalfa'
ARCHIVO = 'equipo.toml'
ORIGEN = CARPETA + '/' + ARCHIVO
MODELO = re.compile(r'[\w.:/-]{1,200}')
MAX_BYTES = 128 * 1024
_RESTRICCION = (modos.PLAN, modos.MANUAL, modos.EDICIONES, modos.AUTO, modos.OMITIR)


class ErrorDeEquipo(ValueError):
    pass


def _tabla(valor, campos, nombre):
    if not isinstance(valor, dict) or set(valor) - campos:
        raise ErrorDeEquipo(ORIGEN + ': campos inválidos en ' + nombre + '.')
    return valor


def _lista(valor, valida, nombre):
    if (not isinstance(valor, list) or not 1 <= len(valor) <= 100
            or any(not isinstance(v, str) or not valida(v) for v in valor)):
        raise ErrorDeEquipo(ORIGEN + ': lista inválida en ' + nombre + '.')
    return tuple(dict.fromkeys(valor))


def _tope(valor):
    try:
        tope = importe(valor) if type(valor) in (int, float) else None
    except (ValueError, OverflowError):
        tope = None
    if tope is None:
        raise ErrorDeEquipo(ORIGEN + ': los topes deben ser números positivos en dólares.')
    return tope


def raiz_proyecto(cwd):
    carpeta = Path(cwd).resolve()
    return next((p for p in (carpeta, *carpeta.parents) if (p / '.git').exists()), carpeta)


def _leer(raiz):
    """Abre ambos componentes sin enlaces y limita la lectura antes de analizar TOML."""
    try:
        carpeta = os.open(raiz / CARPETA, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError:
        # La carpeta de ajustes puede existir por otros motivos. Sólo se rechaza
        # su enlace si contiene una política de equipo que no podemos abrir.
        try:
            os.lstat(raiz / ORIGEN)
        except (FileNotFoundError, NotADirectoryError):
            return None
        raise
    try:
        try:
            fd = os.open(ARCHIVO, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=carpeta)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, 'rb') as archivo:
            info = os.fstat(archivo.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
                raise ErrorDeEquipo(ORIGEN + ' debe ser un archivo regular de hasta 128 KiB, sin enlaces.')
            datos = archivo.read(MAX_BYTES + 1)
            if len(datos) > MAX_BYTES:
                raise ErrorDeEquipo(ORIGEN + ' supera los 128 KiB.')
            return datos
    finally:
        os.close(carpeta)


@dataclass(frozen=True)
class Equipo:
    raiz: Path
    huella: str
    modelos: tuple = ()
    modos: tuple = ()
    reglas: tuple = ()
    sesion: float | None = None
    mes: float | None = None

    @property
    def ruta(self):
        return self.raiz / ORIGEN

    @property
    def con_tope(self):
        return self.sesion is not None or self.mes is not None

    @property
    def exige_vision_propia(self):
        # La visión delegada sale por otro ejecutor, sin el presupuesto de esta sesión.
        return bool(self.modelos) or self.con_tope

    def comprobar_modelo(self, modelo, proveedor=None):
        if self.modelos:
            clave = proveedores.separar(modelo) if proveedor is None else (proveedor, modelo)
            if not any(clave == proveedores.separar(m) for m in self.modelos):
                raise ErrorDeEquipo('Modelo fuera de las reglas de equipo. Modelos permitidos: '
                                    + ', '.join(self.modelos) + '.')

    def comprobar_modo(self, modo):
        if self.modos and modo not in self.modos:
            raise ErrorDeEquipo('Modo fuera de las reglas de equipo. Modos permitidos: '
                                + ', '.join(self.modos) + '.')

    def modo_inicial(self, actual, explicito):
        if not explicito and self.modos and actual not in self.modos:
            # Un archivo del repositorio sólo puede endurecer el modo implícito.
            compatibles = _RESTRICCION[:_RESTRICCION.index(actual) + 1]
            actual = next((m for m in self.modos if m in compatibles), actual)
        self.comprobar_modo(actual)
        return actual

    def comprobar_precio(self, proveedor, precio):
        if self.con_tope and proveedor is not None and not precio:
            if getattr(type(proveedor), 'precio', proveedores.Proveedor.precio) is not proveedores.Proveedor.precio:
                raise ErrorDeEquipo('El equipo fija un presupuesto y este modelo de pago no tiene precio conocido.')

    def combinar(self, personales):
        # En caso de empate gana la procedencia del equipo. Una prohibición personal sigue mandando.
        return reglas.Reglas((*self.reglas, *personales.reglas))


def cargar(cwd):
    raiz = raiz_proyecto(cwd)
    try:
        datos = _leer(raiz)
    except OSError:
        raise ErrorDeEquipo('No se pudo leer ' + ORIGEN + ' sin atravesar enlaces.') from None
    if datos is None:
        return None
    try:
        valor = tomllib.loads(datos.decode('utf-8'))
    except (UnicodeError, tomllib.TOMLDecodeError):
        raise ErrorDeEquipo(ORIGEN + ' no es TOML válido.') from None
    _tabla(valor, {'modelos', 'permisos', 'presupuesto', 'regla'}, 'el archivo')
    permisos = _tabla(valor.get('permisos', {}), {'modos'}, 'permisos')
    topes = _tabla(valor.get('presupuesto', {}), {'sesion', 'mes'}, 'presupuesto')
    modelos = _lista(valor['modelos'], MODELO.fullmatch, 'modelos') if 'modelos' in valor else ()
    permitidos = _lista(permisos['modos'], modos.MODOS.__contains__, 'permisos.modos') if 'modos' in permisos else ()
    filas = valor.get('regla', [])
    if not isinstance(filas, list):
        raise ErrorDeEquipo(ORIGEN + ': usa una tabla [[regla]] por regla.')
    try:
        ordenes = reglas.leer({'regla': filas}, ORIGEN)
    except (reglas.ReglasInvalidas, TypeError):
        raise ErrorDeEquipo(ORIGEN + ': hay una regla de órdenes inválida.') from None
    if any(r.decision == reglas.PERMITIDA for r in ordenes.reglas):
        raise ErrorDeEquipo(ORIGEN + ': una regla de equipo sólo puede preguntar o prohibir.')
    return Equipo(raiz, hashlib.sha256(datos).hexdigest(), modelos, permitidos, tuple(ordenes.reglas),
                  _tope(topes['sesion']) if 'sesion' in topes else None,
                  _tope(topes['mes']) if 'mes' in topes else None)
