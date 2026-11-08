"""propose_session: el agente propone una sesión aparte y el usuario decide, como en Claude Code."""
import asyncio

import pytest

from alfalfa import sesiones as registro
from alfalfa.proveedores.nativo.herramientas import MAX_PROPUESTAS, ON_REQUEST_TOOLS, TOOLS, sesion_propuesta
from alfalfa.proveedores.nativo.runtime import SIN_EFECTOS
from alfalfa.terminal import MANUAL, TerminalManager, texto_del_cierre

ENCARGO = 'En tests/test_web.py cambia `app=` por `transport=` y comprueba que pytest sale sin avisos.'


@pytest.fixture
def m(tmp_path, monkeypatch):
    (tmp_path / 'project').mkdir()
    monkeypatch.chdir(tmp_path / 'project')
    manager = TerminalManager('Encargo.', home=tmp_path / 'alfalfa')
    yield manager
    manager.close()


def llamar(m, nombre, args):
    return asyncio.run(m.runtime().tools.call(nombre, args))


def test_solo_alfalfacode_la_ofrece_y_no_tiene_efectos():
    nombres = {tool['function']['name'] for tool in ON_REQUEST_TOOLS}
    assert 'propose_session' in nombres
    assert 'propose_session' not in {tool['function']['name'] for tool in TOOLS}
    assert 'propose_session' in MANUAL and 'propose_session' in SIN_EFECTOS


def test_valida_titulo_y_encargo():
    assert sesion_propuesta({'title': '  Quitar  el aviso ', 'prompt': ENCARGO + '\n'}) == {
        'titulo': 'Quitar el aviso', 'encargo': ENCARGO}


@pytest.mark.parametrize('args, error', [
    ({'title': ' ', 'prompt': ENCARGO}, 'necesita title'),
    ({'title': 'x' * 61, 'prompt': ENCARGO}, 'acórtalo'),
    ({'title': 'Arreglar', 'prompt': 'arréglalo'}, 'encargo completo'),
    ({'title': 'Arreglar', 'prompt': 'x' * 4001}, 'resúmelo'),
])
def test_rechaza_propuestas_que_no_sirven(args, error):
    with pytest.raises(ValueError, match=error):
        sesion_propuesta(args)


def test_la_propuesta_va_en_el_cierre_y_sobrevive_a_wrap_up(m):
    resultado = llamar(m, 'propose_session', {'title': 'Quitar el aviso', 'prompt': ENCARGO})
    assert resultado['propuesta'] is True and 'No hagas ese trabajo' in resultado['nota']
    llamar(m, 'wrap_up', {'recap': 'Listo.'})
    assert m.task['cierre'] == {'resumen': 'Listo.',
                                'sesiones': [{'titulo': 'Quitar el aviso', 'encargo': ENCARGO}]}


def test_no_repite_ni_pasa_del_maximo(m):
    llamar(m, 'propose_session', {'title': 'Uno', 'prompt': ENCARGO})
    with pytest.raises(ValueError, match='mismo título|ese título'):
        llamar(m, 'propose_session', {'title': 'Uno', 'prompt': ENCARGO})
    for n in range(2, MAX_PROPUESTAS + 1):
        llamar(m, 'propose_session', {'title': 'Tarea ' + str(n), 'prompt': ENCARGO})
    with pytest.raises(ValueError, match='Ya hay 3'):
        llamar(m, 'propose_session', {'title': 'Otra más', 'prompt': ENCARGO})


def test_el_turno_y_el_historial_guardan_las_propuestas():
    task = {}
    turno = registro.abrir_turno(task, 'Hola', 0)
    cierre = {'sesiones': [{'titulo': 'Quitar el aviso', 'encargo': ENCARGO}]}
    registro.cerrar_turno(task, turno['id'], 'completed', 2, cierre=cierre)
    assert task['turnos'][0]['cierre'] == cierre


def test_en_la_terminal_queda_el_encargo_a_mano():
    texto = texto_del_cierre({'sesiones': [{'titulo': 'Quitar el aviso', 'encargo': 'Línea uno\nLínea dos'}]})
    assert texto.splitlines()[1:] == ['── Siguiente paso ──', '', 'Sesión propuesta: Quitar el aviso',
                                      '  Para empezarla aparte: alfalfa "<encargo>". Encargo:',
                                      '  Línea uno', '  Línea dos']


def test_una_sesion_hija_guarda_su_padre_y_sale_en_el_listado(tmp_path, monkeypatch):
    from alfalfa.stdio import sessions
    (tmp_path / 'project').mkdir()
    monkeypatch.chdir(tmp_path / 'project')
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path / 'alfalfa'))
    padre = TerminalManager('Encargo del padre.')
    padre.save()
    padre.close()
    hija = TerminalManager('Encargo de la hija.', parent=padre.task['id'])
    hija.save()
    hija.close()
    assert hija.task['padre'] == padre.task['id']
    filas = {fila['id']: fila for fila in sessions()}
    assert filas[hija.task['id']]['padre'] == padre.task['id']
    assert 'padre' not in filas[padre.task['id']]


@pytest.mark.parametrize('malo', ['../fuera', 'con espacio', ''])
def test_un_padre_invalido_no_se_guarda(tmp_path, monkeypatch, malo):
    (tmp_path / 'project').mkdir()
    monkeypatch.chdir(tmp_path / 'project')
    with pytest.raises(ValueError, match='padre'):
        TerminalManager('Encargo.', home=tmp_path / 'alfalfa', parent=malo)
    # No queda una carpeta a medias que la barra lateral enseñaría como sesión ilegible.
    assert not list((tmp_path / 'alfalfa' / 'sesiones').glob('*'))
