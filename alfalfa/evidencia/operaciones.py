"""Shared, context-aware classification for native runtime command results.

The subprocess output and exit status remain the primary evidence. This module
only assigns a disposition when the command, purpose, and captured context
support it; otherwise every non-zero result remains a failure.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import PurePath

_DIAGNOSTICOS_PYTEST = [
    'Comprueba la invocación de pytest y sus argumentos.',
    'Revisa errores de colección e importación.',
    'Confirma que cwd coincide con el directorio de ejecución esperado.',
    'Comprueba que la ruta objetivo de pruebas existe y es la correcta.',
    'Verifica el intérprete, las dependencias y la configuración del entorno.',
]


def _texto(value):
    value = unicodedata.normalize('NFKD', str(value or '').casefold())
    return ''.join(char for char in value if not unicodedata.combining(char))


def _program(argv):
    if not isinstance(argv, (list, tuple)) or not argv:
        return '', []
    values = [str(value) for value in argv]
    return PurePath(values[0]).name.casefold(), values


def _git_subcommand(argv):
    program, values = _program(argv)
    if program != 'git':
        return []
    index = 1
    with_value = {'-C', '-c', '--git-dir', '--work-tree', '--namespace'}
    with_equals = ('--git-dir=', '--work-tree=', '--namespace=')
    while index < len(values):
        value = values[index]
        if value in with_value:
            if index + 1 >= len(values):
                return []
            index += 2
        elif value.startswith(with_equals):
            index += 1
        elif value in ('--no-pager', '--paginate', '--no-replace-objects', '--literal-pathspecs',
                       '--no-optional-locks', '--bare', '--glob-pathspecs', '--noglob-pathspecs'):
            index += 1
        else:
            break
    return values[index:]


def _rg_purpose_is_absence(purpose):
    text = _texto(purpose)
    return bool(re.search(
        r'\b(?:absence|ausencia|sin coincidencias|sin resultados|no coincidencias|ningun(?:a|as|os)? '
        r'(?:coincidencia|resultado|referencia|ocurrencia)|no matches?|no results?|'
        r'no occurrences?|no references?|no quedan?|no queden|no hay|no haya|no existe|no existen|'
        r'no exista|no existan|no deben? (?:existir|quedar|aparecer|haber)|espero que no|'
        r'no aparece|no aparecen|no contiene|no incluye|no encontrado|no encontrada|'
        r'ya no (?:existe|aparece|esta|queda)|nothing should match|none remain)\b', text))


_RG_TAKES_VALUE = {'-e', '--regexp', '-f', '--file', '-g', '--glob', '--iglob', '-t', '--type',
                   '--type-not', '--sort', '--encoding', '--maxdepth', '--max-count', '-A', '-B', '-C',
                   '--context', '--pre', '--pre-glob', '--max-filesize', '--path-separator'}
_GREP_TAKES_VALUE = {'-e', '--regexp', '-f', '--file', '-m', '--max-count', '-A', '-B', '-C',
                     '--context', '--include', '--exclude', '--exclude-dir', '--label', '-d',
                     '--directories', '-D', '--devices', '--binary-files', '--color', '--colour'}
_GREP_PROGRAMS = ('grep', 'egrep', 'fgrep')
_PATH_PROBES = ('ls', 'test', '[', 'stat')


def _rg_query_matches_purpose(argv, purpose, takes_value=_RG_TAKES_VALUE):
    """Avoid treating an absence goal for an unrelated item as verification."""
    _, values = _program(argv)
    pattern = None
    index = 1
    while index < len(values):
        value = values[index]
        if value in ('-e', '--regexp'):
            if index + 1 >= len(values):
                return False
            pattern = values[index + 1]
            break
        if value.startswith('-e') and len(value) > 2:
            pattern = value[2:]
            break
        if value.startswith('--regexp='):
            pattern = value.partition('=')[2]
            break
        if value in ('-f', '--file') or value.startswith('--file='):
            return False
        if value in takes_value:
            index += 2
            continue
        if value.startswith('--') and '=' in value:
            index += 1
            continue
        if takes_value is _RG_TAKES_VALUE and value.startswith(('-g', '-t')) and len(value) > 2:
            index += 1
            continue
        if value.startswith('-'):
            index += 1
            continue
        pattern = value
        break
    if pattern is None:
        return False
    # Las clases POSIX (`[[:blank:]]`) son sintaxis, no lo que se busca.
    pattern_text = re.sub(r'\[:[a-z]+:\]', ' ', _texto(pattern))
    pattern_text = re.sub(r'\\[bBdDsSwW]', ' ', pattern_text)
    terms = {term for term in re.findall(r'[a-z0-9_]+', pattern_text) if len(term) > 1}
    if not terms:
        return True
    purpose_text = _texto(purpose)
    return all(term in purpose_text for term in terms)


def _probe_purpose(purpose):
    """A yes/no question about whether something exists, where «no» is an answer."""
    text = _texto(purpose)
    return _rg_purpose_is_absence(purpose) or bool(re.search(
        r'\b(?:comprobar|verificar|confirmar|revisar|averiguar|check|verify|confirm|see)\b'
        r'.*\b(?:si|whether|if)\b', text))


def _path_operands(argv):
    program, values = _program(argv)
    if program in ('test', '['):
        return [value for value in values[1:] if value not in (']', '!') and not value.startswith('-')]
    return [value for value in values[1:] if not value.startswith('-')]


def _path_probe_is_absence(argv, purpose, output, exit_code):
    """``ls``/``test``/``stat`` of a missing path, when the purpose asks whether it exists."""
    program, _ = _program(argv)
    paths = _path_operands(argv)
    if program not in _PATH_PROBES or exit_code not in (1, 2) or not paths or not _probe_purpose(purpose):
        return False
    if program in ('test', '['):
        if output.strip() or exit_code != 1:
            return False
    else:
        lines = [line for line in output.splitlines() if line.strip()]
        if not lines or not all('no such file or directory' in _texto(line) for line in lines):
            return False
    purpose_text = _texto(purpose)
    for path in paths:
        terms = {term for term in re.findall(r'[a-z0-9_]+', _texto(PurePath(path).name)) if len(term) > 1}
        if not terms or not all(term in purpose_text for term in terms):
            return False
    return True


def _merge_probe_purpose(purpose):
    text = _texto(purpose)
    names_merge_probe = bool(re.search(r'\bmerge(?:_head)?\b|\bfusion\b', text))
    describes_probe = bool(re.search(
        r'\b(?:check|verify|confirm|comprobar|verificar|confirmar|revisar|determinar|'
        r'inspeccionar|consultar|estado|whether|si hay|si existe|en curso|pendiente|'
        r'activa|activo)\b', text))
    return names_merge_probe and describes_probe


def _is_merge_head_probe(argv):
    return _git_subcommand(argv) == ['rev-parse', '--verify', 'MERGE_HEAD']


def is_forbidden_merge_heads_recipe(argv):
    """The historical ``git merge-heads --print`` recipe does not exist."""
    return _git_subcommand(argv)[:2] == ['merge-heads', '--print']


def _is_diff_check(argv):
    return _git_subcommand(argv)[:2] == ['diff', '--check']


def _is_pytest(argv):
    program, values = _program(argv)
    if program == 'pytest' or re.fullmatch(r'pytest(?:-[0-9.]+)?', program):
        return True
    if any(values[index:index + 2] == ['-m', 'pytest']
           for index in range(1, len(values) - 1)):
        return True
    return (program in ('uv', 'poetry', 'pipenv', 'tox', 'nox', 'hatch')
            and any(PurePath(value).name.casefold() == 'pytest' for value in values[1:]))


def _has_command_error(output):
    text = _texto(output)
    return any(marker in text for marker in (
        'fatal:', 'error:', 'permission denied', 'operation not permitted', 'not permitted',
        'access denied', 'access is denied', 'failed to access', 'failed to read',
        'not a git repository', 'unknown option', 'unrecognized option', 'invalid option',
        'unknown argument', 'unrecognized argument', 'unrecognized flag', 'invalid argument',
        'invalid regex', 'regex parse error', 'unexpected argument', 'usage:', 'cannot open',
        'failed to open', 'no such file or directory'))


def _classification(status, disposition, cause, message, **details):
    value = {'status': status, 'disposition': disposition, 'cause': cause, 'message': message}
    value.update(details)
    return value


def classify_operation(argv, purpose, cwd, output, exit_code, *, timed_out=False, exception=None,
                       output_truncated=False, background=False):
    """Return a stable operation disposition without discarding raw evidence.

    A successful process has exit code zero. The supported expected-absence
    cases (rg/grep without matches, a missing MERGE_HEAD, a missing path) are
    deliberately narrow. All other non-zero exits, exceptions and
    timeouts remain failures, including Swift and Go toolchain failures.
    """
    program, values = _program(argv)
    raw_output = str(output or '')
    if background and exit_code is None and not timed_out and not exception:
        # Sigue vivo en segundo plano (run_command background): ni éxito ni fallo todavía.
        return _classification('running', 'pass', 'background',
                               'Sigue ejecutándose en segundo plano; command_status dice cómo va.')
    if timed_out:
        return _classification('timed_out', 'failure', 'timeout',
                               'La operación excedió su tiempo límite; se conserva la salida parcial.')
    if exception:
        return _classification('execution_error', 'failure', 'execution_exception',
                               'No se pudo ejecutar la operación; se conserva la excepción.')
    if is_forbidden_merge_heads_recipe(argv):
        return _classification('prohibited', 'failure', 'forbidden_recipe',
                               'Receta prohibida: git merge-heads --print no es un comando válido.')

    if _is_pytest(argv) and exit_code == 4:
        return _classification('diagnostic_required', 'failure', 'pytest_invocation_or_collection',
                               'pytest terminó con código 4. '
                               + ' '.join(_DIAGNOSTICOS_PYTEST)
                               + ' El código 4 no demuestra que hayan fallado pruebas.',
                               diagnostics=list(_DIAGNOSTICOS_PYTEST))

    if _is_diff_check(argv) and exit_code == 2:
        if _has_command_error(raw_output):
            return _classification('failed', 'failure', 'diff_check_command_error',
                                   'git diff --check terminó con error de ejecución; revisa la salida conservada.')
        return _classification('whitespace_pending', 'failure', 'whitespace_pending',
                               'Hay whitespace pendiente según git diff --check; corrígelo y repite la comprobación.')

    if _is_merge_head_probe(argv):
        compatible_absence = (exit_code == 128
                              and not output_truncated
                              and _texto(raw_output.strip()) in (
                                  'fatal: needed a single revision',
                                  'fatal: se necesito una revision singular',
                                  "fatal: se necesit'o una revisi'on singular")
                              and _merge_probe_purpose(purpose))
        if compatible_absence:
            return _classification('expected_absence', 'pass', 'merge_head_absent',
                                   'MERGE_HEAD no existe en este repositorio Git válido.',
                                   context_verified=True,
                                   context_evidence='Git resolvió el repositorio antes de informar que falta la '
                                                    'revisión solicitada.')

    if program == 'rg' and exit_code == 1:
        if _has_command_error(raw_output):
            return _classification('failed', 'failure', 'rg_execution_error',
                                   'rg informó un error de acceso o argumentos; revisa la salida conservada.')
        if (_rg_purpose_is_absence(purpose) and _rg_query_matches_purpose(argv, purpose)
                and not raw_output.strip()):
            return _classification('expected_absence', 'pass', 'rg_no_matches_expected',
                                   'rg no encontró coincidencias, como pide el propósito.',
                                   context_verified=True,
                                   context_evidence='El patrón de rg coincide con el elemento cuya ausencia solicita '
                                                    'el propósito.')
        return _classification('failed', 'failure', 'rg_no_matches_unexpected',
                               'rg no encontró coincidencias y el propósito no confirma que la ausencia sea esperada.')

    # grep, como rg, sale con 1 cuando no encuentra nada: un agente buscó whitespace
    # colgante esperando cero líneas y el ticket se bloqueó por ese «fallo».
    if program in _GREP_PROGRAMS and exit_code == 1:
        if (not raw_output.strip() and _rg_purpose_is_absence(purpose)
                and _rg_query_matches_purpose(argv, purpose, _GREP_TAKES_VALUE)):
            return _classification('expected_absence', 'pass', 'grep_no_matches_expected',
                                   'grep no encontró coincidencias, como pide el propósito.',
                                   context_verified=True,
                                   context_evidence='El patrón de grep coincide con el elemento cuya ausencia solicita '
                                                    'el propósito.')
        return _classification('failed', 'failure', 'grep_no_matches_unexpected',
                               'grep no encontró coincidencias y el propósito no confirma que la ausencia sea '
                               'esperada.')

    # Preguntar si una ruta existe y oír «no» es una respuesta, no un fallo
    # (p. ej. `ls .tmp-alfalfa` para confirmar que no quedaron restos).
    if _path_probe_is_absence(argv, purpose, raw_output, exit_code) and not output_truncated:
        return _classification('expected_absence', 'pass', 'path_absent_expected',
                               'La ruta consultada no existe, y el propósito preguntaba por su existencia.',
                               context_verified=True,
                               context_evidence='La salida sólo informa que falta la ruta nombrada en el propósito.')

    if _is_merge_head_probe(argv) and exit_code == 128:
        return _classification('failed', 'failure', 'merge_head_result_unverified',
                               'No se puede concluir que MERGE_HEAD esté ausente: faltan contexto o salida Git '
                               'compatibles.')

    if type(exit_code) is int and exit_code == 0:
        if (program == 'rg' and _rg_purpose_is_absence(purpose)
                and _rg_query_matches_purpose(argv, purpose)):
            return _classification('failed', 'failure', 'rg_matches_unexpected',
                                   'rg encontró coincidencias, pero el propósito exige confirmar su ausencia.')
        if (program in _GREP_PROGRAMS and raw_output.strip() and _rg_purpose_is_absence(purpose)
                and _rg_query_matches_purpose(argv, purpose, _GREP_TAKES_VALUE)):
            return _classification('failed', 'failure', 'grep_matches_unexpected',
                                   'grep encontró coincidencias, pero el propósito exige confirmar su ausencia.')
        return _classification('passed', 'pass', 'exit_zero', 'La operación terminó con código 0.')

    if type(exit_code) is int:
        return _classification('failed', 'failure', 'nonzero_exit',
                               'La operación terminó con código ' + str(exit_code) + '.')
    return _classification('failed', 'failure', 'missing_exit_code',
                           'La operación no produjo un código de salida verificable.')


def operation_record_key(record):
    """Identity used to keep a result pending until its matching check passes.

    Identity must come from the request, not the result's classification: an
    access error and a later expected absence can be retries of the same search.
    """
    argv = record.get('argv') if isinstance(record, dict) else None
    cwd = record.get('cwd') if isinstance(record, dict) else None
    purpose = record.get('purpose') if isinstance(record, dict) else None
    project = record.get('project') if isinstance(record, dict) else None
    session_id = record.get('session_id') if isinstance(record, dict) else None
    objective = ' '.join(_texto(purpose).split())
    return (tuple(argv) if isinstance(argv, (list, tuple)) else (),
            str(cwd or ''), objective, str(project or ''), str(session_id or ''))
