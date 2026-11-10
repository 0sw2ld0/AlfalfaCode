"""Leer de una sesión guardada sólo los mensajes nuevos, sin cargarla entera.

La conversación nativa está en `sesiones/<id>/native/<id>/`:

- Versiones 3 y 4: `messages.jsonl`, un mensaje por línea (`messages[1:]`), que
  sólo crece. `session.json` dice cuántos bytes son válidos (`historial.bytes`):
  lo que haya detrás es un guardado a medias y no se lee. Si el archivo se
  reescribió (bifurcar o un subagente con contexto lo reescriben de forma
  atómica: cambia el inodo) o encogió, se vuelve a leer desde el principio.
- Versiones 1 y 2: todo dentro de `session.json`. Esas sesiones ya no crecen
  (al reanudarlas pasan a la 4), así que se leen enteras una vez.

Una línea que no es JSON se salta: el mensaje cuenta igual para las posiciones,
que son las de `state['messages']` (la 0 es la de sistema y no se guarda).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from . import redaccion

MENSAJES = 'messages.jsonl'
# Lo que se lee de una vez de un messages.jsonl; el resto, en la siguiente pasada.
LOTE = 8 * 1024 * 1024
MAX_TEXTO = 8000
MAX_TEXTO_HERRAMIENTA = 4000
HUELLA = 64
# Llamada que no se pudo leer (una línea más larga que el lote): cualquier salida cuenta como secreta.
COMODIN = '*'
EXTERNAS = frozenset({'read_web', 'web_search', 'wait_agent', 'send_input', 'spawn_agent'})
ID_VALIDO = re.compile(r'^[A-Za-z0-9_-]{1,80}$')
# Mensajes de «usuario» que escribe el arnés (sesiones sin `user_messages`).
PREFIJOS_DEL_ARNES = ('Estado verificado por el runtime', 'Contexto añadido por los hooks',
                      'Captura del navegador que pediste', 'Archivos que cambiaste tú en esta sesión',
                      'Registro histórico resumido')


def _leer_json(ruta):
    ruta = Path(ruta)
    if ruta.is_symlink():
        raise ValueError('enlace simbólico')
    return json.loads(ruta.read_text(encoding='utf-8', errors='replace'))


def _entero(valor):
    return valor if type(valor) is int and valor >= 0 else None


class Sesion:
    """Lo que hace falta saber de una sesión para indexarla."""

    def __init__(self, carpeta):
        self.carpeta = Path(carpeta)
        self.id = self.carpeta.name
        self.task = _leer_json(self.carpeta / 'task.json')
        if not isinstance(self.task, dict):
            raise ValueError('task.json inválido')
        self.nativa = self.carpeta / 'native' / self.id
        self.estado_ruta = self.nativa / 'session.json'

    @property
    def cwd(self):
        cwd = self.task.get('cwd')
        return cwd if isinstance(cwd, str) else ''

    @property
    def titulo(self):
        marcas = {}
        try:
            marcas = _leer_json(self.carpeta / 'marcas.json')
        except (OSError, ValueError):
            pass
        titulo = marcas.get('titulo') if isinstance(marcas, dict) else None
        if not isinstance(titulo, str) or not titulo.strip():
            titulo = self.task.get('objective') if isinstance(self.task.get('objective'), str) else ''
            from ..imagenes import CIERRE, ETIQUETA
            titulo = ETIQUETA.sub('', titulo).replace(CIERRE, '').strip()
        return ' '.join(titulo.split())[:200]

    @property
    def titulo_del_agente(self):
        """El título lo escribió el modelo (una sesión propuesta con propose_session), no el usuario."""
        marcas = {}
        try:
            marcas = _leer_json(self.carpeta / 'marcas.json')
        except (OSError, ValueError):
            pass
        propio = isinstance(marcas, dict) and isinstance(marcas.get('titulo'), str) and marcas['titulo'].strip()
        return bool(self.task.get('padre')) and not propio

    @property
    def fecha(self):
        for clave in ('updated_at', 'created_at'):
            valor = self.task.get(clave)
            if isinstance(valor, (int, float)) and not isinstance(valor, bool):
                return float(valor)
        try:
            return (self.carpeta / 'task.json').stat().st_mtime
        except OSError:
            return 0.0

    def turnos(self):
        """[(desde, hasta|None, id, inicio)] de los turnos con posiciones."""
        filas = []
        for turno in self.task.get('turnos') or []:
            if not isinstance(turno, dict) or not isinstance(turno.get('id'), str):
                continue
            desde = _entero(turno.get('desde'))
            if desde is None:
                continue
            inicio = turno.get('inicio')
            filas.append((desde, _entero(turno.get('hasta')), turno['id'][:40],
                          float(inicio) if isinstance(inicio, (int, float)) and not isinstance(inicio, bool)
                          else None))
        return filas

    def firma(self):
        return firma(self.carpeta)


def firma(carpeta):
    """Cambia si cambió algo que haya que mirar (task.json, session.json, messages.jsonl o el título).

    Sólo mira los metadatos de los archivos: una sesión sin cambios no se abre."""
    carpeta = Path(carpeta)
    nativa = carpeta / 'native' / carpeta.name
    partes = []
    for ruta in (carpeta / 'task.json', nativa / 'session.json', nativa / MENSAJES, carpeta / 'marcas.json'):
        try:
            estado = ruta.stat()
            partes.append(f'{estado.st_ino}:{estado.st_size}:{estado.st_mtime_ns}')
        except OSError:
            partes.append('-')
    return '|'.join(partes)


def turno_de(turnos, posicion):
    for desde, hasta, ident, inicio in reversed(turnos):
        if desde <= posicion and (hasta is None or posicion < hasta):
            return ident, inicio
    return None, None


def huella(ruta, hasta):
    """Huella de los últimos bytes leídos: si cambian, el archivo no es el mismo."""
    if hasta <= 0:
        return ''
    try:
        with open(ruta, 'rb') as entrada:
            entrada.seek(max(0, hasta - HUELLA))
            return hashlib.sha256(entrada.read(min(HUELLA, hasta))).hexdigest()[:32]
    except OSError:
        return None


class Lectura:
    """Lo nuevo de una sesión: mensajes por indexar y dónde se queda la lectura."""

    def __init__(self):
        self.mensajes = []      # dicts listos para el índice
        self.reiniciar = False  # borrar lo indexado de la sesión antes de añadir
        self.bytes = 0
        self.posicion = 1
        self.inodo = None
        self.huella = ''
        self.version = None
        self.llamadas = []      # llamadas sin resultado aún: [nombre, secreta]
        self.pendiente = False  # queda más por leer (LOTE)
        # En esta versión de la sesión llegó una salida externa: el agente puede repetirla en otro turno.
        self.contaminado = False


def leer_nuevos(sesion, previo, secretos=()):
    """Lo que hay que añadir al índice desde `previo` (la fila guardada de la sesión, o None)."""
    lectura = Lectura()
    try:
        estado = _leer_json(sesion.estado_ruta)
    except (OSError, ValueError):
        # Sin conversación nativa (sesión recién creada o de otro motor): nada que indexar aún.
        lectura.reiniciar = previo is not None and previo['bytes'] > 0
        return lectura
    if not isinstance(estado, dict):
        return lectura
    version = estado.get('version')
    lectura.version = version if type(version) is int else None
    usuarios = estado.get('user_messages')
    usuarios = {i for i in usuarios if type(i) is int} if isinstance(usuarios, list) else None
    turnos = sesion.turnos()
    if lectura.version in (1, 2):
        mensajes = estado.get('messages') if isinstance(estado.get('messages'), list) else []
        lectura.reiniciar = True
        lectura.bytes = len(mensajes)
        lectura.posicion = len(mensajes)
        _convertir(lectura, list(enumerate(mensajes))[1:], usuarios, turnos, sesion, secretos)
        return lectura
    historial = estado.get('historial') if isinstance(estado.get('historial'), dict) else {}
    ruta = sesion.nativa / MENSAJES
    try:
        if ruta.is_symlink():
            return lectura
        fd = os.open(ruta, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        lectura.reiniciar = previo is not None and previo['bytes'] > 0
        return lectura
    with os.fdopen(fd, 'rb') as entrada:
        datos_archivo = os.fstat(entrada.fileno())
        tamano = datos_archivo.st_size
        validos = _entero(historial.get('bytes'))
        validos = tamano if validos is None else min(validos, tamano)
        lectura.inodo = datos_archivo.st_ino
        desde, posicion, llamadas = 0, 1, []
        if (previo is not None and previo['inodo'] == lectura.inodo and previo['version'] in (3, 4, None)
                and previo['bytes'] <= validos and previo['bytes'] > 0
                and huella(ruta, previo['bytes']) == previo['huella']):
            desde, posicion = previo['bytes'], previo['posicion']
            lectura.contaminado = bool(previo['contaminado'])
            try:
                llamadas = json.loads(previo['llamadas'] or '[]')
            except ValueError:
                llamadas = []
        else:
            lectura.reiniciar = previo is not None
        entrada.seek(desde)
        datos = entrada.read(min(validos - desde, LOTE))
        # Sólo líneas enteras: una a medias se lee en la siguiente pasada.
        corte = datos.rfind(b'\n') + 1
        if corte == 0 and len(datos) == LOTE:
            # Una sola línea más larga que el lote: no se indexa, pero se salta (cuenta su posición).
            salto = desde + len(datos)
            while salto < validos:
                trozo = entrada.read(min(validos - salto, LOTE))
                if not trozo:
                    break
                fin = trozo.find(b'\n')
                if fin >= 0:
                    salto += fin + 1
                    lectura.bytes, lectura.posicion = salto, posicion + 1
                    lectura.huella = huella(ruta, salto) or ''
                    lectura.llamadas, externa_saltada = _sin_la_saltada(llamadas, datos[:4096])
                    lectura.contaminado = lectura.contaminado or externa_saltada
                    lectura.pendiente = salto < validos
                    return lectura
                salto += len(trozo)
            validos = desde  # sin fin de línea: se espera a que la sesión guarde más
    lectura.pendiente = desde + corte < validos
    datos = datos[:corte]
    filas = []
    for linea in datos.splitlines():
        try:
            mensaje = json.loads(linea)
        except ValueError:
            mensaje = None
        filas.append((posicion, mensaje))
        posicion += 1
    lectura.bytes = desde + corte
    lectura.posicion = posicion
    lectura.huella = huella(ruta, lectura.bytes) or ''
    lectura.llamadas = llamadas
    _convertir(lectura, filas, usuarios, turnos, sesion, secretos)
    return lectura


def _sin_la_saltada(llamadas, principio):
    """Las llamadas pendientes tras saltar una línea demasiado larga, mirando sólo su principio.

    Si era la salida de una herramienta, se gasta su llamada; si era un mensaje del agente con
    llamadas que no se pueden leer, las salidas que vengan detrás se tratan como secretas hasta
    el siguiente mensaje del agente (`COMODIN`). Devuelve también si era una salida externa."""
    texto = principio.decode('utf-8', errors='replace')
    rol = re.search(r'"role"\s*:\s*"([a-z]+)"', texto)
    rol = rol.group(1) if rol else None
    llamadas = [list(llamada) for llamada in llamadas]
    if rol == 'assistant':
        return [[COMODIN, True]], False
    if rol == 'tool':
        nombre = re.search(r'"tool_name"\s*:\s*"([^"]{1,100})"', texto)
        nombre = nombre.group(1) if nombre else None
        for i, (pendiente, _) in enumerate(llamadas):
            if pendiente == COMODIN:
                break
            if nombre is None or pendiente == nombre:
                del llamadas[i]
                break
        return llamadas, nombre is None or externa(nombre)
    return llamadas, False


def externa(herramienta):
    """¿Su salida viene de fuera (la web, una página, un servidor MCP)? Puede traer instrucciones."""
    return isinstance(herramienta, str) and (herramienta in EXTERNAS or herramienta.startswith(('browser_', 'mcp__')))


def _recortar(texto, limite):
    texto = texto.strip()
    return texto if len(texto) <= limite else texto[:limite - 1].rstrip() + '…'


def _convertir(lectura, filas, usuarios, turnos, sesion, secretos):
    from ..imagenes import CIERRE, ETIQUETA
    fecha_sesion = sesion.fecha
    for posicion, mensaje in filas:
        if not isinstance(mensaje, dict):
            continue
        rol = mensaje.get('role')
        herramienta = None
        texto = ''
        if rol == 'user':
            texto = redaccion.texto_visible(mensaje.get('content'))
            if usuarios is not None:
                if posicion not in usuarios:
                    continue  # lo escribió el arnés, no el usuario
            elif texto.startswith(PREFIJOS_DEL_ARNES):
                continue
            texto = ETIQUETA.sub('', texto).replace(CIERRE, '')
            texto = redaccion.tapar(texto, secretos)
            texto = _recortar(texto, MAX_TEXTO)
        elif rol == 'assistant':
            texto = _recortar(redaccion.tapar(redaccion.texto_visible(mensaje.get('content')), secretos), MAX_TEXTO)
            llamadas = mensaje.get('tool_calls') if isinstance(mensaje.get('tool_calls'), list) else []
            lectura.llamadas = []
            lineas = []
            for llamada in llamadas[:20]:
                funcion = llamada.get('function') if isinstance(llamada, dict) else None
                if not isinstance(funcion, dict) or not isinstance(funcion.get('name'), str):
                    continue
                nombre = funcion['name'][:100]
                argumentos = funcion.get('arguments')
                lectura.llamadas.append([nombre, redaccion.llamada_secreta(nombre, argumentos)])
                visibles = redaccion.tapar(redaccion.argumentos_visibles(argumentos), secretos)
                lineas.append('→ ' + nombre + ': ' + visibles)
            if lineas:
                texto = (texto + '\n' if texto else '') + '\n'.join(lineas)
        elif rol == 'tool':
            herramienta = mensaje.get('tool_name') if isinstance(mensaje.get('tool_name'), str) else None
            herramienta = herramienta[:100] if herramienta else None
            secreta = False
            for i, (nombre, es_secreta) in enumerate(lectura.llamadas):
                if nombre == COMODIN:
                    secreta = True  # no se consume: vale hasta el siguiente mensaje del agente
                    break
                if herramienta is None or nombre == herramienta:
                    secreta = bool(es_secreta)
                    del lectura.llamadas[i]
                    break
            if externa(herramienta):
                lectura.contaminado = True
            if secreta:
                texto = redaccion.OMITIDO
            else:
                texto = _recortar(redaccion.tapar(redaccion.texto_de_salida(mensaje.get('content')), secretos),
                                  MAX_TEXTO_HERRAMIENTA)
        else:
            continue  # sistema y lo desconocido
        if not texto.strip():
            continue
        turno, inicio = turno_de(turnos, posicion)
        lectura.mensajes.append(dict(posicion=posicion, rol=rol, herramienta=herramienta, turno=turno,
                                     fecha=inicio if inicio is not None else fecha_sesion, texto=texto,
                                     externo=externa(herramienta) if rol == 'tool'
                                     else rol == 'assistant' and lectura.contaminado))
