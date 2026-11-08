"""One eligibility decision for selection, retries and provider admission.

Admission and manual sleep share a lock. The lock covers process creation or
HTTP stream entry, never consumption of the response. Sleep can therefore
preempt scheduler preparation without waiting for the scheduler's board lock.
"""
import re
from contextlib import asynccontextmanager

from .energia import ollama_destination


class DispatchIneligible(ValueError):
    """Work remains pending until its role/model becomes eligible."""


# Prefijo de todo bloqueo que nace de la reserva automática (no de un descanso manual).
RESERVA_PREFIJO = 'Reserva automática de Ollama'
RESERVA_FAMILIAS = ('glm', 'deepseek')
RUNNERS_OLLAMA = ('native', 'claude', 'opencode')


def model_identity(model):
    name = str(model or '').lower().strip().rsplit('/', 1)[-1]
    name = re.sub(r'[^a-z0-9]+', '-', name).strip('-')
    for family in ('glm', 'deepseek'):
        if re.match(r'^' + family + r'(?:$|-|[0-9]|[vr][0-9])', name):
            return family
    return name


def resolved_model_aliases(info):
    """Extract identities from Ollama show data, including renamed local models."""
    aliases = []
    if isinstance(info.get('remote_model'), str):
        aliases.append(info['remote_model'])
    details = info.get('details')
    if isinstance(details, dict):
        if isinstance(details.get('family'), str):
            aliases.append(details['family'])
        families = details.get('families')
        if isinstance(families, list):
            aliases.extend(f for f in families if isinstance(f, str))
    metadata = info.get('model_info')
    if isinstance(metadata, dict):
        for key, value in metadata.items():
            if key in ('general.architecture', 'general.name', 'general.basename') and isinstance(value, str):
                aliases.append(value)
            # GGUF architecture-specific fields also identify the model family.
            if isinstance(key, str) and '.' in key:
                family = model_identity(key.split('.', 1)[0])
                if family in ('glm', 'deepseek'):
                    aliases.append(family)
    return aliases


def eligibility_error(studio, role=None, provider=None, model=None, runner=None, account_id=None,
                      ollama_cloud_target=False):
    if role in studio.sleeping:
        return 'El rol ' + role + ' duerme por decisión del usuario.'
    # Family aliases, tags and local/cloud names cannot escape a reservation.
    # Other sleeping roles often share Codex models: they reserve their role,
    # rather than disabling that shared provider for the rest of the team.
    for owner in ('developer_glm', 'developer_deepseek'):
        if owner not in studio.sleeping:
            continue
        records = (studio.sleeping_metadata.get(owner, {}).get('scope', {}).get('models') or
                   [r for r in studio.roles if r['id'] == owner])
        if any(model_identity(model) == model_identity(r.get('model'))
               for r in records if r.get('model')):
            return 'El modelo está reservado por ' + owner + '.'
    # The automatic buffer applies only to Cloud GLM/DeepSeek and only when
    # exact current windows are linked to this exact runner account.
    family = model_identity(model)
    is_cloud = ollama_cloud_target or ollama_destination({'model': model}) == 'cloud'
    if provider == 'ollama' and is_cloud and family in ('glm', 'deepseek'):
        energy = getattr(getattr(studio, 'manager', None), 'ollama_energy', None)
        remaining = energy.reliable_remaining(runner, account_id) if energy and runner else None
        threshold = getattr(studio, 'ollama_reserve_percent', 50)
        if remaining is not None and remaining <= threshold:
            return (f'{RESERVA_PREFIJO}: {family.upper()} queda en espera con '
                    f'{remaining:g}% restante (umbral {threshold}%).')
    return None


def automatic_reserve(studio, energy):
    """Estado efectivo de la reserva automática, según la misma decisión que el despacho.

    Pregunta a eligibility_error por un modelo de cada familia reservable con cada
    cuenta vinculada al ejecutor, así publicación y despacho usan una sola regla.
    Sin vínculo verificado o con una lectura que no es fiable, la cuota es
    desconocida y la reserva no puede aplicarse.
    """
    percent = getattr(studio, 'ollama_reserve_percent', 50)
    modelos = {}
    for role in getattr(studio, 'roles', None) or []:
        family = model_identity(role.get('model'))
        if role.get('provider') == 'ollama' and family in RESERVA_FAMILIAS:
            modelos.setdefault(family, role['model'])
    if not modelos:
        return {'estado': 'sin_alcance', 'causa': 'ningún rol usa GLM ni DeepSeek en Ollama Cloud'}
    vinculos = []
    for runner in RUNNERS_OLLAMA:
        account_id = energy.account_for_runner(runner)
        if account_id:
            vinculos.append((runner, account_id))
    if not vinculos:
        return {'estado': 'cuota_desconocida', 'causa': 'sin_vinculo_verificado'}
    restos = [energy.reliable_remaining(runner, account_id) for runner, account_id in vinculos]
    if any(remaining is None for remaining in restos):
        return {'estado': 'cuota_desconocida', 'causa': 'lectura_no_fiable'}
    retenidas = set()
    for runner, account_id in vinculos:
        for family, model in modelos.items():
            error = eligibility_error(studio, None, 'ollama', model, runner=runner, account_id=account_id,
                                     ollama_cloud_target=True)
            # Un descanso manual también bloquea, pero no es la reserva: no se cuenta aquí.
            if error and error.startswith(RESERVA_PREFIJO):
                retenidas.add(family)
    causa = f'queda {min(restos):g}% (umbral {percent}%)'
    if retenidas:
        return {'estado': 'activa', 'familias': sorted(retenidas), 'causa': causa}
    return {'estado': 'inactiva', 'causa': causa}


def check_task(manager, task, model=None, aliases=()):
    studio = getattr(manager, 'studio', None)
    if studio is None:
        return
    error = eligibility_error(studio, task.get('role'), task.get('provider'),
                              task.get('model') if model is None else model,
                              runner=task.get('runner'), account_id=task.get('ollamaAccountId'),
                              ollama_cloud_target=ollama_destination(task) == 'cloud')
    if error:
        raise DispatchIneligible(error)
    for alias in (*task.get('dispatch_model_aliases', []), *aliases):
        error = eligibility_error(studio, task.get('role'), task.get('provider'), alias,
                                  runner=task.get('runner'), account_id=task.get('ollamaAccountId'),
                                  ollama_cloud_target=ollama_destination(task) == 'cloud')
        if error:
            raise DispatchIneligible(error)


@asynccontextmanager
async def admission(manager, task, model=None, aliases=()):
    studio = getattr(manager, 'studio', None)
    if studio is None:
        check_task(manager, task, model, aliases)
        yield
        return
    async with studio.dispatch_lock:
        check_task(manager, task, model, aliases)
        yield


@asynccontextmanager
async def inference_stream(manager, task, client, endpoint, payload, aliases=()):
    stream = client.stream('POST', endpoint, json=payload)
    if endpoint == '/api/show':
        # Metadata does not launch inference. Sleep must be able to preempt
        # this preparation even while the provider is returning its identity.
        check_task(manager, task, payload.get('model'))
        response = await stream.__aenter__()
    else:
        async with admission(manager, task, payload.get('model'), aliases):
            response = await stream.__aenter__()
    try:
        yield response
    except BaseException as exc:
        if not await stream.__aexit__(type(exc), exc, exc.__traceback__):
            raise
    else:
        await stream.__aexit__(None, None, None)
