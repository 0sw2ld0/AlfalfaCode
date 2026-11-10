"""Lo que nunca sale de una búsqueda en sesiones anteriores: secretos y razonamiento.

Se aplica dos veces: al guardar en el índice (así el archivo del índice no los
tiene) y al devolver un fragmento (una clave guardada después de indexar
también se tapa). Es la redacción que ya hace el arnés, más algo propio:

- Las claves conocidas (`proveedores.secretos()`, las de los servidores MCP)
  se sustituyen por `[REDACTADO]`, como `stdio.Transport.clean`.
- En la salida de una herramienta y en los argumentos de una llamada, los
  campos con nombre sensible (`mcp_cliente.gestor.CLAVE_SENSIBLE`: token, key,
  password, auth, cookie…) se tapan enteros, también si son listas u objetos,
  como en las tarjetas de las llamadas MCP. En un texto con JSON dentro (una
  salida larga guardada cortada, un archivo leído), lo mismo con cada
  `"clave": "valor"` de nombre sensible.
- Las asignaciones `ALGO_TOKEN=…` (`permisos.rutas.RE_SECRETO`), `password: …`,
  las cabeceras `Authorization:`, `Cookie:` y `Set-Cookie:`, `Bearer …`, la
  clave de `esquema://usuario:clave@servidor`, las claves privadas PEM, las
  formas conocidas de claves de API (`sk-…`, `ghp_…`, `AKIA…`) y lo que tiene
  forma de clave secreta de AWS también se tapan.
- El resultado de una herramienta que leyó o listó un archivo sensible
  (`.env`, `.ssh`, `id_rsa`, `*.pem`, `credentials.json`… según
  `permisos.rutas` y `NOMBRES_SECRETOS`) o que volcó el entorno o un almacén de
  claves (`env`, `printenv`, `security`, `op`, `gh auth token`, `aws configure
  get`…, también dentro de `sh -c "…"`) no se guarda: queda una marca.

Es una red, no una garantía: un secreto sin forma reconocible en una salida
cualquiera (una contraseña suelta impresa por un programa) puede pasar.

Los bloques de razonamiento (`thinking`, `alfalfa_bloques`, `redacted_thinking`)
nunca se leen: `messages.jsonl` no los tiene y, si una sesión vieja los trae,
sólo se toma el texto visible del mensaje.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import PurePosixPath

REDACTADO = '[REDACTADO]'
OMITIDO = '[Salida omitida en la búsqueda: la herramienta leyó un archivo o un entorno con secretos]'
# Claves que se tapan enteras (la misma lista que las tarjetas MCP).
CLAVE_SENSIBLE = re.compile(r'pass|secret|token|key|auth|credential|cookie|bearer', re.IGNORECASE)
_VALOR = r'([^\s"\'`,;]{3,})'
PATRONES = (
    # export GITHUB_TOKEN=abc (el nombre de una variable que parece un secreto, permisos.rutas.RE_SECRETO)
    (re.compile(r'(\b(?:[A-Z0-9_]*(?:TOKEN|KEY|SECRET|PASSWORD|PASSWD|PASSPHRASE|CREDENTIAL)[A-Z0-9_]*'
                r'|(?:[A-Z0-9]+_)*(?:PASS|PWD))["\']?\s*[=:]\s*["\']?)' + _VALOR), r'\1' + REDACTADO),
    # db_pass: abc, "pwd": "abc", passphrase=abc (pass y pwd como palabra: «compass» o «passenger» no)
    (re.compile(r'(?i)(\b(?:[a-z0-9]+[_.-])*(?:pass|pwd|passphrase)["\']?\s*[=:]\s*["\']?)' + _VALOR),
     r'\1' + REDACTADO),
    # "api_key": "abc", password: abc, client-secret=abc, "private_key_id": "abc"
    (re.compile(r'(?i)(\b[a-z0-9_.-]*(?:token|api[_-]?key|secret|password|passwd|credential|private[_-]?key'
                r'|access[_-]?key|session[_-]?id|dockerconfig(?:json)?)[a-z0-9_.-]*["\']?\s*[=:]\s*["\']?)' + _VALOR),
     r'\1' + REDACTADO),
    # document.cookie = "a=1; b=2": el valor de cada cookie, se llame como se llame.
    (re.compile(r'(?i)(document\.cookie\s*=\s*["\'`])([^"\'`]*)'),
     lambda m: m.group(1) + re.sub(r'([^=;\s]+=)[^;]*', r'\1' + REDACTADO, m.group(2))),
    # Cabeceras: lo que va detrás, hasta el final de la línea.
    (re.compile(r'(?im)(\b(?:proxy-)?authorization\s*:\s*)[^\r\n]+'), r'\1' + REDACTADO),
    (re.compile(r'(?im)(\b(?:set-)?cookie\s*:\s*)[^\r\n]+'), r'\1' + REDACTADO),
    (re.compile(r'(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]{8,}'), r'\1' + REDACTADO),
    (re.compile(r'(?i)(\bbasic\s+)[A-Za-z0-9+/=]{8,}'), r'\1' + REDACTADO),
    # https://usuario:clave@servidor
    (re.compile(r'(?i)(\b[a-z][a-z0-9+.-]*://[^\s:/@\'"]+:)[^\s@/\'"]+(@)'), r'\1' + REDACTADO + r'\2'),
    (re.compile(r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)'),
     REDACTADO),
    (re.compile(r'\b(?:sk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}'
                r'|glpat-[A-Za-z0-9_-]{16,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})'),
     REDACTADO),
)
# Una clave secreta de AWS suelta: 40 caracteres de base64 con mayúsculas, minúsculas y cifras (un sha1
# de git, todo hexadecimal en minúsculas, no lo es).
_AWS = re.compile(r'(?<![A-Za-z0-9/+=])[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+=])')
# "clave": "valor" dentro de un texto, también escapado (\"clave\": \"valor\", con las barras que sean) en
# un JSON dentro de otro, y con comillas simples ({'DB_PASS': 'x'}, lo que imprime Python).
_JSON_CLAVE = re.compile(r'(\\*["\']([^"\'\\\n]{1,60})\\*["\']\s*:\s*\\*["\'])([^"\'\\\n]*)')
# Pares nombre/valor (cabeceras de un HAR, variables de entorno de una API): {"name": "X-Api-Key", "value": "…"}.
_PAR = re.compile(r'\{[^{}]{0,2000}\}')
_NOMBRE_DEL_PAR = re.compile(r'\\*["\'](?:name|key)\\*["\']\s*:\s*\\*["\']([^"\'\\\n]{1,100})')
_VALOR_DEL_PAR = re.compile(r'(\\*["\']value\\*["\']\s*:\s*\\*["\'])([^"\'\\\n]*)')
# Órdenes cuya salida es el entorno, el Llavero o un almacén de claves. Las de SIEMPRE cuentan en cualquier
# lugar de la orden (find … -exec env ;, xargs -0 printenv, timeout 5 env); las otras, como orden.
SIEMPRE_SECRETAS = frozenset({'env', 'printenv', 'security', 'op', 'gopass', 'keepassxc-cli', 'bw', 'vault'})
ORDENES_SECRETAS = SIEMPRE_SECRETAS | {'set', 'export', 'declare', 'pass'}
# Código que lee el entorno (python3 -c "print(os.environ)", node -e "console.log(process.env)").
CODIGO_DEL_ENTORNO = re.compile(r'os\.environ|process\.env|\bENV\[|getenv|\$ENV\{|Environment\.GetEnvironment')
# Y las que lo son con sus primeros argumentos.
SUBORDENES_SECRETAS = (('gh', 'auth', 'token'), ('gh', 'auth', 'status'), ('aws', 'configure', 'get'),
                       ('aws', 'configure', 'export-credentials'), ('aws', 'sts', 'get-session-token'),
                       ('gcloud', 'auth', 'print-access-token'), ('gcloud', 'auth', 'print-identity-token'),
                       ('git', 'credential'), ('docker', 'login'), ('npm', 'token'), ('heroku', 'auth:token'),
                       ('kubectl', 'config', 'view'), ('kubectl', 'get', 'secret'), ('kubectl', 'get', 'secrets'),
                       ('kubectl', 'describe', 'secret'))
SHELLS = frozenset({'sh', 'bash', 'zsh', 'dash', 'ksh', 'fish'})
# Archivos con secretos por su nombre (rutas.sensible compara tramos enteros: «credentials», no
# «credentials.json»).
NOMBRES_SECRETOS = re.compile(
    r'(?i)^(?:[\w.-]*[._-])?(?:credentials?|secrets?|tokens?|passwords?|service[-_]?account[\w.-]*|client[-_]secret)'
    r'(?:\.(?:json|ya?ml|toml|ini|txt|env|cfg|conf|properties|xml|csv|plist))?$'
    r'|^\.(?:netrc|npmrc|pypirc|pgpass|htpasswd|git-credentials)$|\.(?:p12|pfx|keystore|jks|kdbx)$')
MIN_SECRETO = 8


def secretos_conocidos():
    """Las claves de los proveedores y de los servidores MCP de este hogar; nunca falla."""
    valores = set()
    try:
        from .. import proveedores
        valores |= {s for s in proveedores.secretos() if isinstance(s, str) and len(s) >= MIN_SECRETO}
    except Exception:
        pass
    try:
        from ..mcp_cliente import configuracion
        from ..terminal import home_dir
        servidores, _ = configuracion.cargar(home_dir())
        for servidor in servidores:
            valores |= {s for s in servidor.secretos() if isinstance(s, str) and len(s) >= MIN_SECRETO}
    except Exception:
        pass
    return sorted(valores, key=len, reverse=True)


def tapar(texto, secretos=()):
    """`texto` sin las claves conocidas ni lo que tiene forma de secreto."""
    if not isinstance(texto, str) or not texto:
        return '' if not isinstance(texto, str) else texto
    for secreto in secretos:
        if secreto and secreto in texto:
            texto = texto.replace(secreto, REDACTADO)
    for patron, sustituto in PATRONES:
        texto = patron.sub(sustituto, texto)
    texto = _JSON_CLAVE.sub(_valor_sensible, texto)
    texto = _PAR.sub(_par_sensible, texto)
    return _AWS.sub(_aws, texto)


def _par_sensible(coincidencia):
    objeto = coincidencia.group(0)
    nombre = _NOMBRE_DEL_PAR.search(objeto)
    if nombre and CLAVE_SENSIBLE.search(nombre.group(1)):
        return _VALOR_DEL_PAR.sub(lambda m: m.group(1) + (REDACTADO if m.group(2) else ''), objeto)
    return objeto


def _valor_sensible(coincidencia):
    if CLAVE_SENSIBLE.search(coincidencia.group(2)) and coincidencia.group(3) != REDACTADO:
        return coincidencia.group(1) + (REDACTADO if coincidencia.group(3) else '')
    return coincidencia.group(0)


def _aws(coincidencia):
    trozo = coincidencia.group(0)
    if (any(c.isupper() for c in trozo) and any(c.islower() for c in trozo) and any(c.isdigit() for c in trozo)
            and trozo.count('/') <= 4):
        return REDACTADO
    return trozo


def _hojas(valor, clave='', profundidad=0):
    """Las cadenas de un JSON, con su clave; las de clave sensible, tapadas."""
    if profundidad > 8:
        return
    if isinstance(valor, dict):
        nombre = valor.get('name', valor.get('key'))
        par_sensible = isinstance(nombre, str) and bool(CLAVE_SENSIBLE.search(nombre))
        for k, v in valor.items():
            if par_sensible and k == 'value' and v not in (None, ''):
                yield k, REDACTADO
                continue
            if isinstance(k, str) and CLAVE_SENSIBLE.search(k):
                # También una lista o un objeto entero: {"cookies": [...]}.
                if v not in (None, '', [], {}):
                    yield k, REDACTADO
            else:
                yield from _hojas(v, str(k), profundidad + 1)
    elif isinstance(valor, list):
        for v in valor:
            yield from _hojas(v, clave, profundidad + 1)
    elif isinstance(valor, str):
        yield clave, valor
    elif isinstance(valor, (int, float)) and not isinstance(valor, bool):
        yield clave, str(valor)


# Campos de la salida de una herramienta que no dicen nada a quien busca.
_SIN_INTERES = frozenset({'sha256', 'expected_sha256', 'start_line', 'total_lines', 'classification',
                          'workspace_context', 'retry_guard_key', 'fingerprint'})


def texto_de_salida(contenido):
    """La salida de una herramienta (un JSON en texto) como líneas legibles «clave: valor»."""
    if not isinstance(contenido, str):
        contenido = json.dumps(contenido, ensure_ascii=False) if contenido is not None else ''
    try:
        valor = json.loads(contenido)
    except ValueError:
        return _json_incompleto_visible(contenido)
    if isinstance(valor, dict) and valor.get('truncated') is True and isinstance(valor.get('text'), str):
        # La salida larga se guarda cortada como texto JSON a medias: no se puede leer como JSON, así
        # que `tapar` busca sus «"clave": "valor"» sensibles con una expresión regular.
        return _json_incompleto_visible(valor['text'])
    lineas = []
    for clave, hoja in _hojas(_argumentos_visibles(valor)):
        if clave in _SIN_INTERES or not hoja.strip():
            continue
        lineas.append((clave + ': ' if clave else '') + hoja)
    return '\n'.join(lineas)


def _json_incompleto_visible(texto):
    """La salida cortada puede conservar un argv completo antes del campo que se cortó."""
    patron = re.compile(r'"(argv|command|comando)"\s*:\s*')
    decodificador = json.JSONDecoder()
    partes, inicio = [], 0
    for m in patron.finditer(texto):
        if m.start() < inicio:
            continue
        partes.append(texto[inicio:m.end()])
        try:
            valor, fin = decodificador.raw_decode(texto, m.end())
        except ValueError:
            # No se puede identificar dónde termina el valor: se descarta el resto.
            partes.append(json.dumps(REDACTADO))
            return ''.join(partes)
        if m[1] == 'argv' and isinstance(valor, list):
            valor = _argv_visibles(valor)
        elif isinstance(valor, str):
            valor = _orden_visible(valor)
        else:
            valor = REDACTADO
        partes.append(json.dumps(valor, ensure_ascii=False))
        inicio = fin
    partes.append(texto[inicio:])
    return ''.join(partes)


def _argv_visibles(argv, profundidad=0):
    visibles, ocultar = [], False
    sensibles = {'-u', '--user', '-p', '--pass', '--password', '--token', '--api-key', '--secret'}
    for argumento in argv:
        if ocultar:
            visibles.append(REDACTADO)
            ocultar = False
            continue
        if not isinstance(argumento, str):
            visibles.append(argumento)
            continue
        opcion, igual, _ = argumento.partition('=')
        if argumento.startswith(('-p', '-u')) and len(argumento) > 2:
            visibles.append(argumento[:2] + REDACTADO)
        elif opcion in sensibles or (opcion.startswith('-') and CLAVE_SENSIBLE.search(opcion)):
            visibles.append(opcion + '=' + REDACTADO if igual else opcion)
            ocultar = not igual
        elif any(c.isspace() for c in argumento):
            visibles.append(_orden_visible(argumento, profundidad + 1) if profundidad < 2 else REDACTADO)
        else:
            visibles.append(argumento)
    return visibles


def _orden_visible(texto, profundidad=0):
    try:
        argv = shlex.split(texto)
    except ValueError:
        argv = texto.split()
    visibles = _argv_visibles(argv, profundidad)
    return shlex.join(visibles) if visibles != argv else texto


def _argumentos_visibles(valor, profundidad=0):
    if profundidad > 8:
        return REDACTADO
    if isinstance(valor, dict):
        return {k: _argv_visibles(v) if k == 'argv' and isinstance(v, list)
                else _orden_visible(v) if k in ('command', 'comando') and isinstance(v, str)
                else _argumentos_visibles(v, profundidad + 1) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_argumentos_visibles(v, profundidad + 1) for v in valor]
    return valor


def argumentos_visibles(argumentos, limite=600):
    """Los argumentos de una llamada en una línea, con los campos sensibles tapados."""
    if isinstance(argumentos, str):
        try:
            argumentos = json.loads(argumentos)
        except ValueError:
            return _orden_visible(argumentos)[:limite]
    partes = []
    for clave, hoja in _hojas(_argumentos_visibles(argumentos)):
        if clave in _SIN_INTERES:
            continue
        partes.append((clave + '=' if clave else '') + ' '.join(hoja.split())[:200])
    return ' · '.join(partes)[:limite]


def _parece_ruta(texto):
    return isinstance(texto, str) and 0 < len(texto) < 4096 and ('/' in texto or '.' in texto)


def _ruta_secreta(trozo):
    from ..permisos import rutas
    trozo = trozo.strip('\'"<>()')
    if not _parece_ruta(trozo):
        return False
    partes = PurePosixPath(trozo).parts
    if rutas.sensible(trozo) or any(rutas.privado_del_espacio(p) and p not in ('.git', '.DS_Store') for p in partes):
        return True
    return bool(partes) and bool(NOMBRES_SECRETOS.search(partes[-1]))


def _orden_secreta(palabras):
    """¿La orden `palabras` vuelca el entorno o un almacén de claves? Mira dentro de `sh -c "…"`."""
    palabras = [p for p in palabras if isinstance(p, str)]
    # Sin las asignaciones de delante (FOO=1 orden) ni `sudo`, `command`, `exec`, `time`.
    while palabras and (re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', palabras[0])
                        or palabras[0] in ('sudo', 'command', 'exec', 'time', 'nohup', 'xargs')):
        palabras = palabras[1:]
    if not palabras:
        return False
    nombre = PurePosixPath(palabras[0]).name
    if nombre in ORDENES_SECRETAS:
        return True
    llano = [PurePosixPath(p).name for p in palabras]
    # En cualquier lugar: lo que ejecutan find -exec, xargs, timeout, nice, watch…
    if any(p in SIEMPRE_SECRETAS for p in llano):
        return True
    for i in range(len(llano)):
        if any(tuple(llano[i:i + len(sub)]) == sub for sub in SUBORDENES_SECRETAS):
            return True
    if any(CODIGO_DEL_ENTORNO.search(p) for p in palabras):
        return True
    # sh -c "orden", bash -lc "orden", y cualquier otra palabra con espacios: otra línea de órdenes.
    return any(_linea_secreta(p) for p in palabras[1:] if not p.startswith('-') and (nombre in SHELLS or ' ' in p))


def _linea_secreta(linea):
    """Una línea de shell: cada orden separada por `;`, `&&`, `||`, `|` o salto de línea."""
    for orden in re.split(r'[;&|\n]+|\$\(|`', linea):
        try:
            palabras = shlex.split(orden)
        except ValueError:
            palabras = orden.split()
        if _orden_secreta(palabras):
            return True
    return False


def llamada_secreta(nombre, argumentos):
    """¿Lee o lista algo con secretos? Entonces su salida no entra en el índice."""
    if isinstance(argumentos, str):
        try:
            argumentos = json.loads(argumentos)
        except ValueError:
            argumentos = {}
    if not isinstance(argumentos, dict):
        return False
    argv = argumentos.get('argv') if isinstance(argumentos.get('argv'), list) else []
    if argv and _orden_secreta(argv):
        return True
    for _, valor in _hojas(argumentos):
        for trozo in str(valor).split():
            if _ruta_secreta(trozo):
                return True
    return False


def texto_visible(contenido):
    """El texto que vio el usuario de un mensaje: sin bloques de razonamiento ni imágenes."""
    if isinstance(contenido, str):
        return contenido
    if isinstance(contenido, list):
        trozos = []
        for bloque in contenido:
            if isinstance(bloque, str):
                trozos.append(bloque)
            elif isinstance(bloque, dict) and bloque.get('type') == 'text' and isinstance(bloque.get('text'), str):
                trozos.append(bloque['text'])
        return '\n'.join(trozos)
    return ''
