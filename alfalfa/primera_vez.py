"""La primera vez, sin ningún proveedor: los modelos «Gratis ahora» y cómo empezar sin pegar claves.

Sin proveedor quiere decir: ninguna clave ni código en el Llavero o en las variables
estándar, ningún proveedor por defecto elegido en `proveedores.json` y ningún Ollama
instalado ni respondiendo en este Mac. Entonces, en vez de un error de conexión con Ollama, la
terminal y la app ofrecen los modelos gratis del catálogo con su aviso de datos.
OpenRouter se conecta con un clic (OAuth en el navegador, `proveedores/conectar.py`);
los demás proveedores gratis dicen dónde se saca su clave.

    sin_proveedor() -> bool
    ofertas() -> {'sin_proveedor', 'aviso', 'proveedores': [{id, nombre, conectar, donde, modelos, recomendado}]}
    recomendado(proveedor) -> id del modelo gratis que se elige al conectar, o None
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

# Los gratis que se eligen al conectar, en orden, si el catálogo los sigue dando gratis.
# Nemotron 3.5 Super VL se probó con herramientas en AlfalfaCode (ventana de 1M, ve imágenes).
PREFERIDOS = {
    'openrouter': ('nvidia/nemotron-3.5-super-vl:free', 'nvidia/nemotron-3-ultra-550b-a55b:free',
                   'nvidia/nemotron-3.5-lightning:free'),
    'nvidia': ('nvidia/nemotron-3.5-super-vl-120b-a12b', 'nvidia/nemotron-3-ultra-550b-a55b'),
}
# Enrutadores y modelos sin nombre público: no se eligen solos.
_NO_ELEGIR = ('openrouter/', 'stealth/')
ESPERA_OLLAMA = 1.5
# Cuántos proveedores gratis enseña la bienvenida de la terminal (hay decenas).
EN_LA_TERMINAL = 4


def _algo_guardado():
    from . import ajustes
    from .proveedores import compatibles_guardados, configuracion
    from .proveedores.compatible import CONOCIDOS
    if 'por_defecto' in configuracion.leer():
        return True
    lecturas = [ajustes.ollama_clave, ajustes.openai_clave, ajustes.anthropic_clave, ajustes.alfalfa_codigo]
    lecturas += [(lambda i=i: ajustes.clave_compatible(i)) for i in CONOCIDOS]
    for leer in lecturas:
        try:
            if leer():
                return True
        except Exception:
            continue
    try:
        return bool(compatibles_guardados())
    except Exception:
        return False


def ollama_responde(get=None):
    """¿Hay un Ollama en este Mac (o en la URL que el usuario puso)? Un usuario que fijó una URL
    remota ya eligió Ollama, responda o no."""
    from . import ajustes
    from .proveedores.ollama import LOCALES
    url = ajustes.ollama_url()
    if (urlparse(url).hostname or '') not in LOCALES:
        return True
    if get is None:
        import httpx
        get = httpx.get
    try:
        return get(url.rstrip('/') + '/api/version', timeout=ESPERA_OLLAMA, trust_env=False).status_code < 500
    except Exception:
        return False


def ollama_instalado():
    """Ollama en este Mac aunque ahora no esté abierto: entonces el error de siempre (ábrelo)
    ayuda más que la bienvenida."""
    import shutil
    return bool(shutil.which('ollama')) or any(
        p.exists() for p in (Path('/Applications/Ollama.app'), Path.home() / 'Applications' / 'Ollama.app'))


def sin_proveedor(get=None):
    # Para revisar la bienvenida en un Mac que ya tiene claves: ALFALFA_FORZAR_PRIMERA_VEZ=1.
    if os.environ.get('ALFALFA_FORZAR_PRIMERA_VEZ') == '1':
        return True
    return not _algo_guardado() and not ollama_instalado() and not ollama_responde(get)


def _elegible(ficha):
    return not ficha['id'].startswith(_NO_ELEGIR)


def recomendado(proveedor, gratis=None):
    """El modelo gratis que se elige al conectar `proveedor` (sin prefijo), o None."""
    from . import catalogo
    if gratis is None:
        gratis = [f for d, f in catalogo.gratis_ahora() if d['id'] == proveedor]
    por_id = {f['id']: f for f in gratis}
    for ident in PREFERIDOS.get(proveedor, ()):
        if ident in por_id:
            return ident
    candidatos = [f for f in gratis if _elegible(f)]
    if not candidatos:
        return None
    mejor = max(candidatos, key=lambda f: (bool(f.get('herramientas')), f.get('ventana') or 0, f.get('fecha') or ''))
    return mejor['id']


def ofertas(get=None):
    """Lo que se enseña la primera vez: los gratis por proveedor, OpenRouter primero."""
    from . import catalogo
    from .proveedores.compatible import Compatible
    from .proveedores.conectar import CONECTABLES
    por_proveedor = {}
    for datos, ficha in catalogo.gratis_ahora(red=True):
        por_proveedor.setdefault(datos['id'], (datos, []))[1].append(ficha)
    filas = []
    for ident, (datos, fichas) in por_proveedor.items():
        try:
            compatible = Compatible(ident)
            donde = compatible.donde()
            variable = compatible.variable()
        except Exception:
            donde, variable = datos.get('doc'), None
        elegido = recomendado(ident, fichas)
        filas.append({
            'id': ident, 'nombre': datos['nombre'], 'conectar': ident in CONECTABLES,
            'donde': donde, 'variable': variable,
            'recomendado': ident + '/' + elegido if elegido else None,
            'modelos': [{'name': ident + '/' + f['id'], 'nombre': f.get('nombre') or f['id'],
                         'ventana': f.get('ventana'), 'vision': f.get('vision')} for f in fichas],
        })
    filas.sort(key=lambda f: (not f['conectar'], -len(f['modelos']), f['nombre'].lower()))
    return {'sin_proveedor': sin_proveedor(get), 'aviso': catalogo.AVISO_GRATIS, 'proveedores': filas}


def texto(datos):
    """La bienvenida de la terminal."""
    lineas = ['AlfalfaCode · No hay ningún proveedor configurado todavía.', '']
    if datos['proveedores']:
        lineas.append('Gratis ahora (' + datos['aviso'].lower().replace('gratis: ', '') + '):')
        for fila in datos['proveedores'][:EN_LA_TERMINAL]:
            # El recomendado primero: es con el que se empieza.
            modelos = sorted(fila['modelos'], key=lambda m: m['name'] != fila.get('recomendado'))
            nombres = ', '.join(m['nombre'] for m in modelos[:3])
            mas = f' y {len(modelos) - 3} más' if len(modelos) > 3 else ''
            lineas.append(f'  {fila["nombre"]}: {nombres}{mas}.')
            if fila['conectar']:
                lineas.append(f'    Sin pegar claves: alfalfa --conectar {fila["id"]}  '
                              '(se abre el navegador; la cuenta es gratis)')
            else:
                lineas.append(f'    Con su clave gratis{" de " + fila["donde"] if fila["donde"] else ""}: '
                              f'alfalfa --guardar-clave {fila["id"]}')
        resto = len(datos['proveedores']) - EN_LA_TERMINAL
        if resto > 0:
            lineas.append(f'  Y {resto} proveedores más con modelos gratis: alfalfa --catalogo')
        lineas.append('')
    lineas += ['Otras formas:',
               '  Ollama en este Mac (modelos locales): https://ollama.com/download',
               '  Tu clave de OpenAI o de Claude: alfalfa --guardar-clave openai (o anthropic)',
               '  Un código de cortesía del servicio Alfalfa: alfalfa --activar-codigo']
    return '\n'.join(lineas)
