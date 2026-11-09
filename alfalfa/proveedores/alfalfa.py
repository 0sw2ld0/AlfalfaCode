"""El servicio Alfalfa: modelos con cuota mensual a cambio de un código de cortesía.

El servicio habla el formato de Ollama, así que el motor lo usa como a Ollama;
lo único distinto es la autenticación. El código `CUY-XXXX-XXXX-XXXX` queda
atado a este Mac: al activarlo se crea una clave en el Secure Enclave y el
servicio guarda su parte pública. Cada petición va firmada con

    MÉTODO\\nRUTA\\nMARCA\\nNONCE\\nSHA256(CUERPO)

por el ayudante `firmante.swift` (Python no puede usar el Secure Enclave). Cada
respuesta trae el % usado de la cuota del mes, que la app enseña en una barra.

La URL del servicio no viene en el repositorio: va dentro del código que se
reparte, `CUY-XXXX-XXXX-XXXX@servicio.example.com`, y se guarda al activarlo.
Al servicio sólo le llega la parte `CUY-…`. También se puede fijar a mano con
`alfalfa --fijar-url alfalfa https://…` o `ALFALFA_SERVICIO_URL`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .. import empaquetado
from . import Proveedor, cliente_de_lista
from .ollama import LOCALES, validar_url

FUENTE = Path(__file__).with_name('firmante.swift')
COMPILAR_SEGUNDOS = 240
FIRMAR_SEGUNDOS = 15
NOMBRES = {'gpt-6-luna': 'GPT-6 Luna', 'claude-haiku-5-5': 'Claude Haiku 5.5', 'glm-5.3-flash': 'GLM 5.3 Flash',
           'deepseek-v4.1-flash': 'DeepSeek v4.1 Flash'}
SUFIJO = ' - Alfalfa'
# Adonde va un reporte de error cuando no hay servicio Alfalfa o el servicio no lo acepta.
SOPORTE = 'soporte@cuycoders.dev'
MAX_TEXTO = 8_000
MAX_REGISTRO = 60_000


class ErrorDeAlfalfa(ValueError):
    """La activación o una consulta al servicio falló; el texto se puede enseñar tal cual."""


def canonico(metodo, ruta, marca, nonce, cuerpo):
    return '\n'.join([metodo.upper(), ruta, str(marca), nonce, hashlib.sha256(cuerpo or b'').hexdigest()]).encode()


def normalizar(codigo):
    return ''.join(str(codigo or '').upper().split())


def separar(texto):
    """(código, URL del servicio o None) de lo que pega el usuario.

    El código se reparte con su servicio detrás de una arroba:
    `CUY-XXXX-XXXX-XXXX@servicio.example.com`, o con puerto (`@servicio.example.com:8444`).
    Sin esquema es HTTPS, salvo en este mismo equipo. Sin arroba, es sólo el código.
    """
    limpio = ''.join(str(texto or '').split())
    codigo, arroba, servicio = limpio.partition('@')
    if not arroba:
        return normalizar(codigo), None
    servicio = servicio.rstrip('/')
    if not servicio:
        raise ErrorDeAlfalfa('Falta el servicio detrás de la arroba: CUY-…@servicio.example.com.')
    if '://' not in servicio:
        local = urlparse('//' + servicio).hostname in LOCALES
        servicio = ('http://' if local else 'https://') + servicio
    try:
        return normalizar(codigo), validar_url(servicio, 'el código de cortesía', 'El servicio Alfalfa')
    except ValueError as exc:
        raise ErrorDeAlfalfa(str(exc)) from exc


def _hogar():
    from . import configuracion
    return configuracion.ruta().parent


class Firmante:
    """El ayudante nativo, compilado una vez por versión de su fuente en `$ALFALFA_HOME/bin`."""

    def __init__(self, hogar=None):
        self.hogar = Path(hogar) if hogar else _hogar()
        self.clave = self.hogar / 'alfalfa-firma.clave'

    @property
    def binario(self):
        huella = hashlib.sha256(FUENTE.read_bytes()).hexdigest()[:12]
        return self.hogar / 'bin' / ('alfalfa-firma-' + huella)

    def tiene_clave(self):
        return self.clave.is_file()

    def asegurar(self):
        ruta = self.binario
        # En AlfalfaCode.app el ayudante va ya compilado y firmado con la app.
        incluido = empaquetado.ayudante(ruta.name)
        if incluido:
            return incluido
        if ruta.is_file() and os.access(ruta, os.X_OK):
            return ruta
        swiftc = shutil.which('swiftc') or ('/usr/bin/swiftc' if os.path.exists('/usr/bin/swiftc') else None)
        if not swiftc:
            raise ErrorDeAlfalfa('El código de cortesía necesita swiftc (Xcode o sus herramientas de línea de '
                                 'órdenes).')
        ruta.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporal = ruta.with_name(ruta.name + '.' + str(os.getpid()) + '.tmp')
        hecho = subprocess.run([swiftc, '-O', str(FUENTE), '-o', str(temporal)], capture_output=True, text=True,
                               timeout=COMPILAR_SEGUNDOS)
        if hecho.returncode != 0 or not temporal.is_file():
            temporal.unlink(missing_ok=True)
            raise ErrorDeAlfalfa('No se pudo compilar el ayudante de firma: ' + (hecho.stderr or '')[-600:])
        os.chmod(temporal, 0o700)
        os.replace(temporal, ruta)
        for viejo in ruta.parent.glob('alfalfa-firma-*'):
            if viejo != ruta and not viejo.name.endswith('.tmp'):
                viejo.unlink(missing_ok=True)
        return ruta

    def _orden(self, orden, entrada=b''):
        hecho = subprocess.run([str(self.asegurar()), orden, '--archivo', str(self.clave)], input=entrada,
                               capture_output=True, timeout=FIRMAR_SEGUNDOS)
        return self._leer(hecho.stdout, hecho.stderr)

    @staticmethod
    def _leer(salida, errores):
        try:
            datos = json.loads(salida.decode(errors='replace').strip().splitlines()[-1])
        except (IndexError, ValueError):
            raise ErrorDeAlfalfa('Respuesta inválida del ayudante de firma: '
                                 + (errores or b'').decode(errors='replace')[-300:]) from None
        if not datos.get('ok'):
            raise ErrorDeAlfalfa(datos.get('error') or 'El ayudante de firma falló.')
        return datos

    def publica(self):
        """La clave pública de este Mac (la crea en el Secure Enclave la primera vez)."""
        return self._orden('clave')['publica']

    def firmar(self, datos):
        return self._orden('firmar', datos)['firma']

    async def firmar_async(self, datos):
        ruta = await asyncio.to_thread(self.asegurar)
        proc = await asyncio.create_subprocess_exec(str(ruta), 'firmar', '--archivo', str(self.clave),
                                                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, start_new_session=True)
        try:
            salida, errores = await asyncio.wait_for(proc.communicate(datos), FIRMAR_SEGUNDOS)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise ErrorDeAlfalfa('El ayudante de firma no respondió a tiempo.') from None
        return self._leer(salida, errores)['firma']


def cabeceras(codigo, firma_de, metodo, ruta, cuerpo):
    """Las cabeceras firmadas de una petición; `firma_de(datos)` devuelve la firma en base64."""
    marca, nonce = int(time.time()), secrets.token_hex(12)
    return {'X-Alfalfa-Codigo': codigo, 'X-Alfalfa-Marca': str(marca), 'X-Alfalfa-Nonce': nonce,
            'X-Alfalfa-Firma': firma_de(canonico(metodo, ruta, marca, nonce, cuerpo))}


def _cuotas(valor):
    """Las filas de cuota de una respuesta del servicio, con su nombre visible."""
    filas = (valor.get('alfalfa') or {}).get('cuotas') if isinstance(valor, dict) else None
    if not isinstance(filas, list):
        filas = valor.get('cuotas') if isinstance(valor, dict) else None
    if not isinstance(filas, list):
        return None
    nombres = {'openai': 'GPT-6 Luna', 'anthropic': 'Claude Haiku 5.5', 'ollama': 'GLM y DeepSeek Flash'}
    salida = []
    for f in filas:
        if not isinstance(f, dict) or not isinstance(f.get('usado_pct'), (int, float)):
            continue
        fila = {'proveedor': f.get('proveedor'),
                'nombre': f.get('nombre') or nombres.get(f.get('proveedor'), f.get('proveedor')),
                'usado_pct': f.get('usado_pct'), 'renueva': f.get('renueva')}
        # El tope de hoy del código (servicio 0.7.0 en adelante): se renueva a medianoche UTC.
        dia = f.get('dia')
        if isinstance(dia, dict) and isinstance(dia.get('usado_pct'), (int, float)):
            fila['dia'] = {'usado_pct': dia['usado_pct'], 'renueva': dia.get('renueva')}
        salida.append(fila)
    return salida


class Transporte(httpx.AsyncBaseTransport):
    """Firma cada petición, pasa el esfuerzo de la sesión y recoge la cuota de cada respuesta."""

    def __init__(self, codigo, firmante, tarea=None, al_cambiar_cuota=None, interno=None):
        self.codigo = codigo
        self.firmante = firmante
        self.tarea = tarea if tarea is not None else {}
        self.al_cambiar_cuota = al_cambiar_cuota
        self.interno = interno or httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        cuerpo = await request.aread()
        ruta = request.url.path
        if ruta == '/api/chat' and self.tarea.get('effort'):
            try:
                valor = json.loads(cuerpo)
                valor['esfuerzo'] = self.tarea['effort']
                cuerpo = json.dumps(valor, ensure_ascii=False).encode()
            except ValueError:
                pass
        marca, nonce = int(time.time()), secrets.token_hex(12)
        firma = await self.firmante.firmar_async(canonico(request.method, ruta, marca, nonce, cuerpo))
        encabezados = {k: v for k, v in request.headers.items() if k.lower() not in ('content-length',)}
        encabezados.update({'x-alfalfa-codigo': self.codigo, 'x-alfalfa-marca': str(marca),
                            'x-alfalfa-nonce': nonce, 'x-alfalfa-firma': firma})
        firmada = httpx.Request(request.method, request.url, headers=encabezados, content=cuerpo,
                                extensions=request.extensions)
        respuesta = await self.interno.handle_async_request(firmada)
        if ruta != '/api/chat':
            return respuesta
        contenido = await respuesta.aread()
        await respuesta.aclose()
        try:
            cuotas = _cuotas(json.loads(contenido))
        except ValueError:
            cuotas = None
        if cuotas is not None:
            self.tarea['alfalfa_cuotas'] = cuotas
            if self.al_cambiar_cuota:
                self.al_cambiar_cuota(cuotas)
        return httpx.Response(respuesta.status_code, headers=respuesta.headers, content=contenido,
                              extensions=respuesta.extensions)

    async def aclose(self):
        await self.interno.aclose()


class Alfalfa(Proveedor):
    id = 'alfalfa'
    nombre = 'Alfalfa'
    sufijo = SUFIJO
    modelo_por_defecto = 'gpt-6-luna'

    def __init__(self, firmante=None):
        self._firmante = firmante

    @property
    def firmante(self):
        if self._firmante is None:
            self._firmante = Firmante()
        return self._firmante

    def url(self):
        from .. import ajustes
        valor = ajustes.alfalfa_servicio_url()
        if not valor:
            raise ErrorDeAlfalfa('Falta la dirección del servicio Alfalfa: pega el código completo, '
                                 'CUY-…@servicio, en Proveedores o con `alfalfa --activar-codigo`.')
        return validar_url(valor, 'ALFALFA_SERVICIO_URL', 'El servicio Alfalfa')

    def codigo(self):
        from .. import ajustes
        return ajustes.alfalfa_codigo()

    def configurado(self):
        from .. import ajustes
        return bool(ajustes.alfalfa_servicio_url() and self.codigo() and self.firmante.tiene_clave())

    def secretos(self):
        return [self.codigo()]

    def accion(self, clas, error_text):
        estado = clas.get('status_code')
        if estado == 402:
            return ('cuota del mes agotada (o el servicio llegó a su tope de hoy); se renueva sola; '
                    'no se reintenta.')
        if estado == 403:
            return ('el código de cortesía no vale en este Mac (revocado, caducado, en pausa o activado '
                    'en otro); revísalo en Proveedores; no se reintenta.')
        if estado == 401:
            return 'falta activar el código o la hora de este Mac no coincide con la del servicio; no se reintenta.'
        return None

    def cliente(self, timeout, tarea=None, al_cambiar_cuota=None):
        codigo = self.codigo()
        if not codigo:
            raise ErrorDeAlfalfa('Falta el código de cortesía: actívalo en Proveedores.')
        return httpx.AsyncClient(base_url=self.url(), timeout=timeout, trust_env=False,
                                 transport=Transporte(codigo, self.firmante, tarea, al_cambiar_cuota))

    # -- consultas de una sola pasada ------------------------------------------

    def _pedir(self, metodo, ruta, cuerpo=None, codigo=None, url=None):
        codigo = codigo or self.codigo()
        datos = json.dumps(cuerpo, ensure_ascii=False).encode() if cuerpo is not None else b''
        encabezados = cabeceras(codigo, self.firmante.firmar, metodo, ruta, datos)
        encabezados['Content-Type'] = 'application/json'
        with cliente_de_lista() as cliente:
            respuesta = cliente.request(metodo, (url or self.url()) + ruta, content=datos, headers=encabezados)
        try:
            valor = respuesta.json()
        except ValueError:
            valor = {}
        if respuesta.status_code >= 400:
            texto = valor.get('error') if isinstance(valor, dict) else None
            raise ErrorDeAlfalfa(str(texto or 'El servicio Alfalfa respondió ' + str(respuesta.status_code) + '.'))
        return valor

    def modelos(self):
        filas = self._pedir('GET', '/api/tags').get('models') or []
        return [{'name': 'alfalfa/' + f['name'], 'model': 'alfalfa/' + f['name'], 'proveedor': self.id,
                 'nombre': (f.get('nombre') or NOMBRES.get(f['name'], f['name'])) + SUFIJO}
                for f in filas if isinstance(f, dict) and f.get('name')]

    def cuota(self):
        return _cuotas(self._pedir('GET', '/cuota')) or []

    def activar(self, codigo):
        """Ata el código a este Mac y lo guarda en el Llavero. Devuelve lo que dice el servicio.

        Si el código trae su servicio (`CUY-…@servicio`), se activa ahí y esa dirección queda
        guardada en `proveedores.json`, pero sólo cuando el servicio acepta el código.
        """
        from . import configuracion, llavero
        codigo, servicio = separar(codigo)
        if not codigo.startswith('CUY-') or len(codigo) < 10:
            raise ErrorDeAlfalfa('El código de cortesía empieza por CUY-.')
        publica = self.firmante.publica()
        valor = self._pedir('POST', '/activar', {'codigo': codigo, 'clave_publica': publica, 'equipo': _equipo()},
                            codigo=codigo, url=servicio)
        if servicio:
            configuracion.cambiar('alfalfa', 'url', servicio)
        llavero.guardar('alfalfa', codigo)
        return {'nombre': valor.get('nombre'), 'caduca': valor.get('caduca'),
                'modelos': [m.get('name') for m in valor.get('modelos') or []],
                'cuotas': _cuotas(valor) or []}

    def estado(self):
        from .. import ajustes
        return dict(super().estado(), url=ajustes.alfalfa_servicio_url(), clave=bool(self.codigo()))

    def reportar(self, texto, contacto=None, registro=None):
        """Manda un reporte de error al servicio Alfalfa, firmado si este Mac tiene un código activado.

        Devuelve `{enviado, id, soporte, motivo}`. Si no hay servicio, o no lo acepta (por ejemplo,
        429 por demasiados reportes), `enviado` es False y quien llama ofrece escribir a `soporte`.
        El registro pasa antes por el mismo borrado de credenciales que los errores de los proveedores.
        """
        from .. import __version__, ajustes
        from ..reintentos.fallos import _sin_credenciales
        texto = str(texto or '').strip()
        if not texto:
            raise ErrorDeAlfalfa('Cuenta qué pasó.')

        def fallo(motivo, soporte=SOPORTE):
            return {'enviado': False, 'id': None, 'soporte': soporte, 'motivo': motivo}

        try:
            url = ajustes.alfalfa_servicio_url()
            url = validar_url(url, 'ALFALFA_SERVICIO_URL', 'El servicio Alfalfa') if url else None
        except ValueError as exc:
            return fallo(str(exc))
        if not url:
            return fallo('No hay servicio Alfalfa configurado.')
        cuerpo = {'texto': _sin_credenciales(texto)[:MAX_TEXTO], 'version': __version__, 'sistema': _sistema()}
        if contacto and str(contacto).strip():
            cuerpo['contacto'] = str(contacto).strip()[:200]
        if registro and str(registro).strip():
            cuerpo['registro'] = _sin_credenciales(registro)[-MAX_REGISTRO:]
        datos = json.dumps(cuerpo, ensure_ascii=False).encode()
        encabezados = {'Content-Type': 'application/json'}
        codigo = self.codigo()
        if codigo and self.firmante.tiene_clave():
            try:
                encabezados.update(cabeceras(codigo, self.firmante.firmar, 'POST', '/reportar', datos))
            except (ErrorDeAlfalfa, OSError, subprocess.SubprocessError):
                pass  # Sin firma también vale: el servicio lo acepta como anónimo.
        try:
            with cliente_de_lista() as cliente:
                respuesta = cliente.post(url + '/reportar', content=datos, headers=encabezados)
        except httpx.HTTPError:
            return fallo('No se pudo hablar con el servicio Alfalfa.')
        try:
            valor = respuesta.json()
        except ValueError:
            valor = {}
        valor = valor if isinstance(valor, dict) else {}
        soporte = valor.get('soporte') if '@' in str(valor.get('soporte') or '') else SOPORTE
        if respuesta.status_code != 200 or not valor.get('ok'):
            return fallo(str(valor.get('error') or 'El servicio Alfalfa respondió ' + str(respuesta.status_code) + '.'),
                         soporte)
        return {'enviado': True, 'id': valor.get('id'), 'soporte': soporte, 'motivo': None}


def _sistema():
    version = platform.mac_ver()[0]
    return ('macOS ' + version) if version else platform.platform(terse=True)[:120]


def _equipo():
    try:
        hecho = subprocess.run(['scutil', '--get', 'ComputerName'], capture_output=True, text=True, timeout=5)
        if hecho.returncode == 0 and hecho.stdout.strip():
            return hecho.stdout.strip()[:80]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return (platform.node() or 'Mac')[:80]
