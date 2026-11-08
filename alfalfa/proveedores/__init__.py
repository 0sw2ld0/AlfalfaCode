"""Proveedores de modelos: Ollama, OpenAI y el servicio Alfalfa.

El bucle de `NativeRuntime` habla el formato de Ollama (`/api/chat` con
herramientas, `/api/show` para la ventana y las capacidades, `/api/tags` para la
lista). Cada proveedor da un cliente HTTP que entiende ese formato: Ollama tal
cual, y los demás traduciendo por dentro hacia su propia API y de vuelta. Así el
bucle no cambia según con quién hable.

El modelo de una sesión dice su proveedor con un prefijo: `openai/gpt-6-luna`,
`alfalfa/glm-5.3-flash`. Sin prefijo (o con `ollama/`) es de Ollama, como hasta
ahora.
"""
from __future__ import annotations

import httpx

PREFIJOS = ('ollama', 'openai', 'alfalfa')
TIEMPO_DE_LISTA = 10


class ErrorDeProveedor(RuntimeError):
    """Un proveedor no está configurado o no respondió."""


def separar(modelo):
    """(proveedor, modelo sin prefijo). Sin prefijo conocido el proveedor es Ollama."""
    texto = str(modelo or '').strip()
    cabeza, barra, resto = texto.partition('/')
    if barra and cabeza in PREFIJOS and resto:
        return cabeza, resto
    return 'ollama', texto


def con_prefijo(proveedor, modelo):
    """El nombre que guarda la sesión: los de Ollama siguen sin prefijo."""
    return modelo if proveedor == 'ollama' else proveedor + '/' + modelo


class Proveedor:
    """Lo que todo proveedor ofrece al motor y a la lista de modelos."""

    id = ''
    nombre = ''
    # Lo que se añade al nombre visible en el selector («GPT-6 Luna - Alfalfa»).
    sufijo = ''

    def configurado(self):
        """¿Tiene lo necesario (clave, código) para usarse?"""
        return True

    def secretos(self):
        """Claves que nunca deben salir en la actividad, los errores ni el protocolo."""
        return []

    def alcance(self):
        """'local' o 'remote', para explicar los fallos de conexión."""
        return 'remote'

    def cliente(self, timeout, tarea=None, al_cambiar_cuota=None):
        """`httpx.AsyncClient` que habla el formato de Ollama.

        `tarea` es la de la sesión, por si el proveedor necesita su esfuerzo;
        `al_cambiar_cuota(filas)` se llama cuando el proveedor informa de su cuota.
        """
        raise NotImplementedError

    def modelos(self):
        """Filas al estilo de `/api/tags` con `name` (el que guarda la sesión),
        `nombre` (el visible) y `proveedor`."""
        raise NotImplementedError

    def accion(self, clas, error_text):
        """Qué hacer ante un fallo clasificado, en palabras de este proveedor, o None."""
        return None

    def estado(self):
        """Resumen para la pantalla de proveedores, sin secretos."""
        return {'id': self.id, 'nombre': self.nombre, 'configurado': self.configurado()}


def obtener(ident):
    if ident == 'ollama':
        from .ollama import Ollama
        return Ollama()
    if ident == 'openai':
        from .openai import OpenAI
        return OpenAI()
    if ident == 'alfalfa':
        from .alfalfa import Alfalfa
        return Alfalfa()
    raise ErrorDeProveedor('Proveedor desconocido: ' + str(ident))


def disponibles():
    """Los proveedores que este AlfalfaCode sabe usar, en el orden del selector."""
    lista = []
    for ident in PREFIJOS:
        try:
            lista.append(obtener(ident))
        except ErrorDeProveedor:
            continue
    return lista


def para_modelo(modelo):
    """(proveedor, modelo sin prefijo) para el modelo de una sesión."""
    ident, nombre = separar(modelo)
    return obtener(ident), nombre


def secretos():
    """Todas las claves conocidas, para sanear lo que se escribe o se emite."""
    from .. import ajustes
    valores = []
    for leer in (ajustes.ollama_clave, ajustes.openai_clave, ajustes.alfalfa_codigo):
        try:
            valores.append(leer())
        except Exception:
            continue
    for proveedor in disponibles():
        try:
            valores.extend(s for s in proveedor.secretos() if s)
        except Exception:
            continue
    return sorted({v for v in valores if v}, key=len, reverse=True)


def listar_modelos():
    """(modelos, fallos): los de cada proveedor configurado.

    Un proveedor que falla no esconde a los demás; su fallo queda en `fallos`
    (`{proveedor: texto}`) sin el detalle, que podría llevar una clave.
    """
    modelos, fallos = [], {}
    for proveedor in disponibles():
        if not proveedor.configurado():
            continue
        try:
            modelos.extend(proveedor.modelos())
        except Exception:
            fallos[proveedor.id] = 'No se pudo consultar ' + proveedor.nombre + '.'
    return modelos, fallos


def cliente_de_lista():
    return httpx.Client(timeout=TIEMPO_DE_LISTA, trust_env=False)
