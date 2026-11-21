"""Las contraseñas de las órdenes no se guardan en disco (alfalfa/tachado.py).

The Hacker News (11/2026): «Yes, and don't ask again» de Claude Code guardaba la
orden aprobada entera, con la clave, en `.claude/settings.local.json`. AlfalfaCode
no escribe nada así en el proyecto, pero el historial, la transcripción y la
tarea de cada sesión guardaban la orden entera en el hogar.
"""
import json

import pytest

from alfalfa.permisos import sesion
from alfalfa.proveedores.nativo import historial
from alfalfa.tachado import REDACTADO, tachar_argv, tachar_ordenes, tachar_texto
from alfalfa.terminal import TerminalManager

CLAVE = 'hunter22clave'


@pytest.mark.parametrize('argv, esperado', [
    (['env', 'PGPASSWORD=' + CLAVE, 'psql', '-h', 'db'], ['env', 'PGPASSWORD=' + REDACTADO, 'psql', '-h', 'db']),
    (['sshpass', '-p', CLAVE, 'ssh', 'yo@servidor'], ['sshpass', '-p', REDACTADO, 'ssh', 'yo@servidor']),
    (['mysql', '-u', 'root', '-p' + CLAVE, 'db'], ['mysql', '-u', 'root', '-p' + REDACTADO, 'db']),
    (['curl', '-u', 'yo:' + CLAVE, 'https://x.test'], ['curl', '-u', 'yo:' + REDACTADO, 'https://x.test']),
    (['curl', '-H', 'Authorization: Bearer ' + CLAVE, 'https://x.test'],
     ['curl', '-H', 'Authorization: ' + REDACTADO, 'https://x.test']),
    (['gh', '--token', CLAVE, 'api'], ['gh', '--token', REDACTADO, 'api']),
    (['tool', '--api-key=' + CLAVE], ['tool', '--api-key=' + REDACTADO]),
    (['git', 'clone', 'https://yo:' + CLAVE + '@github.com/a/b'],
     ['git', 'clone', 'https://yo:' + REDACTADO + '@github.com/a/b']),
])
def test_tacha_las_claves_de_una_orden(argv, esperado):
    assert tachar_argv(argv) == esperado
    # Dos veces, lo mismo: el historial compara lo que escribe con lo que leyó.
    assert tachar_argv(esperado) == esperado


@pytest.mark.parametrize('argv', [
    ['mkdir', '-p', 'src/x'],
    ['python3', '-m', 'pytest', '-p', 'no:cacheprovider', '-q'],
    ['git', 'push', '-u', 'origin', 'main'],
    ['tool', '--password-file', '/tmp/clave', '--max-tokens', '100'],
    ['git', 'diff', '--no-ext-diff', '--stat'],
])
def test_no_toca_lo_que_no_es_una_clave(argv):
    assert tachar_argv(argv) is argv


def test_una_orden_de_shell_en_texto():
    orden = ('PGPASSWORD=' + CLAVE + ' psql -h db && sshpass -p ' + CLAVE + ' ssh x; mysql -uroot -p' + CLAVE
             + ' db; curl -u yo:' + CLAVE + ' https://x.test --token ' + CLAVE)
    tachada = tachar_texto(orden)
    assert CLAVE not in tachada
    assert tachada.startswith('PGPASSWORD=' + REDACTADO + ' psql -h db && sshpass -p ' + REDACTADO)
    assert tachar_texto(tachada) == tachada
    assert tachar_argv(['sh', '-c', orden]) == ['sh', '-c', tachada]
    # mysql -p sin nada pegado pide la clave: lo que sigue es la base de datos.
    assert tachar_texto('mysql -h db -p mydb') == 'mysql -h db -p mydb'


def _estado():
    argv = ['env', 'PGPASSWORD=' + CLAVE, 'psql', '-c', 'select 1']
    salida = json.dumps({'argv': argv, 'exit_code': 0, 'output': '1 fila'})
    return {
        'version': historial.VERSION,
        'messages': [
            {'role': 'system', 'content': 'sistema'},
            {'role': 'user', 'content': 'Mira la base de datos.'},
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'function': {'name': 'run_command', 'arguments': {'argv': argv, 'purpose': 'Consultar'}}},
                {'function': {'name': 'run_command',
                              'arguments': json.dumps({'argv': ['sshpass', '-p', CLAVE, 'ssh', 'x'],
                                                       'purpose': 'Entrar'})}}]},
            {'role': 'tool', 'content': salida},
        ],
        'operations': [{'argv': argv, 'cwd': '/tmp/x', 'purpose': 'Consultar', 'output': '1 fila'}],
        'checks': [{'argv': ['curl', '-u', 'yo:' + CLAVE, 'https://x.test'], 'cwd': '/tmp/x', 'exit_code': 0}],
        'unresolved_records': {'a': {'argv': ['mysql', '-p' + CLAVE], 'status': 'pending'}},
        'pending': {'command': 'sshpass -p ' + CLAVE + ' ssh x'},
    }


def _en_disco(carpeta):
    return b''.join(p.read_bytes() for p in sorted(carpeta.iterdir()) if p.is_file())


def test_el_historial_nativo_no_guarda_las_claves(tmp_path):
    estado = _estado()
    h = historial.Historial(tmp_path)
    h.empezar(estado['messages'])
    (tmp_path / 'session.json').write_text(json.dumps(h.guardar(estado)))
    assert CLAVE.encode() not in _en_disco(tmp_path)
    # En memoria la orden sigue entera: la que está en curso y el modelo la necesitan así.
    assert estado['operations'][0]['argv'][1] == 'PGPASSWORD=' + CLAVE
    assert estado['messages'][2]['tool_calls'][0]['function']['arguments']['argv'][1] == 'PGPASSWORD=' + CLAVE

    leido = historial.leer(tmp_path / 'session.json')
    llamada = leido['messages'][2]['tool_calls']
    assert llamada[0]['function']['arguments']['argv'] == ['env', 'PGPASSWORD=' + REDACTADO, 'psql', '-c',
                                                          'select 1']
    assert json.loads(llamada[1]['function']['arguments'])['argv'] == ['sshpass', '-p', REDACTADO, 'ssh', 'x']
    assert json.loads(leido['messages'][3]['content'])['argv'][1] == 'PGPASSWORD=' + REDACTADO
    assert leido['checks'][0]['argv'][2] == 'yo:' + REDACTADO
    assert leido['unresolved_records']['a']['argv'] == ['mysql', '-p' + REDACTADO]
    assert leido['pending']['command'] == 'sshpass -p ' + REDACTADO + ' ssh x'
    # Lo demás, como estaba.
    assert leido['messages'][1] == estado['messages'][1] and leido['operations'][0]['output'] == '1 fila'


def test_reanudar_y_guardar_no_reescribe_lo_tachado(tmp_path):
    estado = _estado()
    h = historial.Historial(tmp_path)
    h.empezar(estado['messages'])
    (tmp_path / 'session.json').write_text(json.dumps(h.guardar(estado)))
    jsonl = {p.name: p.read_bytes() for p in tmp_path.glob('*.jsonl')}

    otro = historial.Historial(tmp_path)
    reanudado, _ = otro.cargar(json.loads((tmp_path / 'session.json').read_text()))
    reanudado['messages'].append({'role': 'assistant', 'content': 'Hecho.'})
    reanudado['checks'].append({'argv': ['gh', '--token', CLAVE], 'cwd': '/tmp/x', 'exit_code': 0})
    (tmp_path / 'session.json').write_text(json.dumps(otro.guardar(reanudado)))
    for nombre, antes in jsonl.items():
        # Sólo se añade al final: lo tachado que se leyó no cuenta como cambiado.
        assert (tmp_path / nombre).read_bytes().startswith(antes), nombre
    assert b'$hasta' not in (tmp_path / 'checks.jsonl').read_bytes()
    assert CLAVE.encode() not in _en_disco(tmp_path)


def test_tachar_ordenes_devuelve_lo_mismo_si_no_hay_claves():
    mensaje = {'role': 'tool', 'content': json.dumps({'argv': ['git', 'status'], 'output': 'limpio'})}
    assert tachar_ordenes(mensaje) is mensaje
    # La salida de un programa no se toca: el modelo la vuelve a leer al reanudar.
    salida = {'role': 'tool', 'content': json.dumps({'argv': ['cat', 'x'], 'output': 'API_KEY=algo123'})}
    assert tachar_ordenes(salida) is salida


def test_las_aprobaciones_de_la_sesion_se_guardan_como_huella():
    clave = sesion.clave_de_orden(['env', 'PGPASSWORD=' + CLAVE, 'psql'])
    task = {}
    sesion.recordar(task, [clave])
    assert task[sesion.CAMPO] == [sesion.huella(clave)] and CLAVE not in json.dumps(task)
    assert sesion.aprobadas(task, [clave])
    assert not sesion.aprobadas(task, [sesion.clave_de_orden(['env', 'PGPASSWORD=otra', 'psql'])])


def test_las_aprobaciones_de_una_sesion_de_antes_siguen_valiendo_y_pasan_a_huella():
    vieja = sesion.clave_de_orden(['sshpass', '-p', CLAVE, 'ssh', 'x'])
    task = {sesion.CAMPO: [vieja]}
    assert sesion.aprobadas(task, [vieja])
    otra = sesion.clave_de_orden(['ls'])
    sesion.recordar(task, [otra])
    assert task[sesion.CAMPO] == [sesion.huella(vieja), sesion.huella(otra)]
    assert sesion.aprobadas(task, [vieja]) and CLAVE not in json.dumps(task)


def test_la_tarea_y_la_transcripcion_no_guardan_las_claves(tmp_path):
    m = TerminalManager('Mira la base de datos.', home=tmp_path / 'alfalfa')
    try:
        m.task['approval'] = 'Ejecutar ' + json.dumps(['sshpass', '-p', CLAVE, 'ssh', 'x'])
        m.save()
        m.event(m.task, 'system', m.task['approval'])
        m.event(m.task, 'tool', 'Ejecutando PGPASSWORD=' + CLAVE + ' psql',
                herramienta={'nombre': 'run_command', 'descripcion': 'PGPASSWORD=' + CLAVE + ' psql'})
        m.event(m.task, 'agent', 'Listo.')
        # En memoria, entera: es lo que el usuario ve al aprobar.
        assert CLAVE in m.task['approval']
        guardada = (m.directory / 'task.json').read_text()
        transcripcion = (m.directory / (m.task['id'] + '.jsonl')).read_text()
        assert CLAVE not in guardada and CLAVE not in transcripcion
        assert REDACTADO in json.loads(guardada)['approval']
        assert '"Listo."' in transcripcion
    finally:
        m.close()
