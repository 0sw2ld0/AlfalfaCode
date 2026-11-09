"""Shared, contract-shaped energy snapshots from Codex account limits."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

FRESHNESS_SECONDS = 300
_ACCOUNT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _iso(epoch: float | int | None) -> str | None:
    if epoch is None:
        return None
    try:
        return (datetime.fromtimestamp(float(epoch), timezone.utc).isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"))
    except (OverflowError, OSError, TypeError, ValueError):
        return None


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _unavailable_window() -> dict:
    return {
        "availability": "unavailable", "usedPercent": None, "remainingPercent": None,
        "resetAt": None, "resetKind": "unknown", "resetSource": "none", "observedAt": None,
        "source": {"kind": "none", "locator": None, "method": None, "measuredAt": None},
    }


class CodexEnergyStore:
    """One latest, complete reading per account; Codex model names never key quota."""

    def __init__(self, persisted: dict | None = None):
        self.accounts: dict[str, dict] = {}
        for account_id, value in (persisted or {}).items():
            if not isinstance(account_id, str) or not _ACCOUNT_ID.fullmatch(account_id) or not isinstance(value, dict):
                continue
            snapshot = value.get("snapshot")
            if not isinstance(snapshot, dict) or snapshot.get("schemaVersion") != 1:
                continue
            try:
                revision = int(snapshot["revision"])
                pool_id = str(value["id"])
            except (KeyError, TypeError, ValueError):
                continue
            if revision < 1 or not _ACCOUNT_ID.fullmatch(pool_id):
                continue
            windows = value.get("windows")
            if not isinstance(windows, dict):
                continue
            self.accounts[account_id] = {
                "id": pool_id, "revision": revision,
                "windows": {name: copy.deepcopy(windows.get(name, {})) for name in ("session", "week")},
                "snapshot": copy.deepcopy(snapshot),
            }

    @staticmethod
    def account_id(data: dict | None) -> str | None:
        value = data.get("accountId") if isinstance(data, dict) else None
        return value if isinstance(value, str) and _ACCOUNT_ID.fullmatch(value) else None

    @staticmethod
    def _codex_limit(data: dict) -> dict | None:
        groups = data.get("rateLimitsByLimitId")
        if isinstance(groups, dict) and isinstance(groups.get("codex"), dict):
            return groups["codex"]
        value = data.get("rateLimits")
        if not isinstance(value, dict):
            return None
        limit_id = value.get("limitId")
        return value if limit_id in (None, "codex") else None

    @staticmethod
    def _assign_windows(snapshot: dict) -> dict[str, dict] | None:
        """Map by reported duration; position alone is not sufficient evidence."""
        assigned = {}
        for name in ("primary", "secondary"):
            row = snapshot.get(name)
            if row is None:
                continue
            if not isinstance(row, dict):
                return None
            raw_duration = row.get("windowDurationMins")
            if raw_duration is None:
                continue
            duration = _number(raw_duration)
            if duration is None:
                return None
            if duration <= 0:
                return None
            window_name = "session" if duration <= 720 else "week"
            if window_name in assigned:
                return None
            assigned[window_name] = row
        return assigned or None

    def ingest(self, data: dict, locator: str, *, account_id: str | None = None,
               measured_at: float | None = None) -> dict | None:
        account_id = self.account_id(data) or account_id
        if not isinstance(account_id, str) or not _ACCOUNT_ID.fullmatch(account_id):
            return None
        source_snapshot = self._codex_limit(data)
        if not isinstance(source_snapshot, dict):
            return None
        assigned = self._assign_windows(source_snapshot)
        if assigned is None:
            return None

        measured_at = time.time() if measured_at is None else measured_at
        measured_iso = _iso(measured_at)
        if measured_iso is None:
            return None
        updates = {}
        for name, window in assigned.items():
            used = _number(window.get("usedPercent"))
            reset = _number(window.get("resetsAt"))
            if used is None or not 0 <= used <= 100:
                return None
            if window.get("resetsAt") is not None and reset is None:
                return None
            reset_at = _iso(reset)
            if reset is not None and reset_at is None:
                return None
            updates[name] = {
                "availability": "available", "usedPercent": used,
                "remainingPercent": 100.0 - used, "resetAt": reset_at,
                "resetKind": "fixed" if reset_at else "unknown",
                "resetSource": "exact" if reset_at else "none",
                "observedAt": measured_iso,
                "source": {"kind": "exact", "locator": locator, "method": None,
                           "measuredAt": measured_iso},
            }
        state = self.accounts.get(account_id)
        if state is None:
            state = {"id": uuid4().hex, "revision": 0,
                     "windows": {"session": {}, "week": {}}, "snapshot": {}}
        is_sparse_update = locator == "account/rateLimits/updated"
        candidate = copy.deepcopy(state["windows"]) if is_sparse_update else {
            "session": {}, "week": {}
        }
        candidate.update(updates)
        self.accounts[account_id] = state
        state["revision"] += 1
        state["windows"] = candidate
        state["snapshot"] = self._build_snapshot(account_id, state, measured_at)
        return copy.deepcopy(state["snapshot"])

    def _build_snapshot(self, account_id: str, state: dict, as_of: float) -> dict:
        windows = {}
        for name in ("session", "week"):
            source = state["windows"].get(name)
            windows[name] = copy.deepcopy(source) if source else _unavailable_window()
        applicable = [row for row in windows.values() if row["availability"] == "available"]
        fresh = [row for row in applicable if
                 0 <= as_of - datetime.fromisoformat(row["source"]["measuredAt"].replace("Z", "+00:00")).timestamp()
                 < FRESHNESS_SECONDS
                 and (row["resetAt"] is None
                      or as_of < datetime.fromisoformat(row["resetAt"].replace("Z", "+00:00")).timestamp())]
        has_unknown = len(applicable) != 2 or len(fresh) != len(applicable)
        zeros = [row for row in fresh if row["remainingPercent"] == 0]
        if zeros:
            boundaries = []
            for row in zeros:
                boundaries.append(datetime.fromisoformat(row["source"]["measuredAt"].replace("Z", "+00:00")).timestamp()
                                  + FRESHNESS_SECONDS)
                if row["resetAt"]:
                    boundaries.append(datetime.fromisoformat(row["resetAt"].replace("Z", "+00:00")).timestamp())
            energy = {"state": "descanso", "remainingPercent": 0, "quotaExhausted": True,
                      "basis": "exact", "reason": "quota_exhausted",
                      "validUntil": _iso(min(boundaries))}
        elif has_unknown:
            energy = {"state": None, "remainingPercent": None, "quotaExhausted": None,
                      "basis": "unknown", "reason": "incomplete_or_stale", "validUntil": None}
        else:
            remaining = min(row["remainingPercent"] for row in fresh)
            boundaries = [datetime.fromisoformat(row["source"]["measuredAt"].replace("Z", "+00:00")).timestamp()
                          + FRESHNESS_SECONDS for row in fresh]
            boundaries.extend(datetime.fromisoformat(row["resetAt"].replace("Z", "+00:00")).timestamp()
                              for row in fresh if row["resetAt"])
            energy = {"state": "cansado" if remaining <= 20 else "normal",
                      "remainingPercent": remaining, "quotaExhausted": False, "basis": "exact",
                      "reason": "low_quota" if remaining <= 20 else "quota_available",
                      "validUntil": _iso(min(boundaries))}
        return {
            "schemaVersion": 1,
            "pool": {"provider": "codex", "accountId": account_id,
                     "quotaScope": "account", "quotaSubjectId": account_id},
            "revision": state["revision"], "asOf": _iso(as_of),
            "freshnessSeconds": FRESHNESS_SECONDS,
            "windows": windows, "energy": energy,
        }

    def snapshot(self, account_id: str, *, as_of: float | None = None) -> dict | None:
        state = self.accounts.get(account_id)
        if not state:
            return None
        return self._build_snapshot(account_id, state, time.time() if as_of is None else as_of)

    def export(self) -> dict:
        return copy.deepcopy(self.accounts)

    def envelope(self, task_account_ids: dict[str, str], *, as_of: float | None = None) -> dict | None:
        now = time.time() if as_of is None else as_of
        rows = []
        ids = {}
        for account_id in sorted(self.accounts):
            state = self.accounts[account_id]
            pool_id = state["id"]
            ids[account_id] = pool_id
            snapshot = self.snapshot(account_id, as_of=now)
            availability = "unavailable" if snapshot["energy"]["reason"] == "incomplete_or_stale" else "available"
            rows.append({"id": pool_id, "availability": availability,
                         "snapshot": snapshot, "block": None})
        if not rows:
            return None
        task_pools = [{"taskId": task_id, "poolId": ids[account_id]}
                      for task_id, account_id in task_account_ids.items() if account_id in ids]
        return {"schemaVersion": 1, "asOf": _iso(now), "pools": rows, "taskPools": task_pools}
"""Cuota de Claude como pool de energía v1 (docs/energia/contrato-integracion.md).

Sólo traduce la lectura del puente statusLine (agent_usage.py) a un snapshot
v1; la autoridad de admisión y pausa llega con las historias posteriores.
"""

FRESHNESS_SECONDS = 300
LOCATOR = 'import_claude_usage/agent_usage.py'
# Claude Code identifica sus ventanas como five_hour y seven_day.
WINDOWS = {'session': 'five_hour', 'week': 'seven_day'}
# El puente statusLine sólo conoce la sesión de Claude Code conectada desde
# el integrador y no expone la cuenta: es un único pool opaco, sin correo ni
# credenciales, que comparten todos los modelos (Opus, Sonnet, Haiku).
CLAUDE_ACCOUNT_ID = 'claude-statusline'
CLAUDE_POOL = {'provider': 'claude', 'accountId': CLAUDE_ACCOUNT_ID,
               'quotaScope': 'account', 'quotaSubjectId': CLAUDE_ACCOUNT_ID}


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace('+00:00', 'Z')


def pool_id(pool):
    key = '\x1f'.join(pool[k] for k in ('provider', 'accountId', 'quotaScope', 'quotaSubjectId'))
    return 'pool-' + hashlib.sha256(key.encode()).hexdigest()[:16]


def _percent(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value) if 0 <= value <= 100 else None


def _epoch(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    try:
        iso(value)
    except (OverflowError, OSError, ValueError):
        return None
    return float(value)


def _instant(value):
    """Epoch o texto ISO 8601 UTC -> epoch; None si no es una hora válida."""
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
        return _epoch(moment.timestamp()) if moment.tzinfo else None
    return _epoch(value)


def empty_window():
    return {'availability': 'unavailable', 'usedPercent': None, 'remainingPercent': None,
            'resetAt': None, 'resetKind': 'unknown', 'resetSource': 'none', 'observedAt': None,
            'source': {'kind': 'none', 'locator': None, 'method': None, 'measuredAt': None}}


def claude_window(row, measured, now):
    """Ventana v1 desde una fila rate_limits; lo inválido no es lectura utilizable."""
    used = _percent(row.get('used_percentage')) if isinstance(row, dict) else None
    if used is None or measured > now:
        return empty_window()
    reset = _epoch(row.get('resets_at'))
    stamp = iso(measured)
    # Redondear nunca debe agotar una cuota con uso inferior a 100.
    remaining = 0 if used >= 100 else (round(100 - used, 6) or 100 - used)
    used = 100 if used >= 100 else (round(used, 6) if remaining >= 1e-6 else used)
    return {'availability': 'available', 'usedPercent': used, 'remainingPercent': remaining,
            'resetAt': iso(reset) if reset else None,
            'resetKind': 'fixed' if reset else 'unknown', 'resetSource': 'exact' if reset else 'none',
            'observedAt': stamp,
            'source': {'kind': 'exact', 'locator': LOCATOR, 'method': None, 'measuredAt': stamp}}


def _time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def evaluate(windows, as_of, freshness_seconds=FRESHNESS_SECONDS):
    """Precedencia E05: ceros vigentes, no aplicable, incompleto/caducado, bajo o normal."""
    now = _time(as_of)
    applicable = [w for w in windows.values() if w['availability'] != 'unsupported']
    valid = []
    for window in applicable:
        if window['availability'] != 'available':
            continue
        observed = _time(window['observedAt'])
        measured = window['source']['measuredAt']
        effective = min(observed, _time(measured)) if measured else observed
        until = effective + timedelta(seconds=freshness_seconds)
        if window['resetAt']:
            until = min(until, _time(window['resetAt']))
        if effective <= now < until:
            valid.append((window, until))

    def stamp(value):
        return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

    zero = [(w, u) for w, u in valid if w['remainingPercent'] == 0]
    if zero:
        return {'state': 'descanso', 'remainingPercent': 0, 'quotaExhausted': True,
                'basis': 'exact' if any(w['source']['kind'] == 'exact' for w, _ in zero) else 'estimated',
                'reason': 'quota_exhausted', 'validUntil': stamp(min(u for _, u in zero))}
    if not applicable:
        return {'state': None, 'remainingPercent': None, 'quotaExhausted': False,
                'basis': 'unknown', 'reason': 'not_applicable', 'validUntil': None}
    if len(valid) != len(applicable):
        return {'state': None, 'remainingPercent': None, 'quotaExhausted': None,
                'basis': 'unknown', 'reason': 'incomplete_or_stale', 'validUntil': None}
    remaining = min(w['remainingPercent'] for w, _ in valid)
    tired = remaining <= 20
    return {'state': 'cansado' if tired else 'normal', 'remainingPercent': remaining,
            'quotaExhausted': False,
            'basis': 'estimated' if any(w['source']['kind'] == 'estimated' for w, _ in valid) else 'exact',
            'reason': 'low_quota' if tired else 'quota_available',
            'validUntil': stamp(min(u for _, u in valid))}


class ClaudeEnergy:
    """Lee el archivo del puente y publica el pool compartido de la cuenta Claude."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.pool = dict(CLAUDE_POOL)
        self.id = pool_id(self.pool)

    def _state(self):
        try:
            data = json.loads((self.directory / 'energy-claude.json').read_text())
            if data.get('pool') == self.id and isinstance(data.get('revision'), int) and data['revision'] > 0:
                return data
        except (OSError, ValueError, AttributeError):
            pass
        return {'pool': self.id, 'revision': 0, 'measured': None}

    def _accept(self, measured, windows):
        """Devuelve (revisión, ventanas vigentes).

        Una lectura más nueva avanza la revisión; repetirla no, y una anterior
        a la aceptada se descarta conservando la evidencia vigente.
        """
        state = self._state()
        if state['measured'] == measured:
            return state['revision'], windows
        if state['measured'] is not None and measured < state['measured']:
            kept = state.get('windows')
            return (state['revision'], kept) if isinstance(kept, dict) and kept else (None, None)
        state = {'pool': self.id, 'revision': state['revision'] + 1, 'measured': measured,
                 'windows': windows}
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=self.directory, prefix='.energy-')
            try:
                with os.fdopen(fd, 'w') as out:
                    json.dump(state, out)
                os.replace(name, self.directory / 'energy-claude.json')
            except BaseException:
                # Con el disco lleno cada lectura dejaba un .energy- suelto.
                try:
                    os.unlink(name)
                except OSError:
                    pass
                raise
        except OSError:
            pass
        return state['revision'], windows

    def snapshot(self, now=None):
        """Snapshot v1, o None mientras no haya una primera lectura aceptada."""
        now = time.time() if now is None else now
        try:
            data = json.loads((self.directory / 'claude-usage.json').read_text())
            measured = _epoch(data['updated'])
            limits = data.get('rate_limits')
            if measured is None or measured > now or not isinstance(limits, dict) or not limits:
                return None
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return None
        windows = {name: claude_window(limits.get(key), measured, now) for name, key in WINDOWS.items()}
        if all(w['availability'] != 'available' for w in windows.values()):
            return None
        revision, windows = self._accept(measured, windows)
        if revision is None:
            return None
        as_of = iso(now)
        return {'schemaVersion': 1, 'pool': dict(self.pool), 'revision': revision,
                'asOf': as_of, 'freshnessSeconds': FRESHNESS_SECONDS, 'windows': windows,
                'energy': evaluate(windows, as_of)}

    def container(self, tasks, now=None):
        """Contenedor opcional `energy`: el pool y la asociación tarea → pool.

        Cualquier modelo de una tarea Claude (Opus, Sonnet, Haiku) apunta al
        mismo pool; Ollama o Codex, aunque corran sobre el CLI de Claude, no.
        """
        now = time.time() if now is None else now
        snapshot = self.snapshot(now)
        return {'schemaVersion': 1, 'asOf': iso(now),
                'pools': [{'id': self.id, 'availability': 'available' if snapshot else 'unavailable',
                           'snapshot': snapshot, 'block': None}],
                'tasks': {task['id']: self.id for task in tasks if task.get('provider') == 'claude'}}


_OLLAMA_ACCOUNT = re.compile(r'^[A-Za-z0-9_-]{1,128}$')
# Un modelo de Ollama Cloud se reconoce por el sufijo de su etiqueta; sin modelo
# o con otro nombre el destino es ambiguo y no se afirma ni cuota ni su ausencia.
_CLOUD_SUFFIXES = (':cloud', '-cloud')


def ollama_destination(task):
    """'cloud', 'local' o None (ambiguo) para una tarea del proveedor ollama."""
    model = task.get('model')
    if not isinstance(model, str) or not model.strip():
        return None
    model = model.strip().lower().removeprefix('ollama/')
    return 'cloud' if model.endswith(_CLOUD_SUFFIXES) else 'local'


class OllamaCloudEnergy:
    """Pools de Ollama Cloud a partir de ventanas ya normalizadas por el lector web.

    Sólo recibe porcentajes, reinicios y procedencia; nunca HTML ni cookies. Vive
    en memoria: sin lector, la evidencia caduca a los 300 s y el pool queda incompleto.
    """

    def __init__(self):
        self.accounts: dict[str, dict] = {}
        # Account links are session scoped and must be supplied by an adapter
        # that has independently verified the runner identity. The web reader
        # currently does not identify the Ollama runner, so this starts empty.
        self.runner_accounts: dict[str, str] = {}

    @staticmethod
    def _window(row, received):
        """Ventana v1 o None si el dato no es una lectura utilizable.

        La hora de medición es la original de la lectura (`observedAt` o
        `source.measuredAt`); la de recepción sólo la sustituye si falta.

        Acepta la ventana del lector (precisión del porcentaje en `source.kind`, la de
        la renovación en `resetSource`) o la forma plana `kind`/`method`.
        """
        if not isinstance(row, dict):
            return None
        used = _percent(row.get('usedPercent'))
        if used is None:
            return None
        source = row.get('source') if isinstance(row.get('source'), dict) else {}
        row_kind = row.get('kind')
        source_kind = source.get('kind')
        if row_kind is not None and source_kind is not None and row_kind != source_kind:
            return None
        kind = row_kind or source_kind or 'exact'
        method = row.get('method') or source.get('method')
        method = method.strip() if isinstance(method, str) and method.strip() else None
        raw_reset = row.get('resetAt')
        reset = None if raw_reset is None else _instant(raw_reset)
        if raw_reset is not None and reset is None:
            return None
        reset_source = (row.get('resetSource') or kind) if reset else 'none'
        if kind not in ('exact', 'estimated') or reset_source not in ('exact', 'estimated', 'none'):
            return None
        if 'estimated' in (kind, reset_source) and method is None:
            return None
        # Se conservan ambas horas; `evaluate` usa la más antigua. Si falta una, vale la otra.
        raw_observed = row.get('observedAt')
        raw_measured = source.get('measuredAt')
        observed = None if raw_observed is None else _instant(raw_observed)
        measured = None if raw_measured is None else _instant(raw_measured)
        if (raw_observed is not None and observed is None) or (raw_measured is not None and measured is None):
            return None
        observed = observed or measured or received
        measured = measured or observed
        # Una hora ilegible o futura no es una medición creíble.
        if observed > received + 60 or measured > received + 60:
            return None
        stamp = iso(observed)
        measured_stamp = iso(measured)
        # Un restante positivo nunca se redondea a cero: sería un agotamiento inventado.
        remaining = round(100 - used, 6) or (100 - used if used < 100 else 0.0)
        return {'availability': 'available', 'usedPercent': used,
                'remainingPercent': remaining,
                'resetAt': iso(reset) if reset else None,
                'resetKind': 'fixed' if reset else 'unknown',
                'resetSource': reset_source, 'observedAt': stamp,
                'source': {'kind': kind, 'locator': 'ollama.com/settings', 'method': method,
                           'measuredAt': measured_stamp}}

    @staticmethod
    def _has_exact_reader_provenance(row, name):
        """Whether a raw window carries the reader's explicit exact source."""
        if not isinstance(row, dict) or row.get('availability') != 'available':
            return False
        source = row.get('source')
        if not isinstance(source, dict) or source.get('kind') != 'exact':
            return False
        label = 'Session usage' if name == 'session' else 'Weekly usage'
        if source.get('locator') != f'ollama.com/settings: {label}':
            return False
        # The original observation time is required; receipt time must not
        # rejuvenate a reading whose age is unknown.
        return (_instant(row.get('observedAt')) is not None
                or _instant(source.get('measuredAt')) is not None)

    def ingest(self, account_id, session=None, week=None, measured_at=None, read_status='ok'):
        """Acepta una lectura; devuelve el snapshot o None si se rechaza.

        `read_status` es el estado de lectura del lector web y se conserva junto a
        las ventanas utilizables (p. ej. el respaldo estimado con `load_failed`).
        """
        if not isinstance(account_id, str) or not _OLLAMA_ACCOUNT.fullmatch(account_id):
            return None
        measured = time.time() if measured_at is None else measured_at
        if _epoch(measured) is None:
            return None
        windows = {}
        for name, row in (('session', session), ('week', week)):
            if row is None or (isinstance(row, dict) and row.get('availability') in ('unavailable', 'unsupported')):
                windows[name] = empty_window()
                continue
            windows[name] = self._window(row, measured)
            if windows[name] is None:
                return None
        if all(w['availability'] != 'available' for w in windows.values()):
            return None
        exact_reader_provenance = all(self._has_exact_reader_provenance(row, name)
                                      for name, row in (('session', session), ('week', week)))
        state = self.accounts.setdefault(account_id, {'revision': 0, 'windows': None, 'readStatus': 'ok'})
        state['revision'] += 1
        state['windows'] = windows
        state['readStatus'] = read_status
        state['exactReaderProvenance'] = exact_reader_provenance
        return self.snapshot(account_id, measured)

    def forget(self, account_id):
        """Cerrar sesión o cambiar de cuenta invalida la lectura anterior."""
        self.accounts.pop(account_id, None)
        self.runner_accounts = {runner: account for runner, account in self.runner_accounts.items()
                                if account != account_id}

    def link_runner_account(self, runner, account_id, *, verified=False):
        """Registra un vínculo sólo si el adaptador verificó ambas identidades.

        La asociación no se persiste: un reinicio o cierre de sesión obliga a
        comprobarla de nuevo. No la infiere del modelo ni del lector web.
        """
        if not isinstance(runner, str) or runner not in ('native', 'claude', 'opencode'):
            return False
        if verified is not True or not isinstance(account_id, str) or account_id not in self.accounts:
            self.runner_accounts.pop(runner, None)
            return False
        self.runner_accounts[runner] = account_id
        return True

    def account_for_runner(self, runner):
        account_id = self.runner_accounts.get(runner)
        return account_id if account_id in self.accounts else None

    def reliable_remaining(self, runner, account_id=None, now=None):
        """Exact, complete, current remaining quota for this verified runner.

        `None` means unknown. In particular, estimates, partial windows,
        expired snapshots and a stale runner/account link never drive policy.
        """
        linked_account = self.account_for_runner(runner)
        if linked_account is None or (account_id is not None and account_id != linked_account):
            return None
        state = self.accounts.get(linked_account) or {}
        if state.get('readStatus') != 'ok' or state.get('exactReaderProvenance') is not True:
            return None
        snapshot = self.snapshot(linked_account, now)
        if not isinstance(snapshot, dict) or snapshot.get('schemaVersion') != 1:
            return None
        pool = snapshot.get('pool') or {}
        if (pool.get('provider') != 'ollama' or pool.get('accountId') != linked_account
                or pool.get('quotaScope') != 'account' or pool.get('quotaSubjectId') != linked_account):
            return None
        energy = snapshot.get('energy') or {}
        if (energy.get('basis') != 'exact' or energy.get('reason') not in
                ('quota_available', 'low_quota', 'quota_exhausted')
                or snapshot.get('freshnessSeconds') != FRESHNESS_SECONDS):
            return None
        as_of = _instant(snapshot.get('asOf'))
        moment = time.time() if now is None else _epoch(now)
        if as_of is None or moment is None or not as_of <= moment:
            return None
        windows = snapshot.get('windows')
        if not isinstance(windows, dict) or any(name not in windows for name in ('session', 'week')):
            return None
        remaining = []
        for name in ('session', 'week'):
            window = windows[name]
            if not isinstance(window, dict):
                return None
            source = window.get('source')
            if not isinstance(source, dict):
                return None
            value = _percent(window.get('remainingPercent'))
            used = _percent(window.get('usedPercent'))
            observed = _instant(window.get('observedAt'))
            measured = _instant(source.get('measuredAt'))
            if (window.get('availability') != 'available' or source.get('kind') != 'exact'
                    or source.get('locator') != 'ollama.com/settings'
                    or observed is None or measured is None or value is None or used is None
                    or measured > observed or abs(value + used - 100) > 0.01):
                return None
            # The aggregate evaluator intentionally reports a current zero even
            # when another window is missing or expired. Reservation needs every
            # applicable window to be independently current before using that zero.
            effective = min(observed, measured)
            reset = _instant(window.get('resetAt')) if window.get('resetAt') is not None else None
            if (effective > moment or moment >= effective + FRESHNESS_SECONDS
                    or (window.get('resetAt') is not None and (reset is None or moment >= reset))):
                return None
            remaining.append(value)
        quota = _percent(energy.get('remainingPercent'))
        minimum = min(remaining)
        if quota is None or abs(quota - minimum) > 0.01:
            return None
        return minimum

    @staticmethod
    def pool(account_id):
        return {'provider': 'ollama', 'accountId': account_id, 'quotaScope': 'account', 'quotaSubjectId': account_id}

    def snapshot(self, account_id, now=None):
        state = self.accounts.get(account_id)
        if not state:
            return None
        now = time.time() if now is None else now
        as_of = iso(now)
        return {'schemaVersion': 1, 'pool': self.pool(account_id), 'revision': state['revision'], 'asOf': as_of,
                'freshnessSeconds': FRESHNESS_SECONDS, 'windows': copy.deepcopy(state['windows']),
                'energy': evaluate(state['windows'], as_of)}

    def envelope_pools(self, now=None):
        rows = []
        for account_id in sorted(self.accounts):
            snapshot = self.snapshot(account_id, now)
            unavailable = snapshot['energy']['reason'] == 'incomplete_or_stale'
            linked = account_id in self.runner_accounts.values()
            rows.append({'id': pool_id(self.pool(account_id)),
                         'availability': 'unavailable' if unavailable else 'available',
                         'snapshot': snapshot, 'block': None,
                         'identityLink': 'verified' if linked else 'unverified',
                         'readStatus': self.accounts[account_id].get('readStatus', 'ok')})
        return rows


def combine_energy(tasks, codex=None, claude=None, ollama=None, now=None):
    """Contenedor `energy` único: un pool por proveedor y cuenta, sin mezclar ventanas.

    Cada tarea apunta a un solo pool; las que no tienen cuota (Ollama local) se
    listan en `models` sin barra, y el integrador figura como modelo sin cuota.
    """
    now = time.time() if now is None else now
    tasks = [t for t in tasks if isinstance(t, dict) and t.get('id')]
    pools, task_pools = [], []

    if codex:
        pools += codex['pools']
        task_pools += codex.get('taskPools', [])
    if claude:
        mapped = claude.get('tasks') or {}
        for row in claude['pools']:
            if row['snapshot'] or mapped:
                pools.append(row)
        task_pools += [{'taskId': t, 'poolId': p} for t, p in mapped.items()]

    from .. import ajustes
    models = [{'kind': ajustes.nombre('integrador'), 'quota': 'none', 'showsBar': False}]
    cloud_pools = ollama.envelope_pools(now) if ollama else []
    pools += cloud_pools
    for task in tasks:
        if task.get('provider') != 'ollama':
            continue
        destination = ollama_destination(task)
        if destination == 'local':
            models.append({'kind': 'ollama_local', 'taskId': task['id'], 'quota': 'none', 'showsBar': False})
        elif destination == 'cloud':
            # Sólo con identidad declarada por la tarea: la cuenta web no se adivina.
            pool = next((p for p in cloud_pools
                         if p['snapshot']['pool']['accountId'] == task.get('ollamaAccountId')), None)
            if pool:
                task_pools.append({'taskId': task['id'], 'poolId': pool['id']})

    return {'schemaVersion': 1, 'asOf': iso(now), 'pools': pools, 'taskPools': task_pools, 'models': models}
