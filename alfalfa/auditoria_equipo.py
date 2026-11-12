"""Registro local de herramientas y permisos, sin argumentos, contenido ni resultados completos."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import pwd
import re
import stat
import time
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from .equipo import ErrorDeEquipo

_accion = ContextVar('equipo_accion', default=None)
_respuesta = ContextVar('equipo_respuesta', default=None)
_NOMBRE = re.compile(r'[A-Za-z0-9_.-]{1,120}')
_ORIGENES = {'terminal', 'cliente_stdio', 'solo_lectura', 'omitir', 'sin_respuesta', 'interrumpido'}


def marcar_respuesta(origen):
    respuesta = _respuesta.get()
    if respuesta is not None and origen in _ORIGENES:
        respuesta['origen'] = origen


def permiso_sesion(tipo, task, claves):
    actual = _accion.get()
    if actual is not None and actual.get('activa') and actual['task'] is task:
        datos = {'permiso': actual['permiso']} if tipo == 'permiso_guardado' and 'permiso' in actual else {}
        huellas = [hashlib.sha256(c.encode('utf-8', 'surrogatepass')).hexdigest() for c in claves]
        actual['auditoria'].anotar(task, tipo, accion=actual['id'], claves=huellas, **datos)


class Auditoria:
    def __init__(self, manager):
        self.manager = manager
        self.ruta = manager.directory / 'equipo.jsonl'
        self.uid = os.getuid()
        try:
            self.cuenta = pwd.getpwuid(self.uid).pw_name
        except KeyError:
            self.cuenta = str(self.uid)

    def anotar(self, task, tipo, **datos):
        fila = dict(version=1, hora=time.time(), sesion=self.manager.task['id'],
                    agente=task['id'], turno=self.manager.task.get('turno_actual'),
                    equipo=self.manager.equipo.huella, tipo=tipo, **datos)
        linea = (json.dumps(fila, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
        try:
            fd = os.open(self.ruta, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != self.uid or info.st_mode & 0o077):
                    raise OSError('Archivo de auditoría no privado.')
                if os.write(fd, linea) != len(linea):
                    raise OSError('Escritura incompleta.')
            finally:
                os.close(fd)
        except OSError:
            raise ErrorDeEquipo('No se pudo escribir el registro privado de reglas de equipo.') from None

    @contextmanager
    def herramienta(self, task, nombre):
        ident = uuid4().hex
        herramienta = nombre if isinstance(nombre, str) and _NOMBRE.fullmatch(nombre) else 'desconocida'
        self.anotar(task, 'inicio', accion=ident, herramienta=herramienta)
        actual = dict(auditoria=self, task=task, id=ident, activa=True)
        token = _accion.set(actual)
        salida = {}
        try:
            yield salida
        except BaseException as exc:
            salida['estado'] = 'interrumpida' if isinstance(exc, asyncio.CancelledError) else 'error'
            raise
        finally:
            actual['activa'] = False
            _accion.reset(token)
            try:
                self.anotar(task, 'fin', accion=ident, **salida)
            except ErrorDeEquipo as exc:
                # La acción puede haberse ejecutado. Mantener su resultado evita inducir un reintento.
                self.manager.event(task, 'system', str(exc))

    @contextmanager
    def permiso(self, task):
        ident = uuid4().hex
        actual = _accion.get()
        vinculado = (actual is not None and actual.get('activa') and actual['auditoria'] is self
                     and actual['task'] is task)
        accion = actual['id'] if vinculado else None
        self.anotar(task, 'permiso', permiso=ident, accion=accion)
        respuesta = {'origen': 'sin_identificar'}
        token = _respuesta.set(respuesta)
        estado = 'aprobado'
        try:
            yield
        except BaseException as exc:
            estado = 'interrumpido' if isinstance(exc, asyncio.CancelledError) else 'rechazado'
            raise
        finally:
            _respuesta.reset(token)
            origen = respuesta['origen']
            quien = {'cuenta_local': self.cuenta, 'uid': self.uid} if origen in ('terminal', 'cliente_stdio') else {}
            self.anotar(task, 'decision', permiso=ident, accion=accion, estado=estado, origen=origen, **quien)
            if vinculado and estado == 'aprobado':
                actual['permiso'] = ident
