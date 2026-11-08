"""Búsqueda y lectura web de los agentes nativos, a través de CuySearch.

Kimi K3 marcó «sin verificar» lo que su propuesta decía de MCP y A2A (ticket
d0ef9839f8, 01/11): los agentes de Ollama no tenían ninguna herramienta web.
CuySearch es el servicio propio del taller sobre SearXNG; además bloquea
localhost, IPs privadas y demás rangos no públicos al extraer páginas, así que
el agente no puede usar `read_web` para asomarse a servicios del Mac.
"""
from __future__ import annotations

import httpx

from ... import ajustes

TIEMPO = 25.0
MAX_RESULTADOS = 8
MAX_FRAGMENTO = 600
MAX_PAGINA = 20000


def _base():
    base = (ajustes.cuysearch_url() or '').strip().rstrip('/')
    if not base:
        raise ValueError('La búsqueda web está desactivada: no hay URL de CuySearch configurada.')
    return base


def _cabeceras():
    clave = (ajustes.cuysearch_clave() or '').strip()
    return {'X-CuySearch-Key': clave} if clave else {}


async def _post(ruta, cuerpo):
    base = _base()
    # Si está apagado, el integrador lo enciende; y el uso aplaza su apagado.
    await ajustes.antes_de_cuysearch(base)
    try:
        async with httpx.AsyncClient(timeout=TIEMPO) as cliente:
            respuesta = await cliente.post(base + ruta, json=cuerpo, headers=_cabeceras())
    except httpx.HTTPError as exc:
        raise ValueError('CuySearch no responde en ' + base + ' (' + type(exc).__name__ + '). '
                         'La web no está disponible ahora: dilo en tu informe y marca '
                         'como «sin verificar» lo que dependa de ella.') from exc
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


async def buscar(consulta, dominios=None):
    consulta = ' '.join(str(consulta or '').split())
    if not 2 <= len(consulta) <= 500:
        raise ValueError('La consulta debe tener entre 2 y 500 caracteres.')
    cuerpo = {'query': consulta, 'max_results': MAX_RESULTADOS, 'search_depth': 'basic', 'topic': 'general'}
    if dominios:
        cuerpo['domains'] = list(dominios)[:10]
    datos = await _post('/v1/search', cuerpo)
    resultados = [{'title': str(item.get('title') or '').strip(),
                   'url': str(item.get('url') or '').strip(),
                   'snippet': ' '.join(str(item.get('content') or '').split())[:MAX_FRAGMENTO],
                   **({'published': item['published_date']} if item.get('published_date') else {})}
                  for item in (datos.get('results') or [])[:MAX_RESULTADOS] if isinstance(item, dict)]
    salida = {'query': consulta, 'results': resultados}
    if datos.get('warnings'):
        salida['warnings'] = [str(aviso)[:300] for aviso in datos['warnings'][:5]]
    return salida


async def leer(url):
    url = str(url or '').strip()
    if not url.startswith(('http://', 'https://')) or len(url) > 2000:
        raise ValueError('read_web sólo acepta una URL http o https de hasta 2000 caracteres.')
    datos = await _post('/v1/extract', {'urls': [url]})
    paginas = [p for p in (datos.get('results') or []) if isinstance(p, dict)]
    if not paginas:
        fallo = next(iter(datos.get('failed_results') or []), {})
        raise ValueError('No se pudo leer ' + url + ': ' + str(fallo.get('error') or 'sin contenido')[:300])
    pagina = paginas[0]
    texto = str(pagina.get('content') or '')
    return {'url': str(pagina.get('url') or url), 'title': pagina.get('title'),
            'text': texto[:MAX_PAGINA],
            'truncated': bool(pagina.get('truncated')) or len(texto) > MAX_PAGINA}
