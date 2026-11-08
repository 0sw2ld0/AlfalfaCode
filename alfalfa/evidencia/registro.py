"""Runtime-owned evidence. Model prose cannot turn a failed check into a pass."""
from __future__ import annotations

import json
import posixpath
import re
import shlex

from ..proveedores.nativo.herramientas import atomic_json, digest
from ..reintentos.fallos import _sin_credenciales
from .lectura import clasificacion as _clasificacion
from .lectura import comprobacion_suficiente as _comprobacion_suficiente
from .lectura import comprobacion_sustantiva as _comprobacion_sustantiva
from .lectura import comprobacion_vigente as _comprobacion_vigente

# Con guion bajo: RunEvidence tiene métodos públicos con estos mismos nombres.
from .lectura import dentro_del_espacio as _dentro_del_espacio
from .lectura import salida_incompleta as _salida_incompleta
from .operaciones import classify_operation, operation_record_key

_MAX_MESSAGE = 800
_MAX_PENDING_DETAILS = 10
_MAX_PATH_DETAIL = 160
_MAX_OPERATION_OUTPUT = 4000


class RunEvidence:
    def __init__(self, runtime):
        self.runtime = runtime
        self._ensure_schema()

    def _ensure_schema(self):
        """Add the append-only evidence ledger without discarding v1 session data."""
        state = self.runtime.state
        prior_version = state.get('evidence_version', 1)
        if type(prior_version) is not int:
            prior_version = 1
        state.setdefault('unresolved_records', {})
        state.setdefault('resolution_records', [])
        state.setdefault('evidence_counters', {})
        state.setdefault('legacy_unresolved_index', {})
        if not isinstance(state['unresolved_records'], dict):
            state['legacy_unresolved_records'] = state['unresolved_records']
            state['unresolved_records'] = {}
        if not isinstance(state['resolution_records'], list):
            state['legacy_resolution_records'] = state['resolution_records']
            state['resolution_records'] = []
        if not isinstance(state['evidence_counters'], dict):
            state['evidence_counters'] = {}
        if not isinstance(state['legacy_unresolved_index'], dict):
            state['legacy_unresolved_index'] = {}
        if prior_version < 2:
            self._enrich_legacy_history()
        self._import_legacy_unresolved()
        state['evidence_version'] = 2
        self._refresh_incidents()

    def _next_id(self, kind):
        counters = self.runtime.state['evidence_counters']
        stored = counters.get(kind, 0)
        value = stored if type(stored) is int and stored >= 0 else 0
        state = self.runtime.state
        used = {str(key) for key in state.get('unresolved_records', {})}
        for field in ('operations', 'checks', 'resolution_records'):
            rows = state.get(field, [])
            if isinstance(rows, list):
                used.update(str(row.get('id')) for row in rows if isinstance(row, dict) and row.get('id'))
        while True:
            value += 1
            candidate = f'{kind}-{value:06d}'
            if candidate not in used:
                counters[kind] = value
                return candidate

    def _enrich_legacy_history(self):
        """Give old operation rows identities while retaining their captured fields."""
        rt = self.runtime
        session_id = str(rt.task.get('id') or '')
        project = str(rt.task.get('project') or '')
        operations = rt.state.get('operations')
        if isinstance(operations, list):
            for operation in operations:
                if not isinstance(operation, dict):
                    continue
                operation.setdefault('id', self._next_id('operation'))
                operation.setdefault('session_id', session_id)
                operation.setdefault('project', project)
        checks = rt.state.get('checks')
        if isinstance(checks, list):
            for check in checks:
                if not isinstance(check, dict):
                    continue
                check.setdefault('id', self._next_id('check'))
                check.setdefault('session_id', session_id)
                check.setdefault('project', project)
                if not check.get('operation_id') and isinstance(operations, list):
                    match = next((operation for operation in reversed(operations)
                                  if isinstance(operation, dict)
                                  and operation.get('argv') == check.get('argv')
                                  and operation.get('cwd') == check.get('cwd')
                                  and operation.get('purpose') == check.get('purpose')
                                  and operation.get('revision') == check.get('revision')
                                  and operation.get('exit_code') == check.get('exit_code')),
                                 None)
                    if match:
                        check['operation_id'] = match['id']

    def _import_legacy_unresolved(self):
        state = self.runtime.state
        legacy = state.get('unresolved')
        if not isinstance(legacy, dict):
            return
        indexed = state['legacy_unresolved_index']
        for target, detail in list(legacy.items()):
            target = str(target)
            if target in indexed or target in state['unresolved_records']:
                continue
            matches = []
            for check in state.get('checks', []) if isinstance(state.get('checks'), list) else []:
                if (isinstance(check, dict) and isinstance(check.get('argv'), list)
                        and str(check['argv']) == target
                        and self._historical_failure(check)):
                    matches.append(('check', check))
            if not matches:
                for operation in state.get('operations', []) if isinstance(state.get('operations'), list) else []:
                    if (isinstance(operation, dict) and operation.get('tool') == 'run_command'
                            and isinstance(operation.get('argv'), list)
                            and str(operation['argv']) == target
                            and self._historical_failure(operation)):
                        matches.append(('operation', operation))
            ids = []
            if matches:
                for source, record in matches:
                    ids.append(self._add_finding(record, source=source, legacy=True,
                                                 legacy_target=target,
                                                 legacy_detail=str(detail)))
            else:
                # An old summary without matching command evidence cannot be
                # upgraded into a claim of success. Keep it as an opaque blocker.
                finding_id = self._next_id('finding')
                rt = self.runtime
                self.runtime.state['unresolved_records'][finding_id] = {
                    'id': finding_id, 'status': 'obsolete', 'legacy': True,
                    'legacy_target': target, 'session_id': str(rt.task.get('id') or ''),
                    'project': str(rt.task.get('project') or ''),
                    'cwd': str(rt.tools.root.resolve()), 'revision': None,
                    'fingerprint': None, 'argv': None, 'purpose': None,
                    'source': 'legacy_unresolved',
                    'original': {'cause': 'legacy_unresolved', 'detail': str(detail),
                                 'output': ''},
                    'summary': str(detail)}
                ids.append(finding_id)
            indexed[target] = ids

    @staticmethod
    def _historical_failure(record):
        classification = record.get('classification')
        if isinstance(classification, dict):
            return (classification.get('disposition') == 'failure'
                    or RunEvidence._output_incomplete(record)
                    or record.get('timed_out') is True
                    or record.get('output_limit_exceeded') is True)
        return (record.get('exit_code') != 0 or record.get('error')
                or RunEvidence._output_incomplete(record))

    _output_incomplete = staticmethod(_salida_incompleta)

    def _add_finding(self, record, *, source, legacy=False, legacy_target=None,
                     legacy_detail=None):
        finding_id = self._next_id('finding')
        classification = self._classification(record)
        result_cause = classification.get('cause')
        insufficient = source == 'check' and not self._check_sufficient(record)
        if insufficient and classification.get('disposition') == 'pass':
            result_cause = 'truncated_output' if record.get('truncated') else 'incomplete_check_evidence'
        original = {
            'evidence_id': record.get('id'),
            'operation_id': record.get('operation_id') or (record.get('id') if source == 'operation' else None),
            'cause': result_cause or 'operation_failed',
            'classification': classification,
            'exit_code': record.get('exit_code'),
            'output': str(record.get('output') or ''),
            'error': str(record.get('error') or ''),
            'truncated': bool(record.get('truncated')),
            'output_storage_truncated': record.get('output_storage_truncated'),
            'execution_complete': record.get('execution_complete'),
            'timed_out': bool(record.get('timed_out')),
            'output_limit_exceeded': bool(record.get('output_limit_exceeded')),
        }
        if legacy:
            original['legacy_detail'] = str(legacy_detail or '')
        finding = {
            'id': finding_id, 'status': 'pending', 'legacy': bool(legacy),
            'session_id': str(record.get('session_id') or self.runtime.task.get('id') or ''),
            'project': str(record.get('project') or self.runtime.task.get('project') or ''),
            'cwd': str(record.get('cwd') or self.runtime.tools.root.resolve()),
            'revision': record.get('revision'),
            'fingerprint': record.get('fingerprint'),
            'argv': list(record['argv']) if isinstance(record.get('argv'), list) else None,
            'purpose': str(record.get('purpose') or ''),
            'source': source,
            'legacy_target': legacy_target,
            'original': original,
            'summary': str(record.get('error') or classification.get('message')
                           or record.get('output') or 'La operación falló.')}
        self.runtime.state['unresolved_records'][finding_id] = finding
        return finding_id

    def _operation_fields(self, args, result, revision):
        cwd = str(result.get('cwd') or self.runtime.tools.root.resolve())
        output = str(result.get('output') or '')
        exit_code = result.get('exit_code')
        classification = result.get('classification')
        if not isinstance(classification, dict):
            classification = classify_operation(args.get('argv'), args.get('purpose'), cwd,
                                                 output, exit_code,
                                                 output_truncated=bool(result.get('truncated')),
                                                 timed_out=bool(result.get('timed_out')),
                                                 exception=bool(result.get('exception')
                                                                or result.get('output_limit_exceeded')))
        record = {'argv': list(args['argv']), 'cwd': cwd,
                  'purpose': str(args['purpose']), 'revision': revision,
                  'exit_code': exit_code, 'output': output[-_MAX_OPERATION_OUTPUT:],
                  'truncated': bool(result.get('truncated', False) or len(output) > _MAX_OPERATION_OUTPUT),
                  'output_storage_truncated': bool(result.get('output_storage_truncated')
                                                   or len(output) > _MAX_OPERATION_OUTPUT),
                  'classification': classification,
                  'project': str(self.runtime.task.get('project') or ''),
                  'session_id': str(self.runtime.task.get('id') or ''),
                  'fingerprint': self.fingerprint()}
        for key in ('error', 'timed_out', 'exception', 'output_limit_exceeded',
                    'workspace_context', 'workspace_stable',
                    'retry_guard_key', 'retry_guard_content_generation',
                    'retry_guard_attempts_by_class', 'execution_complete',
                    # Política on-request de AlfalfaCode: aislamiento y relación entre intentos.
                    'sandbox', 'intento', 'reintento_de', 'permisos', 'red', 'denegacion_probable'):
            if key in result:
                record[key] = result[key]
        return record

    def fingerprint(self):
        latest = {}
        changes = self.runtime.state.get('changes', [])
        if not isinstance(changes, list):
            return latest
        for change in changes:
            if (isinstance(change, dict) and isinstance(change.get('path'), str)
                    and not change['path'].startswith('/') and '..' not in change['path'].split('/')
                    and isinstance(change.get('after'), str)):
                latest[change['path']] = change['after']
        return latest

    def integrity(self):
        mismatches = []
        expected = self.fingerprint()
        for name, sha in expected.items():
            try:
                path = self.runtime.tools.path(name)
                if sha == 'DELETED':
                    # Lo borró o movió una orden del agente: sólo vuelve a ser
                    # ajeno si alguien lo recrea.
                    if path.exists() or path.is_symlink():
                        mismatches.append(name)
                    continue
                data, _ = self.runtime.tools.read(path)
                if digest(data) != sha:
                    mismatches.append(name)
            except (OSError, ValueError, UnicodeError):
                mismatches.append(name)
        return {'files_checked': len(expected), 'mismatches': mismatches}

    def record_check(self, args, result, revision):
        if not result.get('operation_id'):
            self.record_operation(args, result, revision, 'run_checks')
        record = self._operation_fields(args, result, revision)
        record['id'] = self._next_id('check')
        record['operation_id'] = result.get('operation_id')
        record['tool'] = 'run_checks'
        record['verification_status'] = ('sufficient' if self._check_sufficient(record)
                                         else self._insufficient_check_reason(record))
        self.runtime.state.setdefault('checks', []).append(record)
        if record['classification'].get('disposition') == 'failure' or record['verification_status'] != 'sufficient':
            self._add_finding(record, source='check')
        self._record_resolution_attempts(record)
        self._refresh_incidents()
        return record

    def _check_sufficient(self, check):
        # La regla es pública en arnes/evidencia/lectura.py; aquí se conserva el
        # método (las pruebas y el runtime lo usan) con el mismo resultado.
        return _comprobacion_suficiente(check, clasificar=self._classification)

    @staticmethod
    def _insufficient_check_reason(check):
        if check.get('workspace_stable') is False:
            return 'workspace_changed_during_check'
        if check.get('timed_out'):
            return 'timeout'
        if check.get('output_limit_exceeded') or RunEvidence._output_incomplete(check):
            return 'truncated_output'
        if check.get('exception'):
            return 'execution_exception'
        classification = check.get('classification') or {}
        return classification.get('cause') or 'check_failed'

    def record_operation(self, args, result, revision, tool):
        record = self._operation_fields(args, result, revision)
        record['id'] = self._next_id('operation')
        record['tool'] = tool
        self.runtime.state.setdefault('operations', []).append(record)
        result['operation_id'] = record['id']
        if tool == 'run_command' and record['classification'].get('disposition') == 'failure':
            self._add_finding(record, source='operation')
            self._refresh_incidents()
        return record

    def record_tool_error(self, tool, args, detail):
        """Keep failed file edits in the same incident ledger, without inventing a check."""
        record_id = self._next_id('operation')
        text = str(detail or 'La herramienta falló.')
        record = {'id': record_id, 'argv': [], 'cwd': str(self.runtime.tools.root.resolve()),
                  'purpose': str(self.runtime.task.get('objective') or ''),
                  'revision': self.runtime.state.get('workspace_revision', 0),
                  'exit_code': None, 'output': text, 'error': text, 'truncated': False,
                  'classification': {'status': 'failed', 'disposition': 'failure',
                                     'cause': 'tool_execution_error', 'message': text},
                  'project': str(self.runtime.task.get('project') or ''),
                  'session_id': str(self.runtime.task.get('id') or ''),
                  'fingerprint': self.fingerprint(), 'tool': str(tool),
                  'path': str(args.get('path') or '') if isinstance(args, dict) else ''}
        self.runtime.state.setdefault('operations', []).append(record)
        self._add_finding(record, source='tool')
        self._refresh_incidents()
        return record

    def _pytest_signature(self, argv, base=''):
        """Qué pruebas corre una orden de pytest, sin lo que sólo cambia el informe.

        Un agente lanzó pytest desde la raíz con `server/tests/…` (código 4: sin el
        paquete `app`) y lo repitió en verde con `bash -c 'cd server && … tests/…'`;
        eran las mismas pruebas y el ticket quedó bloqueado.
        """
        if not isinstance(argv, list) or not argv:
            return None
        # base: carpeta de la orden relativa a la raíz (campo cwd de run_checks).
        base = base or ''
        values = [str(v) for v in argv]
        shell = values[0].replace('\\', '/').rsplit('/', 1)[-1]
        if shell in ('bash', 'sh', 'zsh') and len(values) == 3 and values[1] in ('-c', '-lc'):
            match = re.fullmatch(r'\s*cd\s+(\S+)\s*&&\s*(.+?)\s*', values[2], re.S)
            if not match:
                return None
            try:
                values = shlex.split(match.group(2))
            except ValueError:
                return None
            if any(v in ('&&', '||', ';', '|', '>', '>>', '<', '&') for v in values):
                return None
            carpeta = posixpath.normpath(match.group(1))
            if carpeta.startswith('/') or carpeta.split('/')[0] == '..':
                return None
            base = posixpath.normpath(posixpath.join(base, carpeta)) if base else carpeta
        if not values:
            return None
        program = values[0].replace('\\', '/').rsplit('/', 1)[-1].casefold()
        if program in ('pytest', 'pytest.exe'):
            invocation_end = 1
        elif (re.fullmatch(r'(?:python|pypy)(?:\d+(?:\.\d+)*)?(?:\.exe)?', program)
              and values[1:3] == ['-m', 'pytest']):
            invocation_end = 3
        else:
            return None
        # Ni la verbosidad ni parar al primer fallo cambian qué pruebas pasan en verde.
        report_flags = {'-q', '--quiet', '-v', '--verbose', '-vv', '-vvv', '--no-header', '-x', '--exitfirst'}
        rest = []
        previo = None
        for value in values[invocation_end:]:
            # Tras otra opción puede ser su valor (`--override-ini -q`), no una marca.
            suelta = previo is None or previo in report_flags or not previo.startswith('-')
            previo = value
            if value in report_flags and suelta:
                continue
            path, sep, node = value.partition('::')
            if not value.startswith('-') and (path.endswith('.py') or '/' in path):
                value = posixpath.normpath(posixpath.join(base, path)) + sep + node
            rest.append(value)
        return tuple(values[:invocation_end]) + tuple(rest)

    def _equivalence(self, finding, check):
        # El propósito no entra: un modelo lo redacta distinto en cada intento
        # («Validar de nuevo el esquema…», «Volver a validar…») y la misma orden,
        # ya en verde, nunca contaba como arreglo.
        raiz = str(self.runtime.tools.root.resolve())
        if (finding.get('session_id') != check.get('session_id')
                or finding.get('project') != check.get('project')
                or not _dentro_del_espacio(finding.get('cwd'), raiz)
                or not _dentro_del_espacio(check.get('cwd'), raiz)):
            return None, 'session, proyecto o cwd no coinciden con la incidencia.'
        original_argv = finding.get('argv')
        checking_argv = check.get('argv')
        if (isinstance(original_argv, list) and original_argv == checking_argv
                and finding.get('cwd') == check.get('cwd')):
            return 'same_argv', 'argv, propósito, proyecto y cwd coinciden exactamente.'
        # Misma suite lanzada desde otra carpeta del worktree (cwd de run_checks):
        # las rutas se comparan ya relativas a la raíz.
        old_signature = self._pytest_signature(original_argv, posixpath.relpath(finding['cwd'], raiz))
        new_signature = self._pytest_signature(checking_argv, posixpath.relpath(check['cwd'], raiz))
        if old_signature is not None and old_signature == new_signature:
            return ('pytest_reporting_flags',
                    'Sólo cambian flags de verbosidad pytest o la carpeta desde la que se lanza; '
                    'ejecutable, selectores y demás argumentos coinciden.')
        return None, 'argv o selector de comprobación distinto; no hay equivalencia verificable.'

    def _check_is_current(self, check):
        return _comprobacion_vigente(
            check, sesion=str(self.runtime.task.get('id') or ''),
            proyecto=str(self.runtime.task.get('project') or ''),
            cwd=str(self.runtime.tools.root.resolve()),
            revision=self.runtime.state.get('workspace_revision', 0),
            huella=self.fingerprint, desajustes=lambda: self.integrity()['mismatches'])

    # Lectura pública para quien está fuera del registro (un integrador, por ejemplo):
    # los privados siguen siendo la implementación y se respetan si se parchean.
    def comprobacion_vigente(self, check):
        return self._check_is_current(check)

    def comprobacion_suficiente(self, check):
        return self._check_sufficient(check)

    def clasificacion(self, check):
        return self._classification(check)

    def _record_resolution_attempts(self, check):
        state = self.runtime.state
        for finding in list(state['unresolved_records'].values()):
            previous_status = self._incident_status(finding)
            equivalent, justification = self._equivalence(finding, check)
            if previous_status == 'resolved':
                if equivalent and self._check_is_current(check) and self._check_sufficient(check):
                    status = 'resolved'
                else:
                    continue
            elif equivalent is None:
                status = 'not_relevant'
            elif not self._check_is_current(check):
                status = 'obsolete'
            elif not self._check_sufficient(check):
                status = 'pending'
            else:
                status = 'resolved'
                justification = ('La comprobación actual terminó correctamente y coincide con la operación: '
                                 + justification)
            resolution = {
                'id': self._next_id('resolution'), 'finding_id': finding['id'],
                'evidence_id': check.get('id'), 'operation_id': check.get('operation_id'),
                'session_id': check.get('session_id'),
                'status': status, 'purpose': check.get('purpose'),
                'project': check.get('project'), 'cwd': check.get('cwd'),
                'revision': check.get('revision'), 'fingerprint': check.get('fingerprint'),
                'argv': list(check.get('argv') or []),
                'equivalence': equivalent,
                'verification_status': check.get('verification_status'),
                'classification': self._classification(check),
                'check_evidence': {'id': check.get('id'),
                                   'operation_id': check.get('operation_id'),
                                   'argv': list(check.get('argv') or []),
                                   'exit_code': check.get('exit_code'),
                                   'output': str(check.get('output') or ''),
                                   'truncated': bool(check.get('truncated')),
                                   'output_storage_truncated': check.get('output_storage_truncated'),
                                   'execution_complete': check.get('execution_complete'),
                                   'timed_out': bool(check.get('timed_out')),
                                   'classification': self._classification(check)},
                'justification': justification}
            state['resolution_records'].append(resolution)
            if status != 'not_relevant':
                finding['last_resolution_id'] = resolution['id']

    def _incident_status(self, finding):
        current_revision = self.runtime.state.get('workspace_revision', 0)
        current_fingerprint = self.fingerprint()
        resuelta_antes = False
        for resolution in reversed(self.runtime.state['resolution_records']):
            if resolution.get('finding_id') != finding.get('id') or resolution.get('status') != 'resolved':
                continue
            resuelta_antes = True
            if self._resolution_is_current(finding, resolution, current_revision,
                                           current_fingerprint):
                return 'resolved'
        if (type(finding.get('revision')) is int
                and finding.get('revision') == current_revision
                and isinstance(finding.get('fingerprint'), dict)
                and finding.get('fingerprint') == current_fingerprint):
            return 'pending'
        # La misma orden ya pasó en verde después del fallo: que el workspace
        # cambie luego no lo vuelve a abrir. Un agente copió su validador a la
        # carpeta final y borró el temporal; la orden original ya no podía
        # repetirse y el ticket quedó bloqueado. Si lo último rompió algo,
        # lo dice la verificación vigente, que exige comprobar tras cada cambio.
        return 'resolved' if resuelta_antes else 'obsolete'

    def _resolution_is_current(self, finding, resolution, revision, fingerprint):
        checks = self.runtime.state.get('checks', [])
        operations = self.runtime.state.get('operations', [])
        if not isinstance(checks, list) or not isinstance(operations, list):
            return False
        check = next((row for row in checks if isinstance(row, dict)
                      and row.get('id') == resolution.get('evidence_id')), None)
        if not check or check.get('operation_id') != resolution.get('operation_id'):
            return False
        operation = next((row for row in operations if isinstance(row, dict)
                          and row.get('id') == check.get('operation_id')), None)
        if not operation or operation.get('tool') != 'run_checks':
            return False
        equivalent, justification = self._equivalence(finding, check)
        if equivalent is None or resolution.get('equivalence') != equivalent:
            return False
        if resolution.get('justification') not in (
                justification,
                'La comprobación actual terminó correctamente y coincide con la operación: '
                + justification):
            return False
        if not self._check_sufficient(check) or not self._check_is_current(check):
            return False
        context_fields = ('session_id', 'project', 'cwd', 'purpose', 'revision', 'fingerprint')
        if any(resolution.get(field) != check.get(field) for field in context_fields):
            return False
        if (resolution.get('argv') != check.get('argv')
                or resolution.get('verification_status') != check.get('verification_status')
                or resolution.get('classification') != check.get('classification')):
            return False
        if check.get('revision') != revision or check.get('fingerprint') != fingerprint:
            return False
        if (operation.get('argv') != check.get('argv')
                or any(operation.get(field) != check.get(field)
                       for field in ('session_id', 'project', 'cwd', 'purpose', 'revision',
                                     'fingerprint', 'exit_code', 'output', 'classification',
                                     'truncated', 'output_storage_truncated', 'execution_complete',
                                     'timed_out', 'exception',
                                     'output_limit_exceeded'))):
            return False
        evidence = resolution.get('check_evidence')
        return (isinstance(evidence, dict) and evidence.get('id') == check.get('id')
                and evidence.get('operation_id') == check.get('operation_id')
                and evidence.get('argv') == check.get('argv')
                and evidence.get('exit_code') == check.get('exit_code')
                and evidence.get('output') == check.get('output')
                and evidence.get('classification') == check.get('classification')
                and evidence.get('truncated') == bool(check.get('truncated'))
                and evidence.get('output_storage_truncated') == check.get('output_storage_truncated')
                and evidence.get('execution_complete') == check.get('execution_complete')
                and evidence.get('timed_out') == bool(check.get('timed_out')))

    def _refresh_incidents(self):
        self._import_legacy_unresolved()
        records = self.runtime.state['unresolved_records']
        for finding in records.values():
            finding['status'] = self._incident_status(finding)
        self.runtime.state['unresolved'] = {
            finding['id']: str(finding.get('summary') or finding['original'].get('cause') or 'Pendiente')
            for finding in records.values() if self._blocks(finding) and not self._superada(finding)}
        return records

    def _superada(self, finding):
        """¿El agente ya verificó de otra forma el estado actual tras un fallo sin repetir?

        Un `node --test demo/test/` mal lanzado seguido de las mismas pruebas por
        archivo, en verde, dejaba el ticket bloqueado hasta que alguien lo habilitara.
        Sólo cuenta tras el aviso, para que antes intente repetir la orden, y con una comprobación posterior, vigente y sustantiva. No se da por
        resuelta: el revisor recibe la lista y decide si esconde un fallo real.
        """  # noqa: E501
        if finding.get('status') == 'resolved' or finding.get('source') != 'check' \
                or not self.runtime.state.get('unresolved_nudged'):
            return False
        checks = self.runtime.state.get('checks')
        original = finding.get('original') if isinstance(finding.get('original'), dict) else {}
        if not isinstance(checks, list):
            return False
        posicion = next((n for n, check in enumerate(checks) if isinstance(check, dict)
                         and check.get('id') == original.get('evidence_id')), None)
        if posicion is None:
            return False
        return any(isinstance(check, dict) and _comprobacion_sustantiva(check.get('argv'))
                   and self._check_sufficient(check) and self._check_is_current(check)
                   for check in checks[posicion + 1:])

    @staticmethod
    def _blocks(finding):
        """¿Impide entregar? Una edición fallida o una orden exploratoria que
        quedaron atrás (el workspace cambió después) ya no dicen nada del estado
        actual: una edición fallida no tiene orden que repetir y nunca se
        resolvía, así que cualquier tropiezo bloqueaba el ticket para siempre.
        Las comprobaciones (run_checks) sí siguen pendientes hasta volver a
        pasar: pueden haber dejado un fallo real que nadie volvió a mirar."""
        if finding['status'] == 'resolved':
            return False
        if finding.get('repetida_sin_sandbox'):
            # El sandbox la bloqueó y se repitió fuera: manda el reintento, que deja su
            # propia incidencia si también falla.
            return False
        return not (finding['status'] == 'obsolete' and finding.get('source') in ('tool', 'operation'))

    def repetida_sin_sandbox(self, operation_id, retry_operation_id):
        """El intento en el sandbox que falló por el sandbox se repitió sin él (bajo_demanda):
        su incidencia deja de bloquear. Sin esto, «Abrir app.command» abría la app fuera del
        sandbox y el turno acababa «bloqueado» por el intento de dentro."""
        if not operation_id:
            return
        for finding in self.runtime.state.get('unresolved_records', {}).values():
            if (finding.get('original') or {}).get('operation_id') == operation_id:
                finding['repetida_sin_sandbox'] = retry_operation_id or True
        self._refresh_incidents()

    def unresolved_records(self):
        records = self._refresh_incidents()
        return [dict(record) for record in records.values()]

    def report(self):
        rt = self.runtime
        integrity = self.integrity()
        incident_records = self._refresh_incidents()
        all_incidents = list(incident_records.values())
        # Las mismas que frenan al agente: una edición o una orden obsoletas ya no
        # dicen nada del estado actual y bloqueaban con las pruebas en verde.
        superadas = [finding for finding in all_incidents if self._superada(finding)]
        open_incidents = [finding for finding in all_incidents
                          if self._blocks(finding) and finding not in superadas]
        revision = rt.state.get('workspace_revision', 0)
        checks = rt.state.get('checks', [])
        if not isinstance(checks, list):
            checks = []
        current = [c for c in checks if isinstance(c, dict) and c.get('revision') == revision
                   and ('workspace_context' not in c or self._check_is_current(c))
                   and isinstance(c.get('argv'), list)
                   and ('exit_code' in c)
                   and (type(c.get('exit_code')) is int or c.get('exit_code') is None)]
        # Resolve failures against the full history, including earlier workspace
        # revisions. A file edit or unrelated green check cannot erase a pending
        # whitespace result; the same argv, cwd, and purpose must pass afterward.
        latest_checks = {}
        for check in checks:
            if (isinstance(check, dict) and isinstance(check.get('argv'), list)
                    and 'exit_code' in check
                    and (type(check.get('exit_code')) is int or check.get('exit_code') is None)):
                latest_checks[operation_record_key(check)] = check
        current_latest_checks = {}
        for check in current:
            current_latest_checks[operation_record_key(check)] = check
        operations = rt.state.get('operations', [])
        if not isinstance(operations, list):
            operations = []
        current_operations = [operation for operation in operations
                              if (isinstance(operation, dict)
                                  and operation.get('revision') == revision
                                  and isinstance(operation.get('argv'), list))]
        latest_operations = {}
        for operation in operations:
            if (isinstance(operation, dict) and isinstance(operation.get('argv'), list)
                    and 'exit_code' in operation):
                latest_operations[operation_record_key(operation)] = operation
        resolved_evidence_ids = set()
        for finding in all_incidents:
            if finding.get('status') != 'resolved':
                continue
            original = finding.get('original') if isinstance(finding.get('original'), dict) else {}
            for evidence_id in (original.get('evidence_id'), original.get('operation_id')):
                if evidence_id:
                    resolved_evidence_ids.add(evidence_id)
        for finding in superadas:
            resolved_evidence_ids.add(finding.get('original', {}).get('evidence_id'))
        operation_failures = [self._finding_failure_summary(finding) for finding in open_incidents
                              if finding.get('source') == 'operation']
        recorded_operation_ids = {
            row.get('original', {}).get('operation_id') for row in all_incidents
            if row.get('source') == 'operation'}
        operation_failures.extend(
            self._failure_summary(operation) for operation in latest_operations.values()
            if operation.get('tool') == 'run_command'
            and self._classification(operation).get('disposition') == 'failure'
            and operation.get('id') not in recorded_operation_ids
            and operation.get('id') not in resolved_evidence_ids)
        check_failures = [check for check in latest_checks.values()
                          if self._classification(check).get('disposition') == 'failure'
                          and check.get('id') not in resolved_evidence_ids]
        known_check_ids = {check.get('id') for check in check_failures}
        check_failures.extend(
            self._finding_failure_summary(finding) for finding in open_incidents
            if finding.get('source') == 'check'
            and finding.get('original', {}).get('evidence_id') not in known_check_ids)
        if integrity['mismatches']:
            status = 'changed_externally'
        elif operation_failures or check_failures or open_incidents:
            status = ('failed' if any(finding.get('status') == 'pending'
                                      for finding in open_incidents)
                      or operation_failures or check_failures else 'stale')
        elif current_latest_checks:
            status = 'checks_passed'
        elif checks:
            # Legacy check rows without a revision are evidence, but cannot prove
            # that they ran against the current workspace snapshot.
            status = 'stale'
        else:
            status = 'not_run'
        return dict(integrity, status=status, checks_run=len(current),
                    operations_run=len(current_operations),
                    operation_failures=operation_failures,
                    check_failures=check_failures,
                    comprobaciones_superadas=[self._finding_failure_summary(finding) for finding in superadas],
                    incidents=[self._incident_summary(finding) for finding in all_incidents],
                    incident_counts={name: sum(finding.get('status') == name for finding in all_incidents)
                                     for name in ('resolved', 'obsolete', 'not_relevant', 'pending')},
                    resolutions=len(rt.state.get('resolution_records', [])),
                    resolution_counts={name: sum(row.get('status') == name
                                                 for row in rt.state.get('resolution_records', []))
                                       for name in ('resolved', 'obsolete', 'not_relevant', 'pending')},
                    workspace_revision=revision)

    _classification = staticmethod(_clasificacion)

    def _failure_summary(self, operation):
        return {'argv': list(operation.get('argv') or []),
                'cwd': str(operation.get('cwd') or ''),
                'purpose': str(operation.get('purpose') or ''),
                'exit_code': operation.get('exit_code'),
                'id': operation.get('id'), 'output': str(operation.get('output') or ''),
                'truncated': bool(operation.get('truncated')),
                'output_storage_truncated': operation.get('output_storage_truncated'),
                'execution_complete': operation.get('execution_complete'),
                'classification': self._classification(operation)}

    def _finding_failure_summary(self, finding):
        original = finding.get('original') if isinstance(finding.get('original'), dict) else {}
        return {'id': finding.get('id'), 'evidence_id': original.get('evidence_id'),
                'argv': list(finding.get('argv') or []), 'cwd': str(finding.get('cwd') or ''),
                'purpose': str(finding.get('purpose') or ''),
                'exit_code': original.get('exit_code'), 'output': str(original.get('output') or ''),
                'status': finding.get('status'), 'cause': original.get('cause'),
                'classification': original.get('classification') or {}}

    @staticmethod
    def _incident_summary(finding):
        original = finding.get('original') if isinstance(finding.get('original'), dict) else {}
        return {'id': finding.get('id'), 'status': finding.get('status'),
                'source': finding.get('source'), 'operation_id': original.get('operation_id'),
                'evidence_id': original.get('evidence_id'), 'cause': original.get('cause'),
                'project': finding.get('project'), 'cwd': finding.get('cwd'),
                'purpose': finding.get('purpose'), 'revision': finding.get('revision'),
                'fingerprint': finding.get('fingerprint')}

    def _pending(self):
        pending = self.runtime.state.get('pending', [])
        if not isinstance(pending, list):
            return []
        return [call for call in pending if isinstance(call, dict) and call.get('status') != 'done']

    def _pending_summary(self, pending):
        rows = []
        for call in pending[:_MAX_PENDING_DETAILS]:
            function = call.get('function') if isinstance(call.get('function'), dict) else {}
            args = function.get('arguments') if isinstance(function.get('arguments'), dict) else {}
            name = self._safe_text(function.get('name') or 'unknown')[:80]
            row = {'tool': name,
                   'status': self._safe_text(call.get('status') or 'unknown')[:30],
                   'path': self._safe_text(args.get('path') or '')[:_MAX_PATH_DETAIL]}
            if name == 'run_command':
                argv = args.get('argv')
                if isinstance(argv, list):
                    row['command'] = self._safe_text(json.dumps(argv, ensure_ascii=False))[:400]
                purpose = args.get('purpose')
                if isinstance(purpose, str):
                    row['purpose'] = self._safe_text(purpose)[:160]
            rows.append(row)
        return rows

    @staticmethod
    def _safe_text(value):
        # Se limpian credenciales antes de recortar. La limpieza vive en
        # arnes/reintentos/fallos.py, así que ya no hace falta importar el runtime.
        return str(_sin_credenciales(value) or '')[:_MAX_MESSAGE]

    def _review(self):
        rt = self.runtime
        closing = rt.state.get('review_closing')
        closing = closing if isinstance(closing, dict) else {}
        raw = closing.get('result') if closing.get('status') == 'completed' else None
        if not isinstance(raw, str):
            raw = self._result(closing)
        verdict = None
        payload = None
        if isinstance(raw, str):
            try:
                value = json.loads(raw)
            except (TypeError, ValueError):
                value = None
            if isinstance(value, dict) and value.get('verdict') in ('approved', 'changes_requested'):
                verdict, payload = value['verdict'], value
        if (verdict is None and raw is None and rt.task.get('review')
                and not self._attempt_has_unanswered_user()):
            previous = rt.task.get('runtime_review_verdict')
            if previous in ('approved', 'changes_requested'):
                verdict = previous
        return verdict, payload, closing

    def _attempt_has_unanswered_user(self):
        """Whether the current attempt has a user request but no answer yet."""
        messages = self.runtime.state.get('messages', [])
        if not isinstance(messages, list):
            return False
        start = getattr(self.runtime, 'attempt_start_index', None)
        start = start if type(start) is int and start >= 0 else 0
        last_user = None
        last_assistant = None
        for index in range(start, len(messages)):
            message = messages[index]
            if not isinstance(message, dict):
                continue
            if message.get('role') == 'user':
                last_user = index
            elif message.get('role') == 'assistant':
                last_assistant = index
        return last_user is not None and (last_assistant is None or last_assistant < last_user)

    @staticmethod
    def _has_verify_findings(review_payload):
        if not isinstance(review_payload, dict):
            return False
        findings = review_payload.get('findings')
        return isinstance(findings, list) and any(
            isinstance(finding, dict) and finding.get('kind') == 'verify'
            for finding in findings)

    def _failure_cause(self, error, report, pending, review_payload, closing):
        rt = self.runtime
        task = rt.task
        state = rt.state
        provider = task.get('runtime_provider_error')
        provider = provider if isinstance(provider, dict) else {}
        provider_cause = provider.get('cause')
        provider_kind = provider.get('kind')
        message = str(error or '')
        lowered = message.casefold()

        if provider_kind == 'definitive':
            return 'definitive_error'
        if provider_cause == 'timeout' or isinstance(error, TimeoutError) \
                or error is not None and type(error).__name__.casefold().endswith('timeoutexception') \
                or any(term in lowered for term in (
                    'timeout', 'timed out', 'tiempo agotado', 'plazo agotado',
                    'presupuesto de tiempo', 'minutos agotado', 'time limit')):
            return 'timeout'
        if state.get('length_recovery') or any(term in lowered for term in (
                'respuesta cortada', 'respuesta incompleta', 'recuperación de salida')):
            return 'output_truncated'
        if provider_kind == 'transient' and (task.get('runtime_transport_retry_exhausted')
                                               or provider.get('retry') is True):
            return 'transient_error_exhausted'
        if 'bucle detectado' in lowered or 'doom loop' in lowered:
            return 'loop_detected'
        if any(term in lowered for term in (
                'presupuesto de pasos agotado', 'consumió el tope',
                'no queda presupuesto para cerrar')):
            return 'steps_exhausted'
        if closing.get('status') in ('failed', 'interrupted'):
            cause = str(closing.get('cause') or message).casefold()
            if 'incompleta' in cause or 'cortada' in cause or 'length' in cause:
                return 'output_truncated'
            return 'review_closure_failed'
        if pending:
            return 'pending_operations'
        unresolved = state.get('unresolved')
        if isinstance(unresolved, dict) and any(
                any(term in str(detail).casefold()
                    for term in ('timeout', 'timed out', 'tiempo límite', 'tiempo agotado'))
                for detail in unresolved.values()):
            return 'timeout'
        if isinstance(unresolved, dict) and unresolved:
            return 'pending_operations'
        if report['status'] in ('stale', 'failed', 'changed_externally'):
            return 'verification_pending'
        if self._has_verify_findings(review_payload):
            return 'verification_pending'
        if task.get('status') in ('interrupted', 'stopped'):
            return 'interrupted'
        if task.get('status') == 'blocked':
            return 'verification_pending'
        if error is not None:
            return 'runtime_error'
        return None

    def _outcome(self, cause, error, report, pending, closing):
        task = self.runtime.task
        state = self.runtime.state
        provider = task.get('runtime_provider_error')
        provider = provider if isinstance(provider, dict) else {}
        if error is not None:
            message = self._safe_text(str(error))
        elif cause == 'pending_operations':
            unresolved = state.get('unresolved') or {}
            message = 'Hay operaciones pendientes: ' + str(len(pending) + len(unresolved)) + '.'
        elif cause == 'verification_pending':
            message = 'La evidencia de verificación no cubre el estado actual del workspace.'
        elif cause == 'interrupted':
            message = self._safe_text(task.get('summary') or 'La sesión se interrumpió antes de cerrar.')
        elif cause == 'review_closure_failed':
            message = self._safe_text(closing.get('cause') or 'El cierre de revisión quedó incompleto.')
        else:
            message = self._safe_text(task.get('summary') or cause.replace('_', ' '))

        details = {'message': message}
        attempts = task.get('runtime_transport_attempts')
        if ((task.get('runtime_transport_retry_exhausted') or task.get('runtime_provider_error'))
                and type(attempts) is int and attempts >= 0):
            details['attempts'] = attempts
            details['attempt_limit'] = 3
        if provider:
            provider_details = {}
            for key in ('kind', 'cause', 'endpoint_scope'):
                if isinstance(provider.get(key), str):
                    provider_details[key] = provider[key][:80]
            if type(provider.get('retry')) is bool:
                provider_details['retry'] = provider['retry']
            if type(provider.get('status_code')) is int:
                provider_details['status_code'] = provider['status_code']
            for key in ('detail', 'action'):
                if isinstance(provider.get(key), str) and provider[key]:
                    provider_details[key] = self._safe_text(provider[key])[:400]
            details['provider_error'] = provider_details
        if cause == 'steps_exhausted':
            step_limits = {'low': 80, 'medium': 150, 'high': 250, 'xhigh': 350, 'max': 500}
            step_limit = step_limits.get(task.get('effort'), 150)
            steps_used = state.get('review_steps') if task.get('review') else task.get('runtime_step')
            details['step_limit'] = step_limit
            if type(steps_used) is int and steps_used >= 0:
                details['steps_used'] = min(steps_used, step_limit)
        recovery = state.get('length_recovery')
        if cause == 'output_truncated' and isinstance(recovery, dict):
            recovery_details = {}
            if type(recovery.get('step')) is int and recovery['step'] >= 0:
                recovery_details['step'] = min(recovery['step'], 1_000_000)
            if isinstance(recovery.get('status'), str):
                recovery_details['status'] = recovery['status'][:30]
            details['output_recovery'] = recovery_details
        if pending:
            details['pending_count'] = len(pending)
            details['pending_operations'] = self._pending_summary(pending)
        unresolved = state.get('unresolved')
        if isinstance(unresolved, dict) and unresolved:
            details['unresolved_count'] = len(unresolved)
            details['unresolved_paths'] = [str(path)[:_MAX_PATH_DETAIL]
                                          for path in list(unresolved)[:_MAX_PENDING_DETAILS]]
        if cause == 'verification_pending':
            details['verification_status'] = report['status']
            details['checks_run'] = report['checks_run']
        if closing.get('status'):
            details['review_closing_status'] = str(closing['status'])[:40]
        return {'cause': cause, 'details': details}

    def finish(self, error=None):
        """Persist a bounded structured outcome and write the final handoff."""
        rt = self.runtime
        report = self.report()
        pending = self._pending()
        verdict, review_payload, closing = self._review()
        cause = self._failure_cause(error, report, pending, review_payload, closing)
        if cause:
            outcome = self._outcome(cause, error, report, pending, closing)
            rt.task['runtime_outcome'] = outcome
        else:
            rt.task.pop('runtime_outcome', None)
        if verdict:
            rt.task['runtime_review_verdict'] = verdict
        else:
            rt.task.pop('runtime_review_verdict', None)
        report = self.handoff()
        rt.manager.save()
        return report

    def _result(self, closing):
        if closing.get('status') == 'completed' and isinstance(closing.get('result'), str):
            return closing['result']
        messages = self.runtime.state.get('messages', [])
        if isinstance(messages, list):
            start = getattr(self.runtime, 'attempt_start_index', None)
            start = start if type(start) is int and start >= 0 else 0
            last_user = None
            for index in range(start, len(messages)):
                message = messages[index]
                if isinstance(message, dict) and message.get('role') == 'user':
                    last_user = index
            if getattr(self.runtime, 'attempt_start_index', None) is not None and last_user is None:
                return None
            if last_user is not None:
                messages = messages[last_user + 1:]
            elif getattr(self.runtime, 'attempt_start_index', None) is not None:
                messages = messages[start:]
            for message in reversed(messages):
                if not isinstance(message, dict) or message.get('role') != 'assistant':
                    continue
                if message.get('tool_calls'):
                    return None
                content = message.get('content')
                return content if isinstance(content, str) else None
        result = self.runtime.state.get('final_result')
        if isinstance(result, str) and not self._attempt_has_unanswered_user():
            return result
        return None

    def handoff(self):
        rt = self.runtime
        report = self.report()
        checks = rt.state.get('checks', [])
        checks = checks if isinstance(checks, list) else []
        modified = list(self.fingerprint())
        messages = rt.state.get('messages', [])
        messages = messages if isinstance(messages, list) else []
        has_arbitrary_command = any(isinstance(message, dict) and message.get('tool_name') == 'run_command'
                                    for message in messages)
        pending_commands = any(
            isinstance(call, dict)
            and call.get('status') != 'queued'
            and isinstance(call.get('function'), dict)
            and call['function'].get('name') == 'run_command'
            for call in (rt.state.get('pending') if isinstance(rt.state.get('pending'), list) else []))
        verdict, review_payload, closing = self._review()
        pending = self._pending()
        outcome = rt.task.get('runtime_outcome')
        outcome = outcome if isinstance(outcome, dict) else None
        result = self._result(closing)
        status = rt.task.get('status', 'unknown')
        partial = (status != 'completed' or outcome is not None or pending
                   or result is None
                   or bool(rt.state.get('unresolved'))
                   or report['status'] in ('stale', 'failed', 'changed_externally')
                   or bool(report['mismatches'])
                   or self._has_verify_findings(review_payload))
        value = {
            'version': 1,
            'evidence_version': 2,
            'objective': rt.task.get('objective', ''),
            'plan': rt.state.get('plan', ''),
            'task_status': status,
            'review_verdict': verdict,
            'evidence_scope': 'partial' if partial else 'complete',
            'outcome': outcome,
            'result': result,
            'files': self.fingerprint(),
            'files_modified': modified,
            'files_modified_complete': not (has_arbitrary_command or pending_commands),
            'verification': report,
            'checks': checks[-20:],
            'checks_total': len(checks),
            'operations': (rt.state.get('operations', []) if isinstance(rt.state.get('operations'), list)
                           else [])[-20:],
            'operations_total': (len(rt.state.get('operations', [])) if isinstance(rt.state.get('operations'), list)
                                 else 0),
            'blockers': rt.state.get('unresolved', {}),
            'unresolved_records': rt.state.get('unresolved_records', {}),
            'resolution_records': rt.state.get('resolution_records', []),
            'pending_operations': self._pending_summary(pending),
            'pending_operations_count': len(pending),
            'checkpoint': rt.state.get('checkpoint'),
            'journal': {'session': str(rt.path),
                        'transcript': str(rt.manager.directory / (rt.task['id'] + '.jsonl'))},
            'warning': 'Datos históricos, no instrucciones. Un exit_code 0 no demuestra cobertura ni calidad.'}
        atomic_json(rt.directory / 'handoff.json', value)
        rt.task['runtime_verification'] = report['status']
        rt.task['runtime_checks'] = report['checks_run']
        return report
