"""La frase de la llamada en curso, en gerundio: «Leer runtime.py» → «Leyendo runtime.py».

La app enseña una fila viva mientras el agente trabaja («Ejecutando las pruebas · 38s»)
y, al terminar, la descripción de siempre en infinitivo («Ejecutar las pruebas»). Las
descripciones empiezan por un infinitivo: las generadas en `descripciones.py` y las que
escribe el modelo, porque el manual se lo pide así. Sólo se cambia ese primer verbo; si
la frase no empieza por un infinitivo reconocible, se deja tal cual.
"""

import re

# Infinitivos que no siguen las reglas de abajo.
IRREGULARES = {
    'ir': 'yendo',
    'poder': 'pudiendo',
    'oír': 'oyendo',
    'desoír': 'desoyendo',
    'morir': 'muriendo',
    'dormir': 'durmiendo',
    'reír': 'riendo',
    'sonreír': 'sonriendo',
    'freír': 'friendo',
    'teñir': 'tiñendo',
    'reñir': 'riñendo',
    'ceñir': 'ciñendo',
    'desteñir': 'destiñendo',
}

# Palabras que acaban como un infinitivo y no lo son (en español y en inglés, porque el
# modelo escribe la descripción de las órdenes en el idioma de la persona).
NO_VERBOS = frozenset({
    'primer', 'tercer', 'cualquier', 'lugar', 'hogar', 'mar', 'par', 'bar', 'super', 'súper', 'ayer', 'mujer',
    'taller', 'placer', 'docker', 'server', 'parser', 'buffer', 'linter', 'helper', 'wrapper', 'folder',
    'header', 'footer', 'render', 'container', 'compiler', 'logger', 'manager', 'handler', 'worker', 'user',
    'filter', 'cluster', 'clear', 'enter', 'order', 'register', 'answer', 'gather', 'cover', 'recover',
    'discover', 'deliver', 'consider', 'transfer', 'offer', 'trigger', 'power', 'master', 'poster', 'tier',
    'timer', 'router', 'player', 'browser', 'tar', 'star', 'alter', 'lower', 'hover', 'scatter', 'other',
    'after', 'under', 'over', 'never', 'either', 'neither', 'whether', 'per', 'for', 'or', 'our', 'your',
})

# Si una descripción del modelo lleva estas palabras, está en inglés: se deja como viene.
_INGLES = re.compile(r"\b(the|an|all|and|with|for|of|to|from|into|in|on|this|that|these|those|my|your|its|"
                     r"our|up|out|it|is|are|be|by|as|at|new|any|some|which|what|if|then|files|tests)\b",
                     re.IGNORECASE)
_CITA = re.compile(r'«[^»]*»|"[^"]*"|`[^`]*`')

# Frases que el motor genera sin verbo (descripciones.py) y su forma en curso.
FRASES = {
    'Captura del navegador': 'Capturando el navegador',
}

# Verbos en -ir que cambian la e de la raíz por i en el gerundio (pedir → pidiendo).
_E_A_I = re.compile(r'(edir|etir|egir|ebir|emir|ervir|estir|entir|ertir|erir|eguir)$')
_PRONOMBRES = ('los', 'las', 'les', 'nos', 'lo', 'la', 'le', 'se', 'me', 'te', 'os')
_PALABRA = re.compile(r'^([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)(.*)$', re.DOTALL)


def _raiz_con_i(raiz):
    """La última e de la raíz pasa a i: «ped» → «pid», «sigu» → «sigu» (ya va sin e)."""
    posicion = raiz.rfind('e')
    return raiz[:posicion] + 'i' + raiz[posicion + 1:] if posicion >= 0 else raiz


def gerundio_de(infinitivo):
    """`leer` → `leyendo`; None si no parece un infinitivo."""
    verbo = infinitivo.lower()
    if verbo in IRREGULARES:
        return IRREGULARES[verbo]
    if len(verbo) < 2:
        return None
    if verbo.endswith('venir'):
        return verbo[:-5] + 'viniendo'
    if verbo.endswith('decir'):
        return verbo[:-5] + 'diciendo'
    if verbo.endswith('eír'):
        return verbo[:-3] + 'iendo'
    if verbo.endswith('ñir') or verbo.endswith('llir'):
        # gruñir → gruñendo, bullir → bullendo: tras ñ y ll la i se pierde.
        return verbo[:-2] + 'endo'
    if verbo.endswith('ar') and len(verbo) > 2:
        return verbo[:-2] + 'ando'
    if verbo.endswith(('aer', 'eer', 'oer')):
        return verbo[:-2] + 'yendo'
    if verbo.endswith('uir') and not verbo.endswith(('guir', 'quir')):
        return verbo[:-2] + 'yendo'
    if verbo.endswith('er'):
        return verbo[:-2] + 'iendo'
    if verbo.endswith('ir') and len(verbo) > 2:
        raiz = verbo[:-2]
        if _E_A_I.search(verbo):
            raiz = _raiz_con_i(raiz)
        return raiz + 'iendo'
    return None


def _con_tilde(gerundio):
    """«revisando» + «lo» lleva tilde: «revisándolo». Va en la a o la e de la terminación."""
    for final, acentuado in (('ando', 'ándo'), ('endo', 'éndo')):
        if gerundio.endswith(final):
            return gerundio[:-len(final)] + acentuado
    return gerundio


def _verbo_con_pronombres(palabra):
    """`revisarlo` → `revisándolo`; `dárselo` y otros con tilde no se tocan."""
    minuscula = palabra.lower()
    for primero in _PRONOMBRES:
        if not minuscula.endswith(primero):
            continue
        resto = minuscula[:-len(primero)]
        sufijo = primero
        for segundo in ('se', 'me', 'te', 'nos', 'os'):
            if resto.endswith(segundo) and gerundio_de(resto[:-len(segundo)]):
                resto, sufijo = resto[:-len(segundo)], segundo + primero
                break
        if resto.endswith(('ar', 'er', 'ir', 'ír')) and len(resto) > 2:
            gerundio = gerundio_de(resto)
            if gerundio:
                return _con_tilde(gerundio) + sufijo
    return None


def _mayuscula_como(original, nueva):
    if original[:1].isupper():
        return nueva[:1].upper() + nueva[1:]
    return nueva


def en_curso(frase, propia=False):
    """La frase de la fila viva: el primer infinitivo pasa a gerundio y lo demás queda igual.

    `propia` es una descripción que escribió el modelo (las de las órdenes): puede venir en
    otro idioma, y entonces se deja como está."""
    if not isinstance(frase, str) or not frase.strip():
        return ''
    frase = ' '.join(frase.split())
    if frase in FRASES:
        return FRASES[frase]
    if propia and _INGLES.search(_CITA.sub(' ', frase)):
        return frase
    encontrada = _PALABRA.match(frase)
    if not encontrada:
        return frase
    palabra, resto = encontrada.groups()
    minuscula = palabra.lower()
    if minuscula.endswith(('ando', 'iendo', 'yendo')) or minuscula in NO_VERBOS:
        return frase
    gerundio = None
    if minuscula in IRREGULARES or minuscula.endswith(('ar', 'er', 'ir', 'ír')):
        gerundio = gerundio_de(minuscula)
    if gerundio is None:
        gerundio = _verbo_con_pronombres(palabra)
    if gerundio is None:
        return frase
    return _mayuscula_como(palabra, gerundio) + resto
