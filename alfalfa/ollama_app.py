"""Cargar un chat de la app de Ollama como sesión de AlfalfaCode.

Como `/import` de Codex, que trae chats recientes de Claude Code: el chat se
copia en una sesión nueva, con los mensajes de usuario y asistente, y el
siguiente mensaje la continúa con ese contexto ya cargado.

La app de Ollama guarda los chats en `~/Library/Application Support/Ollama/db.sqlite`
(tablas `chats` y `messages`). Se lee con cuidado: conexión de solo lectura,
`query_only`, una transacción de lectura que se deshace y validación del esquema
antes de consultar. Nunca se escribe, migra ni hace checkpoint en la base de Ollama. Las imágenes adjuntas,
las herramientas y los mensajes en curso no se traen.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import quote
from uuid import uuid4

ESQUEMA = {
    'chats': {'id', 'title', 'created_at'},
    'messages': {'id', 'chat_id', 'role', 'content', 'model_name', 'created_at', 'updated_at', 'stream'},
}
MAX_MENSAJES = 2000
MAX_TEXTO = 200_000
INTENTOS = 3


class ErrorDeOllama(Exception):
    """Algo impide leer o cargar el chat; el texto se enseña tal cual."""


def base_por_defecto():
    """La base de la app de Ollama; `ALFALFA_OLLAMA_DB` la cambia (pruebas)."""
    if os.environ.get('ALFALFA_OLLAMA_DB'):
        return Path(os.environ['ALFALFA_OLLAMA_DB']).expanduser()
    return Path.home() / 'Library' / 'Application Support' / 'Ollama' / 'db.sqlite'


def _leer(base, operacion):
    """Ejecuta `operacion(conexion)` en una lectura que no puede cambiar nada."""
    base = Path(base or base_por_defecto())
    if not base.is_file():
        raise ErrorDeOllama('No se encontró la base de chats de Ollama. Abre la app de Ollama y guarda '
                            'una conversación.')
    for intento in range(INTENTOS):
        try:
            conexion = sqlite3.connect('file:' + quote(str(base)) + '?mode=ro', uri=True, timeout=0.25,
                                       isolation_level=None)
        except sqlite3.Error:
            raise ErrorDeOllama('No se pudo abrir la base de Ollama; comprueba los permisos de lectura.') from None
        try:
            conexion.execute('PRAGMA query_only = ON')
            conexion.execute('BEGIN')
            try:
                _validar(conexion)
                return operacion(conexion)
            finally:
                conexion.execute('ROLLBACK')
        except sqlite3.OperationalError as exc:
            if 'locked' in str(exc) or 'busy' in str(exc):
                if intento + 1 < INTENTOS:
                    time.sleep(0.15 * (intento + 1))
                    continue
                raise ErrorDeOllama('La base de Ollama sigue ocupada; espera a que termine la respuesta y '
                                    'vuelve a intentarlo.') from None
            raise ErrorDeOllama('La base de Ollama tiene un esquema inesperado; esta versión no puede leerla.') from None
        except sqlite3.DatabaseError:
            raise ErrorDeOllama('La base de Ollama tiene un esquema inesperado; esta versión no puede leerla.') from None
        finally:
            conexion.close()
    raise ErrorDeOllama('La base de Ollama sigue ocupada.')


def _validar(conexion):
    for tabla, columnas in ESQUEMA.items():
        tiene = {fila[1] for fila in conexion.execute('PRAGMA table_info(' + tabla + ')')}
        if not columnas <= tiene:
            raise ErrorDeOllama('La base de Ollama tiene un esquema inesperado; esta versión no puede leerla.')


def _fecha(juliano):
    return (juliano - 2440587.5) * 86400 if isinstance(juliano, (int, float)) else 0.0


def chats(base=None):
    """Lista de chats: id, título, mensajes, modelos y fecha, del más reciente al más antiguo.

    Sólo lee títulos y recuentos; el contenido se lee al cargar uno.
    """
    def consulta(conexion):
        filas = conexion.execute(
            "SELECT c.id, c.title, COUNT(m.id), "
            "COALESCE(MAX(julianday(m.updated_at)), julianday(c.created_at)), "
            "GROUP_CONCAT(DISTINCT NULLIF(m.model_name, '')) "
            "FROM chats c LEFT JOIN messages m ON m.chat_id = c.id GROUP BY c.id ORDER BY 4 DESC, c.id ASC")
        resultado = []
        for ident, titulo, cuenta, fecha, modelos in filas:
            if not cuenta:
                continue  # Un chat vacío no tiene nada que cargar.
            titulo = (titulo or '').strip()
            resultado.append(dict(id=str(ident), titulo=titulo or 'Chat sin título', mensajes=int(cuenta or 0),
                                  modelos=sorted(m for m in (modelos or '').split(',') if m),
                                  fecha=_fecha(fecha)))
        return resultado
    return _leer(base, consulta)


def mensajes(chat_id, base=None):
    """Título, modelo y mensajes de usuario y asistente terminados de un chat."""
    def consulta(conexion):
        fila = conexion.execute('SELECT title FROM chats WHERE id = ?', (chat_id,)).fetchone()
        if fila is None:
            raise ErrorDeOllama('Ese chat ya no está en Ollama; actualiza la lista y elige otro.')
        titulo = (fila[0] or '').strip() or 'Chat sin título'
        lista, modelo = [], ''
        for rol, texto, fecha, nombre in conexion.execute(
                "SELECT role, content, julianday(created_at), model_name FROM messages "
                "WHERE chat_id = ? AND role IN ('user', 'assistant') AND stream = 0 ORDER BY id", (chat_id,)):
            if not isinstance(texto, str) or not texto.strip():
                continue
            lista.append(dict(role=rol, content=texto[:MAX_TEXTO], time=_fecha(fecha)))
            if nombre:
                modelo = nombre
        if not lista:
            raise ErrorDeOllama('Ese chat no tiene mensajes de usuario o asistente terminados que cargar.')
        return titulo, modelo, lista[-MAX_MENSAJES:]
    return _leer(base, consulta)


def importar(chat_id, cwd, home, modelo=None, esfuerzo='medium', base=None):
    """Crea una sesión de AlfalfaCode con la conversación del chat. Devuelve su id.

    Queda completada: como cualquier sesión terminada, se continúa con un mensaje nuevo.
    Cada mensaje del usuario abre un turno, para que el historial y bifurcar funcionen.
    """
    from . import sesiones as registro
    from .proveedores.nativo.herramientas import atomic_json
    titulo, modelo_chat, lista = mensajes(chat_id, base)
    cwd = str(Path(cwd).resolve())
    ident = uuid4().hex[:16]
    sesiones = Path(home) / 'sesiones'
    sesiones.mkdir(mode=0o700, parents=True, exist_ok=True)
    carpeta = sesiones / ident
    carpeta.mkdir(mode=0o700)
    nativo = carpeta / 'native' / ident
    nativo.mkdir(mode=0o700, parents=True)
    modelo = modelo or _modelo_de(modelo_chat)
    # messages[0] es el de sistema: el motor lo sustituye por el suyo en cada turno.
    historial = [{'role': 'system', 'content': ''}]
    turnos, filas, turno = [], [], None
    for mensaje in lista:
        if mensaje['role'] == 'user':
            if turno is not None:
                turno['hasta'] = len(historial)
            turno = dict(id=uuid4().hex[:16], inicio=mensaje['time'], fin=mensaje['time'], desde=len(historial),
                         mensaje=mensaje['content'][:registro.MAX_MENSAJE_TURNO], estado='completed')
            turnos.append(turno)
        elif turno is not None:
            turno['fin'] = mensaje['time']
        historial.append({'role': mensaje['role'], 'content': mensaje['content']})
        fila = dict(role='user' if mensaje['role'] == 'user' else 'agent', text=mensaje['content'],
                    time=mensaje['time'])
        if turno is not None:
            fila['turno'] = turno['id']
        filas.append(fila)
    if turno is not None:
        turno['hasta'] = len(historial)
    ahora = time.time()
    task = dict(id=ident, cwd=cwd, objective='Ollama · ' + titulo, status='completed', provider='ollama',
                runner='native', model=modelo, effort=esfuerzo, turnos=turnos, total_tokens=0,
                summary='Chat cargado desde la app de Ollama.', created_at=ahora, updated_at=ahora,
                importada_de=dict(origen='ollama', chat=str(chat_id), mensajes=len(lista)),
                meta=registro.metadatos(ident, cwd, modelo, esfuerzo))
    atomic_json(nativo / 'session.json',
                {'version': 2, 'messages': historial, 'pending': [], 'plan': '', 'changes': []})
    descriptor = os.open(carpeta / (ident + '.jsonl'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as salida:
        for fila in filas:
            salida.write(json.dumps(fila, ensure_ascii=False) + '\n')
    atomic_json(carpeta / 'task.json', task)
    return ident


def _modelo_de(nombre):
    """El modelo del chat si es de Ollama; si no hay, el de AlfalfaCode por defecto."""
    return nombre or 'deepseek-v4.1-flash:cloud'
