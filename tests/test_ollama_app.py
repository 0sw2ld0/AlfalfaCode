"""Cargar un chat de la app de Ollama, siempre sobre una base inventada en tmp."""
import hashlib
import json
import sqlite3

import pytest

from alfalfa import ollama_app
from alfalfa.stdio import history
from alfalfa.terminal import TerminalManager, main


def base_falsa(path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE chats (id TEXT PRIMARY KEY, title TEXT, created_at TIMESTAMP);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, chat_id TEXT, role TEXT, content TEXT, thinking TEXT,
                               stream BOOLEAN, model_name TEXT, created_at TIMESTAMP, updated_at TIMESTAMP);
        INSERT INTO chats VALUES ('c1', 'Plan del juego', '2026-11-01 10:00:00');
        INSERT INTO chats VALUES ('c2', '  ', '2026-11-02 10:00:00');
        INSERT INTO messages (chat_id, role, content, stream, model_name, created_at, updated_at) VALUES
          ('c1', 'system', 'oculto', 0, '', '2026-11-01 10:00:00', '2026-11-01 10:00:00'),
          ('c1', 'user', 'Diseña un NPC', 0, '', '2026-11-01 10:00:01', '2026-11-01 10:00:01'),
          ('c1', 'assistant', 'Se llama Amaru.', 0, 'glm-5.3-flash:cloud', '2026-11-01 10:00:05', '2026-11-01 10:00:05'),
          ('c1', 'tool', 'salida', 0, '', '2026-11-01 10:00:06', '2026-11-01 10:00:06'),
          ('c1', 'user', 'Dale un rival', 0, '', '2026-11-01 10:01:00', '2026-11-01 10:01:00'),
          ('c1', 'assistant', 'Su rival es Cóndor.', 0, 'glm-5.3-flash:cloud', '2026-11-01 10:01:04', '2026-11-01 10:01:04'),
          ('c1', 'assistant', 'a medio escribir', 1, 'glm-5.3-flash:cloud', '2026-11-01 10:02:00', '2026-11-01 10:02:00');
    """)
    con.commit()
    con.close()
    return path


def test_ordena_del_mas_reciente_al_mas_antiguo(tmp_path):
    db = base_falsa(tmp_path / 'db.sqlite')
    con = sqlite3.connect(db)
    con.executescript("""
        INSERT INTO chats VALUES ('c3', 'Receta', '2026-11-03 09:00:00');
        INSERT INTO messages (chat_id, role, content, stream, model_name, created_at, updated_at) VALUES
          ('c3', 'user', 'Hola', 0, '', '2026-11-03 09:00:01', '2026-11-03 09:00:01');
    """)
    con.commit()
    con.close()
    assert [c['id'] for c in ollama_app.chats(db)] == ['c3', 'c1']


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    db = base_falsa(tmp_path / 'db.sqlite')
    monkeypatch.setenv('ALFALFA_OLLAMA_DB', str(db))
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path / 'alfalfa'))
    (tmp_path / 'proyecto').mkdir()
    monkeypatch.chdir(tmp_path / 'proyecto')
    return db


def test_lista_titulos_y_recuentos(entorno):
    lista = ollama_app.chats()
    # c2 no tiene mensajes: no se ofrece.
    assert [c['id'] for c in lista] == ['c1']
    assert lista[0]['titulo'] == 'Plan del juego' and lista[0]['mensajes'] == 7
    assert lista[0]['modelos'] == ['glm-5.3-flash:cloud']


def test_no_cambia_la_base_de_ollama(entorno):
    antes = hashlib.sha256(entorno.read_bytes()).hexdigest()
    ollama_app.chats()
    ollama_app.mensajes('c1')
    assert hashlib.sha256(entorno.read_bytes()).hexdigest() == antes


def test_solo_trae_usuario_y_asistente_terminados(entorno):
    titulo, modelo, lista = ollama_app.mensajes('c1')
    assert titulo == 'Plan del juego' and modelo == 'glm-5.3-flash:cloud'
    assert [(m['role'], m['content']) for m in lista] == [
        ('user', 'Diseña un NPC'), ('assistant', 'Se llama Amaru.'),
        ('user', 'Dale un rival'), ('assistant', 'Su rival es Cóndor.')]


def test_errores_claros(entorno, tmp_path, monkeypatch):
    with pytest.raises(ollama_app.ErrorDeOllama, match='ya no está'):
        ollama_app.mensajes('nada')
    with pytest.raises(ollama_app.ErrorDeOllama, match='no tiene mensajes'):
        ollama_app.mensajes('c2')
    rara = tmp_path / 'rara.sqlite'
    sqlite3.connect(rara).executescript('CREATE TABLE chats (id TEXT);')
    with pytest.raises(ollama_app.ErrorDeOllama, match='esquema inesperado'):
        ollama_app.chats(rara)
    with pytest.raises(ollama_app.ErrorDeOllama, match='No se encontró'):
        ollama_app.chats(tmp_path / 'falta.sqlite')


def test_importa_una_sesion_que_se_puede_continuar(entorno, tmp_path):
    ident = ollama_app.importar('c1', tmp_path / 'proyecto', tmp_path / 'alfalfa')
    task = json.loads((tmp_path / 'alfalfa' / 'sesiones' / ident / 'task.json').read_text())
    assert task['status'] == 'completed' and task['objective'] == 'Ollama · Plan del juego'
    assert task['model'] == 'glm-5.3-flash:cloud' and task['importada_de']['chat'] == 'c1'
    assert [t['mensaje'] for t in task['turnos']] == ['Diseña un NPC', 'Dale un rival']
    assert [(t['desde'], t['hasta']) for t in task['turnos']] == [(1, 3), (3, 5)]
    # El historial que pinta la app.
    h = history(ident)
    assert [(e['rol'], e['texto']) for e in h['eventos']] == [
        ('user', 'Diseña un NPC'), ('agent', 'Se llama Amaru.'), ('user', 'Dale un rival'),
        ('agent', 'Su rival es Cóndor.')]
    # El motor la retoma con todo el contexto cargado.
    manager = TerminalManager('¿Cómo se llama el rival?', resume=ident, home=tmp_path / 'alfalfa')
    try:
        rt = manager.runtime()
        assert [m['role'] for m in rt.state['messages']] == ['system', 'user', 'assistant', 'user', 'assistant']
        assert rt.state['messages'][-1]['content'] == 'Su rival es Cóndor.'
    finally:
        manager.close()


def test_ordenes_json(entorno, capsys):
    assert main(['--chats-ollama', '--json']) == 0
    salida = json.loads(capsys.readouterr().out)
    assert salida['tipo'] == 'chats_ollama' and [c['id'] for c in salida['chats']] == ['c1']
    assert 'content' not in json.dumps(salida)
    assert main(['--importar-ollama', 'c1', '--json']) == 0
    salida = json.loads(capsys.readouterr().out)
    assert salida['tipo'] == 'sesion_cambiada' and salida['importada_de'] == 'ollama'
    assert main(['--importar-ollama', 'nada', '--json']) == 1
    assert 'ya no está' in json.loads(capsys.readouterr().out)['texto']
