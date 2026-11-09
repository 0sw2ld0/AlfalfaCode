"""Cómo se guarda en disco la sesión nativa: lo que crece, sólo añadiendo al final.

Como Claude Code y Codex, cada mensaje se escribe una sola vez, al final de un
JSONL que nunca se reescribe. `session.json` (versión 4) queda pequeño: plan,
cambios, uso, punto de control, lo que no crece del registro de evidencia y
dónde acaba cada archivo que sólo crece.

- `messages.jsonl`: la conversación, `messages[1:]`, un mensaje por línea y sin
  `thinking` ni `alfalfa_bloques` (el razonamiento nunca se escribe a disco). `messages[0]` es el de
  sistema, que el motor sustituye en cada turno: va en session.json como
  `primer_mensaje`, así cambiarlo no obliga a tocar el historial.
- `fotos.jsonl`: las fotos del espacio de trabajo (`workspace_context`) de cada
  operación y comprobación. Cada una ocupaba lo que el árbol entero (264 KB en
  un repo de 400 archivos) y se repetía en `operations` y en `checks`; aquí se
  guarda una vez y como diferencia con la anterior, que casi siempre cambia en
  uno o dos archivos. En el registro, la foto va como `{"$foto": n}`.
- `operations.jsonl` y `checks.jsonl`: cada operación y comprobación, con su
  salida (hasta 4.000 caracteres); `resolution_records.jsonl`: cada intento de
  resolver una incidencia (uno por comprobación relacionada con una abierta).
  Crecían con cada paso dentro de session.json, que se reescribe varias veces
  por paso. Si alguien sustituye o quita un registro ya guardado (`bajo_demanda`
  anota el intento en el sandbox), se añade `{"$hasta": n}` (la lista vuelve a
  tener n registros) y lo que sigue desde ahí.
- `unresolved_records.jsonl`: las incidencias. Cambian por dentro (su estado se
  recalcula), así que cada línea es la incidencia nueva (`=`), lo que cambió
  respecto de lo último escrito (`~`, como las fotos) o que se quitó (`-`).

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
Con las listas de evidencia pasa lo mismo (son `Lista`). Las incidencias sí
pueden cambiarse por dentro: se comparan con una copia de lo último escrito.

Las versiones 1 a 3 llevaban dentro de session.json lo que ahora va aparte: se
convierten la primera vez que se cargan, escribiendo antes los JSONL y luego
session.json.

Para leer una sesión desde fuera (bifurcar, un integrador) usa `leer` y para
crearla `escribir`: devuelven y aceptan el estado completo, como en la versión 2.
Para juzgar su evidencia sin cargar la conversación (Chiyo), `leer_evidencia`.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from .herramientas import atomic_json, atomic_write

VERSION = 4
MENSAJES = 'messages.jsonl'
FOTOS = 'fotos.jsonl'
# Listas de evidencia que crecen con la sesión, cada una en su JSONL.
LISTAS = {'operations': 'operations.jsonl', 'checks': 'checks.jsonl',
          'resolution_records': 'resolution_records.jsonl'}
# Las que llevan foto del espacio de trabajo.
CON_FOTO = ('operations', 'checks')
INCIDENCIAS = 'unresolved_records'
ARCHIVO_INCIDENCIAS = 'unresolved_records.jsonl'
REFERENCIA = '$foto'
HASTA = '$hasta'
_FALTA = object()


class HistorialIncompleto(ValueError):
    """El JSONL tiene menos de lo que session.json dice que se guardó."""


class Lista(list):
    """Lista que recuerda la primera posición cambiada sin añadir.

    `cambiado` es None si desde el último guardado sólo se añadió al final.
    """

    # Mensajes: `messages[0]` se guarda aparte, en session.json.
    _PRIMERO_APARTE = False

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
        elif self._posicion(i) != 0 or not self._PRIMERO_APARTE:
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


class Mensajes(Lista):
    """La conversación. Sustituir `messages[0]` no cuenta: se guarda aparte, en session.json."""

    _PRIMERO_APARTE = True


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

    def leer_indice(self, indice, nombre, cuenta):
        """Las entradas que `indice` (de session.json) dice válidas; `cuenta`, cuántas son."""
        lineas = self.leer(_entero(indice.get('bytes')))
        if len(lineas) != _entero(indice.get(cuenta)):
            raise HistorialIncompleto(self.ruta.name + ' no coincide con session.json (' + nombre + ').')
        return lineas


class _Registro:
    """Una lista de evidencia (`operations`, `checks`, `resolution_records`) en su JSONL, sólo añadiendo.

    Lo normal es añadir al final. Si se sustituyó o quitó un registro ya
    guardado, se añade `{"$hasta": n}` y los registros desde n: el archivo nunca
    se reescribe, así que un corte entre el JSONL y session.json no lo deja
    ilegible, y la sustitución casi siempre toca uno de los últimos.

    Cambiar por dentro un registro ya guardado (`record.update(...)`) no lo ve la
    `Lista`. Como es en los últimos donde se anota lo que se supo después, se
    comparan los `COLA` últimos con lo escrito y, si cambió alguno, se escribe
    desde él. Uno más antiguo cambiado así se perdería al reanudar: sustitúyelo.
    """

    COLA = 8

    def __init__(self, directorio, campo):
        self.campo = campo
        self.diario = _Diario(directorio / LISTAS[campo])
        self.lista = None  # la Lista del estado que está escrita
        self.largo = 0     # registros en disco, aplicados los `$hasta`
        self.entradas = 0  # líneas válidas
        self.cola = []     # líneas de los últimos registros escritos, de largo - len(cola) a largo

    def cargar(self, indice, resolver):
        lineas = self.diario.leer_indice(indice, self.campo, 'entradas')
        filas, escritas = Lista(), []
        for linea in lineas:
            if isinstance(linea, dict) and set(linea) == {HASTA}:
                hasta = linea[HASTA]
                if type(hasta) is not int or not 0 <= hasta <= len(filas):
                    raise HistorialIncompleto(self.diario.ruta.name + ' tiene un «$hasta» inválido.')
                del filas[hasta:]
                del escritas[hasta:]
            else:
                filas.append(resolver(linea))
                escritas.append(linea)
        if len(filas) != _entero(indice.get('filas')):
            raise HistorialIncompleto(self.diario.ruta.name + ' no coincide con session.json.')
        filas.cambiado = None
        self.lista, self.largo, self.entradas = filas, len(filas), len(lineas)
        self.cola = [_linea(linea) for linea in escritas[-self.COLA:]]
        return filas

    def pendiente(self, estado, con_referencia):
        """(lista, líneas por añadir, cola), o None si el campo no es una lista y va en session.json."""
        filas = estado.get(self.campo)
        if not isinstance(filas, list):
            return None
        if self.campo not in CON_FOTO:
            con_referencia = _igual
        if not isinstance(filas, Lista):
            # Una lista nueva (`setdefault('checks', [])`, una sesión vieja, la que deja
            # `_archive_not_relevant`): se adopta.
            filas = estado[self.campo] = Lista(filas)
        inicio = self.largo - len(self.cola)
        if filas is not self.lista:
            desde = 0
        else:
            desde = self.largo if filas.cambiado is None else min(filas.cambiado, self.largo)
            for i in range(inicio, desde):
                if _linea(con_referencia(filas[i])) != self.cola[i - inicio]:
                    desde = i  # cambiado por dentro
                    break
        nuevas = [_linea(con_referencia(fila)) for fila in filas[desde:]]
        lineas = ([_linea({HASTA: desde})] if desde < self.largo else []) + nuevas
        cola = []
        for i in range(max(0, len(filas) - self.COLA), len(filas)):
            if i >= desde:
                cola.append(nuevas[i - desde])
            elif filas is self.lista and inicio <= i:
                cola.append(self.cola[i - inicio])
            else:
                cola.append(_linea(con_referencia(filas[i])))
        return filas, lineas, cola

    def escrito(self, filas, lineas, cola):
        self.diario.añadir(lineas)
        filas.cambiado = None
        self.lista, self.largo, self.cola = filas, len(filas), cola
        self.entradas += len(lineas)

    def indice(self):
        return {'archivo': self.diario.ruta.name, 'entradas': self.entradas, 'filas': self.largo,
                'bytes': self.diario.bytes}


class _Incidencias:
    """`unresolved_records` (id → incidencia) en su JSONL, sólo añadiendo.

    `_refresh_incidents` recalcula el estado de cada incidencia por dentro, así
    que se compara con una copia de lo último escrito y sólo se añade lo que
    cambió: una incidencia pasa por pocos estados (pendiente, obsoleta, resuelta).
    """

    def __init__(self, directorio):
        self.diario = _Diario(directorio / ARCHIVO_INCIDENCIAS)
        self.escritas = {}  # id -> copia de lo escrito
        self.entradas = 0

    def cargar(self, indice):
        lineas = self.diario.leer_indice(indice, INCIDENCIAS, 'entradas')
        registros = {}
        for linea in lineas:
            if not isinstance(linea, dict) or 'id' not in linea:
                raise HistorialIncompleto(ARCHIVO_INCIDENCIAS + ' tiene una línea inválida.')
            clave = linea['id']
            if '-' in linea:
                registros.pop(clave, None)
            elif '~' in linea:
                if not isinstance(registros.get(clave), dict):
                    raise HistorialIncompleto(ARCHIVO_INCIDENCIAS + ' cambia una incidencia que no tiene.')
                registros[clave] = aplicar(registros[clave], linea['~'])
            else:
                registros[clave] = linea.get('=')
        if len(registros) != _entero(indice.get('registros')):
            raise HistorialIncompleto(ARCHIVO_INCIDENCIAS + ' no coincide con session.json.')
        self.escritas = copy.deepcopy(registros)
        self.entradas = len(lineas)
        return registros

    def pendiente(self, estado):
        """(líneas por añadir, copias de lo que escriben), o None si no es un dict y va en session.json."""
        registros = estado.get(INCIDENCIAS)
        if not isinstance(registros, dict):
            return None
        lineas, copias = [], {}
        for clave, valor in registros.items():
            anterior = self.escritas.get(clave, _FALTA)
            if anterior is not _FALTA and anterior == valor:
                continue
            if isinstance(anterior, dict) and isinstance(valor, dict):
                lineas.append(_linea({'id': clave, '~': diferencia(anterior, valor)}))
            else:
                lineas.append(_linea({'id': clave, '=': valor}))
            copias[clave] = copy.deepcopy(valor)
        for clave in self.escritas:
            if clave not in registros:
                lineas.append(_linea({'id': clave, '-': 1}))
                copias[clave] = _FALTA
        return lineas, copias

    def escrito(self, lineas, copias):
        self.diario.añadir(lineas)
        for clave, valor in copias.items():
            if valor is _FALTA:
                self.escritas.pop(clave, None)
            else:
                self.escritas[clave] = valor
        self.entradas += len(lineas)

    def indice(self):
        return {'archivo': ARCHIVO_INCIDENCIAS, 'entradas': self.entradas, 'registros': len(self.escritas),
                'bytes': self.diario.bytes}


class Historial:
    """Lleva la cuenta de lo ya escrito de una sesión nativa (su carpeta `native/<id>`)."""

    def __init__(self, directorio):
        self.directorio = Path(directorio)
        self.mensajes = _Diario(self.directorio / MENSAJES)
        self.fotos = _Diario(self.directorio / FOTOS)
        self.listas = {campo: _Registro(self.directorio, campo) for campo in LISTAS}
        self.incidencias = _Incidencias(self.directorio)
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

    def cargar(self, guardado, *, mensajes=True, fotos=True):
        """El estado completo a partir de lo leído de session.json. `migrar` dice si es de antes de la 4.

        Sin `mensajes`, el estado no lleva `messages`; sin `fotos`, cada registro
        lleva su foto como `{"$foto": n}`. Así no se guarda, sólo se lee.
        """
        if not isinstance(guardado, dict) or guardado.get('version') not in (1, 2, 3, VERSION):
            raise ValueError('Versión de sesión nativa desconocida.')
        estado = dict(guardado)
        version = estado['version']
        if version < 3:
            # Todo dentro de session.json; nada escrito aún en el formato nuevo.
            lista = estado.pop('messages', None)
            if mensajes:
                estado['messages'] = Mensajes(lista if isinstance(lista, list) else [])
            self._lista = None
            indice, reconstruidas = {}, None
        else:
            indice = estado.pop('historial', None)
            indice = indice if isinstance(indice, dict) else {}
            primero = estado.pop('primer_mensaje', None)
            if mensajes:
                resto = self.mensajes.leer(_entero(indice.get('bytes')))
                if len(resto) + (primero is not None) != _entero(indice.get('mensajes')):
                    raise HistorialIncompleto(MENSAJES + ' no coincide con session.json.')
                lista = Mensajes(([primero] if primero is not None else []) + resto)
                estado['messages'] = lista
                self._lista, self._guardados = lista, len(lista)
            reconstruidas = self._cargar_fotos(indice) if fotos else None

        def resolver(fila):
            foto = fila.get('workspace_context') if isinstance(fila, dict) else None
            if reconstruidas is None or not (isinstance(foto, dict) and set(foto) == {REFERENCIA}):
                return fila
            if foto[REFERENCIA] not in reconstruidas:
                raise HistorialIncompleto(FOTOS + ' no tiene la foto ' + str(foto[REFERENCIA]) + '.')
            return dict(fila, workspace_context=reconstruidas[foto[REFERENCIA]])

        for campo, registro in self.listas.items():
            resolver_campo = resolver if campo in CON_FOTO else _igual
            if version == VERSION and isinstance(indice.get(campo), dict):
                estado[campo] = registro.cargar(indice[campo], resolver_campo)
            elif isinstance(estado.get(campo), list):
                # Hasta la 3, dentro de session.json: el primer guardado la escribe entera.
                estado[campo] = Lista(resolver_campo(fila) for fila in estado[campo])
        if version == VERSION and isinstance(indice.get(INCIDENCIAS), dict):
            estado[INCIDENCIAS] = self.incidencias.cargar(indice[INCIDENCIAS])
        return estado, version != VERSION

    def _cargar_fotos(self, indice):
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
        return reconstruidas

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
        # Primero se prepara todo (si algo no se puede escribir, no se escribe nada de la
        # evidencia), luego las fotos, a las que apuntan los registros, y después éstos.
        nuevas = {}
        listas = {campo: registro.pendiente(estado, lambda fila: self._con_referencia(fila, nuevas))
                  for campo, registro in self.listas.items()}
        incidencias = self.incidencias.pendiente(estado)
        self.fotos.añadir([linea for _, _, linea in nuevas.values()])
        # Sólo ahora, ya escritas, cuentan como guardadas.
        for foto, numero, _ in nuevas.values():
            self._numeros[id(foto)] = (foto, numero)
            self._fotos, self._ultima_foto = numero + 1, foto
        aparte = {'messages'}
        for campo, pendiente in listas.items():
            if pendiente is not None:
                self.listas[campo].escrito(*pendiente)
                aparte.add(campo)
        if incidencias is not None:
            self.incidencias.escrito(*incidencias)
            aparte.add(INCIDENCIAS)
        salida = {k: v for k, v in estado.items() if k not in aparte}
        salida['version'] = VERSION
        if mensajes:
            salida['primer_mensaje'] = _sin_razonamiento(mensajes[0])
        indice = {'archivo': MENSAJES, 'mensajes': len(mensajes), 'bytes': self.mensajes.bytes,
                  'fotos_archivo': FOTOS, 'fotos': self._fotos, 'fotos_bytes': self.fotos.bytes}
        for campo in aparte - {'messages', INCIDENCIAS}:
            indice[campo] = self.listas[campo].indice()
        if INCIDENCIAS in aparte:
            indice[INCIDENCIAS] = self.incidencias.indice()
        salida['historial'] = indice
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


def _igual(fila):
    return fila


def _llano(estado):
    for campo in ('messages', *LISTAS):
        if isinstance(estado.get(campo), list):
            estado[campo] = list(estado[campo])
    return estado


def leer(ruta):
    """El estado completo (con `messages`) de un session.json de cualquier versión."""
    ruta = Path(ruta)
    estado, _ = Historial(ruta.parent).cargar(json.loads(ruta.read_text()))
    return _llano(estado)


def leer_evidencia(ruta, *, fotos=False):
    """El estado de un session.json de cualquier versión, sin la conversación.

    Lo que lee quien juzga la sesión desde fuera (Chiyo: `operations`, `checks`,
    `unresolved_records`, `resolution_records`, `pending`…). Sin `fotos`, la foto del
    espacio de trabajo de cada registro va como `{"$foto": n}`.
    """
    ruta = Path(ruta)
    estado, _ = Historial(ruta.parent).cargar(json.loads(ruta.read_text()), mensajes=False, fotos=fotos)
    return _llano(estado)


def escribir(directorio, estado):
    """Crea la sesión nativa de `directorio` con el estado completo (`messages` incluido).

    Sólo para una carpeta nueva (bifurcar, importar): borra los JSONL que hubiera
    antes de escribir. Para convertir una sesión que ya existe, `Historial.cargar`
    y luego `guardar` con el mismo objeto, que nunca borra nada.
    """
    directorio = Path(directorio)
    estado = dict(estado)
    for nombre in (MENSAJES, FOTOS, ARCHIVO_INCIDENCIAS, *LISTAS.values()):
        # Una carpeta nueva no debería tenerlos; si los tiene, no son de esta sesión.
        (directorio / nombre).unlink(missing_ok=True)
    atomic_json(directorio / 'session.json', Historial(directorio).guardar(estado))
