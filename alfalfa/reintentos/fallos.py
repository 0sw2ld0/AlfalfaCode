"""Fallos del proveedor: limpieza del detalle y clasificación de errores de Ollama.

`_sin_credenciales` vivía en `native_runtime`; la evidencia la importaba dentro
de una función para esquivar el ciclo evidencia ↔ runtime. Aquí no depende del
runtime y ambos la importan con el mismo nombre (`app.native_runtime._sin_credenciales`
sigue siendo esta misma función).

La clasificación de fallos de Ollama (`classify_ollama_error`, `OllamaError`, sus
señales y el mensaje acotado) también salió del runtime en F6: no depende del
transporte. El runtime la reimporta con los mismos nombres. Lo que mira la
excepción de red (TLS, desconexión) sigue en el runtime porque usa httpx.

En F8d el «cuota agotada / sin capacidad» dejó de tener cinco detectores
sueltos (`studio.AGOTADO`, `studio.SIN_CAPACIDAD`, `politica._QUOTA`, las
señales de saturación de Ollama y el `status == 'rejected'` de los eventos de
Claude): todos leen el catálogo único de este módulo a través de su «lente».
"""
from __future__ import annotations

import re

_CREDENCIALES = re.compile(
    r'(?i)(authorization[^\n]*|bearer\s+\S+|x-api-key[^\n]*'
    r'|api[_-]?key["\']?\s*[:=]\s*["\']?[^\s,"\']+["\']?'
    r'|token["\']?\s*[:=]\s*["\']?[^\s,"\']+["\']?'
    r'|[?&](?:api_)?key=[^\s&"]+'
    r'|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)')


# Claves con forma de OpenAI («sk-…», «sk-proj-…»), también enmascaradas a medias.
_CLAVE_SUELTA = re.compile(r'\bsk-[A-Za-z0-9_*-]{8,}')


def _sin_credenciales(texto):
    """Quita cabeceras y credenciales que el proveedor pudo reflejar en el error.

    Se aplica antes de mostrar o persistir cualquier detalle del proveedor:
    un 401 que repite «Authorization: Bearer …» no deja el token en el
    mensaje ni en runtime_provider_error, y las formas JSON con la clave
    entre comillas («"api_key": "…"») también se cubren.
    """
    texto = _CREDENCIALES.sub('[credencial eliminada]', str(texto or ''))
    # Las claves de los proveedores configurados, aunque el error las repita
    # sin cabecera («Incorrect API key provided: sk-…»).
    try:
        from ..proveedores import secretos
        conocidas = secretos()
    except Exception:
        conocidas = []
    for clave in conocidas:
        texto = texto.replace(clave, '[credencial eliminada]')
    return _CLAVE_SUELTA.sub('[credencial eliminada]', texto)


# ── Clasificación de fallos de Ollama ────────────────────────────────────────
# Transitorios (se reintentan): 5xx, 429 por saturación, timeouts y fallos de
# red recuperables. Definitivos (no se reintentan): solicitud inválida
# (400/422), modelo inexistente (404 con señal explícita del proveedor) y
# falta de acceso (401/403 o señal del proveedor, incluso con HTTP 200).
# Lo desconocido no se reintenta ni se le inventa una causa.
TRANSITORIO, DEFINITIVO, DESCONOCIDO = 'transient', 'definitive', 'unknown'

# Una señal genérica aparece también en errores de otra causa: «unknown
# parameter: api_key» es una solicitud inválida y «quota configuration is
# invalid» pide corregir la petición. Sólo clasifican las señales explícitas
# del proveedor; las ambiguas no bastan solas. Con la falta de acceso rigen
# frases que afirman un rechazo o piden autenticarse: una palabra suelta
# inventa la causa («unknown parameter: credentials» es una solicitud
# inválida; «authentication backend temporarily unavailable» no dice que se
# haya denegado nada).
_SENALES_MODELO_INEXISTENTE = ('no such model', 'try pulling', 'pull it first', 'model does not exist')
_SENALES_FALTA_DE_ACCESO = ('unauthorized', 'forbidden', 'not authorized', 'access denied',
                            'permission denied', 'invalid api key', 'invalid_api_key',
                            'invalid credentials', 'credentials required', 'missing credentials',
                            'credentials rejected', 'authentication failed', 'authentication required',
                            'authentication expired', 'not authenticated', 'auth required',
                            'requires auth', 'session expired', 'sign in', 'no autorizado',
                            'requiere autenticación', 'autenticación fallida', 'autenticación requerida')
# Las de saturación son la lente «ollama» del clasificador único de más abajo.
_SENALES_SATURACION = ('rate limit', 'too many requests', 'overloaded', 'at capacity',
                       'usage limit', 'exceeded your quota', 'quota exceeded', 'saturad',
                       'cuota llena')
# ── Cuota agotada / sin capacidad: un catálogo, varias lentes ────────────────
# Cada señal se escribe una sola vez. Cada sitio que pregunta «¿se quedó sin
# cuota?» mira el catálogo con su lente: el subconjunto de señales que hoy
# reconoce, en el mismo orden (el orden importa: el descanso guarda como motivo
# el trozo que casó primero). Las diferencias entre lentes son a propósito:
# - «descanso» (studio.AGOTADO): pone a dormir 30 min al proveedor y modelo.
#   Cuenta «límite de uso», el aviso propio del arnés cuando Claude rechaza por
#   cuota, pero no «rate limit» ni «too many requests»: un frenazo por ráfaga no
#   vacía la cuota y dormir media hora al agente por eso sería de más.
# - «reintento» (native_retry_policy._QUOTA): clasifica el fallo como
#   quota_saturation para el presupuesto de reintentos; sí cuenta los frenazos
#   por ráfaga, porque esperar y reintentar es justo lo que piden.
# - «ollama» (las señales de saturación de classify_ollama_error): mira el texto
#   ya en minúsculas; el 429 no está porque el estado HTTP se clasifica aparte, y
#   suma las formas de Ollama Cloud («overloaded», «saturado», «cuota llena»).
# - «sin_herramienta» (studio.SIN_CAPACIDAD): el agente dice que le falta una
#   capacidad (generar imágenes); no es cuota y repetírselo no lo arregla.
_SENALES_DE_AGOTAMIENTO = {
    '429': ('cuota', r'\b429\b'),
    'at capacity': ('cuota', r'at capacity'),
    'usage limit': ('cuota', r'usage limit'),
    'límite de uso': ('cuota', r'límite de uso'),
    'rate limit': ('cuota', r'rate limit'),
    'too many requests': ('cuota', r'too many requests'),
    'quota exceeded': ('cuota', r'quota exceeded'),
    'exceeded your quota': ('cuota', r'exceeded your quota'),
    'overloaded': ('cuota', r'overloaded'),
    'saturad': ('cuota', r'saturad'),
    'cuota llena': ('cuota', r'cuota llena'),
    'sin herramienta': ('sin_herramienta',
                        r'no tengo (?:ninguna |una |acceso a (?:una |ninguna )?)?herramienta'
                        r'|no (?:puedo|es posible) generar im[aá]genes'
                        r'|sin herramienta de (?:generaci[oó]n de )?im[aá]genes'),
}
LENTES_DE_AGOTAMIENTO = {
    'descanso': ('429', 'at capacity', 'usage limit', 'límite de uso'),
    'reintento': ('429', 'rate limit', 'too many requests', 'usage limit', 'quota exceeded',
                  'exceeded your quota', 'at capacity'),
    'ollama': _SENALES_SATURACION,
    'sin_herramienta': ('sin herramienta',),
}
_PATRONES_DE_AGOTAMIENTO = {
    lente: re.compile('|'.join(_SENALES_DE_AGOTAMIENTO[n][1] for n in nombres), re.I)
    for lente, nombres in LENTES_DE_AGOTAMIENTO.items()
}


def patron_de_agotamiento(lente):
    """Expresión compilada de la lente; la usan quienes guardan el nombre viejo (AGOTADO, _QUOTA…)."""
    return _PATRONES_DE_AGOTAMIENTO[lente]


def senal_de_agotamiento(texto, lente):
    """El trozo de `texto` que delata cuota agotada (o falta de herramienta) según la lente, o None."""
    casa = _PATRONES_DE_AGOTAMIENTO[lente].search(str(texto or ''))
    return casa.group(0) if casa else None


def clasificar_agotamiento(texto, lente):
    """{'causa': 'cuota'|'sin_herramienta', 'senal': trozo} si la lente reconoce el texto, o None."""
    senal = senal_de_agotamiento(texto, lente)
    if senal is None:
        return None
    causa = _SENALES_DE_AGOTAMIENTO[LENTES_DE_AGOTAMIENTO[lente][0]][0]
    return {'causa': causa, 'senal': senal}


def cuota_rechazada(info):
    """Un rate_limit_event de Claude con status «rejected»: se quedó sin cuota.

    Es la única señal estructurada (no de texto) del catálogo: Claude Code la
    manda antes de que el texto del fallo diga nada. El aviso al usuario que
    dispara («Claude alcanzó un límite de uso.») es luego la señal «límite de
    uso» de la lente de descanso.
    """
    return isinstance(info, dict) and info.get('status') == 'rejected'


_SENALES_SOLICITUD_INVALIDA = ('invalid request', 'bad request', 'malformed', 'unknown parameter',
                               'unsupported parameter', 'does not support', 'solicitud inválida',
                               'fix your request')

_ACCIONES = {
    'saturation': 'saturación o cuota llena; espera y reintenta más tarde.',
    'server_error': 'fallo temporal del servidor; espera y reintenta.',
    'disconnected': 'el servidor cerró la conexión sin responder; espera y reintenta.',
    'timeout': 'no respondió a tiempo (timed out); espera y reintenta.',
    'network': 'fallo de conexión; revisa la red o el servicio de Ollama y reintenta.',
    'invalid_request': 'solicitud inválida; revisa los parámetros y el cuerpo que envía el arnés; no se reintenta.',
    'model_not_found': 'modelo inexistente o no descargado; selecciona otro modelo o descárgalo (ollama pull); no se '
                       'reintenta.',
    'access_denied': 'falta de acceso; inicia sesión en Ollama por el mecanismo oficial (Ajustes → Ollama) '
                     'o revisa la credencial configurada; no se reintenta.',
    'tls_error': 'el certificado TLS del servidor no valida (caducado o de otra autoridad); revisa el '
                 'certificado o su configuración; no se reintenta y no se desactiva la verificación.',
    'unknown': 'causa desconocida; no se reintenta automáticamente ni se asume una causa; revisa el estado del '
               'proveedor.',
}


def _es_modelo_inexistente(texto):
    """Señal explícita del proveedor de que el modelo no existe."""
    if any(s in texto for s in _SENALES_MODELO_INEXISTENTE):
        return True
    return ('model' in texto or 'modelo' in texto) and 'not found' in texto


def _senales_del_texto(texto):
    """Causas que las señales explícitas del texto sostienen; puede haber varias en conflicto."""
    senales = []
    if _es_modelo_inexistente(texto):
        senales.append('model_not_found')
    if any(s in texto for s in _SENALES_FALTA_DE_ACCESO):
        senales.append('access_denied')
    if senal_de_agotamiento(texto, 'ollama'):
        senales.append('saturation')
    if any(s in texto for s in _SENALES_SOLICITUD_INVALIDA):
        senales.append('invalid_request')
    return senales


def classify_ollama_error(status_code=None, error_text=None):
    """Clasifica un fallo de Ollama en información estructurada reutilizable.

    Devuelve un dict con kind ('transient'|'definitive'|'unknown'), cause,
    retry, status_code y action en lenguaje llano. Decide por el estado HTTP
    y las señales explícitas del proveedor; lo que no basta para explicar
    queda 'unknown', sin causa inventada y sin reintento. En 400/404/422 la
    señal única del texto manda sobre el estado; si hay varias en conflicto,
    unknown.
    """
    texto = str(error_text or '').strip().lower()
    senales = _senales_del_texto(texto) if texto else []

    def con(cause, kind, retry):
        return {'kind': kind, 'cause': cause, 'retry': retry,
                'status_code': status_code, 'action': _ACCIONES[cause]}

    def por_senal():
        """Clasificación por la señal del texto, si hay exactamente una."""
        if len(senales) == 1:
            causa = senales[0]
            return con(causa, TRANSITORIO if causa == 'saturation' else DEFINITIVO,
                       causa == 'saturation')
        return None

    if status_code is not None:
        if status_code == 429:
            return con('saturation', TRANSITORIO, True)
        if 500 <= status_code <= 599:
            return con('server_error', TRANSITORIO, True)
        if status_code in (401, 403):
            return con('access_denied', DEFINITIVO, False)
        if status_code in (400, 404, 422):
            senal = por_senal()
            if senal:
                return senal
            if len(senales) > 1:
                return con('unknown', DESCONOCIDO, False)
            if status_code in (400, 422):
                return con('invalid_request', DEFINITIVO, False)
    senal = por_senal()
    if senal:
        return senal
    return con('unknown', DESCONOCIDO, False)


class OllamaError(ValueError):
    """Fallo de Ollama clasificado, con información estructurada reutilizable.

    kind: 'transient' (esperar y reintentar), 'definitive' (actuar: elegir
    modelo o iniciar sesión por el mecanismo oficial) o 'unknown' (no se
    reintenta ni se inventa causa). El detalle del proveedor va recortado y
    nunca incluye el cuerpo completo ni cabeceras de autorización.
    """

    def __init__(self, message, kind=DESCONOCIDO, cause='unknown', status_code=None,
                 retry=False, action='', detail=None):
        super().__init__(message)
        self.kind = kind
        self.cause = cause
        self.status_code = status_code
        self.retry = retry
        self.action = action
        self.detail = detail

    def as_dict(self):
        """Información estructurada y acotada, segura para registrar o mostrar."""
        return {'kind': self.kind, 'cause': self.cause, 'retry': self.retry,
                'status_code': self.status_code, 'action': self.action, 'detail': self.detail}


_LIMITE_DETALLE = 300


def _recorte(texto, limite=_LIMITE_DETALLE):
    """Recorte de presentación: conserva el principio y el final del detalle.

    El proveedor suele poner la causa al final del mensaje («…; authentication
    failed»); recortar sólo por la derecha ocultaría esa señal, que es justo la
    que distingue un error definitivo de uno transitorio. El texto llega ya
    saneado, así que ninguna credencial puede quedar cortada a la mitad.
    """
    texto = str(texto or '')
    if len(texto) <= limite:
        return texto
    mitad = max(0, (limite - 1) // 2)
    return texto[:mitad] + '…' + texto[-mitad:]


def _mensaje_ollama(clas, error_text=None, intentos=None, nombre='Ollama'):
    """Mensaje comprensible a partir de la clasificación, con detalle acotado."""
    estado = clas.get('status_code')
    if estado is not None and estado >= 400:
        prefijo = nombre + ' HTTP ' + str(estado) + ': '
    elif estado is not None:
        prefijo = nombre + ' respondió con error (HTTP ' + str(estado) + '): '
    else:
        prefijo = nombre + ': '
    partes = [prefijo + clas['action']]
    if error_text:
        partes.append('Detalle del proveedor: ' + _recorte(_sin_credenciales(error_text)))
    if intentos and intentos > 1:
        partes.append('Se realizaron ' + str(intentos) + ' intentos de transporte; la sesión está guardada.')
    return ' '.join(partes)


def _ollama_failure(clas, error_text=None, intentos=None, nombre='Ollama'):
    """Excepción clasificada con mensaje comprensible y detalle acotado."""
    detalle = _recorte(_sin_credenciales(error_text)) or None if error_text else None
    return OllamaError(_mensaje_ollama(clas, detalle, intentos, nombre), kind=clas['kind'],
                       cause=clas['cause'], status_code=clas.get('status_code'),
                       retry=clas['retry'], action=clas['action'], detail=detalle)
