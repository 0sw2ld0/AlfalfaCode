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
        except OSError:  # aún no hay gasto este mes
            return self._total
        with os.fdopen(fd, 'rb') as archivo:
            archivo.seek(self._leido)
            nuevo = archivo.read()
        self._leido += len(nuevo)
        datos = self._resto + nuevo
        lineas = datos.split(b'\n')
        # Una línea a medio escribir por otra sesión se termina de leer la próxima vez.
        self._resto = lineas.pop()
        for linea in lineas:
            try:
                usd = json.loads(linea).get('usd')
            except (ValueError, AttributeError):
                continue
            if isinstance(usd, (int, float)) and not isinstance(usd, bool) and math.isfinite(usd) and usd > 0:
                self._total += usd
        return self._total

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

    `aviso(texto, datos)` dice al usuario el aviso del 80 % y el corte; `datos` es el
    estado del presupuesto con `aviso` o `agotado`."""

    def __init__(self, home, task, sesion=None, mes=None, aviso=None):
        self.task = task
        self.registro = GastoMensual(home)
        self.sesion = importe(sesion) if sesion is not None else _de_entorno(ENTORNO_SESION)
        self.mes = importe(mes) if mes is not None else _de_entorno(ENTORNO_MES)
        self.aviso = aviso

    def cambiar(self, sesion=_SIN_CAMBIO, mes=_SIN_CAMBIO):
        """Desde Ajustes de la app (`presupuesto` por --stdio): vale ya, también a mitad de turno."""
        if sesion is not _SIN_CAMBIO:
            self.sesion = importe(sesion)
        if mes is not _SIN_CAMBIO:
            self.mes = importe(mes)

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
        if self.sesion or self.mes or datos['mes']['gastado'] > 0 or 'cache' in datos:
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
        for cual, tope, gastado, clave in (
                ('sesion', self.sesion, self.gastado_sesion, lambda t: 'sesion:' + repr(t)),
                ('mes', self.mes, self.gastado_mes, lambda t: 'mes:' + mes_actual() + ':' + repr(t))):
            if not tope:
                continue
            valor = gastado()
            if valor < tope * AVISO_AL or valor >= tope or clave(tope) in avisados:
                continue
            avisados.append(clave(tope))
            self.task['presupuesto_avisos'] = avisados[-20:]
            nombre = 'de esta sesión' if cual == 'sesion' else 'del mes en este Mac'
            texto = ('Llevas ' + dolares(valor) + ' de ' + dolares(tope) + ' del presupuesto ' + nombre + ' ('
                     + str(int(valor * 100 // tope)) + ' %). Al llegar al tope el turno se detendrá antes de la '
                     'siguiente petición al modelo.')
            self._decir(texto, dict(self.estado(), aviso=cual))

    def agotado(self):
        """(texto, cual) si algún tope se alcanzó; None si se puede seguir."""
        for cual, tope, gastado in (('sesion', self.sesion, self.gastado_sesion),
                                    ('mes', self.mes, self.gastado_mes)):
            if not tope:
                continue
            valor = gastado()
            if valor >= tope:
                que = ('El presupuesto de esta sesión' if cual == 'sesion'
                       else 'El presupuesto del mes en este Mac')
                texto = (que + ' se agotó: se gastaron ' + dolares(valor) + ' de ' + dolares(tope) + '. El turno '
                         'se detuvo antes de la siguiente petición al modelo. Para seguir, sube el tope ('
                         + COMO_SUBIR[cual] + ') o cambia a un modelo sin coste por token (local o de plan).')
                return texto, cual
        return None

    def comprobar(self, decir=True):
        """Antes de cada petición a un modelo con precio: PresupuestoAgotado si no queda.
        Un subagente no lo dice (`decir=False`): lo dice la sesión al pararse ella."""
        agotado = self.agotado()
        if agotado:
            texto, cual = agotado
            if decir:
                self._decir(texto, dict(self.estado(), agotado=cual))
            raise PresupuestoAgotado(texto, cual)

    def _decir(self, texto, datos):
        if callable(self.aviso):
            try:
                self.aviso(texto, datos)
            except Exception:  # noqa: BLE001 — avisar nunca corta el turno
                pass
