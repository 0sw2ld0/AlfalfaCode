"""Los fallos de un proveedor hablan de ese proveedor.

Antes la acción por defecto hablaba siempre de Ollama: con OpenAI, Anthropic, OpenRouter, OpenCode Zen
o el servicio Alfalfa, un fallo de red decía «revisa la red o el servicio de Ollama» y un modelo que no
existe «descárgalo (ollama pull)». Cada proveedor sólo cambiaba el de la clave.
Al agotar los reintentos se avisaba de «sesión no completada» aunque la sesión siguiera: lo que no se
completó es la petición.
"""
import pytest

from alfalfa import proveedores
from alfalfa.reintentos.fallos import _ACCIONES, _ollama_failure, classify_ollama_error


def red():
    return {'kind': 'transient', 'cause': 'network', 'retry': True, 'status_code': None,
            'action': _ACCIONES['network']}


def mensaje(ident, clas, texto=None):
    """Lo mismo que hace el runtime (`_provider_failure`) con un fallo clasificado."""
    proveedor = proveedores.obtener(ident)
    accion = proveedor.accion(clas, texto)
    if accion:
        clas = dict(clas, action=accion)
    return str(_ollama_failure(clas, texto, 1, proveedor.nombre))


FALLOS = {
    'red': lambda: red(),
    'modelo': lambda: classify_ollama_error(404, 'model not found'),
    'clave': lambda: classify_ollama_error(401, 'invalid api key'),
}


@pytest.mark.parametrize('ident', ['openai', 'anthropic', 'openrouter', 'opencode', 'alfalfa'])
@pytest.mark.parametrize('fallo', list(FALLOS))
def test_otro_proveedor_no_habla_de_ollama(ident, fallo):
    texto = mensaje(ident, FALLOS[fallo]())
    assert 'ollama' not in texto.lower(), texto
    assert texto.startswith(proveedores.obtener(ident).nombre)


@pytest.mark.parametrize('fallo', list(FALLOS))
def test_ollama_sigue_con_sus_consejos(fallo):
    assert mensaje('ollama', FALLOS[fallo]()).startswith('Ollama')
    assert 'Ollama' in mensaje('ollama', red()) and 'ollama pull' in mensaje('ollama', FALLOS['modelo']())


def test_los_consejos_propios_siguen_mandando():
    assert 'clave de OpenAI en Proveedores' in mensaje('openai', FALLOS['clave']())
    assert 'cuota del mes agotada' in mensaje('alfalfa', classify_ollama_error(402, 'payment required'))


def test_agotar_reintentos_no_da_la_sesion_por_terminada(tmp_path, monkeypatch):
    from alfalfa.proveedores.nativo.runtime import NativeRuntime
    eventos = []

    class Gestor:
        def event(self, task, tipo, texto):
            eventos.append(texto)

    rt = NativeRuntime.__new__(NativeRuntime)
    rt.task, rt.manager = {}, Gestor()
    monkeypatch.setattr(NativeRuntime, '_proveedor_actual', lambda self: proveedores.obtener('openrouter'))
    for motivo in ('attempt_limit', 'deadline'):
        rt._log_transport_exhausted(3, motivo)
    assert len(eventos) == 2
    for texto in eventos:
        assert 'la petición no se completó' in texto and 'sesión' not in texto, texto
