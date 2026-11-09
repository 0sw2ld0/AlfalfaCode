"""Proveedores compatibles con OpenAI del catálogo de models.dev, con la clave del usuario.

OpenRouter, OpenCode Zen y cualquier otro proveedor del catálogo que hable el
formato de OpenAI. Como `openai.py`, el cliente atiende el formato de Ollama
(`/api/chat`, `/api/show`, `/api/tags`) y traduce por dentro: a
`/chat/completions` en general, y a `/responses` para los modelos que el catálogo
marca con el paquete de OpenAI (los GPT de Zen, por ejemplo).

El modelo de la sesión es `<proveedor>/<modelo>`: `openrouter/google/gemma-4-31b-it:free`.

Seguridad: la URL y el nombre de la variable vienen de un dato externo. Por eso
la clave de un proveedor sólo se lee del Llavero, guardada a propósito para ese
proveedor, y se ata a la URL que había al guardarla (`proveedores.json`); si el
catálogo cambia luego esa URL, la clave no la sigue. Sólo OpenRouter y OpenCode
Zen, con su URL fija aquí, leen además su variable de entorno estándar.
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit

import httpx

from .. import catalogo
from . import Proveedor
from .ollama import validar_url
from .openai import _argumentos, _json, _mime, _texto, traducir_peticion, traducir_respuesta

# Los que AlfalfaCode conoce: nombre, URL fija, variable estándar y dónde se saca la clave.
CONOCIDOS = {
    'openrouter': {'nombre': 'OpenRouter', 'url': 'https://openrouter.ai/api/v1', 'env': 'OPENROUTER_API_KEY',
                   'claves': 'https://openrouter.ai/settings/keys'},
    'opencode': {'nombre': 'OpenCode Zen', 'url': 'https://opencode.ai/inference/openai/v1',
                 'env': 'OPENCODE_API_KEY', 'claves': 'https://opencode.ai/auth'},
}
ESFUERZOS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
REFERENCIA = 'https://github.com/0sw2ld0/AlfalfaCode'


def esfuerzo_para(ficha, esfuerzo, pensar):
    """El esfuerzo que se pide, de entre los que el catálogo dice que admite el modelo; None si no se pide."""
    admitidos = [e for e in (ficha or {}).get('esfuerzos') or [] if e in ESFUERZOS]
    if not admitidos:
        return None
    if pensar is False:
        return 'none' if 'none' in admitidos else None
    deseado = esfuerzo if esfuerzo in ESFUERZOS else 'medium'
    if deseado in admitidos:
        return deseado
    # El más cercano por debajo; si no hay, el más bajo que no apaga el razonamiento.
    orden = ESFUERZOS.index(deseado)
    debajo = [e for e in admitidos if e != 'none' and ESFUERZOS.index(e) <= orden]
    if debajo:
        return max(debajo, key=ESFUERZOS.index)
    encendidos = [e for e in admitidos if e != 'none']
    return min(encendidos, key=ESFUERZOS.index) if encendidos else None


def traducir_chat(payload, ficha=None, esfuerzo=None, openrouter=False):
    """El cuerpo de `/chat/completions` para un `/api/chat` de Ollama."""
    mensajes, pendientes, llamadas = [], [], 0

    def cerrar_pendientes():
        while pendientes:
            ident, _ = pendientes.pop(0)
            mensajes.append({'role': 'tool', 'tool_call_id': ident,
                             'content': '(sin resultado: la herramienta no llegó a ejecutarse)'})

    for mensaje in payload.get('messages') or []:
        rol = mensaje.get('role')
        contenido = _texto(mensaje.get('content'))
        if rol == 'tool':
            nombre = mensaje.get('tool_name') or mensaje.get('name')
            indice = next((i for i, (_, n) in enumerate(pendientes) if n == nombre), 0 if pendientes else None)
            if indice is None:
                mensajes.append({'role': 'user', 'content': 'Resultado de ' + str(nombre or 'una herramienta')
                                 + ':\n' + contenido})
                continue
            ident, _ = pendientes.pop(indice)
            mensajes.append({'role': 'tool', 'tool_call_id': ident, 'content': contenido})
            continue
        cerrar_pendientes()
        if rol == 'system':
            mensajes.append({'role': 'system', 'content': contenido})
        elif rol == 'assistant':
            fila = {'role': 'assistant', 'content': contenido or None}
            for llamada in mensaje.get('tool_calls') or []:
                funcion = llamada.get('function') or {}
                llamadas += 1
                ident = llamada.get('id') or 'call_alfalfa_' + str(llamadas)
                nombre = str(funcion.get('name') or '')
                fila.setdefault('tool_calls', []).append(
                    {'id': ident, 'type': 'function',
                     'function': {'name': nombre, 'arguments': _argumentos(funcion.get('arguments'))}})
                pendientes.append((ident, nombre))
            if fila['content'] is None and not fila.get('tool_calls'):
                fila['content'] = ''
            mensajes.append(fila)
        else:
            imagenes = mensaje.get('images') or []
            if imagenes:
                partes = [{'type': 'text', 'text': contenido}] if contenido else []
                partes += [{'type': 'image_url', 'image_url': {'url': 'data:' + _mime(i) + ';base64,' + i}}
                           for i in imagenes]
                mensajes.append({'role': 'user', 'content': partes})
            else:
                mensajes.append({'role': 'user', 'content': contenido})
    cerrar_pendientes()

    cuerpo = {'model': payload.get('model'), 'messages': mensajes, 'stream': False}
    if payload.get('tools'):
        cuerpo['tools'] = [{'type': 'function', 'function': {
            'name': t['function']['name'], 'description': t['function'].get('description', ''),
            'parameters': t['function'].get('parameters') or {'type': 'object', 'properties': {}}}}
            for t in payload['tools'] if isinstance(t, dict) and isinstance(t.get('function'), dict)]
    opciones = payload.get('options') or {}
    salida = opciones.get('num_predict')
    if isinstance(salida, int) and salida > 0:
        tope = (ficha or {}).get('salida')
        cuerpo['max_tokens'] = min(salida, tope) if tope else salida
    nivel = esfuerzo_para(ficha, esfuerzo, payload.get('think'))
    if nivel:
        if openrouter:
            cuerpo['reasoning'] = {'effort': nivel} if nivel != 'none' else {'enabled': False}
        else:
            cuerpo['reasoning_effort'] = nivel
    formato = payload.get('format')
    if formato == 'json':
        cuerpo['response_format'] = {'type': 'json_object'}
    elif isinstance(formato, dict):
        cuerpo['response_format'] = {'type': 'json_schema',
                                     'json_schema': {'name': 'respuesta', 'schema': formato, 'strict': False}}
    return cuerpo


def respuesta_chat(datos, modelo):
    """La respuesta de `/chat/completions` como la de `/api/chat` de Ollama."""
    opciones = datos.get('choices') if isinstance(datos.get('choices'), list) else []
    eleccion = opciones[0] if opciones and isinstance(opciones[0], dict) else {}
    origen = eleccion.get('message') if isinstance(eleccion.get('message'), dict) else {}
    contenido = origen.get('content')
    if isinstance(contenido, list):
        contenido = ''.join(p.get('text') or '' for p in contenido if isinstance(p, dict))
    mensaje = {'role': 'assistant', 'content': contenido if isinstance(contenido, str) else ''}
    pensamiento = origen.get('reasoning_content') or origen.get('reasoning')
    if isinstance(pensamiento, str) and pensamiento.strip():
        mensaje['thinking'] = pensamiento
    llamadas = []
    for llamada in origen.get('tool_calls') or []:
        if not isinstance(llamada, dict):
            continue
        funcion = llamada.get('function') or {}
        try:
            argumentos = json.loads(funcion.get('arguments') or '{}')
        except (TypeError, ValueError):
            argumentos = funcion.get('arguments') or ''
        llamadas.append({'id': llamada.get('id'), 'function': {'name': funcion.get('name'), 'arguments': argumentos}})
    if llamadas:
        mensaje['tool_calls'] = llamadas
    motivo = eleccion.get('finish_reason')
    respuesta = {'model': modelo, 'message': mensaje, 'done': True,
                 'done_reason': 'length' if motivo == 'length' else 'stop'}
    uso = datos.get('usage') if isinstance(datos.get('usage'), dict) else {}
    if isinstance(uso.get('prompt_tokens'), int):
        respuesta['prompt_eval_count'] = uso['prompt_tokens']
    if isinstance(uso.get('completion_tokens'), int):
        respuesta['eval_count'] = uso['completion_tokens']
    detalle = uso.get('prompt_tokens_details') if isinstance(uso.get('prompt_tokens_details'), dict) else {}
    if isinstance(detalle.get('cached_tokens'), int):
        respuesta['alfalfa_cache'] = detalle['cached_tokens']
    return respuesta


def mostrar(datos, ficha, modelo, url):
    """Lo que `/api/show` diría del modelo, con lo que cuenta el catálogo."""
    ficha = ficha or {}
    capacidades = ['completion']
    capacidades += ['tools'] if ficha.get('herramientas') else []
    capacidades += ['thinking'] if ficha.get('razona') else []
    capacidades += ['vision'] if ficha.get('vision') else []
    info = {'capabilities': capacidades, 'remote_host': url, 'remote_model': modelo,
            'details': {'family': datos.get('id', '')}, 'model_info': {}, 'catalogo': ficha}
    if ficha.get('ventana'):
        info['model_info']['catalogo.context_length'] = ficha['ventana']
    return info


class Transporte(httpx.AsyncBaseTransport):
    """Atiende el formato de Ollama y habla Chat Completions (o Responses) por detrás."""

    def __init__(self, proveedor, url, clave, tarea=None, timeout=None, transporte=None):
        self.proveedor = proveedor
        self.url = url
        self.tarea = tarea if tarea is not None else {}
        cabeceras = {'Authorization': 'Bearer ' + clave}
        if proveedor.id == 'openrouter':
            # Atribución opcional que OpenRouter pide a las aplicaciones.
            cabeceras.update({'HTTP-Referer': REFERENCIA, 'X-Title': 'AlfalfaCode'})
        self.http = httpx.AsyncClient(timeout=timeout, trust_env=False, transport=transporte, headers=cabeceras)

    async def handle_async_request(self, request):
        ruta = request.url.path
        try:
            pedido = json.loads(await request.aread() or b'{}')
        except ValueError:
            return _json(400, {'error': 'petición que no es JSON'})
        modelo = pedido.get('model')
        ficha = self.proveedor.ficha(modelo)
        if ruta == '/api/show':
            return _json(200, mostrar(self.proveedor.datos(), ficha, modelo, self.url))
        if ruta == '/api/tags':
            return _json(200, {'models': [{'name': f['name'].split('/', 1)[1]} for f in self.proveedor.modelos()]})
        if ruta != '/api/chat':
            return _json(404, {'error': 'ruta no disponible con ' + self.proveedor.nombre + ': ' + ruta})
        tipo, _ = catalogo.api_de(self.proveedor.datos(), ficha)
        if tipo == 'responses':
            cuerpo = traducir_peticion(pedido, self.tarea.get('effort'))
            respuesta = await self.http.post(self.url + '/responses', json=cuerpo)
        else:
            cuerpo = traducir_chat(pedido, ficha, self.tarea.get('effort'),
                                   openrouter=self.proveedor.id == 'openrouter')
            respuesta = await self.http.post(self.url + '/chat/completions', json=cuerpo)
        try:
            datos = respuesta.json()
        except ValueError:
            datos = None
        cabeceras = {k: v for k, v in respuesta.headers.items() if k.lower() == 'retry-after'}
        if respuesta.status_code >= 400 or not isinstance(datos, dict):
            error = datos.get('error') if isinstance(datos, dict) else None
            if isinstance(error, dict):
                error = error.get('message') or error.get('code') or error
            return _json(respuesta.status_code if respuesta.status_code >= 400 else 502,
                         {'error': error or 'respuesta de ' + self.proveedor.nombre + ' que no se entiende'},
                         cabeceras)
        if datos.get('error'):
            error = datos['error']
            return _json(502, {'error': error.get('message') if isinstance(error, dict) else error}, cabeceras)
        if tipo == 'responses':
            return _json(200, traducir_respuesta(datos, modelo), cabeceras)
        return _json(200, respuesta_chat(datos, modelo), cabeceras)

    async def aclose(self):
        await self.http.aclose()


def es_compatible(ident):
    """¿`ident` es un proveedor compatible del catálogo (o uno de los conocidos)?"""
    if ident in CONOCIDOS:
        return True
    if not isinstance(ident, str) or ident in ('ollama', 'openai', 'alfalfa'):
        return False
    datos = catalogo.proveedor(ident)
    return bool(datos and catalogo.compatible(datos))


class Compatible(Proveedor):
    """Un proveedor del catálogo que habla el formato de OpenAI, con la clave del usuario."""

    def __init__(self, ident):
        if not es_compatible(ident):
            from . import ErrorDeProveedor
            raise ErrorDeProveedor('Proveedor desconocido: ' + str(ident))
        self.id = ident
        conocido = CONOCIDOS.get(ident, {})
        self.nombre = conocido.get('nombre') or (catalogo.proveedor(ident) or {}).get('nombre') or ident
        self.sufijo = ' - ' + self.nombre

    def datos(self):
        return catalogo.proveedor(self.id) or {'id': self.id, 'nombre': self.nombre, 'modelos': {}}

    def ficha(self, modelo):
        return catalogo.ficha(self.id, modelo)

    def precio(self, modelo):
        return (self.ficha(modelo) or {}).get('precio')

    def url(self):
        """La guardada al configurar, la fija de los conocidos o la del catálogo."""
        from . import configuracion
        valor = configuracion.de(self.id).get('url') or CONOCIDOS.get(self.id, {}).get('url') \
            or self.datos().get('api')
        if not valor:
            raise ValueError(self.nombre + ' no tiene URL en el catálogo.')
        valor = str(valor).rstrip('/')
        partes = urlsplit(valor)
        validar_url(partes.scheme + '://' + partes.netloc, 'proveedores.json', self.nombre)
        if partes.query or partes.fragment:
            raise ValueError('La URL de ' + self.nombre + ' no lleva consulta ni fragmento.')
        return valor

    def variable(self):
        """El nombre de la clave que se le explica al usuario."""
        conocido = CONOCIDOS.get(self.id, {}).get('env')
        return conocido or next(iter(self.datos().get('env') or []), self.id.upper().replace('-', '_') + '_API_KEY')

    def donde(self):
        """Dónde se consigue la clave: la página conocida o la `doc` del catálogo."""
        return CONOCIDOS.get(self.id, {}).get('claves') or self.datos().get('doc')

    def clave(self):
        from .. import ajustes
        return ajustes.clave_compatible(self.id)

    def configurado(self):
        return bool(self.clave())

    def secretos(self):
        return [self.clave()]

    def falta(self):
        """El texto que explica qué clave falta y dónde se consigue."""
        texto = ('Falta la clave de ' + self.nombre + ' (' + self.variable() + ').')
        if self.donde():
            texto += ' Consíguela en ' + self.donde() + '.'
        return texto + ' Guárdala en Ajustes ▸ Proveedores o con `alfalfa --guardar-clave ' + self.id + '`.'

    def accion(self, clas, error_text):
        if clas.get('cause') == 'access_denied':
            return 'falta de acceso; revisa la clave de ' + self.nombre + ' en Proveedores; no se reintenta.'
        return None

    def cliente(self, timeout, tarea=None, al_cambiar_cuota=None, transporte=None):
        clave = self.clave()
        if not clave:
            raise ValueError(self.falta())
        return httpx.AsyncClient(base_url='https://' + self.id + '.alfalfa.invalid',
                                 transport=Transporte(self, self.url(), clave, tarea, timeout, transporte),
                                 timeout=timeout)

    def fila(self, ficha):
        """La fila de la lista de modelos para una ficha del catálogo."""
        fila = {'name': self.id + '/' + ficha['id'], 'model': self.id + '/' + ficha['id'], 'proveedor': self.id,
                'nombre': ficha.get('nombre') or ficha['id'], 'nombre_proveedor': self.nombre,
                **catalogo.resumen(ficha)}
        if catalogo.es_gratis(self.datos(), ficha):
            fila.update(gratis=True, aviso=catalogo.AVISO_GRATIS)
        if not self.configurado():
            fila['falta_clave'] = {'variable': self.variable(), 'donde': self.donde(), 'texto': self.falta()}
        return fila

    def modelos(self):
        """Los gratis ahora y los que el usuario añadió en `proveedores.json` (`modelos`)."""
        from . import configuracion
        datos = self.datos()
        elegidos = [f for f in datos['modelos'].values() if catalogo.es_gratis(datos, f)]
        if self.configurado():
            vistos = {f['id'] for f in elegidos}
            for nombre in configuracion.de(self.id).get('modelos') or []:
                if isinstance(nombre, str) and nombre not in vistos and catalogo.ID_MODELO.fullmatch(nombre):
                    elegidos.append(self.ficha(nombre) or {'id': nombre, 'nombre': nombre})
                    vistos.add(nombre)
        return [self.fila(f) for f in elegidos]

    def estado(self):
        datos = dict(super().estado(), clave=self.configurado(), variable=self.variable(), compatible=True)
        if self.donde():
            datos['donde'] = self.donde()
        if self.id in catalogo.SOLO_SU_CLIENTE:
            datos['aviso'] = catalogo.SOLO_SU_CLIENTE[self.id]
        try:
            datos['url'] = self.url()
        except ValueError:
            pass
        return datos
