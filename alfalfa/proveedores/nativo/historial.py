"""Cómo se guarda en disco la sesión nativa: lo que crece, sólo añadiendo al final.

Como Claude Code y Codex, cada mensaje se escribe una sola vez, al final de un
JSONL que nunca se reescribe. `session.json` (versión 3) queda pequeño: plan,
cambios, uso, punto de control, el registro de evidencia y dónde acaba cada
archivo que sólo crece.

- `messages.jsonl`: la conversación, `messages[1:]`, un mensaje por línea y sin
  `thinking` ni `alfalfa_bloques` (el razonamiento nunca se escribe a disco). `messages[0]` es el de
  sistema, que el motor sustituye en cada turno: va en session.json como
  `primer_mensaje`, así cambiarlo no obliga a tocar el historial.
- `fotos.jsonl`: las fotos del espacio de trabajo (`workspace_context`) de cada
  operación y comprobación. Cada una ocupaba lo que el árbol entero (264 KB en
  un repo de 400 archivos) y se repetía en `operations` y en `checks`; aquí se
  guarda una vez y como diferencia con la anterior, que casi siempre cambia en
  uno o dos archivos. En session.json el registro lleva `{"$foto": n}`.

session.json guarda, por cada archivo, cuántas entradas y cuántos bytes son
válidos (`historial`). Se escribe siempre después del JSONL: si algo se corta
entre los dos, lo que sobra al final del JSONL se ignora y se recorta al
guardar otra vez; session.json nunca apunta a datos que no estén escritos.

En memoria todo sigue igual que antes (`state['messages']` es una lista con todos
los mensajes y cada registro lleva su foto entera), así que quien lee el estado
no cambia. `Mensajes` recuerda si alguien cambió o quitó un mensaje ya guardado
en vez de añadir (bifurcar o un subagente con contexto lo hacen al empezar): en
ese caso, y sólo entonces, `messages.jsonl` se reescribe entero y de forma
atómica. Cambiar un mensaje guardado por dentro (`m['content'] = …`) no se
detecta: el bucle nunca lo hace; para eso, sustituye el mensaje en la lista.

Las versiones 1 y 2 llevaban todo dentro de session.json: se convierten la
primera vez que se cargan, escribiendo antes los JSONL y luego session.json.

Para leer una sesión desde fuera (bifurcar, un integrador) usa `leer` y para
crearla `escribir`: devuelven y aceptan el estado completo, como en la versión 2.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .herramientas import atomic_json, atomic_write

VERSION = 3
MENSAJES = 'messages.jsonl'
FOTOS = 'fotos.jsonl'
# Listas del estado cuyos registros llevan foto del espacio de trabajo.
CON_FOTO = ('operations', 'checks')
REFERENCIA = '$foto'


class HistorialIncompleto(ValueError):
    """El JSONL tiene menos de lo que session.json dice que se guardó."""


class Mensajes(list):
    """Lista de mensajes que recuerda la primera posición cambiada sin añadir.

    `cambiado` es None si desde el último guardado sólo se añadió al final.
    Sustituir `messages[0]` no cuenta: se guarda aparte, en session.json.
    """

    def __init__(self, *args):
        super().__init__(*args)
        self.cambiado = None

    def _cambia(self, desde):
        desde = max(0, desde)
        if self.cambiado is None or desde < self.cambiado:
            self.cambiado = desde

    def _posicion(self, i):
        return i + len(self) if i < 0 else i

    def __setitem__(self, i, valor):
        if isinstance(i, slice):
            self._cambia(min(range(*i.indices(len(self))), default=i.indices(len(self))[0]))
        elif self._posicion(i) != 0:
            self._cambia(self._posicion(i))
        super().__setitem__(i, valor)

    def __delitem__(self, i):
        if isinstance(i, slice):
            self._cambia(min(range(*i.indices(len(self))), default=len(self)))
        else:
            self._cambia(self._posicion(i))
        super().__delitem__(i)

    def pop(self, i=-1):
        self._cambia(self._posicion(i) if self else 0)
        return super().pop(i)

    def insert(self, i, valor):
        self._cambia(min(self._posicion(i), len(self)))
        super().insert(i, valor)

    def remove(self, valor):
        self._cambia(self.index(valor))
        super().remove(valor)

    def clear(self):
        self._cambia(0)
        super().clear()

    def sort(self, *args, **kwargs):
        self._cambia(0)
        super().sort(*args, **kwargs)

    def reverse(self):
        self._cambia(0)
        super().reverse()

    def __imul__(self, n):
        self._cambia(0)
        return super().__imul__(n)


# Lo que sólo vive en memoria para devolvérselo al modelo: el razonamiento y los
# bloques firmados de Claude (`alfalfa_bloques`).
SOLO_EN_MEMORIA = ('thinking', 'alfalfa_bloques')


def _sin_razonamiento(mensaje):
    return ({k: v for k, v in mensaje.items() if k not in SOLO_EN_MEMORIA}
            if isinstance(mensaje, dict) else mensaje)


def _linea(valor):
    return (json.dumps(valor, ensure_ascii=False) + '\n').encode()


def diferencia(antes, despues):
    """Parche que convierte el dict `antes` en `despues`, recorriendo los dicts anidados."""
    parche = {}
    for clave, valor in despues.items():
        if clave not in antes:
            parche[clave] = {'=': valor}
        elif antes[clave] == valor:
            continue
        elif isinstance(valor, dict) and isinstance(antes[clave], dict):
            parche[clave] = {'~': diferencia(antes[clave], valor)}
        else:
            parche[clave] = {'=': valor}
    for clave in antes:
        if clave not in despues:
            parche[clave] = {'-': 1}
    return parche


def aplicar(base, parche):
    """Lo contrario de `diferencia`; lo que no cambia se comparte con `base`."""
    resultado = dict(base)
    for clave, cambio in parche.items():
        if '-' in cambio:
            resultado.pop(clave, None)
        elif '~' in cambio:
            resultado[clave] = aplicar(base.get(clave) if isinstance(base.get(clave), dict) else {},
                                       cambio['~'])
        else:
            resultado[clave] = cambio['=']
    return resultado


class _Diario:
    """Un JSONL al que sólo se añade; `bytes` es lo válido según session.json."""

    def __init__(self, ruta):
        self.ruta = Path(ruta)
        self.bytes = 0

    def leer(self, bytes_validos):
        """Las entradas de los primeros `bytes_validos` bytes."""
        if bytes_validos == 0:
            self.bytes = 0
            return []
        try:
            fd = os.open(self.ruta, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            raise HistorialIncompleto('Falta ' + self.ruta.name + ' de la sesión.') from None
        with os.fdopen(fd, 'rb') as entrada:
            datos = entrada.read(bytes_validos)
        if len(datos) != bytes_validos or (datos and not datos.endswith(b'\n')):
            raise HistorialIncompleto(self.ruta.name + ' de la sesión está incompleto.')
        self.bytes = bytes_validos
        return [json.loads(linea) for linea in datos.splitlines()]

    def añadir(self, lineas):
        if not lineas:
            return
        datos = b''.join(lineas)
        fd = os.open(self.ruta, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            # Lo que quedó tras el último guardado válido (un corte a medias) sobra.
            if os.fstat(fd).st_size != self.bytes:
                os.ftruncate(fd, self.bytes)
            os.lseek(fd, self.bytes, os.SEEK_SET)
            vista = memoryview(datos)
            while vista:
                vista = vista[os.write(fd, vista):]
            os.fsync(fd)
        finally:
            os.close(fd)
        self.bytes += len(datos)

    def reescribir(self, lineas):
        datos = b''.join(lineas)
        atomic_write(self.ruta, datos, 0o600)
        self.bytes = len(datos)


class Historial:
    """Lleva la cuenta de lo ya escrito de una sesión nativa (su carpeta `native/<id>`)."""

    def __init__(self, directorio):
        self.directorio = Path(directorio)
        self.mensajes = _Diario(self.directorio / MENSAJES)
        self.fotos = _Diario(self.directorio / FOTOS)
        self._lista = None
        self._guardados = 0
        # id(foto en memoria) -> (foto, número): la misma foto la comparten la
        # operación y su comprobación, y se escribe una sola vez.
        self._numeros = {}
        self._fotos = 0
        self._ultima_foto = None

    # -- Cargar -------------------------------------------------------------

    def empezar(self, mensajes):
        """Sesión nueva: nada escrito aún; el primer guardado sólo añade."""
        self._lista, self._guardados = mensajes, 0

    def cargar(self, guardado):
        """El estado completo a partir de lo leído de session.json. `migrar` dice si es de antes de la 3."""
        if not isinstance(guardado, dict) or guardado.get('version') not in (1, 2, VERSION):
            raise ValueError('Versión de sesión nativa desconocida.')
        estado = dict(guardado)
        if estado['version'] != VERSION:
            mensajes = estado.get('messages')
            estado['messages'] = Mensajes(mensajes if isinstance(mensajes, list) else [])
            self._lista = None  # nada escrito aún en el formato nuevo
            return estado, True
        indice = estado.pop('historial', None)
        indice = indice if isinstance(indice, dict) else {}
        primero = estado.pop('primer_mensaje', None)
        resto = self.mensajes.leer(_entero(indice.get('bytes')))
        if len(resto) + (primero is not None) != _entero(indice.get('mensajes')):
            raise HistorialIncompleto(MENSAJES + ' no coincide con session.json.')
        mensajes = Mensajes(([primero] if primero is not None else []) + resto)
        estado['messages'] = mensajes
        self._lista, self._guardados = mensajes, len(mensajes)
        fotos = self.fotos.leer(_entero(indice.get('fotos_bytes')))
        reconstruidas, anterior = {}, None
        for fila in fotos:
            base = reconstruidas.get(fila.get('base')) if fila.get('base') is not None else {}
            if base is None:
                raise HistorialIncompleto(FOTOS + ' no tiene la foto ' + str(fila.get('base')) + '.')
            anterior = aplicar(base, fila.get('cambios') or {})
            reconstruidas[fila['n']] = anterior
            self._numeros[id(anterior)] = (anterior, fila['n'])
        self._fotos, self._ultima_foto = len(fotos), anterior
        for campo in CON_FOTO:
            filas = estado.get(campo)
            if not isinstance(filas, list):
                continue
            for i, fila in enumerate(filas):
                foto = fila.get('workspace_context') if isinstance(fila, dict) else None
                if isinstance(foto, dict) and set(foto) == {REFERENCIA}:
                    if foto[REFERENCIA] not in reconstruidas:
                        raise HistorialIncompleto(FOTOS + ' no tiene la foto ' + str(foto[REFERENCIA]) + '.')
                    filas[i] = dict(fila, workspace_context=reconstruidas[foto[REFERENCIA]])
        return estado, False

    # -- Guardar ------------------------------------------------------------

    def guardar(self, estado):
        """Escribe lo nuevo en los JSONL y devuelve lo que va en session.json."""
        mensajes = estado.get('messages')
        if not isinstance(mensajes, Mensajes):
            # Alguien puso una lista nueva (un subagente con el contexto del principal):
            # se adopta, y como no se sabe qué tiene, se escribe entera.
            mensajes = Mensajes(mensajes if isinstance(mensajes, list) else [])
            estado['messages'] = mensajes
        if (mensajes is not self._lista or len(mensajes) < self._guardados
                or (mensajes.cambiado is not None and mensajes.cambiado < self._guardados)):
            self._reescribir(mensajes)
        else:
            self.mensajes.añadir([_linea(_sin_razonamiento(m)) for m in mensajes[max(self._guardados, 1):]])
        mensajes.cambiado = None
        self._lista, self._guardados = mensajes, len(mensajes)
        salida = {k: v for k, v in estado.items() if k != 'messages'}
        nuevas = {}
        for campo in CON_FOTO:
            filas = estado.get(campo)
            if isinstance(filas, list):
                salida[campo] = [self._con_referencia(fila, nuevas) for fila in filas]
        self.fotos.añadir([linea for _, _, linea in nuevas.values()])
        # Sólo ahora, ya escritas, cuentan como guardadas.
        for foto, numero, _ in nuevas.values():
            self._numeros[id(foto)] = (foto, numero)
            self._fotos, self._ultima_foto = numero + 1, foto
        salida['version'] = VERSION
        if mensajes:
            salida['primer_mensaje'] = _sin_razonamiento(mensajes[0])
        salida['historial'] = {'archivo': MENSAJES, 'mensajes': len(mensajes), 'bytes': self.mensajes.bytes,
                               'fotos_archivo': FOTOS, 'fotos': self._fotos, 'fotos_bytes': self.fotos.bytes}
        return salida

    def _reescribir(self, mensajes):
        self.mensajes.reescribir([_linea(_sin_razonamiento(m)) for m in mensajes[1:]])

    def _con_referencia(self, fila, nuevas):
        foto = fila.get('workspace_context') if isinstance(fila, dict) else None
        if not isinstance(foto, dict):
            return fila
        conocida = self._numeros.get(id(foto))
        if conocida is not None and conocida[0] is foto:
            return dict(fila, workspace_context={REFERENCIA: conocida[1]})
        if id(foto) not in nuevas:
            # Como diferencia con la anterior: entre dos comprobaciones casi siempre
            # cambian uno o dos archivos del árbol.
            numero = self._fotos + len(nuevas)
            anterior = list(nuevas.values())[-1][0] if nuevas else self._ultima_foto
            nuevas[id(foto)] = (foto, numero, _linea({
                'n': numero, 'base': numero - 1 if anterior is not None else None,
                'cambios': diferencia(anterior if anterior is not None else {}, foto)}))
        return dict(fila, workspace_context={REFERENCIA: nuevas[id(foto)][1]})


def _entero(valor):
    return valor if type(valor) is int and valor >= 0 else 0


def leer(ruta):
    """El estado completo (con `messages`) de un session.json de cualquier versión."""
    ruta = Path(ruta)
    estado, _ = Historial(ruta.parent).cargar(json.loads(ruta.read_text()))
    estado['messages'] = list(estado['messages'])
    return estado


def escribir(directorio, estado):
    """Crea la sesión nativa de `directorio` con el estado completo (`messages` incluido)."""
    directorio = Path(directorio)
    estado = dict(estado)
    for nombre in (MENSAJES, FOTOS):
        # Una carpeta nueva no debería tenerlos; si los tiene, no son de esta sesión.
        (directorio / nombre).unlink(missing_ok=True)
    atomic_json(directorio / 'session.json', Historial(directorio).guardar(estado))
