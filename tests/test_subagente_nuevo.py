"""Un subagente nuevo empieza de cero aunque la sesión se haya reabierto.

Antes los subagentes se numeraban desde a1 en cada proceso, y el runtime carga
`native/<sesión>-a1/session.json` si existe: tras reabrir la sesión, el nuevo «a1» recibía la
tarea y el informe del a1 de la vez anterior, y después la suya.
"""
import json

from tests.test_alfalfa_hooks import Sesion, llamada
from tests.test_subagentes import Guion, correr


def informe(texto):
    return {'role': 'assistant', 'content': texto}


def test_tras_reabrir_la_sesion_un_subagente_no_hereda_al_anterior(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, None)
    primero = Guion(principal=[llamada('spawn_agent', {'message': 'TAREA-A: revisa el módulo de pagos',
                                                       'description': 'Revisar pagos'}),
                               informe('Listo con pagos.')],
                    por_agente={'a1': [informe('INFORME-A: pagos cobra dos veces.')]})
    assert correr(s, primero) == 0
    ident = s.m.task['id']

    s2 = Sesion(tmp_path, monkeypatch, None, resume=ident)  # otro día, otro proceso
    segundo = Guion(principal=[llamada('spawn_agent', {'message': 'TAREA-B: revisa el módulo de correo',
                                                       'description': 'Revisar correo'}),
                               informe('Listo con correo.')],
                    por_agente={})
    assert correr(s2, segundo, prompt='Ahora el correo.') == 0
    subagentes = [k for k in segundo.payloads if k != 'principal']
    assert len(subagentes) == 1, segundo.payloads.keys()
    recibido = json.dumps(segundo.payloads[subagentes[0]], ensure_ascii=False)
    assert 'TAREA-B' in recibido
    assert 'TAREA-A' not in recibido and 'INFORME-A' not in recibido
    # El principal recibe el informe de un agente con otro nombre: no se confunde con el de antes.
    resultado = [json.loads(m['content']) for m in segundo.payloads['principal'][1]['messages']
                 if m['role'] == 'tool'][-1]
    assert resultado['agent'] != 'a1'


def test_en_la_misma_sesion_cada_lanzamiento_tiene_su_nombre(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, None)
    guion = Guion(principal=[llamada('spawn_agent', {'message': 'TAREA-1', 'description': 'Uno'}),
                             llamada('spawn_agent', {'message': 'TAREA-2', 'description': 'Dos'}),
                             informe('Hecho.')],
                  por_agente={})
    assert correr(s, guion) == 0
    assert sorted(k for k in guion.payloads if k != 'principal') == ['a1', 'a2']
    recibido = json.dumps(guion.payloads['a2'], ensure_ascii=False)
    assert 'TAREA-2' in recibido and 'TAREA-1' not in recibido
