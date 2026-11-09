"""Proveedores de modelos: Ollama, OpenAI, Anthropic, el servicio Alfalfa y los compatibles con OpenAI.

El bucle de `NativeRuntime` habla el formato de Ollama (`/api/chat` con
herramientas, `/api/show` para la ventana y las capacidades, `/api/tags` para la
lista). Cada proveedor da un cliente HTTP que entiende ese formato: Ollama tal
cual, y los demás traduciendo por dentro hacia su propia API y de vuelta. Así el
bucle no cambia según con quién hable.

El modelo de una sesión dice su proveedor con un prefijo: `openai/gpt-6-luna`,
`anthropic/claude-sonnet-5-5`, `alfalfa/glm-5.3-flash`, `openrouter/google/gemma-4-31b-it:free`. Sin prefijo (o
con `ollama/`) es de Ollama, como hasta ahora. Los prefijos de los compatibles con
OpenAI salen del catálogo de models.dev (`alfalfa.catalogo`); un modelo de Ollama
cuyo espacio de nombres coincida con uno de ellos se guarda con `ollama/` delante.
"""
from __future__ import annotations

import threading

import httpx

PREFIJOS = ('ollama', 'openai', 'anthropic', 'alfalfa')
TIEMPO_DE_LISTA = 10


class ErrorDeProveedor(RuntimeError):
    """Un proveedor no está configurado o no respondió."""


def es_prefijo(cabeza):
    """¿`cabeza` nombra un proveedor? Los fijos o un compatible con OpenAI del catálogo."""
    if cabeza in PREFIJOS:
        return True
    from .compatible import es_compatible
    try:
        return es_compatible(cabeza)
    except Exception:
        return False


def separar(modelo):
    """(proveedor, modelo sin prefijo). Sin prefijo conocido el proveedor es Ollama."""
    texto = str(modelo or '').strip()
    cabeza, barra, resto = texto.partition('/')
    if barra and resto and es_prefijo(cabeza):
        return cabeza, resto
    return 'ollama', texto


def con_prefijo(proveedor, modelo):
    """El nombre que guarda la sesión: los de Ollama siguen sin prefijo salvo que se confundan."""
    if proveedor == 'ollama':
        cabeza, barra, _ = str(modelo).partition('/')
        return 'ollama/' + modelo if barra and es_prefijo(cabeza) else modelo
    return proveedor + '/' + modelo


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

    def ficha(self, modelo):
        """La ficha del modelo en el catálogo de models.dev, o None."""
        return None

    def precio(self, modelo):
        """Precio por millón de tokens si este proveedor cobra por token; None si no (local, cuota, plan)."""
        return None

    def falta(self):
        """Qué falta configurar, en palabras para el usuario."""
        return ('El proveedor ' + self.nombre + ' no está configurado: añade su clave o su código en '
                'Proveedores.')

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
    if ident == 'anthropic':
        from .anthropic import Anthropic
        return Anthropic()
    if ident == 'alfalfa':
        from .alfalfa import Alfalfa
        return Alfalfa()
    from .compatible import Compatible, es_compatible
    if es_compatible(ident):
        return Compatible(ident)
    raise ErrorDeProveedor('Proveedor desconocido: ' + str(ident))


def compatibles_guardados():
    """Los compatibles con OpenAI para los que el usuario guardó una clave (`proveedores.json`)."""
    from . import configuracion
    from .compatible import es_compatible
    return [ident for ident, seccion in configuracion.leer().items()
            if isinstance(seccion, dict) and seccion.get('guardada') is True and es_compatible(ident)]


def disponibles():
    """Los proveedores que este AlfalfaCode sabe usar, en el orden del selector.

    Los fijos, OpenRouter y OpenCode Zen siempre (para poder configurarlos) y los
    demás compatibles del catálogo con una clave guardada.
    """
    from .compatible import CONOCIDOS
    lista = []
    for ident in PREFIJOS + tuple(CONOCIDOS) + tuple(i for i in compatibles_guardados() if i not in CONOCIDOS):
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
    for leer in (ajustes.ollama_clave, ajustes.openai_clave, ajustes.anthropic_clave, ajustes.alfalfa_codigo,
                 *[(lambda i=i: ajustes.clave_compatible(i)) for i in _compatibles_para_secretos()]):
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


def _compatibles_para_secretos():
    from .compatible import CONOCIDOS
    try:
        return list(CONOCIDOS) + compatibles_guardados()
    except Exception:
        return list(CONOCIDOS)


def listar_modelos():
    """(modelos, fallos): los de cada proveedor configurado y los gratis ahora.

    Un proveedor que falla no esconde a los demás; su fallo queda en `fallos`
    (`{proveedor: texto}`) sin el detalle, que podría llevar una clave. Los gratis
    ahora salen del catálogo de models.dev (se refresca aquí si tiene más de un
    día) aunque su proveedor no tenga clave: su fila dice entonces qué clave falta
    (`falta_clave`). Cada fila gratis lleva `gratis` y el `aviso` de datos.
    """
    from .. import catalogo
    catalogo.cargar(red=True)
    modelos, fallos = [], {}
    vistos = set()
    for proveedor in disponibles():
        if not proveedor.configurado():
            continue
        try:
            filas = proveedor.modelos()
        except Exception:
            fallos[proveedor.id] = 'No se pudo consultar ' + proveedor.nombre + '.'
            continue
        modelos.extend(filas)
        vistos.add(proveedor.id)
    from .compatible import Compatible
    for datos, _ in catalogo.gratis_ahora():
        if datos['id'] in vistos:
            continue
        vistos.add(datos['id'])
        try:
            modelos.extend(Compatible(datos['id']).modelos())
        except Exception:
            continue
    for fila in modelos:
        # `vision`: true si ve imágenes, false si no, null si no se sabe.
        vision = fila.get('vision')
        fila['vision'] = vision if isinstance(vision, bool) else None
    return modelos, fallos


def vision_de(info):
    """¿Ve imágenes, según lo que diría `/api/show`? True, False o None si no se sabe.

    Un proveedor que no sabe qué ve un modelo (uno añadido a mano que el catálogo no
    describe) lo dice con `vision_conocida: false`.
    """
    if not isinstance(info, dict):
        return None
    capacidades = info.get('capabilities')
    if isinstance(capacidades, list) and 'vision' in capacidades:
        return True
    if info.get('vision_conocida') is False or not isinstance(capacidades, list):
        return None
    return False


async def vision_del_modelo(modelo, timeout=15):
    """`vision_de` de un modelo preguntándole a su proveedor; None si no responde o no está configurado."""
    try:
        proveedor, nombre = para_modelo(modelo)
        if not proveedor.configurado():
            return None
        async with proveedor.cliente(httpx.Timeout(timeout, connect=5)) as cliente:
            respuesta = await cliente.post('/api/show', json={'model': nombre})
            if respuesta.status_code >= 400:
                return None
            return vision_de(respuesta.json())
    except Exception:
        return None


# Tope de `con_vision`: el rechazo no espera a que respondan todos los proveedores.
TOPE_CON_VISION = 3


def con_vision(modelo=None, limite=3, tope=TOPE_CON_VISION):
    """Los modelos con visión que este usuario puede usar ya (con su clave), los de su
    mismo proveedor primero: [(nombre que guarda la sesión, nombre visible)]. None si la
    lista no llegó en `tope` segundos o falló (la app tiene la suya)."""
    resultado = {}

    def listar():
        try:
            resultado['filas'] = listar_modelos()[0]
        except Exception:
            pass

    hilo = threading.Thread(target=listar, name='alfalfa-con-vision', daemon=True)
    hilo.start()
    hilo.join(tope)
    filas = resultado.get('filas')
    if filas is None:
        return None
    propio = separar(modelo)[0] if modelo else None
    elegidos = [f for f in filas if f.get('vision') is True and not f.get('falta_clave')
                and f.get('name') and f.get('name') != modelo and 'embed' not in f['name'].lower()]
    elegidos.sort(key=lambda f: (f.get('proveedor') != propio, bool(f.get('gratis'))))
    return [(f['name'], f.get('nombre') or f['name']) for f in elegidos[:limite]]


_PREGUNTAR = object()


def texto_sin_vision(modelo, sugeridos=_PREGUNTAR):
    """El error de un mensaje con imágenes para un modelo que no las ve, con los que sí las ven
    (None: no se pudo saber cuáles)."""
    nombre = separar(modelo)[1] or 'de la sesión'
    texto = 'El modelo ' + nombre + ' no ve imágenes, así que no se pueden adjuntar. '
    sugeridos = con_vision(modelo) if sugeridos is _PREGUNTAR else sugeridos
    if sugeridos:
        lista = ', '.join(visible + ('' if visible == ident else ' (' + ident + ')') for ident, visible in sugeridos)
        return texto + 'Cambia a uno que sí las ve, como ' + lista + ', o quita las imágenes.'
    if sugeridos is None:
        return texto + 'Cambia a un modelo con visión o quita las imágenes.'
    return texto + 'Ninguno de tus modelos las ve: quita las imágenes o añade uno con visión en Proveedores.'


def cliente_de_lista():
    return httpx.Client(timeout=TIEMPO_DE_LISTA, trust_env=False)
