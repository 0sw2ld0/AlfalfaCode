"""update_plan como el de Codex: pasos con estado que la app enseña bajo los mensajes."""
import pytest

from alfalfa.proveedores.nativo.herramientas import plan_valido, texto_del_plan
from alfalfa.subagentes import _GestorDeSubagente
from tests.test_alfalfa_compactacion import Modelo, Sesion, llamada

PASOS = [{'step': 'Leer el código', 'status': 'completed'},
         {'step': 'Arreglar la suma', 'status': 'in_progress'},
         {'step': 'Pasar las pruebas', 'status': 'pending'}]


def test_el_plan_llega_a_la_app_y_queda_en_la_sesion(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch)
    modelo = Modelo([llamada('update_plan', {'plan': PASOS, 'explanation': 'Empiezo'}),
                     {'role': 'assistant', 'content': 'Hecho.'}])
    assert s.correr(modelo) == 0
    fila, = s.filas('plan')
    assert fila['pasos'] == [{'paso': 'Leer el código', 'estado': 'completed'},
                             {'paso': 'Arreglar la suma', 'estado': 'in_progress'},
                             {'paso': 'Pasar las pruebas', 'estado': 'pending'}]
    assert fila['explicacion'] == 'Empiezo'
    estado = s.estado()
    assert estado['plan_pasos'] == PASOS and '[>] Arreglar la suma' in estado['plan']
    assert s.m.task['plan_pasos'] == PASOS
    # El modelo recibe el plan como texto.
    respuesta = [m for m in modelo.payloads[-1]['messages'] if m['role'] == 'tool'][-1]['content']
    assert '[x] Leer el código' in respuesta


def test_un_texto_suelto_sigue_valiendo():
    pasos, explicacion = plan_valido({'plan': '1. Inspeccionar\n- Editar\n\n* Verificar'})
    assert [p['step'] for p in pasos] == ['1. Inspeccionar', 'Editar', 'Verificar'] and explicacion is None
    assert all(p['status'] == 'pending' for p in pasos)


@pytest.mark.parametrize('plan', [
    [],
    [{'step': 'a', 'status': 'hecho'}],
    [{'step': '', 'status': 'pending'}],
    [{'step': 'a', 'status': 'pending', 'extra': 1}],
    [{'step': 'a', 'status': 'in_progress'}, {'step': 'b', 'status': 'in_progress'}],
    [{'step': str(n), 'status': 'pending'} for n in range(31)],
])
def test_planes_invalidos(plan):
    with pytest.raises(ValueError):
        plan_valido({'plan': plan})


def test_texto_del_plan():
    assert texto_del_plan(PASOS, 'Por qué') == ('Por qué\n[x] Leer el código\n[>] Arreglar la suma\n'
                                                '[ ] Pasar las pruebas')


def test_el_plan_de_un_subagente_no_sustituye_al_principal():
    avisos = []

    class Padre:
        def plan(self, *args):
            avisos.append(args)
    gestor = _GestorDeSubagente.__new__(_GestorDeSubagente)
    gestor._padre = Padre()
    gestor.plan({}, PASOS)
    assert avisos == []
