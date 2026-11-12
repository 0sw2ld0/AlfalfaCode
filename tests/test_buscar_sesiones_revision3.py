"""Tercera revisión: credenciales argv, contaminación entre turnos e informes de agentes."""
import json
import sqlite3

import pytest

from alfalfa import busqueda_sesiones as busqueda
from alfalfa.busqueda_sesiones import redaccion
from tests.test_buscar_sesiones import Demo


@pytest.fixture
def demo(tmp_path, monkeypatch):
    monkeypatch.setattr(redaccion, 'secretos_conocidos', lambda: [])
    return Demo(tmp_path, monkeypatch)


def llamada(nombre, argumentos):
    return {'role': 'assistant', 'content': '', 'tool_calls': [
        {'function': {'name': nombre, 'arguments': argumentos}}]}


def bytes_indice(demo):
    return b''.join(p.read_bytes() for p in busqueda.ruta_indice(demo.home).parent.iterdir() if p.is_file())


@pytest.mark.parametrize('argv,secreto', [
    (['curl', '-u', 'admin:clave-demo'], 'admin:clave-demo'),
    (['curl', '--user=admin:clave-demo'], 'admin:clave-demo'),
    (['sshpass', '-p', 'clave-demo'], 'clave-demo'),
    (['docker', 'login', '-p', 'clave-demo'], 'clave-demo'),
    (['mysql', '-pCLAVE_DEMO'], 'CLAVE_DEMO'),
    (['deploy', '--token', 'clave-demo'], 'clave-demo'),
    (['deploy', '--api-key', 'clave-demo'], 'clave-demo'),
    (['deploy', '--client-secret', 'clave-demo'], 'clave-demo'),
    (['deploy', '--auth-session=clave-demo'], 'clave-demo'),
    (['sh', '-c', 'deploy --token clave-demo'], 'clave-demo'),
])
def test_argv_no_filtra_credenciales_de_otra_carpeta(demo, argv, secreto):
    demo.crear('b', [{'role': 'user', 'content': 'pangolin'},
                     llamada('run_command', {'argv': argv, 'purpose': 'pangolin'})], cwd=demo.otro)
    informe = demo.buscar('pangolin', todas=True, maximo=20)
    assert informe['resultados']
    assert secreto not in json.dumps(informe, ensure_ascii=False)
    assert secreto.encode() not in bytes_indice(demo)


@pytest.mark.parametrize('nombre', ['read_web', 'wait_agent', 'send_input', 'spawn_agent'])
def test_una_salida_contamina_tambien_los_turnos_posteriores(demo, nombre):
    demo.crear('w', [{'role': 'user', 'content': 'primero'}, llamada(nombre, {}),
                     {'role': 'tool', 'tool_name': nombre, 'content': 'dato externo'},
                     {'role': 'user', 'content': 'pregunta pangolin'},
                     {'role': 'assistant', 'content': 'repeticion pangolin'}])
    filas = demo.buscar('pangolin', maximo=20)['resultados']
    assert next(f for f in filas if f['rol'] == 'assistant')['origen'] == 'externo'
    assert 'origen' not in next(f for f in filas if f['rol'] == 'user')
    # Al ampliar a otra carpeta no reaparece como texto de confianza.
    assert all(f['rol'] == 'user' for f in demo.buscar('pangolin', cwd=str(demo.otro), todas=True)['resultados'])


def test_la_contaminacion_sobrevive_a_una_actualizacion_incremental(demo):
    demo.crear('w', [{'role': 'user', 'content': 'primero'}, llamada('wait_agent', {}),
                     {'role': 'tool', 'tool_name': 'wait_agent', 'content': 'dato externo'}])
    demo.buscar('primero')
    demo.añadir('w', [{'role': 'user', 'content': 'otra pregunta'},
                      {'role': 'assistant', 'content': 'repeticion pangolin'}], usuario=True)
    fila, = demo.buscar('pangolin')['resultados']
    assert fila['origen'] == 'externo'


def test_un_indice_anterior_se_reconstruye_con_la_redaccion_nueva(demo):
    demo.crear('b', [{'role': 'user', 'content': 'pangolin'},
                     llamada('run_command', {'argv': ['deploy', '--token', 'clave-demo'], 'purpose': 'pangolin'})])
    demo.buscar('pangolin')
    with sqlite3.connect(busqueda.ruta_indice(demo.home)) as conexion:
        conexion.execute("UPDATE mensajes SET texto='pangolin clave-demo' WHERE rol='assistant'")
        conexion.execute("UPDATE meta SET valor='3' WHERE clave='version'")
    demo.buscar('pangolin')
    assert b'clave-demo' not in bytes_indice(demo)
