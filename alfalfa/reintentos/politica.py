"""Rule-based retry classes and durable per-ticket retry budgets."""
from __future__ import annotations

import hashlib
import json
import math
import re

from .fallos import patron_de_agotamiento


LOCAL_SERVICE_UNAVAILABLE = 'local_service_unavailable'
QUOTA_SATURATION = 'quota_saturation'
TRANSIENT = 'transient'
DEFECTIVE = 'defective_task_invocation'
UNKNOWN = 'unknown'

NO_PROGRESS_LIMIT_SECONDS = 120 * 60

# Automatic attempts are cumulative for the life of a ticket. Progress can
# renew the no-progress clock, but it never refunds an attempt.
RETRY_POLICIES = {
    LOCAL_SERVICE_UNAVAILABLE: {
        'max_attempts': 3, 'max_elapsed_seconds': 2 * 60 * 60,
        'backoff_base_seconds': 5 * 60, 'backoff_max_seconds': 30 * 60,
    },
    QUOTA_SATURATION: {
        'max_attempts': 2, 'max_elapsed_seconds': 2 * 60 * 60,
        'backoff_base_seconds': 15 * 60, 'backoff_max_seconds': 60 * 60,
    },
    TRANSIENT: {
        'max_attempts': 2, 'max_elapsed_seconds': 60 * 60,
        'backoff_base_seconds': 10 * 60, 'backoff_max_seconds': 30 * 60,
    },
    # A deterministic failure may be retried once only after observable
    # progress; the failing command itself is not repeated by the supervisor.
    DEFECTIVE: {
        'max_attempts': 2, 'max_elapsed_seconds': 2 * 60 * 60,
        'backoff_base_seconds': 0, 'backoff_max_seconds': 0,
        'requires_progress': True,
    },
    UNKNOWN: {
        'max_attempts': 0, 'max_elapsed_seconds': 0,
        'backoff_base_seconds': 0, 'backoff_max_seconds': 0,
    },
}

# Cuota o saturación: la lente «reintento» del clasificador único (reintentos/fallos.py),
# que conserva las mismas señales y el mismo orden que la expresión que vivía aquí.
_QUOTA = patron_de_agotamiento('reintento')
_LOCAL_REFUSAL = re.compile(
    r'(?:localhost|127\.0\.0\.1|\[?::1\]?)\S{0,80}.*(?:connection refused|econnrefused|connecterror|failed to connect)'
    r'|(?:connection refused|econnrefused|connecterror|failed to connect).*'
    r'(?:localhost|127\.0\.0\.1|\[?::1\])', re.I | re.S)
_TRANSIENT = re.compile(
    r'\b(?:502|503|504)\b|timed? out|timeout|connection reset|temporarily unavailable|'
    r'network error|dns|enotfound|eai_again|ehostunreach|etimedout|git tard[oó] demasiado|'
    r'no entreg[oó] un resultado final|respuesta incompleta del modelo|respuesta cortada|'
    r'se desconect[oó] antes de completar|cerr[oó] la conexi[oó]n|reinici[oó]', re.I)
_DEFECT = re.compile(
    r'invalid request|unsupported parameter|unknown parameter|pytest termin[oó] con c[oó]digo 4|'
    r'pytest_invocation_or_collection|verification_pending|pending_operations|loop_detected|'
    r'forbidden_recipe|execution_exception|does not support this model|not a model this version|'
    r'correcciones seguidas no cambiaron', re.I)
_PYTEST_COUNT = (
    r'(?:\d+|no)\s+(?:passed|failed|errors?|warnings?|skipped|xfailed|xpassed|'
    r'deselected|selected|rerun)'
)
_PYTEST_SUMMARY_DURATION = re.compile(
    rf'(?im)^(?P<prefix>[= \t]*(?:{_PYTEST_COUNT})(?:,\s*{_PYTEST_COUNT})*\s+in\s+)'
    r'\d+(?:\.\d+)?s(?:\s+\(\d+(?::\d{2}){1,2}\))?(?P<suffix>[= \t]*)$')
_TRANSIENT_OPERATION_CAUSES = {
    'timeout', 'server_error', 'network', 'disconnected', 'connection_reset',
    'temporarily_unavailable', 'dns', 'transient',
}
_DEFECTIVE_OPERATION_CAUSES = {
    'test_failed', 'pytest_invocation_or_collection', 'forbidden_recipe',
    'execution_exception', 'unsupported_parameter', 'invalid_request',
    'verification_pending', 'pending_operations', 'loop_detected',
    'steps_exhausted', 'review_closure_failed', 'whitespace_pending',
    'rg_no_matches_unexpected', 'rg_matches_unexpected',
    'merge_head_result_unverified',
}
_DOWNLOAD_ACTIONS = {
    'npm': {'ci', 'install', 'update'},
    'pnpm': {'install', 'add', 'update', 'up'},
    'yarn': {'install', 'add', 'upgrade', 'up'},
    'pip': {'install', 'download', 'sync'},
    'pip3': {'install', 'download', 'sync'},
    'uv': {'sync'},
    'poetry': {'install', 'update'},
    'pipenv': {'install', 'sync'},
    'cargo': {'fetch'},
    'go': {'get'},
    'swift': set(),
    'pod': {'install', 'update'},
    'bundle': {'install', 'update'},
    'composer': {'install', 'update'},
    'git': {'clone', 'fetch', 'pull'},
}


def _curl_download(args):
    """Recognize curl only when argv clearly writes a response to disk."""
    index = 0
    while index < len(args):
        value = args[index]
        if value in ('-o', '--output') and index + 1 < len(args) and args[index + 1]:
            return True
        if value.startswith('--output=') and value.partition('=')[2]:
            return True
        if value == '-O' or value == '--remote-name':
            return True
        if value.startswith('-o') and value != '-o' and not value.startswith('--'):
            return True
        index += 1
    return False


def _wget_download(args):
    """wget downloads a URL by default; a spider request does not download."""
    if '--spider' in args:
        return False
    return any(value.startswith(('http://', 'https://', 'ftp://')) for value in args)


def expensive_operation_identity(argv):
    """Identify downloads and explicit clean builds that should not repeat silently."""
    if not isinstance(argv, (list, tuple)) or not argv:
        return None
    values = [str(value) for value in argv]
    program = values[0].replace('\\', '/').rsplit('/', 1)[-1].casefold()
    args = [value.casefold() for value in values[1:]]
    if program in ('python', 'python3', 'python3.11', 'python3.12') and len(args) >= 3 \
            and args[:2] == ['-m', 'pip']:
        program, args = 'pip', args[2:]
    if program == 'swift' and args[:2] == ['package', 'resolve']:
        return 'download:swift-package'
    if program == 'curl' and _curl_download(args):
        return 'download:curl'
    if program == 'wget' and _wget_download(args):
        return 'download:wget'
    if program in _DOWNLOAD_ACTIONS and args and args[0] in _DOWNLOAD_ACTIONS[program]:
        family = 'node' if program in ('npm', 'pnpm', 'yarn') else program
        return 'download:' + family
    if program == 'go' and args[:2] == ['mod', 'download']:
        return 'download:go-mod'
    if program == 'git' and args and args[0] in ('clone', 'fetch', 'pull'):
        return 'download:git'
    build_tool = program in ('xcodebuild', 'swift', 'cargo', 'gradle', 'gradlew', 'make', 'ninja', 'cmake')
    if build_tool and 'clean' in args:
        family = 'gradle' if program == 'gradlew' else program
        return 'clean_build:' + family
    return None


def retry_guard_key(identity, scope):
    if not isinstance(identity, str) or not identity or not isinstance(scope, str) or not scope:
        return None
    raw = json.dumps([identity, scope], ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def expensive_operation_needs_cause(records, key, content_generation, attempts_by_class=None):
    """A source change or a bounded retry for a recorded transient cause permits repetition."""
    if not key:
        return False
    current = content_generation if type(content_generation) is int and content_generation >= 0 else 0
    current_attempts = attempts_by_class if isinstance(attempts_by_class, dict) else {}
    matches = [record for record in records if isinstance(record, dict)
               and record.get('retry_guard_key') == key] if isinstance(records, list) else []
    if not matches:
        return False
    record = matches[-1]
    previous = record.get('retry_guard_content_generation', 0)
    previous = previous if type(previous) is int and previous >= 0 else 0
    if current > previous:
        return False
    classification = record.get('classification')
    if isinstance(classification, dict) and classification.get('disposition') == 'failure':
        failure_class = classify_retry_failure(operation_failures=[record])
        if failure_class in (LOCAL_SERVICE_UNAVAILABLE, QUOTA_SATURATION, TRANSIENT):
            before = record.get('retry_guard_attempts_by_class')
            before = before if isinstance(before, dict) else {}
            old_count = before.get(failure_class, 0)
            new_count = current_attempts.get(failure_class, 0)
            if type(old_count) is not int or old_count < 0:
                old_count = 0
            if type(new_count) is int and new_count > old_count:
                return False
    return True


def _provider_error(task):
    provider = task.get('runtime_provider_error')
    if isinstance(provider, dict):
        return provider
    outcome = task.get('runtime_outcome')
    details = outcome.get('details') if isinstance(outcome, dict) else None
    nested = details.get('provider_error') if isinstance(details, dict) else None
    return nested if isinstance(nested, dict) else {}


def classify_retry_failure(task=None, ticket_error='', operation_failures=None):
    """Classify only explicit provider/runtime/evidence signals.

    This never reads model prose from chat messages and never infers a cause
    from a generic failed status. Missing or conflicting evidence is unknown.
    """
    task = task if isinstance(task, dict) else {}
    provider = _provider_error(task)
    cause = str(provider.get('cause') or '').casefold()
    kind = str(provider.get('kind') or '').casefold()
    status = provider.get('status_code')
    scope = str(provider.get('endpoint_scope') or provider.get('service_scope') or '').casefold()
    detail = str(provider.get('detail') or '')
    summary = str(task.get('summary') or '')
    # ticket_error is a mutable summary and may describe a resolved earlier
    # attempt. Prefer structured current-attempt and operation evidence below.
    text = '\n'.join((summary, detail))

    outcome = task.get('runtime_outcome')
    outcome_cause = str(outcome.get('cause') or '').casefold() if isinstance(outcome, dict) else ''
    failures = operation_failures if isinstance(operation_failures, list) else []
    candidates = set()
    if cause == 'saturation' or status == 429:
        candidates.add(QUOTA_SATURATION)
    if (scope in ('local', 'loopback') and cause in ('network', 'disconnected', 'timeout')):
        candidates.add(LOCAL_SERVICE_UNAVAILABLE)
    if kind == 'transient' and cause in ('server_error', 'timeout', 'network', 'disconnected') \
            and not (scope in ('local', 'loopback')
                     and cause in ('network', 'disconnected', 'timeout')):
        candidates.add(TRANSIENT)
    if kind == 'definitive':
        candidates.add(DEFECTIVE)
    if outcome_cause in ('definitive_error', 'verification_pending', 'pending_operations',
                         'loop_detected', 'steps_exhausted', 'review_closure_failed'):
        candidates.add(DEFECTIVE)

    # Lo que el proveedor dijo de forma estructurada manda sobre el texto: su
    # propio mensaje («no respondió a tiempo (timed out)») volvía a sumar
    # «transitorio» a un timeout ya clasificado como servicio local, y con dos
    # clases el fallo quedaba «desconocido» y nunca se reintentaba (GLM, 31/10).
    del_proveedor = set(candidates)

    operation_classes = set()
    for failure in failures:
        classification = failure.get('classification') if isinstance(failure, dict) else None
        if isinstance(classification, dict) and classification.get('disposition') == 'failure':
            failure_cause = str(classification.get('cause') or '').casefold()
            operation_text = '\n'.join(str(failure.get(key) or '') for key in
                                        ('output', 'error', 'stderr', 'stdout'))
            operation_text += '\n' + str(classification.get('message') or '')
            row_classes = set()
            if _QUOTA.search(operation_text) or failure_cause in ('saturation', 'quota', 'rate_limit'):
                row_classes.add(QUOTA_SATURATION)
            if _LOCAL_REFUSAL.search(operation_text) or failure_cause in (
                    'local_service_unavailable', 'local_unavailable', 'connection_refused'):
                row_classes.add(LOCAL_SERVICE_UNAVAILABLE)
            if (failure_cause in _TRANSIENT_OPERATION_CAUSES or _TRANSIENT.search(operation_text)) \
                    and QUOTA_SATURATION not in row_classes \
                    and LOCAL_SERVICE_UNAVAILABLE not in row_classes:
                row_classes.add(TRANSIENT)
            if failure_cause in _DEFECTIVE_OPERATION_CAUSES or _DEFECT.search(operation_text):
                row_classes.add(DEFECTIVE)
            operation_classes.update(row_classes)
    candidates.update(operation_classes)

    if not del_proveedor:
        if _QUOTA.search(text):
            candidates.add(QUOTA_SATURATION)
        if _LOCAL_REFUSAL.search(text):
            candidates.add(LOCAL_SERVICE_UNAVAILABLE)
        if _TRANSIENT.search(text):
            candidates.add(TRANSIENT)
        if _DEFECT.search(text):
            candidates.add(DEFECTIVE)
    if not failures and not candidates:
        ticket_text = str(ticket_error or '')
        if _QUOTA.search(ticket_text):
            candidates.add(QUOTA_SATURATION)
        if _LOCAL_REFUSAL.search(ticket_text):
            candidates.add(LOCAL_SERVICE_UNAVAILABLE)
        if _TRANSIENT.search(ticket_text):
            candidates.add(TRANSIENT)
        if _DEFECT.search(ticket_text):
            candidates.add(DEFECTIVE)
    return next(iter(candidates)) if len(candidates) == 1 else UNKNOWN


def new_retry_budget(now, snapshot=None):
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    workspaces = snapshot.get('workspaces')
    evidence = snapshot.get('evidence')
    return {
        'version': 1,
        'no_progress_since': float(now),
        'last_progress_at': float(now),
        'last_observed_at': float(now),
        'progress_generation': 0,
        'content_generation': 0,
        'workspaces': dict(workspaces) if isinstance(workspaces, dict) else {},
        'evidence': sorted(set(evidence)) if isinstance(evidence, list) else [],
        'attempts_by_class': {},
        'first_seen_by_class': {},
        'last_retry_by_class': {},
        'class_progress_generation': {},
        'exhausted': None,
    }


def _timestamp(value, fallback):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(fallback)
    value = float(value)
    return value if value >= 0 and math.isfinite(value) else float(fallback)


def observe_retry_budget(budget, snapshot, now):
    """Record content/evidence changes; messages and duplicate operations do not count."""
    if not isinstance(budget, dict) or budget.get('version') != 1:
        return new_retry_budget(now, snapshot), False

    effective_now = max(float(now), _timestamp(budget.get('last_observed_at'), now))
    old_workspaces = budget.get('workspaces')
    old_workspaces = old_workspaces if isinstance(old_workspaces, dict) else {}
    new_workspaces = snapshot.get('workspaces') if isinstance(snapshot, dict) else {}
    new_workspaces = new_workspaces if isinstance(new_workspaces, dict) else {}
    content_changed = any(key in old_workspaces and old_workspaces[key] != value
                          for key, value in new_workspaces.items())
    changed = content_changed

    old_evidence = set(item for item in budget.get('evidence', []) if isinstance(item, str))
    new_evidence = set(item for item in (snapshot.get('evidence', []) if isinstance(snapshot, dict) else [])
                       if isinstance(item, str))
    if new_evidence - old_evidence:
        changed = True

    # New workspaces are baselined without treating their initial checkout as
    # progress. Deleted workspaces do not erase history or refresh the clock.
    merged_workspaces = dict(old_workspaces)
    merged_workspaces.update(new_workspaces)
    budget['workspaces'] = merged_workspaces
    budget['evidence'] = sorted(old_evidence | new_evidence)[-5000:]
    budget['last_observed_at'] = effective_now
    if changed:
        budget['last_progress_at'] = effective_now
        budget['no_progress_since'] = effective_now
        generation = budget.get('progress_generation', 0)
        generation = generation if type(generation) is int and generation >= 0 else 0
        budget['progress_generation'] = generation + 1
        if budget.get('exhausted') == 'no_progress':
            # El progreso observado después de una pausa vuelve a abrir el
            # presupuesto de supervisión para que el ticket pueda reanudarse.
            budget['exhausted'] = None
    if content_changed:
        content_generation = budget.get('content_generation', 0)
        content_generation = content_generation if type(content_generation) is int and content_generation >= 0 else 0
        budget['content_generation'] = content_generation + 1
    else:
        content_generation = budget.get('content_generation', 0)
        budget['content_generation'] = content_generation if type(content_generation) is int \
            and content_generation >= 0 else 0
    return budget, changed


def retry_decision(budget, failure_class, now, *, failure_since=None, cause_corrected=False):
    """Return retry, wait, or stop under the finite persistent class limits."""
    effective_now = max(float(now), _timestamp(budget.get('last_observed_at'), now))
    stalled_for = max(0.0, effective_now - _timestamp(budget.get('no_progress_since'), effective_now))
    if stalled_for >= NO_PROGRESS_LIMIT_SECONDS:
        return {'action': 'stop', 'reason': 'no_progress', 'stalled_seconds': stalled_for}

    policy = RETRY_POLICIES.get(failure_class, RETRY_POLICIES[UNKNOWN])
    attempts = budget.get('attempts_by_class')
    attempts = attempts if isinstance(attempts, dict) else {}
    count = attempts.get(failure_class, 0)
    count = count if type(count) is int and count >= 0 else 0
    if policy['max_attempts'] == 0:
        return {'action': 'stop', 'reason': 'class_not_retryable', 'attempts': count}
    if count >= policy['max_attempts']:
        return {'action': 'stop', 'reason': 'attempt_limit', 'attempts': count}

    first_seen = budget.get('first_seen_by_class')
    first_seen = first_seen if isinstance(first_seen, dict) else {}
    budget['first_seen_by_class'] = first_seen
    started = first_seen.setdefault(failure_class, _timestamp(failure_since, effective_now))
    started = _timestamp(started, effective_now)
    if effective_now - started >= policy['max_elapsed_seconds']:
        return {'action': 'stop', 'reason': 'class_time_limit', 'attempts': count}

    if policy.get('requires_progress'):
        if not cause_corrected:
            return {'action': 'wait', 'reason': 'cause_not_corrected', 'attempts': count}

    delay = min(policy['backoff_base_seconds'] * (2 ** count),
                policy['backoff_max_seconds'])
    last_retry = budget.get('last_retry_by_class')
    last_retry = last_retry if isinstance(last_retry, dict) else {}
    anchor = _timestamp(last_retry.get(failure_class), started)
    if effective_now - anchor < delay:
        return {'action': 'wait', 'reason': 'backoff', 'wait_seconds': delay - (effective_now - anchor),
                'attempts': count}
    return {'action': 'retry', 'reason': 'eligible', 'attempts': count, 'wait_seconds': 0}


def record_retry(budget, failure_class, now):
    attempts = budget.setdefault('attempts_by_class', {})
    count = attempts.get(failure_class, 0)
    attempts[failure_class] = (count if type(count) is int and count >= 0 else 0) + 1
    budget.setdefault('last_retry_by_class', {})[failure_class] = float(now)
    budget.setdefault('first_seen_by_class', {}).setdefault(failure_class, float(now))


def _is_pytest_invocation(argv):
    """Recognize pytest commands so their wall-clock summary is not evidence."""
    if not argv:
        return False
    values = [str(value) for value in argv]
    program = values[0].replace('\\', '/').rsplit('/', 1)[-1].casefold()
    if program == 'pytest' or re.fullmatch(r'pytest(?:-[0-9.]+)?', program):
        return True
    if program.startswith('python') and values[1:3] == ['-m', 'pytest']:
        return True
    return (program in ('uv', 'poetry', 'pipenv', 'tox', 'nox', 'hatch')
            and any(value.replace('\\', '/').rsplit('/', 1)[-1].casefold() == 'pytest'
                    for value in values[1:]))


def operation_progress_key(record):
    """Stable operation identity plus observed output, ignoring prose and timestamps."""
    if not isinstance(record, dict):
        return None
    tool = str(record.get('tool') or '')
    if tool not in ('run_command', 'run_checks'):
        return None
    classification = record.get('classification')
    classification = classification if isinstance(classification, dict) else {}
    argv = record.get('argv') if isinstance(record.get('argv'), list) else []
    normalize_pytest_time = _is_pytest_invocation(argv)
    observed = {}
    for key in ('output', 'stdout', 'stderr', 'error'):
        observed_value = record.get(key)
        if isinstance(observed_value, str) and observed_value:
            if normalize_pytest_time:
                observed_value = _PYTEST_SUMMARY_DURATION.sub(
                    r'\g<prefix><duration>\g<suffix>', observed_value)
            observed[key] = observed_value
    observed_digest = hashlib.sha256(json.dumps(
        observed, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    value = {
        'tool': tool,
        'argv': argv,
        'cwd': str(record.get('cwd') or ''),
        'exit_code': record.get('exit_code'),
        'disposition': str(classification.get('disposition') or ''),
        'cause': str(classification.get('cause') or ''),
        'verification_status': str(record.get('verification_status') or ''),
        'observed_digest': observed_digest,
    }
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()
