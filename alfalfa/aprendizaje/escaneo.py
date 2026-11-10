"""Escaneo de lo que se va a guardar como memoria o habilidad.

Lo guardado entra en el prompt de sistema de sesiones futuras, así que se rechaza
(no se «limpia»: el agente tiene que reescribirlo) lo que parezca:

- una inyección de prompt (instrucciones para saltarse las reglas, cambiar de
  identidad, ocultar algo al usuario o sacar datos fuera);
- una credencial: las claves de los proveedores configurados, cabeceras y formas
  `api_key=…` (las mismas que `reintentos.fallos` quita de los errores) y formas
  conocidas de tokens y claves privadas;
- caracteres Unicode invisibles o de control de dirección, con los que un texto
  enseña una cosa y dice otra.

Como en Hermes Agent (Nous Research, MIT), que bloquea inyección, exfiltración de
credenciales, puertas traseras SSH y Unicode invisible antes de guardar memoria.

Es una defensa secundaria: lo que de verdad protege es que cada escritura de memoria
o habilidad sale en una tarjeta con su diff, y que en el prompt las entradas van
citadas, como datos. Por eso se rechaza también lo que intenta romper ese marco
(títulos Markdown, las marcas de la memoria) y la mezcla de alfabetos en una palabra
(homoglifos cirílicos o griegos que imitan letras latinas).
"""
from __future__ import annotations

import math
import re
import unicodedata

from ..reintentos.fallos import _CLAVE_SUELTA, _CREDENCIALES

# Formas de credenciales que `_CREDENCIALES` no cubre.
_MAS_CREDENCIALES = re.compile(
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----'
    r'|\bsk-ant-[A-Za-z0-9_-]{8,}'
    r'|\bgh[pousr]_[A-Za-z0-9]{20,}'
    r'|\bgithub_pat_[A-Za-z0-9_]{20,}'
    r'|\bglpat-[A-Za-z0-9_-]{16,}'
    r'|\bxox[abposr]-[A-Za-z0-9-]{10,}'
    r'|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'
    r'|\bAIza[0-9A-Za-z_-]{30,}'
    r'|(?i:\b(?:password|passwd|contraseña|secret|secreto)\b["\']?\s*[:=]\s*\S{4,})'
    r'|\b(?:ecdsa-sha2-nistp\d+|ssh-dss|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com)\s+AAAA'
    r'|\bhf_[A-Za-z0-9]{20,}|\bnpm_[A-Za-z0-9]{20,}|\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{10,}'
    r'|\bpypi-[A-Za-z0-9_-]{20,}|\bdop_v1_[a-f0-9]{20,}|\bsq0[a-z]{3}-[A-Za-z0-9_-]{20,}'
    r'|hooks\.slack\.com/(?:services|workflows)/|discord(?:app)?\.com/api/webhooks/'
    r'|\b[a-z][a-z0-9+.-]{1,20}://[^/\s:@]+:[^/\s@]+@'
    r'|\b[0-9a-fA-F]{32,}\b')

# Inyección de prompt y exfiltración, en español e inglés. Se buscan órdenes dirigidas
# al agente, no palabras sueltas: «el prompt de sistema» en una nota técnica no basta.
_INYECCION = re.compile(r'(?ix)'
    r'\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}\b(?:previous|prior|above|all|earlier|your|system)\b'
    r'[^.\n]{0,20}\b(?:instructions?|rules?|prompts?|guidelines?|directives?)'
    r'|\b(?:ignora|olvida|desobedece|omite|salta(?:te)?)\b[^.\n]{0,40}'
    r'\b(?:instrucciones|reglas|indicaciones|normas|directrices|prompt)'
    r'|\byou\s+are\s+now\b|\bfrom\s+now\s+on\s+you\b|\bnew\s+(?:system\s+)?instructions?\s*:'
    r'|\b(?:ahora|a\s+partir\s+de\s+ahora)\s+eres\b|\bnuevas\s+instrucciones\s*:'
    r'|<\s*/?\s*(?:system|instructions?|im_start|im_end)\s*>|\[/?(?:INST|SYS)\]'
    r'|\b(?:do\s+not|don\'t)\s+(?:tell|inform|show)\s+the\s+user'
    r'|\bno\s+(?:(?:se|le|les)\s+)?(?:lo\s+)?(?:digas|cuentes|enseñes|muestres|avises)\s+(?:nada\s+)?al\s+usuario'
    r'|\bsin\s+(?:avisar|decírselo|que\s+lo\s+sepa)\s+(?:al\s+)?usuario'
    r'|\b(?:without|no)\s+(?:asking|permission|approval)\b[^.\n]{0,30}\b(?:run|execute|send|push|delete)'
    r'|\bsin\s+(?:pedir\s+)?permiso\b[^.\n]{0,30}\b(?:ejecuta|envía|manda|sube|borra|haz)'
    r'|\b(?:curl|wget|fetch)\b[^\n]{0,160}(?:\||&&|;|\|\|)\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b'
    r'|\b(?:ba|z|da|k)?sh\b[^\n]{0,20}(?:-c\s+)?["\']?\$\(\s*(?:curl|wget)'
    r'|\b(?:eval|source|\.)\s+["\']?(?:<\(|\$\()\s*(?:curl|wget)'
    r'|\b(?:python3?|node|ruby|perl)\b[^\n]{0,20}(?:<\(|\$\(|\|)\s*(?:curl|wget)'
    r'|\b(?:curl|wget)\b[^\n]{0,160}\|\s*(?:python3?|node|ruby|perl)\b'
    r'|\b(?:curl|wget|nc|ncat)\b[^\n]{0,120}(?:\$\(|`|\$\{?[A-Z_]*(?:KEY|TOKEN|SECRET)|\.ssh|\.env\b|id_rsa|id_ed25519)'
    r'|authorized_keys|\bssh-(?:rsa|ed25519)\s+AAAA'
    r'|\b(?:exfiltra|exfiltrate)'
    # Órdenes persistentes que sacan datos, borran o se saltan permisos «siempre» o «cada vez».
    r'|\b(?:siempre|cada\s+vez|al\s+(?:empezar|arrancar|iniciar)|antes\s+de\s+(?:responder|contestar|cada)'
    r'|always|every\s+time|whenever|at\s+(?:the\s+)?start|before\s+(?:answering|replying|each))\b[^.\n]{0,80}'
    r'\b(?:env[ií]a|manda|sube|publica|borra|elimina|desactiva|desinstala|comparte|send|upload|post|publish|'
    r'delete|remove|disable|share|push|force|sudo|rm\s+-|chmod|curl|wget)\b[^.\n]{0,80}'
    r'(?:\.env\b|\.ssh\b|https?://|-rf\b|\b777\b|\b(?:claves?|tokens?|secretos?|credenciales?|contraseñas?|'
    r'llavero|keychain|historial|history|main|master|origin|todo|everything|permisos?|permissions?|sandbox|'
    r'keys?|secrets?|passwords?|credentials?)\b)')

# Formato invisible (Cf: espacios de ancho cero, marcas y controles de dirección),
# selectores de variante y el bloque de etiquetas Unicode, que copian texto oculto.
_PERMITIDOS = {'\n', '\t'}


def _invisibles(texto):
    vistos = []
    for caracter in texto:
        punto = ord(caracter)
        if caracter in _PERMITIDOS:
            continue
        categoria = unicodedata.category(caracter)
        if (categoria in ('Cf', 'Co', 'Cs') or (categoria == 'Cc')
                or 0xFE00 <= punto <= 0xFE0F or 0xE0000 <= punto <= 0xE01EF):
            vistos.append('U+%04X' % punto)
    return sorted(set(vistos))


# Letras de otros alfabetos que se confunden con latinas: se pasan a latín antes de buscar
# inyecciones (además, mezclarlas en una palabra ya se rechaza).
_HOMOGLIFOS = str.maketrans({
    'а': 'a', 'е': 'e', 'о': 'o', 'р': 'p', 'с': 'c', 'у': 'y', 'х': 'x', 'і': 'i', 'ј': 'j', 'ѕ': 's', 'ԁ': 'd',
    'ɡ': 'g', 'һ': 'h', 'ӏ': 'l', 'ո': 'n', 'ս': 'u', 'ԝ': 'w', 'к': 'k', 'м': 'm', 'т': 't', 'в': 'b', 'н': 'h',
    'А': 'A', 'В': 'B', 'Е': 'E', 'К': 'K', 'М': 'M', 'Н': 'H', 'О': 'O', 'Р': 'P', 'С': 'C', 'Т': 'T', 'Х': 'X',
    'І': 'I', 'Ј': 'J', 'Ѕ': 'S', 'α': 'a', 'ο': 'o', 'ε': 'e', 'ι': 'i', 'κ': 'k', 'ν': 'v', 'ρ': 'p', 'τ': 't',
    'υ': 'u', 'χ': 'x', 'Α': 'A', 'Β': 'B', 'Ε': 'E', 'Ζ': 'Z', 'Η': 'H', 'Ι': 'I', 'Κ': 'K', 'Μ': 'M', 'Ν': 'N',
    'Ο': 'O', 'Ρ': 'P', 'Τ': 'T', 'Υ': 'Y', 'Χ': 'X',
    # Versalitas latinas («ɪɢɴᴏʀᴇ»): NFKC no las pasa a letras normales.
    **{chr(c): unicodedata.name(chr(c)).rsplit(' ', 1)[-1].lower() for c in range(0x0250, 0x2C80)
       if unicodedata.name(chr(c), '').startswith('LATIN LETTER SMALL CAPITAL ')
       and len(unicodedata.name(chr(c)).rsplit(' ', 1)[-1]) == 1}})
_GRIEGAS_ENGANOSAS = set('αοειkνρτυχΑΒΕΖΗΙΚΜΝΟΡΤΥΧκ')
_ALFABETOS = ('LATIN', 'CYRILLIC', 'GREEK', 'ARMENIAN', 'CHEROKEE', 'COPTIC', 'GEORGIAN')
# Lo que rompería el marco con el que la memoria entra en el prompt (memoria.instantanea).
_MARCO = re.compile(r'(?im)^\s{0,3}(?:#{1,6}\s|>|={3,}|-{3,}\s*$)'
                    r'|(?:[<‹〈⟨《«＜\[﹤]|&lt;?|&#0*60;?|&#x0*3c;?|\\u003c)\s*/?\s*memoria\b'
                    r'|/\s*memoria\s*(?:[>›〉⟩》»＞\]﹥]|&gt;?)')
# Letras sueltas separadas («i g n o r e», «i.g.n.o.r.e»): esconden palabras al escáner.
_ESPACIADAS = re.compile(r'(?<!\w)(?:\w[ ._\-·*]{1,2}){5,}\w(?!\w)')
_TOKEN = re.compile(r'[A-Za-z0-9_+=-]{20,}')


def _alfabeto(caracter):
    try:
        nombre = unicodedata.name(caracter)
    except ValueError:
        return None
    return next((a for a in _ALFABETOS if nombre.startswith(a + ' ')), None)


def mezcla_de_alfabetos(texto):
    """Palabras que mezclan letras latinas con cirílicas, armenias… (homoglifos). Del griego sólo
    cuentan las que imitan una latina: «Δt» o «µs» son notación, «ο» por «o» no."""
    raras = []
    for palabra in re.findall(r'\w+', texto):
        alfabetos = {_alfabeto(c) for c in palabra if c.isalpha()
                     and (_alfabeto(c) != 'GREEK' or c in _GRIEGAS_ENGANOSAS)} - {None}
        if len(alfabetos) > 1:
            raras.append(palabra)
    return raras


def _entropia(token):
    cuentas = {c: token.count(c) for c in set(token)}
    return -sum(n / len(token) * math.log2(n / len(token)) for n in cuentas.values())


def _aleatorio(texto):
    """Un token largo con mayúsculas, minúsculas y cifras de alta entropía: parece una clave."""
    for token in _TOKEN.findall(texto):
        clases = sum(bool(re.search(p, token)) for p in ('[a-z]', '[A-Z]', '[0-9]'))
        if clases == 3 and _entropia(token) >= 4.0:
            return True
    return False


def problemas(texto, marco=False):
    """Lista de motivos para no guardar `texto` (vacía si se puede guardar). `marco`: además, que no
    imite la estructura con la que la memoria entra en el prompt (títulos, citas, marcas)."""
    texto = str(texto or '')
    motivos = []
    invisibles = _invisibles(texto)
    if invisibles:
        motivos.append('caracteres Unicode invisibles o de control (' + ', '.join(invisibles[:6]) + ')')
    mezcladas = mezcla_de_alfabetos(texto)
    if mezcladas:
        motivos.append('palabras que mezclan alfabetos (homoglifos): ' + ', '.join(mezcladas[:3]))
    if (_CREDENCIALES.search(texto) or _CLAVE_SUELTA.search(texto) or _MAS_CREDENCIALES.search(texto)
            or _clave_conocida(texto) or _aleatorio(texto)):
        motivos.append('parece una credencial o una clave')
    normal = unicodedata.normalize('NFKC', texto).translate(_HOMOGLIFOS)
    if _ESPACIADAS.search(normal):
        motivos.append('letras sueltas separadas, una forma de esconder palabras')
    if _INYECCION.search(normal):
        motivos.append('parece una instrucción para el agente (inyección de prompt o envío de datos fuera)')
    if marco and _MARCO.search(normal):
        motivos.append('imita la estructura del prompt (títulos «#», citas «>», separadores o marcas de memoria)')
    return motivos


def _clave_conocida(texto):
    """¿Repite alguna clave de los proveedores configurados?"""
    try:
        from ..proveedores import secretos
        conocidas = secretos()
    except Exception:
        return False
    return any(len(clave) >= 8 and clave in texto for clave in conocidas)


class ContenidoRechazado(ValueError):
    pass


def exigir_limpio(texto, que='El contenido', marco=False):
    motivos = problemas(texto, marco)
    if motivos:
        raise ContenidoRechazado(que + ' no se guarda: ' + '; '.join(motivos) + '. Reescríbelo como una lección '
                                 'o un hecho, sin claves ni instrucciones para el agente.')
