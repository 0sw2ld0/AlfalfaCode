"""Claude con la clave del propio usuario, por la API de mensajes de Anthropic.

El motor sigue hablando el formato de Ollama: este cliente traduce cada
`/api/chat` a `POST /v1/messages` (con streaming, para que los turnos largos no
se corten por tiempo) y junta la respuesta de vuelta; `/api/show` y `/api/tags`
los contesta con su propio catálogo. Habla HTTP directo con `httpx`, como el
proveedor de OpenAI, porque el motor necesita un transporte de `httpx` y no un
SDK.

Lo propio de Claude:

- Razonamiento adaptativo con el esfuerzo de la sesión (`output_config.effort`).
  `think: false` no lo apaga (Opus 5.5 no deja): baja el esfuerzo a `low`.
- Los bloques de razonamiento vuelven tal cual, con su firma, junto con los
  resultados de las herramientas. Viajan en el mensaje como `alfalfa_bloques`,
  sólo en memoria. Como el motor recorta salidas viejas y compacta, cada
  petición pide `drop_block`: si la historia cambió, la API descarta los
  bloques que ya no valen en vez de rechazar la petición.
- Caché de prompts: un punto fijo en la última herramienta, otro al final del
  prompt del sistema y la caché automática para la conversación que crece.
- El uso trae la entrada sin caché, la escrita y la leída; el coste se calcula
  con cada una a su precio (`alfalfa_cache_escritura`).
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import httpx

from . import Proveedor
from .ollama import validar_url
from .openai import _json, _mime, _texto

URL = 'https://api.anthropic.com/v1'
VERSION_API = '2023-06-01'
BETA_BLOQUES = 'thinking-binding-controls-2026-08-01'
# Lo que se ofrece en el selector, con la ventana real. Haiku 5.5 cobra cinco
# veces más toda la petición si el prompt pasa de 100K tokens (`tramo`): el modo
# Ahorro de la sesión compacta antes, como con GPT-6 Luna. Los precios son los de la
# documentación de Anthropic (noviembre de 2026); manda el catálogo de
# models.dev cuando trae el modelo.
CATALOGO = {
    'claude-haiku-5-5': {
        'nombre': 'Claude Haiku 5.5', 'ventana': 1000000, 'salida': 128000, 'vision': True,
        'precio': {'entrada': 0.10, 'salida': 0.50, 'cache_lectura': 0.01, 'cache_escritura': 0.125,
                   'tramo': {'desde': 100000, 'entrada': 0.50, 'salida': 2.50, 'cache_lectura': 0.05,
                             'cache_escritura': 0.625}}},
    'claude-sonnet-5-5': {
        'nombre': 'Claude Sonnet 5.5', 'ventana': 1000000, 'salida': 128000, 'vision': True,
        'precio': {'entrada': 2.0, 'salida': 10.0, 'cache_lectura': 0.20, 'cache_escritura': 2.50}},
    'claude-opus-5-5': {
        'nombre': 'Claude Opus 5.5', 'ventana': 1000000, 'salida': 128000, 'vision': True,
        'precio': {'entrada': 4.0, 'salida': 20.0, 'cache_lectura': 0.20, 'cache_escritura': 5.0}},
}
MODELO_POR_DEFECTO = 'claude-sonnet-5-5'
ESFUERZOS = ('low', 'medium', 'high', 'xhigh', 'max')
SALIDA_POR_DEFECTO = 32000
# Razonamiento adaptativo con esfuerzo y la comprobación de historia (`block_binding`).
_FAMILIA_5 = re.compile(r'^claude-(fable|mythos|opus|sonnet|haiku)-5(?:-|$)')
# Adaptativo también, pero sin `xhigh` ni la comprobación de historia.
_FAMILIA_4 = ('claude-opus-4-6', 'claude-opus-4-7', 'claude-opus-4-8', 'claude-sonnet-4-6')
# Los que aceptan mensajes `system` a mitad de la conversación (Sonnet 5 no).
_SISTEMA_EN_MEDIO = re.compile(r'^claude-(fable-5|mythos-5|opus-5|opus-4-8|sonnet-5-5|haiku-5-5)(?:-|$)')
# Lo que las salidas estructuradas no admiten en un esquema: se quita (el motor valida después).
_SIN_SOPORTE = ('minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf', 'minLength',
                'maxLength', 'minItems', 'maxItems', 'uniqueItems', 'minProperties', 'maxProperties')
_ID_VALIDO = re.compile(r'^[A-Za-z0-9_-]{1,200}$')
SOLO_JSON = 'Responde sólo con un objeto JSON válido, sin texto antes ni después ni bloques de código.'


def adaptativo(modelo):
    return bool(_FAMILIA_5.match(str(modelo or ''))) or modelo in _FAMILIA_4


def esfuerzo_para(modelo, esfuerzo, pensar):
    """El esfuerzo que se manda, o None si el modelo no lo admite."""
    if not adaptativo(modelo):
        return None
    nivel = 'low' if pensar is False else (esfuerzo if esfuerzo in ESFUERZOS else 'medium')
    # `xhigh` llegó con Opus 4.7: los 4.6 se quedan en `high`.
    return 'high' if nivel == 'xhigh' and modelo in ('claude-opus-4-6', 'claude-sonnet-4-6') else nivel


def _id(valor, numero):
    texto = str(valor or '')
    if _ID_VALIDO.fullmatch(texto):
        return texto
    limpio = re.sub(r'[^A-Za-z0-9_-]', '_', texto)[:180]
    return limpio or 'toolu_alfalfa_' + str(numero)


def _entrada(valor):
    """Los argumentos de una llamada como objeto, que es lo que la API acepta."""
    if isinstance(valor, dict):
        return valor
    if isinstance(valor, str):
        try:
            datos = json.loads(valor or '{}')
        except ValueError:
            return {'argumentos': valor}
        return datos if isinstance(datos, dict) else {'argumentos': datos}
    return {} if valor is None else {'argumentos': valor}


def _imagenes(lista):
    return [{'type': 'image', 'source': {'type': 'base64', 'media_type': _mime(i), 'data': i}}
            for i in lista or [] if isinstance(i, str) and i]


def _texto_bloque(texto):
    return {'type': 'text', 'text': texto}


def _bloques_guardados(mensaje):
    """Los bloques de una respuesta de Claude, rehechos con su orden y su razonamiento firmado; None si no cuadran.

    `alfalfa_bloques` sólo guarda el razonamiento entero; el texto y las llamadas
    son marcas (`{'type': 'text', 'largo': n}`, `{'type': 'tool_use', 'id': …}`) que
    se rellenan con `content` y `tool_calls`. Así el contexto no lleva dos veces lo
    mismo (un `write_file` grande duplicaba el tamaño medido). Si el motor cambió el
    texto o las llamadas, no cuadra y se rehace sin el razonamiento.
    """
    bloques = mensaje.get('alfalfa_bloques')
    if not isinstance(bloques, list) or not bloques:
        return None
    contenido = mensaje.get('content') if isinstance(mensaje.get('content'), str) else ''
    llamadas = {c.get('id'): c.get('function') or {} for c in mensaje.get('tool_calls') or [] if isinstance(c, dict)}
    validos, posicion = [], 0
    for bloque in bloques:
        if not isinstance(bloque, dict):
            return None
        tipo = bloque.get('type')
        if tipo == 'text':
            texto = bloque.get('text')
            if not isinstance(texto, str):
                largo = bloque.get('largo')
                if not isinstance(largo, int) or largo < 0:
                    return None
                texto = contenido[posicion:posicion + largo]
            if contenido[posicion:posicion + len(texto)] != texto:
                return None
            posicion += len(texto)
            if texto:
                validos.append({'type': 'text', 'text': texto})
        elif tipo == 'thinking' and bloque.get('signature'):
            # Sin firma no se puede devolver: se queda fuera.
            validos.append(bloque)
        elif tipo == 'redacted_thinking' and bloque.get('data'):
            validos.append(bloque)
        elif tipo == 'tool_use':
            funcion = llamadas.get(bloque.get('id'))
            if funcion is None:
                return None
            entrada = bloque.get('input') if isinstance(bloque.get('input'), dict) \
                else _entrada(funcion.get('arguments'))
            validos.append({'type': 'tool_use', 'id': bloque['id'],
                            'name': str(bloque.get('name') or funcion.get('name') or ''), 'input': entrada})
    ids = [b['id'] for b in validos if b['type'] == 'tool_use']
    pedidos = [c.get('id') for c in mensaje.get('tool_calls') or [] if isinstance(c, dict)]
    return validos if validos and ids == pedidos and posicion == len(contenido) else None


def _llamada_en_texto(llamada):
    funcion = llamada.get('function') or {}
    argumentos = funcion.get('arguments')
    return ('[Llamada a ' + str(funcion.get('name') or 'una herramienta') + ': '
            + (argumentos if isinstance(argumentos, str) else json.dumps(argumentos, ensure_ascii=False)) + ']')


def esquema_estricto(esquema):
    """El esquema apto para `output_config.format`, o None si no se puede adaptar."""
    if isinstance(esquema, list):
        partes = [esquema_estricto(e) for e in esquema]
        return None if any(p is None for p in partes) else partes
    if not isinstance(esquema, dict):
        return esquema
    limpio = {}
    for clave, valor in esquema.items():
        if clave in _SIN_SOPORTE:
            continue
        if clave in ('properties', '$defs', 'definitions') and isinstance(valor, dict):
            hijos = {k: esquema_estricto(v) for k, v in valor.items()}
            if any(v is None for v in hijos.values()):
                return None
            limpio[clave] = hijos
        elif clave in ('items', 'anyOf', 'allOf', 'oneOf', 'not'):
            hijo = esquema_estricto(valor)
            if hijo is None:
                return None
            limpio[clave] = hijo
        else:
            limpio[clave] = valor
    if limpio.get('type') == 'object' or 'properties' in limpio:
        if limpio.get('additionalProperties', False) is not False:
            return None
        limpio['additionalProperties'] = False
    return limpio


def traducir_peticion(payload, esfuerzo=None, salida_max=None):
    """(cuerpo de `/v1/messages`, betas) para un `/api/chat` de Ollama."""
    modelo = str(payload.get('model') or '')
    sistema, mensajes, pendientes = [], [], []
    llamadas = 0

    def poner(rol, bloques):
        if not bloques:
            return
        if mensajes and mensajes[-1]['role'] == rol and rol != 'system':
            mensajes[-1]['content'].extend(bloques)
        else:
            mensajes.append({'role': rol, 'content': list(bloques)})

    def cerrar_pendientes():
        # La API rechaza una llamada sin su resultado: las que el motor no
        # ejecutó (sesión cortada, recuperación) se cierran sin resultado.
        resultados = []
        while pendientes:
            ident, _ = pendientes.pop(0)
            resultados.append({'type': 'tool_result', 'tool_use_id': ident, 'is_error': True,
                               'content': '(sin resultado: la herramienta no llegó a ejecutarse)'})
        poner('user', resultados)

    # Sin herramientas declaradas (la compactación, una conversación) la API no acepta
    # bloques `tool_use` ni `tool_result`: las llamadas y sus resultados van como texto.
    con_herramientas = bool(payload.get('tools'))
    for mensaje in payload.get('messages') or []:
        rol = mensaje.get('role')
        contenido = _texto(mensaje.get('content'))
        if rol == 'tool':
            nombre = mensaje.get('tool_name') or mensaje.get('name')
            indice = next((i for i, (_, n) in enumerate(pendientes) if n == nombre), 0 if pendientes else None)
            imagenes = _imagenes(mensaje.get('images'))
            fallo = mensaje.get('alfalfa_fallo') is True
            if indice is None:
                poner('user', [_texto_bloque('Resultado de ' + str(nombre or 'una herramienta')
                                             + (' (falló)' if fallo else '') + ':\n'
                                             + (contenido or '(vacío)'))] + imagenes)
                continue
            ident, _ = pendientes.pop(indice)
            cuerpo = ([_texto_bloque(contenido)] if contenido else []) + imagenes
            resultado = {'type': 'tool_result', 'tool_use_id': ident, 'content': cuerpo or '(sin salida)'}
            if fallo:
                resultado['is_error'] = True
            poner('user', [resultado])
            continue
        cerrar_pendientes()
        if rol == 'system':
            if not mensajes:
                if contenido:
                    sistema.append(_texto_bloque(contenido))
            elif contenido:
                if mensajes[-1]['role'] == 'system':
                    mensajes[-1]['content'].append(_texto_bloque(contenido))
                else:
                    mensajes.append({'role': 'system', 'content': [_texto_bloque(contenido)]})
        elif rol == 'assistant' and not con_herramientas and mensaje.get('tool_calls'):
            partes = ([contenido] if contenido.strip() else []) + [_llamada_en_texto(c) for c in
                                                                   mensaje.get('tool_calls') or []
                                                                   if isinstance(c, dict)]
            poner('assistant', [_texto_bloque('\n'.join(partes))] if partes else [])
        elif rol == 'assistant':
            bloques = _bloques_guardados(mensaje)
            if bloques is None:
                bloques = [_texto_bloque(contenido)] if contenido.strip() else []
                for llamada in mensaje.get('tool_calls') or []:
                    funcion = llamada.get('function') or {}
                    llamadas += 1
                    bloques.append({'type': 'tool_use', 'id': _id(llamada.get('id'), llamadas),
                                    'name': str(funcion.get('name') or ''),
                                    'input': _entrada(funcion.get('arguments'))})
            for bloque in bloques:
                if bloque['type'] == 'tool_use':
                    pendientes.append((bloque['id'], bloque['name']))
            poner('assistant', bloques)
        else:
            poner('user', _imagenes(mensaje.get('images')) + [_texto_bloque(contenido or '(sin texto)')])
    cerrar_pendientes()
    mensajes = _colocar_sistema(mensajes, bool(_SISTEMA_EN_MEDIO.match(modelo)))
    if not mensajes or mensajes[0]['role'] != 'user':
        mensajes.insert(0, {'role': 'user', 'content': [_texto_bloque('(continúa)')]})

    formato = payload.get('format')
    esquema = esquema_estricto(formato) if isinstance(formato, dict) else None
    if formato == 'json' or (isinstance(formato, dict) and esquema is None):
        aviso = SOLO_JSON + ('' if formato == 'json' else
                             ' Sigue este esquema JSON: ' + json.dumps(formato, ensure_ascii=False))
        ultimo = next(m for m in reversed(mensajes) if m['role'] == 'user')
        ultimo['content'].append(_texto_bloque(aviso))

    opciones = payload.get('options') or {}
    tope = opciones.get('num_predict') if isinstance(opciones.get('num_predict'), int) \
        and opciones['num_predict'] > 0 else SALIDA_POR_DEFECTO
    if salida_max:
        tope = min(tope, salida_max)
    cuerpo = {'model': modelo, 'max_tokens': tope, 'messages': mensajes, 'stream': True,
              # La caché automática sigue a la conversación según crece.
              'cache_control': {'type': 'ephemeral'}}
    if sistema:
        sistema[-1]['cache_control'] = {'type': 'ephemeral'}
        cuerpo['system'] = sistema
    if payload.get('tools'):
        herramientas = [{'name': t['function']['name'], 'description': t['function'].get('description', ''),
                         'input_schema': t['function'].get('parameters') or {'type': 'object', 'properties': {}}}
                        for t in payload['tools'] if isinstance(t, dict) and isinstance(t.get('function'), dict)]
        if herramientas:
            herramientas[-1]['cache_control'] = {'type': 'ephemeral'}
            cuerpo['tools'] = herramientas
    betas = []
    nivel = esfuerzo_para(modelo, esfuerzo, payload.get('think'))
    if nivel:
        # Los modelos que razonan no admiten temperature, top_p ni top_k: no se mandan.
        cuerpo['thinking'] = {'type': 'adaptive', 'display': 'summarized'}
        cuerpo['output_config'] = {'effort': nivel}
        if _FAMILIA_5.match(modelo):
            cuerpo['thinking']['block_binding'] = {'prefix_mismatch_behavior': 'drop_block'}
            betas.append(BETA_BLOQUES)
    if esquema is not None:
        cuerpo.setdefault('output_config', {})['format'] = {'type': 'json_schema', 'schema': esquema}
    return cuerpo, betas


def _colocar_sistema(mensajes, admite):
    """Los `system` a mitad sólo donde la API los acepta; si no, texto del turno de usuario.

    Tienen que ir tras un mensaje del usuario y ser el último o ir antes de una
    respuesta. Los modelos que no los admiten los reciben como aviso del sistema
    dentro del turno del usuario.
    """
    salida = []
    for i, mensaje in enumerate(mensajes):
        if mensaje['role'] == 'system':
            siguiente = mensajes[i + 1]['role'] if i + 1 < len(mensajes) else None
            texto = '\n\n'.join(b['text'] for b in mensaje['content'])
            if admite and salida and salida[-1]['role'] == 'user' and siguiente in (None, 'assistant'):
                salida.append({'role': 'system', 'content': texto})
                continue
            mensaje = {'role': 'user', 'content': [_texto_bloque('<aviso-del-sistema>\n' + texto
                                                                 + '\n</aviso-del-sistema>')]}
        if salida and salida[-1]['role'] == mensaje['role'] == 'user':
            salida[-1]['content'].extend(mensaje['content'])
        else:
            salida.append(mensaje)
    return salida


def _sin_cercas(texto):
    limpio = texto.strip()
    if limpio.startswith('```'):
        limpio = re.sub(r'^```[A-Za-z0-9]*\s*', '', limpio)
        limpio = re.sub(r'\s*```$', '', limpio)
    return limpio


def traducir_respuesta(datos, modelo, con_formato=False):
    """El mensaje de Claude (ya juntado del stream) como la respuesta de `/api/chat` de Ollama."""
    textos, pensamiento, llamadas, originales = [], [], [], []
    for bloque in datos.get('content') or []:
        if not isinstance(bloque, dict):
            continue
        tipo = bloque.get('type')
        if tipo == 'text':
            if bloque.get('text'):
                textos.append(bloque['text'])
                # Sólo una marca: el texto ya va en `content` (ver _bloques_guardados).
                originales.append({'type': 'text', 'largo': len(bloque['text'])})
        elif tipo == 'thinking':
            if bloque.get('thinking'):
                pensamiento.append(bloque['thinking'])
            originales.append({'type': 'thinking', 'thinking': bloque.get('thinking') or '',
                               'signature': bloque.get('signature') or ''})
        elif tipo == 'redacted_thinking':
            originales.append({'type': 'redacted_thinking', 'data': bloque.get('data') or ''})
        elif tipo == 'tool_use':
            entrada = bloque.get('input')
            llamadas.append({'id': bloque.get('id'), 'function': {'name': bloque.get('name'),
                                                                  'arguments': entrada}})
            originales.append({'type': 'tool_use', 'id': bloque.get('id')})
    motivo = datos.get('stop_reason')
    contenido = ''.join(textos)
    if motivo == 'refusal':
        # Un rechazo de los filtros de seguridad: nada que ejecutar, y se dice por qué.
        llamadas = []
        categoria = (datos.get('stop_details') or {}).get('category')
        contenido = (contenido + '\n\n' if contenido else '') + (
            'Claude no ha seguido con esta petición: sus filtros de seguridad la rechazaron'
            + (' (' + str(categoria) + ')' if categoria else '') + '.')
        originales = []
    elif con_formato:
        contenido = _sin_cercas(contenido)
    mensaje = {'role': 'assistant', 'content': contenido}
    if pensamiento:
        mensaje['thinking'] = '\n\n'.join(pensamiento)
    if llamadas:
        mensaje['tool_calls'] = llamadas
    if originales:
        mensaje['alfalfa_bloques'] = originales
    respuesta = {'model': modelo, 'message': mensaje, 'done': True,
                 'done_reason': 'length' if motivo in ('max_tokens', 'model_context_window_exceeded') else 'stop'}
    uso = datos.get('usage') if isinstance(datos.get('usage'), dict) else {}
    partes = [uso.get(k) for k in ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')]
    if isinstance(partes[0], int):
        # `input_tokens` es sólo lo que no salió de la caché: el prompt entero es la suma.
        respuesta['prompt_eval_count'] = sum(p for p in partes if isinstance(p, int))
        respuesta['alfalfa_cache'] = partes[2] if isinstance(partes[2], int) else 0
        respuesta['alfalfa_cache_escritura'] = partes[1] if isinstance(partes[1], int) else 0
    if isinstance(uso.get('output_tokens'), int):
        respuesta['eval_count'] = uso['output_tokens']
    descartes = [t for t in datos.get('input_transformations') or []
                 if isinstance(t, dict) and t.get('type') == 'thinking_dropped']
    if descartes:
        # La historia cambió (recorte o compactación): la API no usó ese razonamiento.
        motivos = sorted({str(t.get('reason') or '?') for t in descartes})
        respuesta['alfalfa_aviso'] = ('Anthropic descartó ' + str(len(descartes)) + ' bloque(s) de razonamiento '
                                      'anterior (' + ', '.join(motivos) + ').')
    return respuesta


# El estado HTTP de cada tipo de error de Anthropic, para un error que llega a mitad del stream.
ESTADOS_DE_ERROR = {'invalid_request_error': 400, 'authentication_error': 401, 'permission_error': 403,
                    'not_found_error': 404, 'request_too_large': 413, 'rate_limit_error': 429,
                    'api_error': 500, 'overloaded_error': 529}


class StreamCortado(ValueError):
    """El stream terminó sin `message_stop` o trajo un evento de error."""

    def __init__(self, texto, estado=502):
        super().__init__(texto)
        self.estado = estado


async def juntar(lineas):
    """Junta los eventos SSE de `/v1/messages` en un mensaje como el de la respuesta sin stream."""
    mensaje, bloques, parciales = None, {}, {}
    evento, datos = None, []

    async def procesar(nombre, crudo):
        nonlocal mensaje
        try:
            valor = json.loads(crudo)
        except ValueError:
            return False
        tipo = valor.get('type') or nombre
        if tipo == 'message_start':
            mensaje = dict(valor.get('message') or {})
            mensaje['content'] = []
        elif tipo == 'content_block_start':
            bloques[valor.get('index')] = dict(valor.get('content_block') or {})
        elif tipo == 'content_block_delta':
            bloque = bloques.setdefault(valor.get('index'), {})
            delta = valor.get('delta') or {}
            clase = delta.get('type')
            if clase == 'text_delta':
                bloque['text'] = (bloque.get('text') or '') + (delta.get('text') or '')
            elif clase == 'thinking_delta':
                bloque['thinking'] = (bloque.get('thinking') or '') + (delta.get('thinking') or '')
            elif clase == 'signature_delta':
                bloque['signature'] = (bloque.get('signature') or '') + (delta.get('signature') or '')
            elif clase == 'input_json_delta':
                parciales[valor.get('index')] = parciales.get(valor.get('index'), '') + (delta.get('partial_json')
                                                                                          or '')
        elif tipo == 'content_block_stop':
            indice = valor.get('index')
            bloque = bloques.get(indice, {})
            if bloque.get('type') == 'tool_use' and indice in parciales:
                try:
                    bloque['input'] = json.loads(parciales[indice] or '{}')
                except ValueError:
                    # Entrada cortada: el motor no ejecuta llamadas de una respuesta incompleta.
                    bloque['input'] = parciales[indice]
        elif tipo == 'message_delta' and mensaje is not None:
            delta = valor.get('delta') or {}
            for clave in ('stop_reason', 'stop_sequence', 'stop_details'):
                if clave in delta:
                    mensaje[clave] = delta[clave]
            if isinstance(valor.get('usage'), dict):
                mensaje['usage'] = {**(mensaje.get('usage') or {}),
                                    **{k: v for k, v in valor['usage'].items() if v is not None}}
        elif tipo == 'error':
            error = valor.get('error') or {}
            estado = ESTADOS_DE_ERROR.get(error.get('type'), 500)
            raise StreamCortado(error.get('message') or 'error de Anthropic a mitad de la respuesta', estado)
        elif tipo == 'message_stop':
            return True
        return False

    terminado = False
    async for linea in lineas:
        if linea == '':
            if datos:
                terminado = await procesar(evento, '\n'.join(datos)) or terminado
            evento, datos = None, []
        elif linea.startswith('event:'):
            evento = linea[6:].strip()
        elif linea.startswith('data:'):
            datos.append(linea[5:].lstrip())
    if datos:
        terminado = await procesar(evento, '\n'.join(datos)) or terminado
    if mensaje is None or not terminado:
        raise StreamCortado('la respuesta de Anthropic se cortó antes de terminar')
    mensaje['content'] = [bloques[i] for i in sorted(k for k in bloques if isinstance(k, int))]
    return mensaje


def mostrar(modelo):
    """Lo que `/api/show` diría del modelo: capacidades y ventana.

    Lo propio (`CATALOGO`) manda; para otros modelos, lo que diga models.dev.
    """
    from .. import catalogo
    externa = catalogo.ficha('anthropic', modelo) or {}
    propia = CATALOGO.get(modelo) or {}
    ventana = propia.get('ventana') or externa.get('ventana') or 200000
    vision = propia.get('vision', bool(externa.get('vision')))
    capacidades = ['completion', 'tools'] + (['thinking'] if adaptativo(modelo) else []) \
        + (['vision'] if vision else [])
    info = {'capabilities': capacidades, 'remote_host': URL, 'remote_model': modelo,
            'details': {'family': 'claude'}, 'model_info': {'anthropic.context_length': ventana}}
    ficha = dict(externa)
    if propia.get('salida') and not ficha.get('salida'):
        ficha['salida'] = propia['salida']
    if ficha:
        info['catalogo'] = ficha
    return info


def _error(datos):
    error = datos.get('error') if isinstance(datos, dict) else None
    if isinstance(error, dict):
        return error.get('message') or error.get('type')
    return error


class Transporte(httpx.AsyncBaseTransport):
    """Atiende el formato de Ollama y habla con la API de Anthropic por detrás."""

    def __init__(self, proveedor, url, clave, tarea=None, timeout=None, transporte=None):
        self.proveedor = proveedor
        self.url = url
        self.tarea = tarea if tarea is not None else {}
        # `transporte` sólo lo dan las pruebas, para no salir a la red.
        self.http = httpx.AsyncClient(timeout=timeout, trust_env=False, transport=transporte,
                                      headers={'x-api-key': clave, 'anthropic-version': VERSION_API})

    async def handle_async_request(self, request):
        ruta = request.url.path
        try:
            pedido = json.loads(await request.aread() or b'{}')
        except ValueError:
            return _json(400, {'error': 'petición que no es JSON'})
        modelo = pedido.get('model')
        if ruta == '/api/show':
            return _json(200, mostrar(modelo))
        if ruta == '/api/tags':
            return _json(200, {'models': [{'name': f['name'].split('/', 1)[1]} for f in self.proveedor.modelos()]})
        if ruta != '/api/chat':
            return _json(404, {'error': 'ruta no disponible con Anthropic: ' + ruta})
        salida = (mostrar(modelo).get('catalogo') or {}).get('salida')
        cuerpo, betas = traducir_peticion(pedido, self.tarea.get('effort'), salida)
        cabeceras = {'anthropic-beta': ','.join(betas)} if betas else {}
        async with self.http.stream('POST', self.url + '/messages', json=cuerpo, headers=cabeceras) as respuesta:
            reintento = {k: v for k, v in respuesta.headers.items() if k.lower() == 'retry-after'}
            if respuesta.status_code >= 400:
                try:
                    datos = json.loads(await respuesta.aread())
                except ValueError:
                    datos = None
                return _json(respuesta.status_code,
                             {'error': _error(datos) or 'respuesta de Anthropic que no se entiende'}, reintento)
            try:
                datos = await juntar(respuesta.aiter_lines())
            except StreamCortado as exc:
                return _json(exc.estado, {'error': str(exc)}, reintento)
        return _json(200, traducir_respuesta(datos, modelo, con_formato=bool(pedido.get('format'))), reintento)

    async def aclose(self):
        await self.http.aclose()


class Anthropic(Proveedor):
    id = 'anthropic'
    nombre = 'Anthropic'
    modelo_por_defecto = MODELO_POR_DEFECTO

    def url(self):
        from . import configuracion
        valor = str(configuracion.de('anthropic').get('url') or URL).rstrip('/')
        # Como la de Ollama, pero la base puede llevar ruta (/v1).
        partes = urlsplit(valor)
        validar_url(partes.scheme + '://' + partes.netloc, 'proveedores.json', 'Anthropic')
        if partes.query or partes.fragment:
            raise ValueError('La URL de Anthropic (proveedores.json) no lleva consulta ni fragmento.')
        return valor

    def clave(self):
        from .. import ajustes
        return ajustes.anthropic_clave()

    def ficha(self, modelo):
        from .. import catalogo
        return catalogo.ficha('anthropic', modelo)

    def precio(self, modelo):
        """El propio para los modelos de `CATALOGO`; para los demás, el del catálogo de models.dev.

        El propio sale de la documentación de Anthropic y lleva el tramo largo de
        Haiku 5.5; models.dev no siempre coincide (la caché leída de Sonnet 5.5).
        """
        return (CATALOGO.get(modelo) or {}).get('precio') or (self.ficha(modelo) or {}).get('precio')

    def configurado(self):
        return bool(self.clave())

    def secretos(self):
        return [self.clave()]

    def falta(self):
        return ('Falta la clave de Anthropic (ANTHROPIC_API_KEY). Consíguela en '
                'https://platform.claude.com/settings/keys y guárdala en Ajustes ▸ Proveedores o con '
                '`alfalfa --guardar-clave anthropic`.')

    def accion(self, clas, error_text):
        if clas.get('cause') == 'access_denied':
            return 'falta de acceso; revisa la clave de Anthropic en Proveedores; no se reintenta.'
        return None

    def cliente(self, timeout, tarea=None, al_cambiar_cuota=None, transporte=None):
        clave = self.clave()
        if not clave:
            raise ValueError(self.falta())
        return httpx.AsyncClient(base_url='https://anthropic.alfalfa.invalid',
                                 transport=Transporte(self, self.url(), clave, tarea, timeout, transporte),
                                 timeout=timeout)

    def modelos(self):
        from . import configuracion
        extra = [m for m in configuracion.de('anthropic').get('modelos') or [] if isinstance(m, str)]
        nombres = list(CATALOGO) + [m for m in extra if m not in CATALOGO]
        from .. import catalogo
        filas = []
        for m in nombres:
            propia = CATALOGO.get(m) or {}
            ficha = self.ficha(m) or ({'id': m, 'nombre': propia['nombre'], 'ventana': propia['ventana'],
                                       'salida': propia['salida'], 'razona': True, 'herramientas': True,
                                       'vision': propia['vision'], 'esfuerzos': list(ESFUERZOS),
                                       'precio': propia['precio']} if propia else None)
            resumen = catalogo.resumen(ficha, self.id)
            if propia:
                resumen['ventana'] = propia['ventana']
                resumen['precio'] = {k: propia['precio'][k] for k in ('entrada', 'salida')}
            filas.append({'name': 'anthropic/' + m, 'model': 'anthropic/' + m, 'proveedor': self.id,
                          'nombre': propia.get('nombre') or (ficha or {}).get('nombre') or m, **resumen})
        return filas

    def estado(self):
        return dict(super().estado(), clave=self.configurado(), variable='ANTHROPIC_API_KEY',
                    donde='https://platform.claude.com/settings/keys')
