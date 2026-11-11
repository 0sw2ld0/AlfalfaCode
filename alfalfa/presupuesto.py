"""Presupuesto con tope duro y lo que ahorra la caché de prompts.

Dos topes en dólares, los dos opcionales:

- de la sesión: lo que ha costado esta sesión, con lo de sus subagentes;
- del mes: lo gastado este mes natural (hora local) por todas las sesiones de este Mac.

Al 80 % de cualquiera de los dos se avisa una vez; al 100 % el turno se detiene antes
de la siguiente petición al modelo. Sólo cuenta lo que tiene precio en el catálogo:
un modelo local, de plan o con cortesía no gasta y puede seguir aunque el tope se
haya alcanzado.

El gasto del mes vive en `$ALFALFA_HOME/gasto/AAAA-MM.jsonl`, una línea por petición
con coste, que sólo crece y se lee por el final: ni `task.json` ni el `session.json`
del motor engordan, y varias sesiones a la vez escriben sin pisarse.
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

AVISO_AL = 0.8
ENTORNO_SESION = 'ALFALFA_PRESUPUESTO'
ENTORNO_MES = 'ALFALFA_PRESUPUESTO_MES'
# Lo que sube cada tope, para el mensaje del corte.
COMO_SUBIR = {
    'sesion': ('Ajustes ▸ General ▸ Presupuesto en la app, --presupuesto USD en la terminal o '
               + ENTORNO_SESION),
    'mes': ('Ajustes ▸ General ▸ Presupuesto en la app, --presupuesto-mes USD en la terminal o '
            + ENTORNO_MES),
}
_SIN_CAMBIO = object()


class PresupuestoAgotado(ValueError):
    """El turno se detiene: el gasto llegó al tope. `cual` es «sesion» o «mes»."""

    def __init__(self, texto, cual):
        super().__init__(texto)
        self.cual = cual


def importe(valor):
    """Un tope en dólares: float > 0, o None si no hay tope (vacío, 0, «no»). ValueError si no es un importe."""
    if valor is None:
        return None
    if isinstance(valor, str):
        texto = valor.strip().replace('$', '').replace(',', '.').strip()
        if texto.lower() in ('', '0', 'no', 'ninguno', 'sin'):
            return None
        try:
            valor = float(texto)
        except ValueError:
            raise ValueError('El presupuesto es un importe en dólares, como 5 o 12,50.') from None
    if (isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor)
            or valor < 0):
        raise ValueError('El presupuesto es un importe en dólares, como 5 o 12,50.')
    return float(valor) if valor > 0 else None


def dolares(valor):
    """«0,42 $», «< 0,01 $», «12,40 $»: como el resto de los textos en español."""
    if valor <= 0:
        return '0 $'
    if valor < 0.01:
        return '< 0,01 $'
    return f'{valor:.2f}'.replace('.', ',') + ' $'


def mes_actual(ahora=None):
    return time.strftime('%Y-%m', time.localtime(ahora))


def sumar_cache(task, leidos=0, escritos=0, sin_cache=None, con_cache=None):
    """Acumula en `task['cache']` los tokens leídos y escritos en la caché y, con precio, lo que
    habrían costado esas peticiones sin caché (`sin_cache`) frente a lo que costaron (`con_cache`)."""
    if not (leidos or escritos):
        return
    cache = task.get('cache') if isinstance(task.get('cache'), dict) else {}
    cache['leidos'] = int(cache.get('leidos') or 0) + int(leidos or 0)
    cache['escritos'] = int(cache.get('escritos') or 0) + int(escritos or 0)
    if sin_cache is not None and con_cache is not None:
        cache['sin_cache'] = float(cache.get('sin_cache') or 0.0) + sin_cache
        cache['con_cache'] = float(cache.get('con_cache') or 0.0) + con_cache
    task['cache'] = cache


def resumen_cache(task):
    """{leidos, escritos, sin_cache?, con_cache?, ahorro?} para el cliente, o None sin caché."""
    cache = task.get('cache')
    if not isinstance(cache, dict):
        return None
    datos = {}
    for clave in ('leidos', 'escritos'):
        if type(cache.get(clave)) is int and cache[clave] >= 0:
            datos[clave] = cache[clave]
    if not datos.get('leidos') and not datos.get('escritos'):
        return None
    sin, con = cache.get('sin_cache'), cache.get('con_cache')
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0
           for v in (sin, con)):
        datos['sin_cache'] = round(sin, 6)
        datos['con_cache'] = round(con, 6)
        # Negativo si escribir en la caché (1,25× en Anthropic) aún no se ha recuperado leyendo.
        datos['ahorro'] = round(sin - con, 6)
    return datos


def texto_cache(datos):
    """«Caché: ahorró 0,42 $ (1.234.567 tokens leídos)», o None."""
    if not datos or not datos.get('leidos') and not datos.get('escritos'):
        return None
    tokens = f"{datos.get('leidos', 0):,}".replace(',', '.') + ' tokens leídos'
    ahorro = datos.get('ahorro')
    if ahorro is None:
        return 'Caché: ' + tokens
    if ahorro >= 0:
        return 'Caché: ahorró ' + dolares(ahorro) + ' (' + tokens + ')'
    return 'Caché: aún cuesta ' + dolares(-ahorro) + ' más de lo que ahorra (' + tokens + ')'


def _de_entorno(nombre):
    try:
        return importe(os.environ.get(nombre))
    except ValueError as exc:
        raise ValueError(nombre + ': ' + str(exc)) from None


class GastoMensual:
    """El gasto de este Mac en el mes, por un registro JSONL que sólo crece.

    Se lee de una vez al empezar y después sólo lo añadido desde la última lectura,
    también lo que escriben otras sesiones a la vez."""

    def __init__(self, home):
        self.carpeta = Path(home) / 'gasto'
        self._mes = None
        self._leido = 0
        self._total = 0.0
        self._resto = b''

    def _archivo(self, mes):
        return self.carpeta / (mes + '.jsonl')

    def total(self, mes=None):
        mes = mes or mes_actual()
        if mes != self._mes:
            self._mes, self._leido, self._total, self._resto = mes, 0, 0.0, b''
        try:
            fd = os.open(self._archivo(mes), os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:  # aún no hay gasto este mes, o se borró el registro
            self._leido, self._total, self._resto = 0, 0.0, b''
            return self._total
        with os.fdopen(fd, 'rb') as archivo:
            if os.fstat(archivo.fileno()).st_size < self._leido:
                # Lo vaciaron o lo cambiaron por otro: se vuelve a leer entero.
                self._leido, self._total, self._resto = 0, 0.0, b''
            archivo.seek(self._leido)
            nuevo = archivo.read()
        self._leido += len(nuevo)
        lineas = nuevo.split(b'\n')
        resto, self._resto = self._resto, b''
        if resto:
            # La línea que quedó a medias se completa con el principio de lo nuevo; si así no es
            # válida (quien la escribía no la terminó), vale sólo el trozo nuevo.
            unida = resto + lineas[0]
            lineas[0] = unida if len(lineas) == 1 or self._usd(unida) is not None else lineas[0]
        # Una línea a medio escribir por otra sesión se termina de leer la próxima vez.
        self._resto = lineas.pop()
        for linea in lineas:
            usd = self._usd(linea)
            if usd is not None:
                self._total += usd
        return self._total

    @staticmethod
    def _usd(linea):
        try:
            usd = json.loads(linea).get('usd')
        except (ValueError, AttributeError):
            return None
        if isinstance(usd, (int, float)) and not isinstance(usd, bool) and math.isfinite(usd) and usd > 0:
            return usd
        return None

    def avisado(self, clave, mes=None):
        """¿Ya se avisó este mes (en cualquier sesión) del 80 % de este tope?"""
        try:
            return clave in (self.carpeta / ((mes or mes_actual()) + '.avisos')).read_text().splitlines()
        except OSError:
            return False

    def marcar_aviso(self, clave, mes=None):
        try:
            self.carpeta.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(self.carpeta / ((mes or mes_actual()) + '.avisos'),
                         os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'a') as archivo:
                archivo.write(clave + '\n')
        except OSError:
            pass

    def apuntar(self, sesion, usd, ahora=None):
        """Una línea por petición con coste; un fallo al escribir no corta el turno."""
        if not usd or usd <= 0:
            return
        ahora = time.time() if ahora is None else ahora
        try:
            self.carpeta.mkdir(mode=0o700, parents=True, exist_ok=True)
            fila = json.dumps({'t': round(ahora, 3), 's': sesion, 'usd': round(usd, 8)}) + '\n'
            fd = os.open(self._archivo(mes_actual(ahora)), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
                         0o600)
            with os.fdopen(fd, 'a') as archivo:
                archivo.write(fila)
        except OSError:
            pass


class Presupuesto:
    """Los topes de una sesión y lo gastado. Los subagentes usan el de su sesión: lo que
    gastan suma a ella (`coste_usd`, `cache`) y cuenta para sus topes.

    Antes de cada petición con precio, `reservar` mira lo gastado más lo que costará la
    entrada de esa petición (estimada) y lo ya reservado por las que están en marcha
    (subagentes en paralelo). Lo que no se puede estimar es la salida: un turno puede
    pasarse del tope, como mucho, lo que cueste la última respuesta.

    `aviso(texto, datos)` dice al usuario el aviso del 80 % (`datos` con `aviso`) y avisa
    a la app del corte (`agotado`); el texto del corte va en el error, que es el resumen
    del turno."""

    def __init__(self, home, task, sesion=None, mes=None, aviso=None, equipo=None):
        self.task = task
        self.registro = GastoMensual(home)
        self._sesion = importe(sesion) if sesion is not None else _de_entorno(ENTORNO_SESION)
        self._mes = importe(mes) if mes is not None else _de_entorno(ENTORNO_MES)
        self.equipo = equipo
        self.aviso = aviso
        self._reservas = {}

    def cambiar(self, sesion=_SIN_CAMBIO, mes=_SIN_CAMBIO):
        """Desde Ajustes de la app (`presupuesto` por --stdio): vale ya, también a mitad de turno."""
        if sesion is not _SIN_CAMBIO:
            self._sesion = importe(sesion)
        if mes is not _SIN_CAMBIO:
            self._mes = importe(mes)

    def _tope(self, cual, personal):
        compartido = getattr(self.equipo, cual, None)
        if compartido is None:
            return personal
        return compartido if personal is None else min(personal, compartido)

    @property
    def sesion(self):
        return self._tope('sesion', self._sesion)

    @property
    def mes(self):
        return self._tope('mes', self._mes)

    @property
    def con_tope(self):
        return bool(self.sesion or self.mes)

    def gastado_sesion(self):
        coste = self.task.get('coste_usd')
        return float(coste) if isinstance(coste, (int, float)) and not isinstance(coste, bool) else 0.0

    def gastado_mes(self):
        return self.registro.total()

    def estado(self):
        """Lo que va en el evento `presupuesto`: topes (null sin tope), gastado y la caché."""
        datos = {'sesion': {'tope': self.sesion, 'gastado': round(self.gastado_sesion(), 6)},
                 'mes': {'tope': self.mes, 'gastado': round(self.gastado_mes(), 6), 'mes': mes_actual()}}
        cache = resumen_cache(self.task)
        if cache:
            datos['cache'] = cache  # al abrir la sesión, el panel ya enseña lo ahorrado
        return datos

    def para_el_cliente(self):
        """El estado si hay algo que enseñar (un tope, gasto este mes o caché); None si no."""
        datos = self.estado()
        if self.con_tope or datos['mes']['gastado'] > 0 or 'cache' in datos:
            return datos
        return None

    def sumar(self, task, coste, cache=None):
        """Lo que costó una petición de `task` (la sesión o uno de sus subagentes). El motor ya lo
        sumó a `task`; si es un subagente, se suma también a la sesión."""
        if task is not self.task and coste:
            self.task['coste_usd'] = self.gastado_sesion() + coste
            if cache:
                sumar_cache(self.task, **cache)
        self.registro.apuntar(self.task.get('id'), coste)
        self._avisar_si_toca()

    def _avisar_si_toca(self):
        avisados = self.task.get('presupuesto_avisos')
        avisados = avisados if isinstance(avisados, list) else []
        for cual, tope, gastado in (('sesion', self.sesion, self.gastado_sesion),
                                    ('mes', self.mes, self.gastado_mes)):
            if not tope:
                continue
            valor = gastado()
            if valor < tope * AVISO_AL or valor >= tope:
                continue
            clave = cual + ':' + repr(tope)
            # El de la sesión, una vez por sesión; el del mes, una vez al mes en este Mac.
            if clave in avisados if cual == 'sesion' else self.registro.avisado(clave):
                continue
            if cual == 'sesion':
                avisados.append(clave)
                self.task['presupuesto_avisos'] = avisados[-20:]
            else:
                self.registro.marcar_aviso(clave)
            nombre = 'de esta sesión' if cual == 'sesion' else 'del mes en este Mac'
            texto = ('Llevas ' + dolares(valor) + ' de ' + dolares(tope) + ' del presupuesto ' + nombre + ' ('
                     + str(int(valor * 100 // tope)) + ' %). Al llegar al tope se detendrá antes de la '
                     'siguiente petición al modelo.')
            self._decir(texto, dict(self.estado(), aviso=cual))

    def agotado(self, estimado=0.0, clave=None):
        """(texto, cual) si lo gastado, más lo reservado por otras peticiones en marcha y lo que
        costará esta (`estimado`), pasa de algún tope; None si se puede seguir."""
        otras = sum(v for k, v in self._reservas.items() if k != clave)
        for cual, tope, gastado in (('sesion', self.sesion, self.gastado_sesion),
                                    ('mes', self.mes, self.gastado_mes)):
            if not tope:
                continue
            valor = gastado()
            que = 'El presupuesto de esta sesión' if cual == 'sesion' else 'El presupuesto del mes en este Mac'
            como = (' Para seguir, sube el tope (' + COMO_SUBIR[cual] + ') o cambia a un modelo sin coste por '
                    'token (local o de plan).')
            if getattr(self.equipo, cual, None) == tope:
                como = ' El tope viene de .alfalfa/equipo.toml. Puedes elegir un modelo permitido sin coste por token.'
            if valor >= tope:
                return (que + ' se agotó: se gastaron ' + dolares(valor) + ' de ' + dolares(tope) + '. Se detuvo '
                        'antes de la siguiente petición al modelo.' + como), cual
            if valor + otras + estimado > tope:
                return (que + ' no alcanza para la siguiente petición al modelo: se gastaron ' + dolares(valor)
                        + ' de ' + dolares(tope) + (' (' + dolares(otras) + ' más en peticiones en marcha)'
                                                    if otras > 0 else '')
                        + ' y su entrada costaría unos ' + dolares(estimado) + '. Se detuvo antes de pedirla.'
                        + como), cual
        return None

    def reservar(self, clave, estimado=0.0):
        """Antes de una petición con precio: PresupuestoAgotado si no alcanza; si alcanza, aparta
        `estimado` hasta `liberar(clave)`."""
        agotado = self.agotado(estimado, clave)
        if agotado:
            texto, cual = agotado
            self._decir(texto, dict(self.estado(), agotado=cual))
            raise PresupuestoAgotado(texto, cual)
        if estimado > 0:
            self._reservas[clave] = estimado

    def liberar(self, clave):
        self._reservas.pop(clave, None)

    def comprobar(self):
        """Sin petición concreta (al empezar un turno): PresupuestoAgotado si un tope ya se alcanzó."""
        self.reservar(None, 0.0)

    def _decir(self, texto, datos):
        if callable(self.aviso):
            try:
                self.aviso(texto, datos)
            except Exception:  # noqa: BLE001 — avisar nunca corta el turno
                pass
