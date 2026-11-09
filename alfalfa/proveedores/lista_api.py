"""La lista real de modelos de OpenAI y de Anthropic con la clave del usuario, con caché.

Con clave, `GET /v1/models` dice qué modelos puede usar esa cuenta. Se pregunta
con un tiempo de espera corto y se guarda en `$ALFALFA_HOME/catalogo/` con su
fecha, como el catálogo de models.dev: el motor la lee en cada turno sin salir a
la red, y la lista de modelos la renueva si tiene más de `CADA` segundos. Sin red
vale la última copia; si nunca se pudo bajar, el proveedor usa models.dev.

Lo que llega es un dato externo: se reduce a lo que AlfalfaCode usa y se valida.
De OpenAI sólo sirve el id (y la fecha de apagado); Anthropic además dice, por
modelo, la ventana, la salida máxima, si ve imágenes, qué esfuerzos admite, si
razona de forma adaptativa y su ciclo de vida.

La copia guardada lleva una huella (no la clave) de la URL y la clave con que se
bajó: con otra clave, la lista se vuelve a pedir en cuanto hay red.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone

import httpx

CADA = 6 * 3600
# Sin red o con la clave rechazada no se reintenta en cada consulta.
REINTENTO = 600
TIEMPO = httpx.Timeout(5, connect=3)
FORMATO = 1
MAX_MODELOS = 2000
MAX_PAGINAS = 5
NIVELES = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
ESTADOS = ('active', 'deprecated', 'retired')

# Las pruebas ponen aquí un transporte falso para no salir a la red.
_transporte = None
_memoria: dict = {}


def ruta(ident):
    from .. import catalogo
    return catalogo.casa() / 'catalogo' / ('modelos-' + ident + '.json')


def huella(url, clave):
    """Identifica la cuenta sin guardar la clave: 16 cifras de un SHA-256."""
    return hashlib.sha256((str(url) + '\n' + str(clave)).encode()).hexdigest()[:16]


# --- Validación -----------------------------------------------------------

def _texto(valor, maximo=120):
    if not isinstance(valor, str):
        return None
    valor = ''.join(c for c in valor if c.isprintable()).strip()
    return valor[:maximo] or None


def _entero(valor):
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
        return None
    return int(valor) if 1 <= valor <= 1e9 else None


def _fecha(valor):
    """«AAAA-MM-DD» de un instante de la API (segundos o RFC 3339), o None. Una fecha anterior a
    2000 es «no se sabe» (Anthropic pone la época cuando no conoce la fecha de salida)."""
    fecha = None
    if isinstance(valor, (int, float)) and not isinstance(valor, bool) and math.isfinite(valor) and 0 < valor < 1e11:
        fecha = datetime.fromtimestamp(valor, timezone.utc).strftime('%Y-%m-%d')
    texto = _texto(valor, 40)
    if texto and len(texto) >= 10 and texto[4] == '-' and texto[7] == '-' and texto[:4].isdigit() \
            and texto[5:7].isdigit() and texto[8:10].isdigit():
        fecha = texto[:10]
    return fecha if fecha and fecha >= '2000' else None


def limpiar(fila):
    """Una fila reducida y validada ({'id', …}), o None. Vale para lo bajado y para lo guardado."""
    from .. import catalogo
    if not isinstance(fila, dict) or not isinstance(fila.get('id'), str) \
            or not catalogo.ID_MODELO.fullmatch(fila['id']):
        return None
    limpia = {'id': fila['id']}
    for clave in ('nombre',):
        if _texto(fila.get(clave)):
            limpia[clave] = _texto(fila.get(clave))
    for clave in ('ventana', 'salida'):
        if _entero(fila.get(clave)):
            limpia[clave] = _entero(fila.get(clave))
    for clave in ('vision', 'adaptativo'):
        if isinstance(fila.get(clave), bool):
            limpia[clave] = fila[clave]
    if isinstance(fila.get('esfuerzos'), list):
        limpia['esfuerzos'] = [n for n in NIVELES if n in fila['esfuerzos']]
    if fila.get('estado') in ESTADOS:
        limpia['estado'] = fila['estado']
    if _fecha(fila.get('fecha')):
        limpia['fecha'] = _fecha(fila['fecha'])
    # La de apagado es siempre una fecha escrita («2026-01-01»): un número no se toma por una.
    if isinstance(fila.get('apagado'), str) and _fecha(fila['apagado']):
        limpia['apagado'] = _fecha(fila['apagado'])
    return limpia


def _soporta(valor):
    return valor.get('supported') if isinstance(valor, dict) and isinstance(valor.get('supported'), bool) else None


def de_openai(dato):
    """La fila reducida de un modelo de `GET /v1/models` de OpenAI (id, created, shutdown_date)."""
    if not isinstance(dato, dict):
        return None
    return limpiar({'id': dato.get('id'), 'fecha': dato.get('created'), 'apagado': dato.get('shutdown_date')})


def de_anthropic(dato):
    """La fila reducida de un modelo de `GET /v1/models` de Anthropic, con sus capacidades."""
    if not isinstance(dato, dict) or dato.get('type', 'model') != 'model':
        return None
    fila = {'id': dato.get('id'), 'nombre': dato.get('display_name'), 'ventana': dato.get('max_input_tokens'),
            'salida': dato.get('max_tokens'), 'estado': dato.get('lifecycle'), 'fecha': dato.get('created_at')}
    capacidades = dato.get('capabilities')
    if isinstance(capacidades, dict):
        fila['vision'] = _soporta(capacidades.get('image_input'))
        esfuerzo = capacidades.get('effort')
        if _soporta(esfuerzo) is False:
            fila['esfuerzos'] = []
        elif _soporta(esfuerzo):
            fila['esfuerzos'] = [n for n in NIVELES if _soporta(esfuerzo.get(n))]
        pensar = capacidades.get('thinking')
        tipos = pensar.get('types') if isinstance(pensar, dict) and isinstance(pensar.get('types'), dict) else {}
        fila['adaptativo'] = _soporta(tipos.get('adaptive'))
    return limpiar(fila)


# --- Descarga -------------------------------------------------------------

def _cliente(cabeceras):
    return httpx.Client(timeout=TIEMPO, trust_env=False, follow_redirects=False, transport=_transporte,
                        headers={'Accept': 'application/json', 'User-Agent': 'AlfalfaCode', **cabeceras})


def bajar_openai(url, clave):
    with _cliente({'Authorization': 'Bearer ' + clave}) as cliente:
        respuesta = cliente.get(url + '/models')
        respuesta.raise_for_status()
        datos = respuesta.json()
    filas = datos.get('data') if isinstance(datos, dict) else None
    if not isinstance(filas, list):
        raise ValueError('La lista de modelos de OpenAI no tiene la forma esperada.')
    return [f for f in (de_openai(d) for d in filas[:MAX_MODELOS]) if f]


def bajar_anthropic(url, clave, version):
    filas, despues = [], None
    with _cliente({'x-api-key': clave, 'anthropic-version': version}) as cliente:
        for _ in range(MAX_PAGINAS):
            parametros = {'limit': 1000, **({'after_id': despues} if despues else {})}
            respuesta = cliente.get(url + '/models', params=parametros)
            respuesta.raise_for_status()
            datos = respuesta.json()
            pagina = datos.get('data') if isinstance(datos, dict) else None
            if not isinstance(pagina, list):
                raise ValueError('La lista de modelos de Anthropic no tiene la forma esperada.')
            filas.extend(f for f in (de_anthropic(d) for d in pagina) if f)
            despues = datos.get('last_id') if datos.get('has_more') is True else None
            if not isinstance(despues, str) or not despues or len(filas) >= MAX_MODELOS:
                break
    return filas[:MAX_MODELOS]


# --- Caché ----------------------------------------------------------------

def _leer(ident):
    """{'fecha', 'huella', 'modelos'} guardado, o None."""
    if ident in _memoria:
        return _memoria[ident]
    camino = ruta(ident)
    try:
        if camino.is_symlink() or camino.stat().st_size > 4 * 1024 * 1024:
            return None
        valor = json.loads(camino.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(valor, dict) or valor.get('formato') != FORMATO or not isinstance(valor.get('modelos'), list):
        return None
    fecha = valor.get('fecha')
    guardado = {'fecha': fecha if isinstance(fecha, (int, float)) and math.isfinite(fecha) else 0,
                'huella': valor.get('huella') if isinstance(valor.get('huella'), str) else '',
                'modelos': [f for f in (limpiar(m) for m in valor['modelos'][:MAX_MODELOS]) if f]}
    _memoria[ident] = guardado
    return guardado


def _guardar(ident, sello, modelos, ahora):
    destino = ruta(ident)
    destino.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporal = destino.with_suffix('.tmp')
    temporal.write_text(json.dumps({'fecha': ahora, 'huella': sello, 'formato': FORMATO, 'modelos': modelos},
                                   ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    os.chmod(temporal, 0o600)
    os.replace(temporal, destino)
    _memoria[ident] = {'fecha': ahora, 'huella': sello, 'modelos': modelos}


def _toca_intentar(ident, ahora):
    try:
        return ahora - ruta(ident).with_suffix('.intento').stat().st_mtime >= REINTENTO
    except OSError:
        return True


def _marcar_intento(ident):
    try:
        camino = ruta(ident).with_suffix('.intento')
        camino.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        camino.touch()
    except OSError:
        pass


def modelos(ident, url, clave, bajar, red=True):
    """Las filas de la lista real del proveedor, o None si no hay ninguna copia de esta cuenta.

    `bajar()` pide la lista; sólo se llama con `red`, si la copia de esta cuenta
    falta o tiene más de `CADA` segundos, y como mucho una vez cada `REINTENTO`
    si falló. Un fallo nunca se propaga: se sigue con lo guardado.
    """
    sello = huella(url, clave)
    guardado = _leer(ident)
    propio = guardado if guardado and guardado['huella'] == sello else None
    ahora = time.time()
    viejo = propio is None or ahora - propio['fecha'] >= CADA
    if red and viejo and _toca_intentar(ident, ahora):
        _marcar_intento(ident)
        try:
            filas = bajar()
        except Exception:
            filas = None
        if filas:
            try:
                _guardar(ident, sello, filas, ahora)
            except OSError:
                _memoria[ident] = {'fecha': ahora, 'huella': sello, 'modelos': filas}
            propio = _memoria[ident]
    return list(propio['modelos']) if propio else None


def info(ident, modelo):
    """Lo que la última lista guardada dice de `modelo` (de cualquier cuenta: las capacidades
    de un modelo no dependen de la clave), sin salir a la red; None si no lo trae."""
    guardado = _leer(ident)
    if not guardado or not isinstance(modelo, str):
        return None
    return next((f for f in guardado['modelos'] if f['id'] == modelo), None)


def olvidar():
    """Descarta las copias en memoria (las pruebas, o tras cambiar de casa)."""
    _memoria.clear()
