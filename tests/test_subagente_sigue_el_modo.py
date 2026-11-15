"""Un subagente ya lanzado obedece el cambio de modo de la sesión.

El subagente copiaba el modo al lanzarlo (`task['modo']`): si el usuario pasaba de Automático a
Manual con un subagente trabajando en segundo plano, sus órdenes seguían corriendo sin preguntar.
"""
import asyncio

from tests.test_alfalfa_hooks import Sesion, llamada
from tests.test_subagentes import Guion, correr


def test_pasar_a_manual_hace_preguntar_al_subagente(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, None, respuesta=False)
    s.m.task['modo'] = 'auto'
    cambiado = []

    def cambiar_a_manual(payload):
        s.m.aplicar_ajustes({'modo': 'manual'})  # el usuario cambia el modo en la app
        cambiado.append(True)
        return llamada('wait_agent', {'timeout_seconds': 30})

    async def orden_tras_el_cambio(payload):
        while not cambiado:
            await asyncio.sleep(0.01)
        return llamada('run_command', {'argv': ['touch', 'nuevo.txt'], 'purpose': 'Crear un archivo'})

    guion = Guion(
        principal=[llamada('spawn_agent', {'message': 'Crea nuevo.txt', 'description': 'Crear',
                                           'background': True}),
                   cambiar_a_manual,
                   {'role': 'assistant', 'content': 'Listo.'}],
        por_agente={'a1': [orden_tras_el_cambio, {'role': 'assistant', 'content': 'Hecho.'}]})
    assert correr(s, guion) == 0
    permisos = [p for p in s.eventos('permiso') if 'Subagente a1' in p.get('proposito', '')]
    assert permisos, 'en Manual, la orden del subagente tenía que preguntar'
    assert not (s.root / 'nuevo.txt').exists()


def test_en_automatico_sigue_sin_preguntar(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, None, respuesta=False)
    s.m.task['modo'] = 'auto'
    guion = Guion(
        principal=[llamada('spawn_agent', {'message': 'Crea nuevo.txt', 'description': 'Crear'}),
                   {'role': 'assistant', 'content': 'Listo.'}],
        por_agente={'a1': [llamada('run_command', {'argv': ['touch', 'nuevo.txt'], 'purpose': 'Crear'}),
                           {'role': 'assistant', 'content': 'Hecho.'}]})
    assert correr(s, guion) == 0
    assert not [p for p in s.eventos('permiso') if 'Subagente a1' in p.get('proposito', '')]
    assert (s.root / 'nuevo.txt').exists()
