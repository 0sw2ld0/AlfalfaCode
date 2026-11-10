"""¿Prueba algo de verdad la comprobación con la que acabó el turno?

Una habilidad sólo se guarda si el camino acabó en verde, y `functional_command` (la
evidencia) acepta demasiado para eso: `python -c "assert 1"`, `ruff --version`,
`make -n test`, `npm test --if-present` o cualquier `python x.py`. Aquí se exige que la
orden sea:

- de pruebas, compilación o lint (functional_command) y sin opciones que no prueban nada
  (`--version`, `--help`, `-n`, `--dry-run`, `--collect-only`, `--if-present` sin ese
  script, `python -c`);
- y, además, una orden real del proyecto: detectada por sus archivos (pyproject.toml,
  Package.swift, package.json y sus scripts, Cargo.toml, go.mod, un Makefile con ese
  objetivo…), escrita en su AGENTS.md, o que nombra un archivo cambiado en el turno.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

from ..evidencia.espacio import functional_command

NO_PRUEBAN = {'--version', '-V', '--help', '-h', '-n', '--dry-run', '--just-print', '--recon', '--collect-only',
              '--co', '--list-tests', '--setup-plan', '--print-targets', '--passWithNoTests', '--pass-with-no-tests'}
MAX_LEIDO = 256 * 1024


def _leer(ruta):
    try:
        if ruta.is_file() and not ruta.is_symlink() and ruta.stat().st_size <= MAX_LEIDO:
            return ruta.read_text(encoding='utf-8', errors='replace')
    except OSError:
        pass
    return ''


def _makefile(raiz):
    # El primer archivo que make abre, incluso si está vacío o no podemos leerlo.
    for nombre in ('GNUmakefile', 'makefile', 'Makefile'):
        ruta = Path(raiz) / nombre
        if ruta.exists() or ruta.is_symlink():
            return _leer(ruta)
    return ''


def orden_final(argv):
    """La orden que decide: la de `sh -c "cd x && orden"`; None si no se puede saber cuál es."""
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return None
    programa = Path(argv[0]).name
    if programa in ('sh', 'bash', 'zsh') and len(argv) == 3 and argv[1] in ('-c', '-lc'):
        partes = [p.strip() for p in argv[2].split('&&')]
        if any(re.search(r'[|;<>`$\n]', p) for p in partes):
            return None
        try:
            ordenes = [shlex.split(p) for p in partes]
        except ValueError:
            return None
        if not ordenes or any(o[:1] != ['cd'] or len(o) != 2 for o in ordenes[:-1]) or not ordenes[-1]:
            return None
        return ordenes[-1]
    return list(argv)


def _herramienta(argv):
    """(herramienta, subórdenes) de una orden: `python -m pytest` es pytest."""
    programa = Path(argv[0]).name.lower()
    resto = argv[1:]
    if programa.startswith(('python', 'pypy')):
        while resto and resto[0] in ('-B', '-u', '-E', '-s', '-I'):
            resto = resto[1:]
        if resto[:1] == ['-m'] and len(resto) > 1:
            return resto[1], resto[2:]
        return 'python', resto
    return programa, resto


def ordenes_del_proyecto(raiz):
    """{herramienta: subórdenes válidas (None: cualquiera)} que el proyecto tiene de verdad."""
    raiz = Path(raiz)
    encontradas = {}
    pyproject = _leer(raiz / 'pyproject.toml')
    hay_pruebas = any(True for _ in _primeros(raiz, ('test_*.py', '*_test.py')))
    if ('[tool.pytest' in pyproject or (raiz / 'pytest.ini').is_file() or (raiz / 'conftest.py').is_file()
            or '[tool:pytest]' in _leer(raiz / 'setup.cfg') or (raiz / 'tox.ini').is_file() or hay_pruebas):
        encontradas['pytest'] = None
        encontradas['unittest'] = None
    if '[tool.ruff' in pyproject or (raiz / 'ruff.toml').is_file() or (raiz / '.ruff.toml').is_file():
        encontradas['ruff'] = {'check', 'format'}
    if '[tool.mypy' in pyproject or (raiz / 'mypy.ini').is_file():
        encontradas['mypy'] = None
    if (raiz / 'Package.swift').is_file():
        encontradas['swift'] = {'build', 'test'}
    paquete = _leer(raiz / 'package.json')
    if paquete:
        try:
            scripts = set((json.loads(paquete).get('scripts') or {}).keys())
        except (ValueError, AttributeError):
            scripts = set()
        for gestor in ('npm', 'pnpm', 'yarn', 'bun'):
            encontradas[gestor] = scripts
        encontradas['npx'] = {'tsc', 'eslint', 'vitest', 'jest'}
        encontradas['node'] = {'--test'}
    if (raiz / 'Cargo.toml').is_file():
        encontradas['cargo'] = {'test', 'build', 'check', 'clippy'}
    if (raiz / 'go.mod').is_file():
        encontradas['go'] = {'test', 'build', 'vet'}
    makefile = _makefile(raiz)
    objetivos = set(re.findall(r'(?m)^([A-Za-z0-9_.-]+)\s*:', makefile))
    if objetivos:
        encontradas['make'] = objetivos
    if any(True for _ in _primeros(raiz, ('*.xcodeproj', '*.xcworkspace'), hondo=False)):
        encontradas['xcodebuild'] = {'test', 'build'}
    return encontradas


def _primeros(raiz, patrones, hondo=True, limite=2000):
    vistos = 0
    pendientes = [raiz]
    while pendientes:
        carpeta = pendientes.pop()
        try:
            hijos = list(carpeta.iterdir())
        except OSError:
            continue
        for hijo in hijos:
            vistos += 1
            if vistos > limite:
                return
            if any(hijo.match(p) for p in patrones):
                yield hijo
            elif hondo and hijo.is_dir() and not hijo.is_symlink() and not hijo.name.startswith('.') \
                    and hijo.name not in ('node_modules', 'venv', '__pycache__', 'build', 'dist'):
                pendientes.append(hijo)


def _del_proyecto(herramienta, resto, encontradas, raiz):
    if herramienta not in encontradas:
        return False
    validas = encontradas[herramienta]
    if validas is None:
        return True
    palabras = [r for r in resto if not r.startswith('-') or r == '--test']
    if herramienta in ('npm', 'pnpm', 'yarn', 'bun'):
        if any(a.split('=')[0] in ('--prefix', '--cwd', '--dir', '-C', '--workspace', '-w') for a in resto):
            return False  # estas opciones cambian el manifiesto; no se acredita otra carpeta
        if '--if-present' in resto and not (palabras and palabras[-1] in validas):
            return False
        script = palabras[1] if palabras[:1] == ['run'] and len(palabras) > 1 else (palabras[0] if palabras else '')
        return script in validas or (script == 'test' and 'test' in validas)
    return any(p in validas for p in (palabras[:1] or [''])) or (herramienta == 'node' and '--test' in resto)


def _en_agents_md(argv, documentos):
    herramienta, resto = _herramienta(argv)
    formas = {' '.join([Path(argv[0]).name] + argv[1:3]), ' '.join([herramienta] + resto[:2]).strip()}
    if Path(argv[0]).name.startswith('python'):
        formas.add(' '.join(['python'] + argv[1:3]))
        formas.add(' '.join(['python3'] + argv[1:3]))
    return any(len(f) > 4 and f in texto for f in formas for texto in documentos)


def _nombra_cambiados(argv, raiz, cambiados):
    for valor in argv[1:]:
        if valor.startswith('-') or not cambiados:
            continue
        valor = valor.split('::', 1)[0].rstrip('/')
        if not valor:
            continue
        for cambiado in cambiados:
            if cambiado == valor or cambiado.startswith(valor + '/'):
                return True
    return False


def _recetas_make(raiz, objetivos, vistos=()):
    """Recetas sencillas y sus dependencias. Una receta ambigua no acredita aprendizaje."""
    texto = _makefile(raiz)
    reglas, actual = {}, None
    for linea in texto.splitlines():
        if linea.startswith('\t'):
            if actual is not None:
                reglas[actual][1].append(linea.strip())
        elif linea.strip() and not linea.lstrip().startswith('#'):
            m = re.fullmatch(r'([A-Za-z0-9_.-]+)\s*:\s*([A-Za-z0-9_. /-]*)', linea)
            if m is None or (m[1].startswith('.') and m[1] != '.PHONY'):
                return None  # includes, variables, .IGNORE y demás cambian lo que se ejecuta
            actual = m[1] if m else None
            if actual is not None:
                reglas[actual] = (m[2].split(), [])
    recetas = []
    for objetivo in objetivos:
        if objetivo in vistos or objetivo not in reglas:
            return None
        deps, propias = reglas[objetivo]
        for dep in deps:
            if dep in reglas:
                otras = _recetas_make(raiz, [dep], (*vistos, objetivo))
                if otras is None:
                    return None
                recetas.extend(otras)
            elif not (Path(raiz) / dep).is_file():
                return None
        recetas.extend(propias)
    return recetas


def _receta_sustantiva(texto, raiz, profundidad=0):
    if profundidad > 4 or texto.startswith('-') or re.search(r'[|;<>`$\n\\]', texto):
        return False
    try:
        tramos = [shlex.split(t.strip().removeprefix('@')) for t in texto.split('&&')]
    except ValueError:
        return False
    if not tramos or any(not t for t in tramos):
        return False
    # El último paso decide el resultado; `pytest && true` no acredita por su nombre.
    return any(t[0] != 'cd' for t in tramos) and all(
        (t[0] == 'cd' and len(t) == 2) or _orden_sustantiva(t, raiz, profundidad + 1) for t in tramos)


def _orden_sustantiva(argv, raiz, profundidad=0):
    final = orden_final(argv)
    if final is None or not functional_command(final):
        return False
    if any(a in NO_PRUEBAN or a.startswith('--version') for a in final[1:]):
        return False
    herramienta, resto = _herramienta(final)
    if herramienta == 'python' and resto[:1] == ['-c']:
        return False
    if herramienta == 'make':
        # Las opciones pueden cambiar de Makefile, omitir errores o simular la ejecución.
        if not resto or any(a.startswith('-') or '=' in a for a in resto):
            return False
        recetas = _recetas_make(raiz, resto)
        return bool(recetas) and all(_receta_sustantiva(r, raiz, profundidad) for r in recetas)
    if herramienta in ('npm', 'pnpm', 'yarn', 'bun'):
        if any(a.split('=')[0] in ('--prefix', '--cwd', '--dir', '-C', '--workspace', '--workspaces', '--ws', '-w')
               or a.startswith(('-w', '-C')) for a in resto):
            return False
        palabras = [a for a in resto if not a.startswith('-')]
        script = palabras[1] if palabras[:1] == ['run'] and len(palabras) > 1 else (palabras[0] if palabras else '')
        try:
            receta = json.loads(_leer(Path(raiz) / 'package.json')).get('scripts', {}).get(script)
        except (ValueError, AttributeError):
            return False
        return isinstance(receta, str) and _receta_sustantiva(receta, raiz, profundidad + 1)
    return True


def prueba_el_proyecto(argv, raiz, cambiados=(), documentos=(), salida=None, cwd=None):
    """¿`argv` es una comprobación real de este proyecto? `cambiados`: rutas relativas cambiadas en el
    turno; `documentos`: textos de AGENTS.md."""
    final = orden_final(argv)
    carpeta = Path(cwd or raiz)
    if final is not None and Path(argv[0]).name in ('sh', 'bash', 'zsh'):
        for tramo in argv[2].split('&&')[:-1]:
            carpeta = (carpeta / shlex.split(tramo)[1]).resolve()
    if final is None or not _orden_sustantiva(final, carpeta):
        return False
    herramienta, resto = _herramienta(final)
    if herramienta == 'python' and resto[:1] == ['-c']:
        return False  # un assert escrito en la orden no prueba el proyecto
    if '--if-present' in resto and herramienta not in ('npm', 'pnpm', 'yarn', 'bun'):
        return False
    if salida is not None:
        salida = re.sub(r'\x1b\[[0-9;]*m', '', str(salida))
        if herramienta in ('make', 'npm', 'pnpm', 'yarn', 'bun') and (
                not salida.strip() or re.search(r'is up to date|Nothing to be done|está actualizado', salida, re.I)):
            return False
        if re.search(r'\bRan\s+0\s+tests?\b', salida):
            return False
        if re.search(r'(?m)^#\s*tests\s+0(?:\s|$)', salida) or re.search(r'no tests? (?:files? )?found', salida, re.I):
            return False
        if re.search(r'(?m)^#\s*pass\s+0(?:\s|$)', salida):
            return False
        pasaron = re.search(r'\b[1-9][0-9]*\s+passed\b', salida)
        if herramienta == 'pytest' and not pasaron:
            return False
        if re.search(r'\b[0-9]+\s+skipped\b', salida) and not pasaron:
            return False
        unittest = re.search(r'\bRan\s+([0-9]+)\s+tests?\b', salida)
        omitidas = re.search(r'\bskipped=([0-9]+)\b', salida)
        if unittest and omitidas and int(unittest[1]) <= int(omitidas[1]):
            return False
        if herramienta == 'unittest' and not re.search(r'\bRan\s+[1-9][0-9]*\s+tests?\b', salida):
            return False
    return (_del_proyecto(herramienta, resto, ordenes_del_proyecto(carpeta), carpeta)
            or (herramienta not in ('make', 'npm', 'pnpm', 'yarn', 'bun')
                and _del_proyecto(herramienta, resto, ordenes_del_proyecto(raiz), raiz))
            or _en_agents_md(final, documentos) or _nombra_cambiados(final, raiz, list(cambiados)))
