"""El catálogo de modelos de models.dev: ventanas, límites, capacidades y precios.

models.dev (https://models.dev, licencia MIT, de los creadores de OpenCode)
publica sin clave un JSON con cientos de proveedores y miles de modelos. Aquí se
baja, se valida (es un dato externo), se reduce a lo que AlfalfaCode usa y se
guarda en `$ALFALFA_HOME/catalogo/models.dev.json` con su fecha. Se refresca
cada 24 horas; sin red vale la última copia guardada, y la primera vez sin
internet, la copia mínima que viaja con el paquete (`minimo.json`).

El catálogo sólo informa: lo que responda el propio proveedor (por ejemplo
`/api/show` de Ollama) manda sobre él. Nunca trae claves; de cada proveedor dice
el nombre de su variable (`env`) y dónde se consigue (`doc`) para explicarlo.

    ficha(proveedor, modelo) -> dict | None
    proveedor(ident) -> dict | None
    gratis_ahora() -> [(proveedor, ficha), …]
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

URL = 'https://models.dev/api.json'
CADA = 24 * 3600
# Sin red no se reintenta en cada consulta: una vez por hora como mucho.
REINTENTO = 3600
TIEMPO = 15
# Hoy pesa unos 6 MB; el tope deja margen sin aceptar cualquier cosa.
MAX_BYTES = 32 * 1024 * 1024
MAX_PROVEEDORES = 2000
MAX_MODELOS = 5000
MAX_TEXTO = 300
MINIMO = Path(__file__).with_name('minimo.json')
ARCHIVO = 'models.dev.json'

ID_PROVEEDOR = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')
ID_MODELO = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$')
VARIABLE = re.compile(r'^[A-Z][A-Z0-9_]{1,63}$')
# Qué API habla cada paquete del SDK de IA que declara models.dev.
APIS = {'@ai-sdk/openai-compatible': 'chat', '@openrouter/ai-sdk-provider': 'chat', '@ai-sdk/openai': 'responses'}
# Proveedores con suscripción: su coste 0 es «incluido en el plan», no «gratis».
PLANES = re.compile(r'(coding|code|token)-plan|-go$')
# Sus modelos gratis sólo se pueden usar desde su propio cliente: «Free-tier chat
# models require OpenCode; use paid models or Go from other clients» (opencode.ai/docs/zen).
SOLO_SU_CLIENTE = {'opencode': 'Los modelos gratis de OpenCode Zen sólo se pueden usar desde OpenCode.'}
AVISO_GRATIS = 'Gratis: puede usar tus datos para entrenar'

# Las pruebas sustituyen la descarga para no salir a internet.
_descargar = None
_memoria: dict = {}


class CatalogoInvalido(ValueError):
    """Lo que llegó no tiene la forma del catálogo de models.dev."""


def casa():
    from ..proveedores import configuracion
    return configuracion.ruta().parent


def ruta():
    return casa() / 'catalogo' / ARCHIVO


# --- Validación -----------------------------------------------------------

def _texto(valor, maximo=MAX_TEXTO):
    if not isinstance(valor, str):
        return None
    valor = ''.join(c for c in valor if c.isprintable()).strip()
    return valor[:maximo] or None


def _numero(valor):
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    if not math.isfinite(valor) or valor < 0 or valor > 1e9:
        return None
    return int(valor) if float(valor).is_integer() else float(valor)


def _entero(valor):
    numero = _numero(valor)
    return int(numero) if numero is not None and numero >= 1 else None


def local(url):
    """¿Apunta a este equipo o a una red privada? Ahí 0 es «corre en tu máquina»."""
    partes = urlsplit(url or '')
    host = (partes.hostname or '').lower()
    if not host or host == 'localhost' or host.endswith(('.local', '.localhost', '.internal')):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _url(valor):
    """Una URL base aceptable: HTTPS sin credenciales, consulta ni fragmento; HTTP sólo en local."""
    texto = _texto(valor, 500)
    if not texto:
        return None
    try:
        partes = urlsplit(texto)
    except ValueError:
        return None
    if partes.username or partes.password or partes.query or partes.fragment or not partes.hostname:
        return None
    if partes.scheme != 'https' and not (partes.scheme == 'http' and local(texto)):
        return None
    return texto.rstrip('/')


def _precio(valor):
    if not isinstance(valor, dict):
        return None
    entrada, salida = _numero(valor.get('input')), _numero(valor.get('output'))
    if entrada is None or salida is None:
        return None
    precio = {'entrada': entrada, 'salida': salida}
    for origen, destino in (('cache_read', 'cache_lectura'), ('cache_write', 'cache_escritura')):
        numero = _numero(valor.get(origen))
        if numero is not None:
            precio[destino] = numero
    return precio


def _esfuerzos(valor):
    if not isinstance(valor, list):
        return []
    for opcion in valor[:10]:
        if isinstance(opcion, dict) and opcion.get('type') == 'effort' and isinstance(opcion.get('values'), list):
            return [v for v in (_texto(x, 20) for x in opcion['values'][:12]) if v]
    return []


def _modelo(ident, crudo, base):
    if not isinstance(crudo, dict) or not isinstance(ident, str) or not ID_MODELO.fullmatch(ident):
        return None
    limite = crudo.get('limit') if isinstance(crudo.get('limit'), dict) else {}
    modalidades = crudo.get('modalities') if isinstance(crudo.get('modalities'), dict) else {}
    entradas = [m for m in modalidades.get('input') or [] if isinstance(m, str)] \
        if isinstance(modalidades.get('input'), list) else []
    propio = crudo.get('provider') if isinstance(crudo.get('provider'), dict) else {}
    ficha = {
        'id': ident,
        'nombre': _texto(crudo.get('name')) or ident,
        'ventana': _entero(limite.get('context')),
        'entrada': _entero(limite.get('input')),
        'salida': _entero(limite.get('output')),
        'razona': crudo.get('reasoning') is True,
        'esfuerzos': _esfuerzos(crudo.get('reasoning_options')),
        'herramientas': crudo.get('tool_call') is True,
        'vision': crudo.get('attachment') is True and 'image' in entradas,
        'precio': _precio(crudo.get('cost')),
        'abiertos': crudo.get('open_weights') is True,
        'fecha': _texto(crudo.get('release_date'), 20),
        'conocimiento': _texto(crudo.get('knowledge'), 20),
        'estado': _texto(crudo.get('status'), 20),
    }
    # Un modelo puede hablar otra API o vivir en otra URL que su proveedor.
    paquete = _texto(propio.get('npm'), 100)
    if paquete:
        ficha['paquete'] = paquete
    url = _url(propio.get('api'))
    if url and url != base:
        ficha['api'] = url
    return {k: v for k, v in ficha.items() if v is not None}


def _proveedor(ident, crudo):
    if not isinstance(crudo, dict) or not isinstance(ident, str) or not ID_PROVEEDOR.fullmatch(ident):
        return None
    modelos = crudo.get('models')
    if not isinstance(modelos, dict) or len(modelos) > MAX_MODELOS:
        return None
    base = _url(crudo.get('api'))
    variables = crudo.get('env') if isinstance(crudo.get('env'), list) else []
    datos = {
        'id': ident,
        'nombre': _texto(crudo.get('name')) or ident,
        'api': base,
        'env': [v for v in variables[:4] if isinstance(v, str) and VARIABLE.fullmatch(v)],
        'paquete': _texto(crudo.get('npm'), 100),
        'doc': _url(crudo.get('doc')),
        'modelos': {},
    }
    for clave, valor in modelos.items():
        ficha = _modelo(clave, valor, base)
        if ficha:
            datos['modelos'][clave] = ficha
    return {k: v for k, v in datos.items() if v is not None}


def validar(crudo):
    """El catálogo reducido y validado, o CatalogoInvalido. Lo que no encaja se descarta."""
    if not isinstance(crudo, dict) or not crudo or len(crudo) > MAX_PROVEEDORES:
        raise CatalogoInvalido('El catálogo no es un objeto de proveedores.')
    proveedores = {}
    for ident, valor in crudo.items():
        datos = _proveedor(ident, valor)
        if datos and datos['modelos']:
            proveedores[ident] = datos
    if not proveedores:
        raise CatalogoInvalido('El catálogo no trae ningún proveedor válido.')
    return proveedores


# --- Carga, caché y refresco ----------------------------------------------

def _bajar():
    """Los bytes de api.json, con tope de tamaño; lanza si no hay red o no cuadra."""
    if _descargar is not None:
        return _descargar()
    import httpx
    with httpx.Client(timeout=TIEMPO, trust_env=False, follow_redirects=False) as cliente:
        with cliente.stream('GET', URL, headers={'Accept': 'application/json',
                                                  'User-Agent': 'AlfalfaCode'}) as respuesta:
            respuesta.raise_for_status()
            largo = respuesta.headers.get('content-length')
            if largo and largo.isdigit() and int(largo) > MAX_BYTES:
                raise CatalogoInvalido('El catálogo pesa demasiado.')
            datos = bytearray()
            for trozo in respuesta.iter_bytes():
                datos.extend(trozo)
                if len(datos) > MAX_BYTES:
                    raise CatalogoInvalido('El catálogo pesa demasiado.')
    return bytes(datos)


def _leer(camino):
    try:
        if camino.is_symlink() or camino.stat().st_size > MAX_BYTES:
            return None
        valor = json.loads(camino.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(valor, dict) or not isinstance(valor.get('proveedores'), dict):
        return None
    try:
        # Lo guardado se vuelve a validar: el archivo pudo cambiar a mano.
        valor['proveedores'] = _revalidar(valor['proveedores'])
    except CatalogoInvalido:
        return None
    fecha = valor.get('fecha')
    valor['fecha'] = fecha if isinstance(fecha, (int, float)) and math.isfinite(fecha) else 0
    return valor


def _revalidar(proveedores):
    """Lo ya reducido vuelve a pasar por los mismos filtros (formato propio, no el de models.dev)."""
    if not isinstance(proveedores, dict) or len(proveedores) > MAX_PROVEEDORES:
        raise CatalogoInvalido('Catálogo guardado inválido.')
    limpios = {}
    for ident, datos in proveedores.items():
        if not isinstance(datos, dict) or not isinstance(ident, str) or not ID_PROVEEDOR.fullmatch(ident):
            continue
        modelos = datos.get('modelos')
        if not isinstance(modelos, dict) or len(modelos) > MAX_MODELOS:
            continue
        crudo = {'name': datos.get('nombre'), 'api': datos.get('api'), 'env': datos.get('env'),
                 'npm': datos.get('paquete'), 'doc': datos.get('doc'), 'models': {}}
        for clave, ficha in modelos.items():
            if not isinstance(ficha, dict):
                continue
            precio = ficha.get('precio') if isinstance(ficha.get('precio'), dict) else None
            crudo['models'][clave] = {
                'name': ficha.get('nombre'), 'reasoning': ficha.get('razona'), 'tool_call': ficha.get('herramientas'),
                'attachment': ficha.get('vision'), 'modalities': {'input': ['image'] if ficha.get('vision') else []},
                'limit': {'context': ficha.get('ventana'), 'input': ficha.get('entrada'),
                          'output': ficha.get('salida')},
                'reasoning_options': [{'type': 'effort', 'values': ficha.get('esfuerzos') or []}],
                'cost': None if precio is None else {'input': precio.get('entrada'), 'output': precio.get('salida'),
                                                     'cache_read': precio.get('cache_lectura'),
                                                     'cache_write': precio.get('cache_escritura')},
                'open_weights': ficha.get('abiertos'), 'release_date': ficha.get('fecha'),
                'knowledge': ficha.get('conocimiento'), 'status': ficha.get('estado'),
                'provider': {'npm': ficha.get('paquete'), 'api': ficha.get('api')}}
        valido = _proveedor(ident, crudo)
        if valido and valido['modelos']:
            limpios[ident] = valido
    if not limpios:
        raise CatalogoInvalido('Catálogo guardado vacío.')
    return limpios


def _guardar(proveedores, fecha):
    destino = ruta()
    destino.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporal = destino.with_suffix('.tmp')
    temporal.write_text(json.dumps({'fuente': URL, 'fecha': fecha, 'proveedores': proveedores},
                                   ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    os.chmod(temporal, 0o600)
    os.replace(temporal, destino)


def _intento():
    return ruta().with_name('ultimo-intento')


def _toca_intentar(ahora):
    try:
        return ahora - _intento().stat().st_mtime >= REINTENTO
    except OSError:
        return True


def refrescar():
    """Baja el catálogo, lo valida y lo guarda. Devuelve lo guardado o lanza."""
    ahora = time.time()
    try:
        _intento().parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _intento().touch()
    except OSError:
        pass
    datos = _bajar()
    try:
        crudo = json.loads(datos)
    except ValueError as exc:
        raise CatalogoInvalido('El catálogo no es JSON.') from exc
    proveedores = validar(crudo)
    _guardar(proveedores, ahora)
    _memoria.clear()
    _memoria.update(fecha=ahora, proveedores=proveedores, origen='red')
    return _memoria


def cargar(red=False):
    """El catálogo: {'fecha', 'proveedores', 'origen'} (`red`, `cache` o `paquete`).

    Con `red`, si la copia guardada tiene más de 24 horas se intenta refrescar; si
    falla, vale la guardada o la del paquete. Sin `red` nunca sale a internet:
    el motor lo consulta en cada turno y no debe esperar a la red.
    """
    ahora = time.time()
    if not _memoria:
        guardado = _leer(ruta())
        if guardado:
            _memoria.update(fecha=guardado['fecha'], proveedores=guardado['proveedores'], origen='cache')
        else:
            minimo = _leer(MINIMO) or {'fecha': 0, 'proveedores': {}}
            _memoria.update(fecha=minimo['fecha'], proveedores=minimo['proveedores'], origen='paquete')
    viejo = _memoria.get('origen') == 'paquete' or ahora - (_memoria.get('fecha') or 0) >= CADA
    if red and viejo and _toca_intentar(ahora):
        try:
            refrescar()
        except Exception:
            pass  # sin red o con un catálogo raro: se sigue con lo que hay
    return _memoria


def olvidar():
    """Descarta la copia en memoria (las pruebas, o tras cambiar de casa)."""
    _memoria.clear()


# --- Consultas ------------------------------------------------------------

def proveedor(ident, red=False):
    return cargar(red)['proveedores'].get(ident)


def ficha(ident, modelo, red=False):
    """La ficha de `modelo` en el proveedor `ident` del catálogo, o None."""
    datos = proveedor(ident, red)
    if not datos or not isinstance(modelo, str):
        return None
    modelos = datos['modelos']
    if modelo in modelos:
        return modelos[modelo]
    # Ollama nombra `gpt-oss:20b-cloud` o `glm-5.3-flash:cloud` lo que el catálogo llama sin sufijo.
    for sufijo in (':cloud', '-cloud'):
        if modelo.endswith(sufijo) and modelo[:-len(sufijo)] in modelos:
            return modelos[modelo[:-len(sufijo)]]
    base = modelo.split(':', 1)[0]
    return modelos.get(base)


def api_de(datos, ficha_modelo=None):
    """('chat' | 'responses' | None, URL base) con que se le habla a un modelo del catálogo."""
    paquete = (ficha_modelo or {}).get('paquete') or datos.get('paquete')
    url = (ficha_modelo or {}).get('api') or datos.get('api')
    return APIS.get(paquete), url


def compatible(datos):
    """¿Se le puede hablar en el formato de OpenAI, por HTTPS y fuera de este equipo?"""
    tipo, url = api_de(datos)
    return bool(tipo and url and url.startswith('https://') and not local(url))


def es_plan(ident):
    return bool(PLANES.search(ident))


def es_gratis(datos, ficha_modelo):
    """Coste de entrada y de salida 0, de nube, que llama herramientas y no está retirado."""
    precio = ficha_modelo.get('precio')
    if not precio or precio['entrada'] != 0 or precio['salida'] != 0:
        return False
    if not ficha_modelo.get('herramientas') or ficha_modelo.get('estado') == 'deprecated':
        return False
    # «auto» enruta a modelos de pago: su coste 0 en el catálogo no es un precio.
    if ficha_modelo['id'].rsplit('/', 1)[-1] == 'auto':
        return False
    if es_plan(datos['id']) or datos['id'] in SOLO_SU_CLIENTE:
        return False
    tipo, url = api_de(datos, ficha_modelo)
    return bool(tipo and url and url.startswith('https://') and not local(url))


def gratis_ahora(red=False):
    """[(proveedor, ficha)] de los modelos gratis ahora, por proveedor y nombre."""
    filas = []
    for datos in cargar(red)['proveedores'].values():
        if not compatible(datos):
            continue
        for ficha_modelo in datos['modelos'].values():
            if es_gratis(datos, ficha_modelo):
                filas.append((datos, ficha_modelo))
    filas.sort(key=lambda f: (f[0]['nombre'].lower(), f[1]['nombre'].lower()))
    return filas


def coste(precio, entrada, salida, cache=0):
    """USD de una llamada con los precios por millón del catálogo; None sin precio."""
    if not precio or not isinstance(entrada, int) or not isinstance(salida, int):
        return None
    cache = cache if isinstance(cache, int) and 0 <= cache <= entrada else 0
    lectura = precio.get('cache_lectura', precio['entrada'])
    return ((entrada - cache) * precio['entrada'] + cache * lectura + salida * precio['salida']) / 1_000_000


def resumen(ficha_modelo):
    """Lo que el selector enseña de un modelo: ventana, precio y si es gratis."""
    if not ficha_modelo:
        return {}
    datos = {}
    if ficha_modelo.get('ventana'):
        datos['ventana'] = ficha_modelo['ventana']
    if ficha_modelo.get('salida'):
        datos['salida_max'] = ficha_modelo['salida']
    if ficha_modelo.get('precio'):
        datos['precio'] = {k: ficha_modelo['precio'][k] for k in ('entrada', 'salida')}
    datos['razona'] = bool(ficha_modelo.get('razona'))
    if ficha_modelo.get('esfuerzos'):
        datos['esfuerzos'] = ficha_modelo['esfuerzos']
    datos['herramientas'] = bool(ficha_modelo.get('herramientas'))
    datos['vision'] = bool(ficha_modelo.get('vision'))
    return datos
