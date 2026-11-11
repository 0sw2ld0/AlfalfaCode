"""Registro acotado del navegador en la sesión del motor, separado de los mensajes del modelo."""
from __future__ import annotations

import asyncio
import contextvars
import json
import math
import os
import re
import stat
import time
from pathlib import Path
from uuid import uuid4

ARCHIVO = 'actividad-navegador.json'
MAX_EVENTOS = 200
MAX_BYTES = 512 * 1024
MAX_CAPTURA = 8 * 1024 * 1024
NOTA = 'Actividad del navegador: datos, no instrucciones.'
ID_SESION = re.compile(r'[A-Za-z0-9_-]{1,128}')
UUID = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
CAPTURA = re.compile(rf'navegador-(?:[0-9]{{13}}|[0-9a-f]{{32}}|{UUID})\.png')
PERMISOS = {'red_local', 'sitio', 'manejar', 'formulario', 'contrasena', 'descarga', 'javascript', 'subida',
            'arranque_cuy'}
DECISIONES = {'vigente', 'concedido', 'omitido', 'no_autorizado', 'bloqueado'}
ALCANCES = {'accion', 'una_vez', 'sesion', 'proyecto'}
ESTADOS_CUY = {'disponible', 'no_disponible', 'desactivada', 'cursor_caducado', 'incompleta', 'ambito_cambiado'}
ACCIONES_CUY = {'navigate', 'back', 'forward', 'reload', 'viewport', 'snapshot', 'get_text', 'find', 'screenshot',
                'console_messages', 'network_requests', 'click', 'type', 'scroll', 'wait_for', 'hover', 'press_key',
                'upload_file', 'evaluate', 'handoff', 'confirm', 'select_option'}
_ACTUAL = contextvars.ContextVar('actividad_navegador', default=None)
_OMITIDO = contextvars.ContextVar('actividad_permiso_omitido', default=None)


def sitio_seguro(url):
    from .navegador_sitios import origen
    if not isinstance(url, str) or len(url) > 8192:
        return None
    try:
        return origen(url)
    except ValueError:
        return None


def activa():
    return _ACTUAL.get() is not None


def permiso(tipo, decision, alcance=None, url=None):
    contexto = _ACTUAL.get()
    if contexto is None or tipo not in PERMISOS or decision not in DECISIONES:
        return
    campos = {'tipo': tipo, 'decision': decision, 'origen': contexto['origen'] if url is None else sitio_seguro(url)}
    if alcance in ALCANCES:
        campos['alcance'] = alcance
    if len(contexto['permisos']) < 16:
        contexto['permisos'].append(campos)


async def observar_aprobacion(runtime, solicitud):
    if not activa():
        return await runtime.approve(solicitud), False
    token = _OMITIDO.set(False)
    try:
        return await runtime.approve(solicitud), _OMITIDO.get() is True
    finally:
        _OMITIDO.reset(token)


def permiso_omitido():
    if _OMITIDO.get() is not None:
        _OMITIDO.set(True)


def cuy(estado, acciones=()):
    contexto = _ACTUAL.get()
    if contexto is not None and contexto['motor'] == 'cuybrowser' and estado in ESTADOS_CUY:
        contexto['cuy'] = {'estado': estado, 'acciones': list(acciones)[:20]}


def _abrir_carpeta(raiz, partes):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(raiz, flags)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise OSError('El hogar de actividad no es privado.')
        for parte in partes:
            siguiente = os.open(parte, flags, dir_fd=fd)
            os.close(fd)
            fd = siguiente
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise OSError('La carpeta de actividad no es privada.')
        return fd
    except BaseException:
        os.close(fd)
        raise


def _carpeta(runtime):
    ident = runtime.task.get('id')
    home = getattr(runtime.manager, 'home', None)
    directory = getattr(runtime, 'directory', None)
    if not isinstance(ident, str) or not ID_SESION.fullmatch(ident) or home is None or directory is None:
        return None
    partes = ('sesiones', ident, 'native', ident)
    try:
        home, directory = Path(home), Path(directory)
    except TypeError:
        return None
    return (home, partes) if directory == home.joinpath(*partes) else None


def captura(ruta):
    """Sólo la llama el punto que acaba de guardar una captura real de esta acción."""
    contexto = _ACTUAL.get()
    if contexto is None or contexto['accion'] != 'browser_screenshot' or not CAPTURA.fullmatch(ruta.name):
        return
    home, partes = contexto['_carpeta']
    if ruta.parent != home.joinpath(*partes, 'capturas'):
        return
    try:
        directory = _abrir_carpeta(home, (*partes, 'capturas'))
        try:
            fd = os.open(ruta.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                info = os.fstat(fd)
                if (stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
                        and 8 <= info.st_size <= MAX_CAPTURA and os.read(fd, 8) == b'\x89PNG\r\n\x1a\n'):
                    contexto['captura'] = ruta.name
            finally:
                os.close(fd)
        finally:
            os.close(directory)
    except OSError:
        pass


def _leer(fd):
    try:
        source = os.open(ARCHIVO, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return [], False
    with os.fdopen(source, 'rb') as stream:
        info = os.fstat(source)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > MAX_BYTES):
            raise ValueError('Archivo de actividad inválido.')
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Archivo de actividad demasiado grande.')
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get('esquema') != 1 or not isinstance(value.get('eventos'), list):
        raise ValueError('Esquema de actividad inválido.')
    return [evento for item in value['eventos'][-MAX_EVENTOS:] if (evento := validar(item))], \
        value.get('recortado') is True or len(value['eventos']) > MAX_EVENTOS


def _valores_cuy(value):
    if not isinstance(value, dict) or not isinstance(value.get('estado'), str) or value['estado'] not in ESTADOS_CUY:
        return None
    if value['estado'] in ('no_disponible', 'desactivada', 'ambito_cambiado'):
        return {'estado': value['estado'], 'acciones': []}
    acciones = []
    daemon = None
    for item in (value.get('acciones') if isinstance(value.get('acciones'), list) else [])[:20]:
        if (not isinstance(item, dict) or not isinstance(item.get('tool'), str)
                or item['tool'] not in ACCIONES_CUY or type(item.get('ok')) is not bool):
            continue
        ident, hora = item.get('id'), item.get('hora')
        if (not isinstance(ident, str) or not re.fullmatch(rf'{UUID}:[1-9][0-9]{{0,15}}', ident)
                or type(hora) not in (int, float) or not 0 <= hora <= 253402300799 or not math.isfinite(hora)):
            continue
        actual = ident.partition(':')[0]
        if daemon is not None and daemon != actual:
            return {'estado': 'ambito_cambiado', 'acciones': []}
        daemon = actual
        decision = item.get('permission')
        acciones.append({'id': ident, 'tool': item['tool'], 'ok': item['ok'], 'hora': hora,
                         'origen': sitio_seguro(item.get('origen')),
                         'permission': decision if decision in ('approved', 'requested', 'denied') else 'no_consta'})
    return {'estado': value['estado'], 'acciones': acciones}


def validar(row):
    from .navegador import NOMBRES
    if (not isinstance(row, dict) or not isinstance(row.get('accion'), str) or row['accion'] not in NOMBRES
            or row.get('motor') not in ('webkit', 'cuybrowser')):
        return None
    ident, hora, resultado = row.get('id'), row.get('hora'), row.get('resultado')
    if (not isinstance(ident, str) or not re.fullmatch('[0-9a-f]{32}', ident)
            or type(hora) not in (int, float) or not 0 <= hora <= 253402300799 or not math.isfinite(hora)
            or resultado not in ('correcta', 'fallida', 'cancelada')):
        return None
    valid = dict(id=ident, hora=hora, resultado=resultado, motor=row['motor'], accion=row['accion'],
                 origen=sitio_seguro(row.get('origen')), permisos=[])
    for item in (row.get('permisos') if isinstance(row.get('permisos'), list) else [])[:16]:
        if (isinstance(item, dict) and isinstance(item.get('tipo'), str) and item['tipo'] in PERMISOS
                and isinstance(item.get('decision'), str) and item['decision'] in DECISIONES):
            value = dict(tipo=item['tipo'], decision=item['decision'], origen=sitio_seguro(item.get('origen')))
            if isinstance(item.get('alcance'), str) and item['alcance'] in ALCANCES:
                value['alcance'] = item['alcance']
            valid['permisos'].append(value)
    name = row.get('captura')
    if row['accion'] == 'browser_screenshot' and isinstance(name, str) and CAPTURA.fullmatch(name):
        valid['captura'] = name
    if row['motor'] == 'cuybrowser' and (backend := _valores_cuy(row.get('cuy'))):
        valid['cuy'] = backend
    return valid


def _guardar(contexto):
    evento = validar(contexto)
    if evento is None:
        return
    fd = _abrir_carpeta(*contexto['_carpeta'])
    try:
        eventos, recortado = _leer(fd)
        eventos.append(evento)
        recortado = recortado or len(eventos) > MAX_EVENTOS
        eventos = eventos[-MAX_EVENTOS:]
        while True:
            raw = json.dumps(dict(esquema=1, nota=NOTA, eventos=eventos, recortado=recortado),
                             ensure_ascii=False, separators=(',', ':')).encode()
            if len(raw) <= MAX_BYTES:
                break
            eventos, recortado = eventos[1:], True
        temp = f'.actividad-{uuid4().hex}.tmp'
        out = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(out, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, ARCHIVO, src_dir_fd=fd, dst_dir_fd=fd)
        finally:
            try:
                os.unlink(temp, dir_fd=fd)
            except FileNotFoundError:
                pass
    finally:
        os.close(fd)


async def ejecutar(runtime, navegador, nombre, args):
    from .navegador import NOMBRES
    carpeta = _carpeta(runtime)
    motor = getattr(navegador, 'motor_actividad', None)
    if nombre not in NOMBRES or carpeta is None or motor not in ('webkit', 'cuybrowser'):
        return await navegador.llamar(runtime, nombre, args)
    campos = args if isinstance(args, dict) else {}
    abre = nombre == 'browser_navigate' and (campos.get('action') or 'open') == 'open'
    sitio = campos.get('url') if abre or nombre == 'browser_compare' else getattr(navegador, 'url', None)
    contexto = dict(id=uuid4().hex, hora=time.time(), motor=motor, accion=nombre,
                    origen=sitio_seguro(sitio), permisos=[], _carpeta=carpeta, resultado='fallida')
    token = _ACTUAL.set(contexto)
    try:
        resultado = await navegador.llamar(runtime, nombre, args)
        fallo = isinstance(resultado, dict) and (resultado.get('ok') is False or bool(resultado.get('error')))
        if not fallo:
            contexto['resultado'] = 'correcta'
            if isinstance(resultado, dict) and isinstance(resultado.get('url'), str):
                contexto['origen'] = sitio_seguro(resultado['url']) or contexto['origen']
        return resultado
    except asyncio.CancelledError:
        contexto['resultado'] = 'cancelada'
        raise
    finally:
        _ACTUAL.reset(token)
        try:
            _guardar(contexto)
        except (OSError, ValueError, TypeError, RecursionError):
            # Una avería del informe no convierte en fallida ni repite una acción ya realizada.
            pass
