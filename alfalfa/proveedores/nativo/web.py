"""Búsqueda y lectura web de los agentes nativos.

La hace el proceso del arnés con la librería CuySearch, desde la conexión del
usuario: el sandbox de las órdenes sigue sin red. `read_web` rechaza
localhost, las IPs privadas y demás destinos no públicos (también tras cada
redirección), así que un agente no puede usarla para asomarse a servicios del
Mac.

Qué buscador se usa (Ajustes ▸ General de la app, `alfalfa --buscador` o
`ALFALFA_BUSCADOR`):

- `auto`: DuckDuckGo, Bing, Brave, Mojeek y Wikipedia, en ese orden;
- `ollama`: Ollama Web Search con la clave de Ollama del usuario, y si falla los anteriores;
- `searxng`: un SearXNG propio (por defecto el de CuySearch Manager), y si falla los anteriores;
- `apagado`: el modelo no recibe web_search ni read_web.

Si el integrador da la URL de un servicio CuySearch (`ALFALFA_CUYSEARCH_URL`),
se usa ese servicio por HTTP en vez de la librería.
"""
from __future__ import annotations

import hashlib
import os

import httpx

from ... import ajustes
from .. import configuracion

TIEMPO = 25.0
MAX_RESULTADOS = 8
MAX_FRAGMENTO = 600
MAX_PAGINA = 20000

MODOS = ('auto', 'ollama', 'searxng', 'apagado')
SEARXNG_POR_DEFECTO = 'http://127.0.0.1:8888'
SECCION = 'web'
SIN_WEB = ('La web no está disponible ahora: dilo en tu informe y marca como «sin verificar» lo que '
           'dependa de ella.')


# ---------------------------------------------------------------- ajustes

def _seccion():
    valor = configuracion.leer().get(SECCION)
    return valor if isinstance(valor, dict) else {}


def modo():
    """El buscador elegido: `ALFALFA_BUSCADOR`, el de `proveedores.json` o `auto`."""
    valor = str(os.getenv('ALFALFA_BUSCADOR') or _seccion().get('modo') or 'auto').strip().lower()
    return valor if valor in MODOS else 'auto'


def searxng_url():
    return (os.getenv('ALFALFA_SEARXNG_URL') or _seccion().get('searxng_url')
            or SEARXNG_POR_DEFECTO).strip().rstrip('/')


def _remoto():
    return (ajustes.cuysearch_url() or '').strip().rstrip('/')


def libreria_disponible():
    try:
        import cuysearch  # noqa: F401
    except ImportError:
        return False
    return True


def disponible():
    """Si el modelo debe recibir web_search y read_web."""
    return modo() != 'apagado' and bool(_remoto() or libreria_disponible())


def configurar(nuevo_modo=None, url=None):
    """Guarda el modo y, si se da, la URL de SearXNG (`-` la quita) en `proveedores.json`."""
    datos = configuracion.leer()
    seccion = _seccion()
    if nuevo_modo is not None:
        if nuevo_modo not in MODOS:
            raise ValueError('Buscador desconocido: ' + str(nuevo_modo) + '. Usa ' + ', '.join(MODOS) + '.')
        seccion['modo'] = nuevo_modo
    if url is not None:
        if url == '-':
            seccion.pop('searxng_url', None)
        else:
            from ..ollama import validar_url
            seccion['searxng_url'] = validar_url(url, 'proveedores.json', 'searxng')
    datos[SECCION] = seccion
    configuracion.escribir(datos)
    return estado()


def estado():
    """Lo que enseña Ajustes, sin secretos."""
    return {'modo': modo(), 'searxng_url': searxng_url(), 'remoto': bool(_remoto()),
            'libreria': libreria_disponible(), 'ollama_clave': bool(ajustes.ollama_clave())}


# ---------------------------------------------------------------- librería

_buscadores = {}


def _buscador():
    """Un Buscador por configuración: guarda su caché y la pausa de los motores que bloquean."""
    import cuysearch
    from cuysearch.motores import GRATUITOS, Configuracion

    elegido = modo()
    claves, url, motores, aviso = {}, None, 'auto', None
    if elegido == 'ollama':
        clave = (ajustes.ollama_clave() or '').strip()
        if clave:
            claves['ollama'] = clave
            motores = ['ollama', *GRATUITOS]
        else:
            aviso = 'ollama: falta la clave de Ollama (Ajustes ▸ Proveedores); se usaron los motores gratuitos'
    elif elegido == 'searxng':
        url = searxng_url()
        motores = ['searxng', *GRATUITOS]
    huella = (elegido, url, hashlib.sha256(claves.get('ollama', '').encode()).hexdigest())
    if huella not in _buscadores:
        _buscadores.clear()
        _buscadores[huella] = cuysearch.Buscador(motores, config=Configuracion(searxng_url=url, claves=claves))
    return _buscadores[huella], aviso


def _sin_libreria():
    return ValueError('Falta la librería cuysearch en el entorno de AlfalfaCode: reinstala con '
                      '`uv pip install -e .` en el repositorio. ' + SIN_WEB)


async def _buscar_local(consulta, dominios):
    try:
        import cuysearch
    except ImportError as exc:
        raise _sin_libreria() from exc
    buscador, aviso = _buscador()
    try:
        busqueda = await buscador.buscar(consulta, dominios, max_resultados=MAX_RESULTADOS)
    except cuysearch.ErrorDeBusqueda as exc:
        raise ValueError(str(exc)[:600] + ' ' + SIN_WEB) from exc
    resultados = [{'title': r.titulo, 'url': r.url, 'snippet': r.fragmento[:MAX_FRAGMENTO],
                   **({'published': r.fecha} if r.fecha else {})} for r in busqueda.resultados]
    salida = {'query': busqueda.consulta, 'results': resultados}
    if busqueda.motor:
        salida['engine'] = busqueda.motor
    avisos = ([aviso] if aviso else []) + list(busqueda.avisos)
    if avisos:
        salida['warnings'] = [str(a)[:300] for a in avisos[:5]]
    return salida


async def _leer_local(url):
    try:
        import cuysearch
    except ImportError as exc:
        raise _sin_libreria() from exc
    buscador, _ = _buscador()
    try:
        pagina = await buscador.leer(url)
    except (cuysearch.ErrorDeLectura, cuysearch.UnsafeUrlError) as exc:
        raise ValueError('No se pudo leer ' + url + ': ' + str(exc)[:300]) from exc
    return {'url': pagina.url, 'title': pagina.titulo, 'text': pagina.texto[:MAX_PAGINA],
            'truncated': pagina.truncada or len(pagina.texto) > MAX_PAGINA}


# ---------------------------------------------------------------- servicio remoto

def _cabeceras():
    clave = (ajustes.cuysearch_clave() or '').strip()
    return {'X-CuySearch-Key': clave} if clave else {}


async def _post(base, ruta, cuerpo):
    # Si está apagado, el integrador lo enciende; y el uso aplaza su apagado.
    await ajustes.antes_de_cuysearch(base)
    try:
        async with httpx.AsyncClient(timeout=TIEMPO) as cliente:
            respuesta = await cliente.post(base + ruta, json=cuerpo, headers=_cabeceras())
    except httpx.HTTPError as exc:
        raise ValueError('CuySearch no responde en ' + base + ' (' + type(exc).__name__ + '). ' + SIN_WEB) from exc
    if respuesta.status_code in (401, 403):
        raise ValueError('CuySearch rechazó la clave de acceso.')
    if respuesta.status_code >= 400:
        try:
            detalle = str(respuesta.json().get('detail', ''))
        except ValueError:
            detalle = respuesta.text[:200]
        raise ValueError('CuySearch devolvió HTTP ' + str(respuesta.status_code) + ': ' + detalle[:300])
    try:
        return respuesta.json()
    except ValueError as exc:
        raise ValueError('CuySearch devolvió una respuesta ilegible.') from exc


async def _buscar_remoto(base, consulta, dominios):
    cuerpo = {'query': consulta, 'max_results': MAX_RESULTADOS, 'search_depth': 'basic', 'topic': 'general'}
    if dominios:
        cuerpo['domains'] = list(dominios)[:10]
    datos = await _post(base, '/v1/search', cuerpo)
    resultados = [{'title': str(item.get('title') or '').strip(),
                   'url': str(item.get('url') or '').strip(),
                   'snippet': ' '.join(str(item.get('content') or '').split())[:MAX_FRAGMENTO],
                   **({'published': item['published_date']} if item.get('published_date') else {})}
                  for item in (datos.get('results') or [])[:MAX_RESULTADOS] if isinstance(item, dict)]
    salida = {'query': consulta, 'results': resultados}
    if datos.get('warnings'):
        salida['warnings'] = [str(aviso)[:300] for aviso in datos['warnings'][:5]]
    return salida


async def _leer_remoto(base, url):
    datos = await _post(base, '/v1/extract', {'urls': [url]})
    paginas = [p for p in (datos.get('results') or []) if isinstance(p, dict)]
    if not paginas:
        fallo = next(iter(datos.get('failed_results') or []), {})
        raise ValueError('No se pudo leer ' + url + ': ' + str(fallo.get('error') or 'sin contenido')[:300])
    pagina = paginas[0]
    texto = str(pagina.get('content') or '')
    return {'url': str(pagina.get('url') or url), 'title': pagina.get('title'),
            'text': texto[:MAX_PAGINA],
            'truncated': bool(pagina.get('truncated')) or len(texto) > MAX_PAGINA}


# ---------------------------------------------------------------- herramientas

def _comprobar_encendida():
    if modo() == 'apagado':
        raise ValueError('La búsqueda web está apagada en Ajustes. ' + SIN_WEB)


async def buscar(consulta, dominios=None):
    _comprobar_encendida()
    consulta = ' '.join(str(consulta or '').split())
    if not 2 <= len(consulta) <= 500:
        raise ValueError('La consulta debe tener entre 2 y 500 caracteres.')
    dominios = [str(d) for d in (dominios or [])][:10]
    base = _remoto()
    if base:
        return await _buscar_remoto(base, consulta, dominios)
    return await _buscar_local(consulta, dominios)


async def leer(url):
    _comprobar_encendida()
    url = str(url or '').strip()
    if not url.startswith(('http://', 'https://')) or len(url) > 2000:
        raise ValueError('read_web sólo acepta una URL http o https de hasta 2000 caracteres.')
    base = _remoto()
    if base:
        return await _leer_remoto(base, url)
    return await _leer_local(url)
