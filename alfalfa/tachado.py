"""Las contraseñas de las órdenes no se guardan en disco.

Una orden que el modelo escribe con la clave dentro (`PGPASSWORD=… psql`,
`sshpass -p … ssh`, `mysql -p…`, `curl -u usuario:clave`, `--token …`) se
guardaba tal cual en el historial de la sesión, la transcripción y la tarea. No
acaba en git (todo vive en el hogar de AlfalfaCode, con permisos 0600), pero un
programa que rastree la carpeta personal en busca de claves la encontraría: es
lo que hizo el gusano Shai-Hulud con `.claude/settings.local.json` (The Hacker
News, 11/2026).

Aquí se tachan antes de escribir:

- `tachar_texto`: una orden en texto (`sh -c "…"`, la descripción de una
  llamada, el aviso de aprobación). Las formas de `busqueda_sesiones.redaccion`
  (asignaciones `ALGO_TOKEN=…`, `usuario:clave@servidor`, cabeceras
  `Authorization:`, `Bearer …`, claves PEM y JWT, `sk-…`, `ghp_…`, `AKIA…`) y
  las opciones de las órdenes que llevan la clave.
- `tachar_argv`: lo mismo para una orden en lista, donde la clave suele ir en
  el argumento siguiente a la opción.
- `tachar_ordenes`: recorre un registro, un mensaje o un estado y tacha sólo
  las órdenes (`argv`, `command`…, los argumentos de las llamadas y la salida
  JSON de las herramientas). Lo demás (un archivo leído, la salida de un
  programa) no se toca: tacharlo cambiaría lo que el modelo ve al reanudar.

En memoria nada cambia: el modelo y la orden en curso siguen viendo la orden
entera, y el usuario la ve entera al aprobarla. Al reanudar una sesión, las
órdenes de antes llegan tachadas. Es una red, no una garantía: una clave sin
forma reconocible (un argumento suelto) puede pasar.

Tachar dos veces deja lo mismo: el historial compara lo que va a escribir con
lo que leyó, ya tachado.
"""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from .busqueda_sesiones.redaccion import PATRONES, REDACTADO

# Una opción larga cuyo valor es una clave: --password, --api-key, --client-secret, --auth-token…
# No las que llevan a dónde está la clave (--password-file, --token-env, --secret-id).
_OPCION_SECRETA = re.compile(
    r'^--[\w-]*(?:pass(?:word|wd|phrase)?|secret|token|api-?key|apikey|access-?key|credentials?|auth)[\w-]*$',
    re.IGNORECASE)
_OPCION_A_OTRO_SITIO = re.compile(r'-(?:file|path|dir|env|var|stdin|fd|cmd|command|helper|id|name|type|url)s?$',
                                  re.IGNORECASE)
# Programas que aceptan la contraseña pegada a -p (mysql -psecreto) o detrás (sshpass -p secreto).
_P_PEGADA = frozenset({'mysql', 'mysqldump', 'mysqladmin', 'mysqlimport', 'mysqlshow', 'mysqlcheck', 'mariadb',
                       'mariadb-dump', 'mariadb-admin'})
_P_DETRAS = frozenset({'sshpass'})
# curl -u usuario:clave, --proxy-user…
_USUARIO = frozenset({'-u', '--user', '--proxy-user', '-U'})

_VALOR = r'([^\s\'"`;|&]+)'
_EN_TEXTO = (
    # sshpass -p clave ssh …
    (re.compile(r'(\bsshpass\s+(?:-[a-oq-zA-Z]\S*\s+)*-p\s*)' + _VALOR), r'\1' + REDACTADO),
    # mysql -h db -pclave
    (re.compile(r'(\b(?:' + '|'.join(sorted(_P_PEGADA, key=len, reverse=True)) + r')\b[^|;&\n]*?\s-p)'
                + r'(?=[^\s\'"`;|&])' + _VALOR), r'\1' + REDACTADO),
    # curl -u usuario:clave
    (re.compile(r'((?<![\w-])(?:-u|-U|--user|--proxy-user)(?:\s+|=)["\']?[^\s:\'"]+:)' + _VALOR),
     r'\1' + REDACTADO),
)
_OPCION_EN_TEXTO = re.compile(r'((?<![\w-])(--[\w-]+)(?:=|\s+)["\']?)' + _VALOR)
# Una orden en lista dentro de un texto: «Ejecutar ["sshpass", "-p", "…"]» (el aviso de aprobar).
_LISTA_EN_TEXTO = re.compile(r'\[\s*"(?:[^"\\\n]|\\.)*"(?:\s*,\s*"(?:[^"\\\n]|\\.)*")*\s*\]')


def _opcion_secreta(opcion):
    return bool(_OPCION_SECRETA.match(opcion)) and not _OPCION_A_OTRO_SITIO.search(opcion)


def _valor_tachable(valor):
    # Un número (--max-tokens 100) o una opción que sigue no son claves.
    return bool(valor) and not valor.isdigit() and not valor.startswith('-') and valor != REDACTADO


def _opcion_en_texto(coincidencia):
    if _opcion_secreta(coincidencia.group(2)) and _valor_tachable(coincidencia.group(3)):
        return coincidencia.group(1) + REDACTADO
    return coincidencia.group(0)


def tachar_texto(texto):
    """`texto` (una orden o la descripción de una) sin lo que tiene forma de clave."""
    if not isinstance(texto, str) or not texto:
        return texto
    if '["' in texto or '[ "' in texto:
        texto = _LISTA_EN_TEXTO.sub(_lista_en_texto, texto)
    for patron, sustituto in PATRONES:
        texto = patron.sub(sustituto, texto)
    for patron, sustituto in _EN_TEXTO:
        texto = patron.sub(sustituto, texto)
    return _OPCION_EN_TEXTO.sub(_opcion_en_texto, texto)


def _lista_en_texto(coincidencia):
    try:
        argv = json.loads(coincidencia.group(0))
    except ValueError:
        return coincidencia.group(0)
    tachada = tachar_argv(argv)
    return coincidencia.group(0) if tachada is argv else json.dumps(tachada, ensure_ascii=False)


def tachar_argv(argv):
    """La misma orden en lista, con las claves tachadas; la misma lista si no había ninguna."""
    if not isinstance(argv, list):
        return argv
    programa = None
    tachada, siguiente = [], False
    for i, argumento in enumerate(argv):
        if not isinstance(argumento, str):
            tachada.append(argumento)
            siguiente = False
            continue
        if programa is None and not re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', argumento) and argumento not in (
                'env', 'sudo', 'command', 'exec', 'time', 'nohup'):
            programa = PurePosixPath(argumento).name
        anterior = argv[i - 1] if i and isinstance(argv[i - 1], str) else ''
        if siguiente and _valor_tachable(argumento):
            tachada.append(REDACTADO)
        elif anterior in _USUARIO and ':' in argumento:
            tachada.append(argumento.split(':', 1)[0] + ':' + REDACTADO)
        elif programa in _P_PEGADA and argumento.startswith('-p') and len(argumento) > 2 \
                and argumento[2:] != REDACTADO:
            tachada.append('-p' + REDACTADO)
        elif programa in _P_DETRAS and argumento.startswith('-p') and len(argumento) > 2:
            tachada.append('-p' + REDACTADO)
        elif argumento.startswith('--') and '=' in argumento and _opcion_secreta(argumento.split('=', 1)[0]):
            opcion, valor = argumento.split('=', 1)
            tachada.append(opcion + '=' + REDACTADO if _valor_tachable(valor) else argumento)
        else:
            tachada.append(tachar_texto(argumento))
        siguiente = ((argumento.startswith('--') and '=' not in argumento and _opcion_secreta(argumento))
                     or (programa in _P_DETRAS and argumento == '-p'))
    return tachada if tachada != argv else argv


# Campos que llevan una orden en texto.
CAMPOS_DE_ORDEN = frozenset({'command', 'comando', 'cmd', 'orden', 'approval'})


def tachar_ordenes(valor, _profundidad=0):
    """`valor` con sus órdenes tachadas; el mismo objeto si no había nada que tachar."""
    if _profundidad > 12:
        return valor
    if isinstance(valor, dict):
        cambios = {}
        for clave, dentro in valor.items():
            if clave == 'argv' and isinstance(dentro, list):
                nuevo = tachar_argv(dentro)
            elif clave in CAMPOS_DE_ORDEN and isinstance(dentro, str):
                nuevo = tachar_texto(dentro)
            elif clave == 'arguments' and isinstance(dentro, str):
                nuevo = _json_en_texto(dentro, _profundidad)
            elif clave == 'content' and isinstance(dentro, str) and valor.get('role') == 'tool':
                nuevo = _json_en_texto(dentro, _profundidad)
            else:
                nuevo = tachar_ordenes(dentro, _profundidad + 1)
            if nuevo is not dentro:
                cambios[clave] = nuevo
        return {**valor, **cambios} if cambios else valor
    if isinstance(valor, list):
        nueva = [tachar_ordenes(v, _profundidad + 1) for v in valor]
        return nueva if any(n is not v for n, v in zip(nueva, valor)) else valor
    return valor


def _json_en_texto(texto, profundidad):
    """Los argumentos de una llamada o la salida de una herramienta, que van como JSON en texto."""
    if '{' not in texto:
        return texto
    try:
        dentro = json.loads(texto)
    except ValueError:
        return texto
    if not isinstance(dentro, (dict, list)):
        return texto
    tachado = tachar_ordenes(dentro, profundidad + 1)
    return texto if tachado is dentro else json.dumps(tachado, ensure_ascii=False)


def tachar_textos(valor, _profundidad=0):
    """Todas las cadenas de `valor` con `tachar_texto` (una línea de la transcripción)."""
    if _profundidad > 12:
        return valor
    if isinstance(valor, str):
        return tachar_texto(valor)
    if isinstance(valor, dict):
        return {k: tachar_textos(v, _profundidad + 1) for k, v in valor.items()}
    if isinstance(valor, list):
        return [tachar_textos(v, _profundidad + 1) for v in valor]
    return valor
