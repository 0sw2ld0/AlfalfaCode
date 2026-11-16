"""Ayudantes de las pruebas de PACAI: un cliente en el mismo proceso y la comprobación de una transcripción."""
import asyncio
import io
import json
from datetime import datetime, timedelta, timezone

from alfalfa.pacai.esquema import validar
from alfalfa.pacai.transporte import TransportePacai
from alfalfa.pacai.verificador import Session
from alfalfa.proveedores.nativo.descripciones import datos_de_fin, datos_de_inicio

SESION = 's_prueba_0123456789abcdef'


def problemas(mensajes):
    """Esquema y reglas de sesión de una transcripción completa (la misma lógica que `conformance/verificar.py`)."""
    return Session.check(mensajes)


def leer_jsonl(texto):
    return [json.loads(linea) for linea in texto.splitlines() if linea.strip()]


class RelojFalso:
    """Un reloj que sólo avanza cuando se le dice, para probar `expires` sin esperar."""

    def __init__(self):
        self.ahora = datetime(2026, 11, 15, 10, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.ahora

    def avanzar(self, segundos):
        self.ahora += timedelta(seconds=segundos)


class Cliente:
    """Un cliente PACAI contra un `TransportePacai` en el mismo proceso. `log` es la transcripción de los dos lados."""

    def __init__(self, perfiles=('work',), extensiones=('alfalfa',), herramientas=(), caducidad=None, reloj=None,
                 **opciones):
        self.salida = io.StringIO()
        self.t = TransportePacai(self.salida, **({'caducidad': caducidad} if caducidad is not None else {}),
                                 **({'reloj': reloj} if reloj else {}), **opciones)
        self.log, self.seq, self._leidas = [], 0, 0
        self.hello = dict(versions=['0.2'], profiles=list(perfiles), extensions=list(extensiones),
                          client_tools=[{'name': n, 'input_schema': {'type': 'object'}} for n in herramientas])

    def drenar(self):
        """Lo que el agente escribió desde la última vez, ya en el log."""
        filas = leer_jsonl(self.salida.getvalue())
        nuevos, self._leidas = filas[self._leidas:], len(filas)
        self.log += nuevos
        return nuevos

    def mensaje(self, tipo, payload=None, **sobre):
        self.seq += 1
        return dict(pacai='0.2', id='c_%08d' % self.seq, type=tipo, session=SESION, sender='client', seq=self.seq,
                    time='2026-11-15T10:00:00Z', payload=payload or {}, **sobre)

    def crudo(self, mensaje):
        """Manda un mensaje tal cual (también uno mal formado) y devuelve lo que contesta el agente."""
        self.log.append(mensaje) if isinstance(mensaje, dict) else None
        self.t.receive(mensaje if isinstance(mensaje, str) else json.dumps(mensaje))
        return self.drenar()

    def enviar(self, tipo, payload=None, **sobre):
        m = self.mensaje(tipo, payload, **sobre)
        self.ultimo = m
        return self.crudo(m)

    def saludar(self, **cambios):
        return self.enviar('hello', dict(self.hello, **cambios))

    def entrada(self, texto='haz algo', **payload):
        return self.enviar('input', dict(blocks=[{'type': 'text', 'text': texto}], **payload))

    def del_agente(self, tipo=None):
        self.drenar()
        return [m for m in self.log if m['sender'] == 'agent' and (tipo is None or m['type'] == tipo)]

    def comprobar(self):
        """La transcripción entera pasa esquema y reglas de sesión."""
        assert problemas(self.log) == [], problemas(self.log)
        return self.log


def errores_de_esquema(mensajes):
    return [e for m in mensajes for e in validar(m)]


# -- lo que hace el motor, visto desde su transporte ---------------------------------------------------
async def reposo():
    await asyncio.sleep(0.02)


async def abrir(c, texto='haz algo'):
    """Saludo, un `input` y su ejecución raíz: devuelve el `run_id`."""
    c.saludar()
    c.entrada(texto)
    c.t.turno_empieza(await asyncio.wait_for(c.t.next_message(), 1))      # el motor toma el mensaje y abre el turno
    return c.drenar()[-1]['id']


def herramienta(c, nombre, args, **extra):
    texto = 'Inicio ' + nombre + ' · ' + str(args)[:1000]
    c.t.emit('evento', rol='tool', texto=texto, hora=1.0, herramienta=datos_de_inicio(nombre, args), **extra)
    return c.drenar()


def fin_de_herramienta(c, nombre, resultado):
    c.t.emit('evento', rol='tool', texto=nombre + '\n' + json.dumps(resultado), hora=2.0,
             herramienta=datos_de_fin(nombre, resultado))
    return c.drenar()


async def cerrar(c, estado='completed', **campos):
    """El motor termina el turno y se espera a que el transporte termine de cerrar la ejecución."""
    c.t.emit('turno_terminado', estado=estado, resumen=campos.pop('resumen', 'Hecho'), codigo_salida=0, tokens=10,
             **campos)
    if c.t._cerrando is not None:
        await asyncio.wait_for(asyncio.shield(c.t._cerrando), 5)
    await asyncio.sleep(0)
    return c.drenar()
