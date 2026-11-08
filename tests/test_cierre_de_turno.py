"""wrap_up: el agente cierra el turno con resumen, pregunta y botones, como Claude Code."""
import asyncio

import pytest

from alfalfa import sesiones as registro
from alfalfa.proveedores.nativo.herramientas import ON_REQUEST_TOOLS, TOOLS, cierre_de_turno
from alfalfa.terminal import MANUAL, TerminalManager, texto_del_cierre


@pytest.fixture
def m(tmp_path, monkeypatch):
    (tmp_path / 'project').mkdir()
    monkeypatch.chdir(tmp_path / 'project')
    manager = TerminalManager('Encargo.', home=tmp_path / 'alfalfa')
    yield manager
    manager.close()


def nombres(catalogo):
    return {tool['function']['name'] for tool in catalogo}


def test_solo_alfalfacode_ofrece_wrap_up():
    # Los agentes del integrador ven TOOLS; AlfalfaCode, el catálogo on-request.
    assert 'wrap_up' in nombres(ON_REQUEST_TOOLS)
    assert 'wrap_up' not in nombres(TOOLS)
    assert 'wrap_up' in MANUAL


def test_valida_y_normaliza_el_cierre():
    cierre = cierre_de_turno({'recap': '  Arreglé  la suma.\nPasan las pruebas. ', 'ask': '¿Hago commit?',
                              'options': ['Sí, haz commit', ' Todavía no ']})
    assert cierre == {'resumen': 'Arreglé la suma. Pasan las pruebas.', 'pregunta': '¿Hago commit?',
                      'opciones': ['Sí, haz commit', 'Todavía no']}
    assert cierre_de_turno({'recap': 'Listo.'}) == {'resumen': 'Listo.'}


@pytest.mark.parametrize('args, error', [
    ({'recap': ' '}, 'necesita recap'),
    ({'recap': 'x' * 301}, 'máximo es 300'),
    ({'recap': 'Listo.', 'ask': '¿' + 'x' * 100 + '?'}, 'una sola pregunta'),
    ({'recap': 'Listo.', 'options': ['Sí']}, 'sin pregunta no hay botones'),
    ({'recap': 'Listo.', 'ask': '¿Sigo?', 'options': ['x' * 31]}, '1 a 4 palabras'),
    ({'recap': 'Listo.', 'ask': '¿Sigo?', 'options': ['Sí', 'Sí']}, 'iguales'),
])
def test_rechaza_cierres_que_no_caben(args, error):
    with pytest.raises(ValueError, match=error):
        cierre_de_turno(args)


def test_la_herramienta_guarda_el_cierre_en_la_tarea(m):
    rt = m.runtime()
    resultado = asyncio.run(rt.tools.call('wrap_up', {'recap': 'Listo.', 'ask': '¿Sigo?', 'options': ['Sí']}))
    assert resultado['mostrado'] is True
    assert m.task['cierre'] == {'resumen': 'Listo.', 'pregunta': '¿Sigo?', 'opciones': ['Sí']}


def test_el_turno_guarda_su_cierre():
    task = {}
    turno = registro.abrir_turno(task, 'Hola', 0)
    registro.cerrar_turno(task, turno['id'], 'completed', 2, cierre={'resumen': 'Listo.'})
    assert task['turnos'][0]['cierre'] == {'resumen': 'Listo.'}


def test_texto_para_la_terminal():
    texto = texto_del_cierre({'resumen': 'Listo.', 'pregunta': '¿Sigo?', 'opciones': ['Sí', 'No']})
    assert texto.splitlines()[1:] == ['── Siguiente paso ──', 'Listo.', '¿Sigo?', 'Responde: «Sí» · «No»']


def test_historial_de_una_sesion_guardada(tmp_path, monkeypatch):
    import json

    from alfalfa.stdio import history
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path))
    carpeta = tmp_path / 'sesiones' / 'abc'
    carpeta.mkdir(parents=True)
    task = {'id': 'abc', 'objective': 'Arregla suma.py', 'status': 'completed'}
    t1 = registro.abrir_turno(task, 'Arregla suma.py', 0)
    registro.cerrar_turno(task, t1['id'], 'completed', 3)
    t2 = registro.abrir_turno(task, '¿Y la resta?', 3)
    registro.cerrar_turno(task, t2['id'], 'completed', 5, cierre={'resumen': 'Listo.', 'pregunta': '¿Sigo?'})
    (carpeta / 'task.json').write_text(json.dumps(task))
    filas = [('system', 'Ollama transporte: intento 1/3.', t1['id']), ('tool', 'Inicio read_file', t1['id']),
             ('agent', 'Corregido.', t1['id']), ('agent', 'La resta también.', t2['id'])]
    (carpeta / 'abc.jsonl').write_text('\n'.join(json.dumps(dict(role=r, text=x, time=1, turno=t)) for r, x, t in filas)
                                       + '\nno es json\n')
    h = history('abc')
    assert [(e['rol'], e['texto']) for e in h['eventos']] == [
        ('user', 'Arregla suma.py'), ('tool', 'Inicio read_file'), ('agent', 'Corregido.'),
        ('user', '¿Y la resta?'), ('agent', 'La resta también.')]
    assert h['cierre'] == {'resumen': 'Listo.', 'pregunta': '¿Sigo?'}
    # Cada evento lleva su turno: la app lo usa para volver a un mensaje o bifurcar desde él.
    assert [e.get('turno') for e in h['eventos']] == [t1['id'], t1['id'], t1['id'], t2['id'], t2['id']]
    with pytest.raises(ValueError):
        history('../fuera')


def test_ajustes_para_el_proximo_turno():
    import io

    from alfalfa.stdio import Transport
    out = io.StringIO()
    t = Transport(out)
    t.receive('{"tipo":"ajustes","modelo":"glm-5.3-flash:cloud","esfuerzo":"high"}')
    t.receive('{"tipo":"ajustes","esfuerzo":"enorme"}')
    assert t.take_settings() == {'model': 'glm-5.3-flash:cloud', 'effort': 'high'}
    assert t.take_settings() == {}
    assert 'inválido' in out.getvalue()


def test_desglose_suma_lo_medido():
    from alfalfa.proveedores.nativo.runtime import NativeRuntime
    payload = {'messages': [{'role': 'system', 'content': 'x' * 4000}, {'role': 'user', 'content': 'y' * 2000}],
               'tools': [{'function': {'name': 'read_file', 'description': 'z' * 1000}},
                         {'function': {'name': 'mcp__a__b', 'description': 'w' * 1000}}]}
    partes = NativeRuntime._desglose(payload, 5000)
    assert set(partes) == {'sistema', 'herramientas', 'mcp', 'mensajes'}
    assert abs(sum(partes.values()) - 5000) <= 2
    assert partes['sistema'] > partes['mensajes'] > partes['mcp'] > 0
