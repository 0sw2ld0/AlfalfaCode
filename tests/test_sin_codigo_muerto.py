"""El código que nadie llamaba ya no está (hallazgo B5 de la auditoría del 14-11-2026)."""
import inspect

import pytest

from alfalfa import procesos
from alfalfa.admision import energia
from alfalfa.proveedores.nativo import runtime
from alfalfa.reintentos import politica

BORRADOS = [
    (procesos, 'pid_vivo'),
    (runtime, 'podar_sesiones_nativas'),
    (runtime, 'SESIONES_NATIVAS_VIGENTES'),
    (energia, 'CodexEnergyStore'),
    (energia, 'ClaudeEnergy'),
    (energia, 'OllamaCloudEnergy'),
    (energia, 'combine_energy'),
    (politica, 'new_retry_budget'),
    (politica, 'observe_retry_budget'),
    (politica, 'retry_decision'),
    (politica, 'record_retry'),
    (politica, 'operation_progress_key'),
    (politica, 'RETRY_POLICIES'),
    (politica, 'NO_PROGRESS_LIMIT_SECONDS'),
]


@pytest.mark.parametrize('modulo, nombre', BORRADOS, ids=lambda x: getattr(x, '__name__', x))
def test_el_codigo_sin_uso_ya_no_existe(modulo, nombre):
    assert not hasattr(modulo, nombre)


def test_energia_solo_guarda_el_destino_de_ollama():
    funciones = {nombre for nombre, valor in vars(energia).items()
                 if not nombre.startswith('_') and (inspect.isfunction(valor) or inspect.isclass(valor))}
    assert funciones == {'ollama_destination'}
    assert energia.ollama_destination({'model': 'glm-4.6:cloud'}) == 'cloud'
    assert energia.ollama_destination({'model': 'ollama/qwen3:8b'}) == 'local'
    assert energia.ollama_destination({}) is None


def test_lo_que_se_usa_sigue_ahi():
    """recoger_huerfanos lo llama terminal.py y matar_grupos lo llama recoger_huerfanos."""
    assert callable(procesos.recoger_huerfanos) and callable(procesos.matar_grupos)
    for nombre in ('expensive_operation_identity', 'retry_guard_key', 'expensive_operation_needs_cause',
                   'classify_retry_failure'):
        assert callable(getattr(politica, nombre))
