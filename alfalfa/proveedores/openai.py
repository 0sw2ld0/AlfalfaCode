"""OpenAI con la clave del propio usuario, por la Responses API.

El motor sigue hablando el formato de Ollama: este cliente traduce cada
`/api/chat` a `POST /v1/responses` y la respuesta de vuelta, y contesta
`/api/show` y `/api/tags` con su propio catálogo. Se usa la Responses API y no
Chat Completions porque en Chat Completions los modelos que razonan sólo llaman
herramientas con el razonamiento apagado.

Las peticiones van con `store: false`: OpenAI no guarda la conversación y cada
turno manda la historia entera, como con Ollama.
"""
from __future__ import annotations

import base64
import json
from urllib.parse import urlsplit

import httpx

from . import Proveedor
from .ollama import validar_url

URL = 'https://api.openai.com/v1'
# Lo que se ofrece en el selector, con la ventana real (developers.openai.com, 2026-11-09).
# Por encima de 272K tokens de entrada OpenAI cobra 2× la entrada y 1,5× la salida de toda
# la petición: el modo Ahorro de la sesión compacta antes (catalogo.tarifa).
CATALOGO = {
    'gpt-6-luna': {'nombre': 'GPT-6 Luna', 'ventana': 1_050_000, 'vision': True},
}
MODELO_POR_DEFECTO = 'gpt-6-luna'
# El esfuerzo de la sesión es el de OpenAI tal cual; `think: false` lo apaga.
ESFUERZOS = {'low': 'low', 'medium': 'medium', 'high': 'high', 'xhigh': 'xhigh', 'max': 'max'}


def _mime(dato):
    """Tipo de una imagen en base64 por su firma; PNG si no se reconoce."""
    try:
        cabeza = base64.b64decode(dato[:32] + '=' * (-len(dato[:32]) % 4))
    except (ValueError, TypeError):
        return 'image/png'
    if cabeza.startswith(b'\xff\xd8'):
        return 'image/jpeg'
    if cabeza.startswith(b'GIF8'):
        return 'image/gif'
    if cabeza[:4] == b'RIFF' and cabeza[8:12] == b'WEBP':
        return 'image/webp'
    return 'image/png'


def _texto(valor):
    return valor if isinstance(valor, str) else ('' if valor is None else json.dumps(valor, ensure_ascii=False))


def _argumentos(valor):
    if isinstance(valor, str):
        return valor
    return json.dumps(valor if valor is not None else {}, ensure_ascii=False)


def traducir_peticion(payload, esfuerzo=None, identificador=None):
    """El cuerpo de `/v1/responses` para un `/api/chat` de Ollama."""
    instrucciones, entrada = [], []
    # Las llamadas sin resultado todavía, en orden: el motor guarda cada
    # resultado como {'role': 'tool', 'tool_name': …} sin el id de la llamada.
    pendientes = []
    llamadas = 0

    def cerrar_pendientes():
        # La API rechaza una llamada sin su resultado: las que el motor no
        # ejecutó (sesión cortada, recuperación) se cierran sin resultado.
        while pendientes:
            ident, _ = pendientes.pop(0)
            entrada.append({'type': 'function_call_output', 'call_id': ident,
                            'output': '(sin resultado: la herramienta no llegó a ejecutarse)'})

    for mensaje in payload.get('messages') or []:
        rol = mensaje.get('role')
        contenido = _texto(mensaje.get('content'))
        if rol == 'tool':
            nombre = mensaje.get('tool_name') or mensaje.get('name')
            indice = next((i for i, (_, n) in enumerate(pendientes) if n == nombre), 0 if pendientes else None)
            if indice is None:
                entrada.append({'role': 'user', 'content': 'Resultado de ' + str(nombre or 'una herramienta')
                                + ':\n' + contenido})
                continue
            ident, _ = pendientes.pop(indice)
            entrada.append({'type': 'function_call_output', 'call_id': ident, 'output': contenido})
            continue
        cerrar_pendientes()
        if rol == 'system':
            if not entrada:
                instrucciones.append(contenido)
            else:
                entrada.append({'role': 'developer', 'content': contenido})
        elif rol == 'assistant':
            if contenido.strip():
                entrada.append({'role': 'assistant', 'content': contenido})
            for llamada in mensaje.get('tool_calls') or []:
                funcion = llamada.get('function') or {}
                llamadas += 1
                ident = llamada.get('id') or 'call_alfalfa_' + str(llamadas)
                nombre = str(funcion.get('name') or '')
                entrada.append({'type': 'function_call', 'call_id': ident, 'name': nombre,
                                'arguments': _argumentos(funcion.get('arguments'))})
                pendientes.append((ident, nombre))
        else:
            partes = [{'type': 'input_text', 'text': contenido}] if contenido else []
            for imagen in mensaje.get('images') or []:
                partes.append({'type': 'input_image', 'detail': 'auto',
                               'image_url': 'data:' + _mime(imagen) + ';base64,' + imagen})
            entrada.append({'role': 'user', 'content': partes or [{'type': 'input_text', 'text': ''}]})
    cerrar_pendientes()

    cuerpo = {'model': payload.get('model'), 'input': entrada, 'store': False}
    if instrucciones:
        cuerpo['instructions'] = '\n\n'.join(instrucciones)
    if payload.get('tools'):
        cuerpo['tools'] = [{'type': 'function', 'name': t['function']['name'],
                            'description': t['function'].get('description', ''),
                            'parameters': t['function'].get('parameters') or {'type': 'object', 'properties': {}},
                            'strict': False}
                           for t in payload['tools'] if isinstance(t, dict) and isinstance(t.get('function'), dict)]
    opciones = payload.get('options') or {}
    if isinstance(opciones.get('num_predict'), int) and opciones['num_predict'] > 0:
        cuerpo['max_output_tokens'] = opciones['num_predict']
    # Los modelos que razonan no admiten temperature ni top_p: no se mandan.
    if payload.get('think') is False:
        cuerpo['reasoning'] = {'effort': 'none'}
    else:
        cuerpo['reasoning'] = {'effort': ESFUERZOS.get(esfuerzo, 'medium'), 'summary': 'auto'}
    formato = payload.get('format')
    if formato == 'json':
        cuerpo['text'] = {'format': {'type': 'json_object'}}
    elif isinstance(formato, dict):
        cuerpo['text'] = {'format': {'type': 'json_schema', 'name': 'respuesta', 'schema': formato,
                                     'strict': False}}
    if identificador:
        cuerpo['safety_identifier'] = identificador
    return cuerpo


def traducir_respuesta(datos, modelo):
    """La respuesta de `/v1/responses` como la de `/api/chat` de Ollama."""
    textos, pensamiento, llamadas = [], [], []
    for item in datos.get('output') or []:
        tipo = item.get('type')
        if tipo == 'message':
            for parte in item.get('content') or []:
                if parte.get('type') == 'output_text':
                    textos.append(parte.get('text') or '')
                elif parte.get('type') == 'refusal':
                    textos.append(parte.get('refusal') or '')
        elif tipo == 'function_call':
            try:
                argumentos = json.loads(item.get('arguments') or '{}')
            except ValueError:
                argumentos = item.get('arguments') or ''
            llamadas.append({'id': item.get('call_id') or item.get('id'),
                             'function': {'name': item.get('name'), 'arguments': argumentos}})
        elif tipo == 'reasoning':
            for parte in item.get('summary') or []:
                if parte.get('text'):
                    pensamiento.append(parte['text'])
    mensaje = {'role': 'assistant', 'content': ''.join(textos)}
    if pensamiento:
        mensaje['thinking'] = '\n\n'.join(pensamiento)
    if llamadas:
        mensaje['tool_calls'] = llamadas
    estado = datos.get('status')
    motivo = (datos.get('incomplete_details') or {}).get('reason')
    respuesta = {'model': modelo, 'message': mensaje,
                 'done': estado in ('completed', 'incomplete'),
                 'done_reason': 'length' if motivo == 'max_output_tokens' else
                                ('stop' if estado == 'completed' else (motivo or estado))}
    uso = datos.get('usage') or {}
    if isinstance(uso.get('input_tokens'), int):
        respuesta['prompt_eval_count'] = uso['input_tokens']
    if isinstance(uso.get('output_tokens'), int):
        respuesta['eval_count'] = uso['output_tokens']
    detalle = uso.get('input_tokens_details') if isinstance(uso.get('input_tokens_details'), dict) else {}
    if isinstance(detalle.get('cached_tokens'), int):
        respuesta['alfalfa_cache'] = detalle['cached_tokens']
    return respuesta


def mostrar(modelo):
    """Lo que `/api/show` diría del modelo: capacidades y ventana.

    Lo propio (`CATALOGO`) manda; para otros modelos, lo que diga models.dev.
    """
    from .. import catalogo
    externa = catalogo.ficha('openai', modelo) or {}
    ficha = CATALOGO.get(modelo) or {'ventana': externa.get('ventana') or 128000,
                                     'vision': bool(externa.get('vision'))}
    capacidades = ['completion', 'tools', 'thinking'] + (['vision'] if ficha.get('vision') else [])
    info = {'capabilities': capacidades, 'remote_host': URL, 'remote_model': modelo,
            'details': {'family': 'gpt'}, 'model_info': {'openai.context_length': ficha['ventana']}}
    if externa:
        info['catalogo'] = externa
    return info


def _json(estado, valor, cabeceras=None):
    return httpx.Response(estado, content=json.dumps(valor, ensure_ascii=False).encode(),
                          headers={'content-type': 'application/json', **(cabeceras or {})})


class Transporte(httpx.AsyncBaseTransport):
    """Atiende el formato de Ollama y habla con OpenAI por detrás."""

    def __init__(self, url, clave, tarea=None, timeout=None, transporte=None):
        self.url = url
        self.tarea = tarea if tarea is not None else {}
        # `transporte` sólo lo dan las pruebas, para no salir a la red.
        self.http = httpx.AsyncClient(timeout=timeout, trust_env=False, transport=transporte,
                                      headers={'Authorization': 'Bearer ' + clave})

    async def handle_async_request(self, request):
        ruta = request.url.path
        try:
            pedido = json.loads(await request.aread() or b'{}')
        except ValueError:
            return _json(400, {'error': 'petición que no es JSON'})
        if ruta == '/api/show':
            return _json(200, mostrar(pedido.get('model')))
        if ruta == '/api/tags':
            return _json(200, {'models': [{'name': m} for m in CATALOGO]})
        if ruta != '/api/chat':
            return _json(404, {'error': 'ruta no disponible con OpenAI: ' + ruta})
        cuerpo = traducir_peticion(pedido, self.tarea.get('effort'))
        respuesta = await self.http.post(self.url + '/responses', json=cuerpo)
        try:
            datos = respuesta.json()
        except ValueError:
            datos = None
        cabeceras = {k: v for k, v in respuesta.headers.items() if k.lower() == 'retry-after'}
        if respuesta.status_code >= 400 or not isinstance(datos, dict):
            error = datos.get('error') if isinstance(datos, dict) else None
            return _json(respuesta.status_code if respuesta.status_code >= 400 else 502,
                         {'error': error or 'respuesta de OpenAI que no se entiende'}, cabeceras)
        if datos.get('status') == 'failed':
            return _json(502, {'error': datos.get('error') or 'OpenAI no completó la respuesta'})
        return _json(200, traducir_respuesta(datos, pedido.get('model')), cabeceras)

    async def aclose(self):
        await self.http.aclose()


class OpenAI(Proveedor):
    id = 'openai'
    nombre = 'OpenAI'
    modelo_por_defecto = MODELO_POR_DEFECTO

    def url(self):
        from . import configuracion
        valor = str(configuracion.de('openai').get('url') or URL).rstrip('/')
        # Como la de Ollama, pero la base puede llevar ruta (/v1).
        partes = urlsplit(valor)
        validar_url(partes.scheme + '://' + partes.netloc, 'proveedores.json', 'OpenAI')
        if partes.query or partes.fragment:
            raise ValueError('La URL de OpenAI (proveedores.json) no lleva consulta ni fragmento.')
        return valor

    def clave(self):
        from .. import ajustes
        return ajustes.openai_clave()

    def ficha(self, modelo):
        from .. import catalogo
        return catalogo.ficha('openai', modelo)

    def precio(self, modelo):
        return (self.ficha(modelo) or {}).get('precio')

    def configurado(self):
        return bool(self.clave())

    def secretos(self):
        return [self.clave()]

    def accion(self, clas, error_text):
        if clas.get('cause') == 'access_denied':
            return 'falta de acceso; revisa la clave de OpenAI en Proveedores; no se reintenta.'
        return None

    def cliente(self, timeout, tarea=None, al_cambiar_cuota=None):
        clave = self.clave()
        if not clave:
            raise ValueError('Falta la clave de OpenAI: guárdala con `alfalfa --guardar-clave openai`.')
        return httpx.AsyncClient(base_url='https://openai.alfalfa.invalid',
                                 transport=Transporte(self.url(), clave, tarea, timeout), timeout=timeout)

    def modelos(self):
        from . import configuracion
        extra = [m for m in configuracion.de('openai').get('modelos') or [] if isinstance(m, str)]
        nombres = list(CATALOGO) + [m for m in extra if m not in CATALOGO]
        from .. import catalogo
        filas = []
        for m in nombres:
            resumen = catalogo.resumen(self.ficha(m), self.id)
            if m in CATALOGO:
                resumen['ventana'] = CATALOGO[m]['ventana']
            filas.append({'name': 'openai/' + m, 'model': 'openai/' + m, 'proveedor': self.id,
                          'nombre': CATALOGO.get(m, {}).get('nombre', m), **resumen})
        return filas

    def estado(self):
        return dict(super().estado(), clave=self.configurado())
