"""Sondas independientes finales: sólo sesiones y credenciales sintéticas."""
import json
import shlex
import sqlite3

import pytest

from alfalfa import busqueda_sesiones as busqueda
from alfalfa.busqueda_sesiones import redaccion
from alfalfa.proveedores.nativo import historial
from alfalfa.proveedores.nativo.herramientas import atomic_json
from tests.test_buscar_sesiones import SISTEMA, Demo


@pytest.fixture
def demo(tmp_path, monkeypatch):
    monkeypatch.delenv('ALFALFA_BUSCAR_SESIONES', raising=False)
    monkeypatch.setattr(redaccion, 'secretos_conocidos', lambda: [])
    return Demo(tmp_path, monkeypatch)


def llamada(nombre, argumentos):
    return {'role': 'assistant', 'content': '', 'tool_calls': [
        {'function': {'name': nombre, 'arguments': argumentos}}]}


def salida(nombre, contenido):
    return {'role': 'tool', 'tool_name': nombre, 'content': json.dumps(contenido)}


def bytes_indice(demo):
    return b''.join(r.read_bytes() for r in busqueda.ruta_indice(demo.home).parent.iterdir() if r.is_file())


@pytest.mark.parametrize('opcion', [
    '-u', '--user', '-p', '--pass', '--password', '--token', '--api-key', '--secret',
    '--credential-file', '--auth-code', '--session-cookie', '--bearer-value', '--signing-key',
])
@pytest.mark.parametrize('forma', ['separada', 'igual'])
def test_opciones_sensibles_se_tapan_en_otra_carpeta(demo, opcion, forma):
    secreto = 'FiccionX92RondaFinal'
    valor = 'admin:' + secreto if opcion in ('-u', '--user') else secreto
    argv = ['deploy', opcion, valor] if forma == 'separada' else ['deploy', opcion + '=' + valor]
    demo.crear('credencial', [llamada('run_command', {'argv': argv, 'purpose': 'verificacionfinal'})],
               cwd=demo.otro)
    informe = demo.buscar('verificacionfinal', todas=True)
    assert informe['resultados']
    assert secreto not in json.dumps(informe)
    assert secreto.encode() not in bytes_indice(demo)


@pytest.mark.parametrize('opcion,valor', [
    ('-p', 'FiccionX92RondaFinal'),
    ('-u', 'admin:FiccionX92RondaFinal'),
    ('-p', 'SecretSinteticoFinal'),
    ('-p', 'PasswordSinteticoFinal'),
    ('-p', 'TokenSinteticoFinal'),
    ('-u', 'admin:SecretSinteticoFinal'),
    ('-u', 'admin:PasswordSinteticoFinal'),
])
def test_credencial_pegada_se_tapa_aunque_contenga_un_nombre_sensible(demo, opcion, valor):
    programa = 'mysql' if opcion == '-p' else 'curl'
    demo.crear('pegada', [llamada('run_command', {'argv': [programa, opcion + valor],
                                               'purpose': 'verificacionfinal'})], cwd=demo.otro)
    informe = demo.buscar('verificacionfinal', todas=True)
    assert informe['resultados']
    filtraciones = {'informe': valor in json.dumps(informe), 'indice': valor.encode() in bytes_indice(demo)}
    assert not any(filtraciones.values()), filtraciones


@pytest.mark.parametrize('niveles', [1, 2, 3])
def test_shells_anidados_no_dejan_credenciales_en_la_orden(demo, niveles):
    secreto = 'FiccionX92RondaFinal'
    orden = 'deploy --token ' + secreto
    for _ in range(niveles - 1):
        orden = 'sh -c ' + shlex.quote(orden)
    demo.crear('anidada', [llamada('run_command', {'argv': ['sh', '-c', orden],
                                                'purpose': 'verificacionfinal'})], cwd=demo.otro)
    informe = demo.buscar('verificacionfinal', todas=True)
    assert informe['resultados']
    filtraciones = {'informe': secreto in json.dumps(informe), 'indice': secreto.encode() in bytes_indice(demo)}
    assert not any(filtraciones.values()), filtraciones


@pytest.mark.parametrize('opcion', ['--token', '-u', '-p'])
def test_argv_que_devuelve_la_orden_no_reintroduce_la_credencial(demo, opcion):
    secreto = 'FiccionX92RondaFinal'
    argv = ['deploy', opcion, secreto]
    # run_command devuelve de verdad argv/cwd/purpose mediante _record_command_result.
    demo.crear('eco', [llamada('run_command', {'argv': argv, 'purpose': 'verificacionfinal'}),
                       salida('run_command', {'argv': argv, 'cwd': str(demo.proyecto),
                                              'purpose': 'verificacionfinal', 'output': 'listo', 'exit_code': 0})])
    informe = demo.buscar('verificacionfinal', maximo=20)
    assert informe['resultados']
    filtraciones = {'informe': secreto in json.dumps(informe), 'indice': secreto.encode() in bytes_indice(demo)}
    assert not any(filtraciones.values()), filtraciones


@pytest.mark.parametrize('argv,secreto', [
    (['deploy', '--token', 'FiccionX92RondaFinal'], 'FiccionX92RondaFinal'),
    (['mysql', '-pSecretSinteticoFinal'], 'SecretSinteticoFinal'),
    (['deploy', '--auth-code', 'FiccionX92RondaFinal'], 'FiccionX92RondaFinal'),
])
def test_salida_json_truncada_no_reintroduce_credenciales_argv(demo, argv, secreto):
    proposito = 'verificacionfinal ' + 'detalle ' * 4000
    # El runtime conserva output[:19000] dentro de truncated/text si supera 20000.
    # Un propósito largo deja argv antes del punto de corte, como _record_command_result.
    resultado = {'output': 'listo', 'exit_code': 0, 'argv': argv, 'cwd': str(demo.proyecto),
                 'purpose': proposito}
    serializado = json.dumps(resultado, ensure_ascii=False)
    assert len(serializado) > 20000
    cortado = {'truncated': True, 'text': serializado[:19000]}
    demo.crear('truncada', [llamada('run_command', {'argv': argv, 'purpose': proposito}),
                           salida('run_command', cortado)])
    informe = demo.buscar('listo', maximo=20)
    # También vale omitir la salida completa si no se puede interpretar con seguridad.
    filtraciones = {'informe': secreto in json.dumps(informe), 'indice': secreto.encode() in bytes_indice(demo)}
    assert not any(filtraciones.values()), filtraciones


@pytest.mark.parametrize('campo', ['argv', 'command', 'comando'])
@pytest.mark.parametrize('envuelto', [False, True])
@pytest.mark.parametrize('corte', ['despues_del_campo', 'dentro_del_secreto'])
def test_cortes_json_dentro_y_despues_del_argumento_sensible(demo, campo, envuelto, corte):
    secreto = 'FiccionX92RondaFinal'
    argv = ['curl', '-u', 'admin:' + secreto]
    valor = argv if campo == 'argv' else 'curl -u admin:' + secreto
    serializado = json.dumps({'output': 'listo', campo: valor, 'detalle': 'x' * 20000})
    hasta = 19000 if corte == 'despues_del_campo' else serializado.index(secreto) + 10
    fragmento = serializado[:hasta]
    contenido = json.dumps({'truncated': True, 'text': fragmento}) if envuelto else fragmento
    demo.crear('cortada', [llamada('run_command', {'argv': argv, 'purpose': 'verificacionfinal'}),
                          {'role': 'tool', 'tool_name': 'run_command', 'content': contenido}])
    informe = demo.buscar('listo', maximo=20)
    # Si el corte parte el secreto, tampoco puede sobrevivir su prefijo.
    prefijo = secreto[:10]
    assert prefijo not in json.dumps(informe)
    assert prefijo.encode() not in bytes_indice(demo)


@pytest.mark.parametrize('nombre', ['read_web', 'wait_agent', 'send_input', 'spawn_agent'])
@pytest.mark.parametrize('version', [2, 4])
def test_informe_externo_y_repeticion_despues_del_usuario_siguen_marcados(demo, nombre, version):
    mensajes = [
        {'role': 'user', 'content': 'inicia'}, llamada(nombre, {}),
        salida(nombre, {'report': 'ordensinteticaexterna'}),
        {'role': 'user', 'content': 'continua con el trabajo local'},
        {'role': 'assistant', 'content': 'ordensinteticaexterna repetida despues'},
    ]
    demo.crear('externa', mensajes, cwd=demo.otro)
    if version == 2:
        (demo.nativa('externa') / 'session.json').write_text(json.dumps({
            'version': 2, 'messages': [SISTEMA, *mensajes], 'user_messages': [1, 4]}))
    propias = demo.buscar('ordensinteticaexterna', cwd=str(demo.otro), maximo=20)['resultados']
    assert {f['rol'] for f in propias} == {'assistant', 'tool'}
    assert all(f.get('origen') == 'externo' for f in propias)
    assert not demo.buscar('ordensinteticaexterna', todas=True)['resultados']
    contexto = busqueda.alrededor('externa#5', home=demo.home, cwd=str(demo.proyecto),
                                  todas=True, secretos=[], cuantos=3)
    assert 'ordensinteticaexterna' not in json.dumps(contexto)


def test_checkpoint_de_compactacion_y_nueva_pregunta_no_lavan_la_contaminacion(demo):
    demo.crear('checkpoint', [
        {'role': 'user', 'content': 'inicia'}, llamada('wait_agent', {}),
        salida('wait_agent', {'report': 'dato externo'}),
    ], cwd=demo.otro)
    demo.buscar('inicia', todas=True)
    mensajes = demo.nativa('checkpoint') / 'messages.jsonl'
    inodo, datos = mensajes.stat().st_ino, mensajes.read_bytes()
    ruta = demo.nativa('checkpoint') / 'session.json'
    h = historial.Historial(ruta.parent)
    estado, _ = h.cargar(json.loads(ruta.read_text()))
    # La compactación real añade un checkpoint y conserva el registro completo.
    estado['checkpoint'] = {'estilo': 'codex', 'through': 4, 'text': 'resumen sintetico',
                            'usuario': 'inicia', 'motivo': 'manual'}
    atomic_json(ruta, h.guardar(estado))
    assert mensajes.stat().st_ino == inodo and mensajes.read_bytes() == datos
    demo.buscar('inicia', todas=True)
    demo.añadir('checkpoint', [{'role': 'user', 'content': 'nueva pregunta'},
                               {'role': 'assistant', 'content': 'repeticionexternafinal'}], usuario=True)
    propia, = demo.buscar('repeticionexternafinal', cwd=str(demo.otro))['resultados']
    assert propia['origen'] == 'externo'
    assert not demo.buscar('repeticionexternafinal', todas=True)['resultados']


def test_reescritura_sin_fuente_externa_reinicia_el_estado(demo):
    demo.crear('reescrita', [llamada('spawn_agent', {}), salida('spawn_agent', {'report': 'externo'})])
    demo.buscar('externo')
    historial.escribir(demo.nativa('reescrita'), {
        'messages': [SISTEMA, {'role': 'user', 'content': 'nuevo pedido'},
                     {'role': 'assistant', 'content': 'respuestalimpiadeprueba'}],
        'user_messages': [1], 'pending': [],
    })
    limpia, = demo.buscar('respuestalimpiadeprueba')['resultados']
    assert 'origen' not in limpia


@pytest.mark.parametrize('version_anterior', [1, 2, 3])
def test_version_cuatro_descarta_datos_y_esquema_previos(demo, version_anterior):
    demo.crear('migrada', [
        {'role': 'user', 'content': 'inicia'}, llamada('wait_agent', {}),
        salida('wait_agent', {'report': 'externo'}),
        {'role': 'user', 'content': 'segunda pregunta'},
        {'role': 'assistant', 'content': 'repeticionexternamigrada'},
        llamada('run_command', {'argv': ['deploy', '--token', 'FiccionX92RondaFinal'],
                                'purpose': 'credencialmigrada'}),
    ], cwd=demo.otro)
    ruta = busqueda.ruta_indice(demo.home)
    ruta.parent.mkdir()
    with sqlite3.connect(ruta) as c:
        c.executescript('''
            CREATE TABLE meta(clave TEXT PRIMARY KEY, valor TEXT);
            CREATE TABLE sesiones(id TEXT PRIMARY KEY, cwd TEXT, titulo TEXT);
            CREATE TABLE mensajes(id INTEGER PRIMARY KEY, sesion TEXT, posicion INTEGER, rol TEXT, texto TEXT);
            INSERT INTO sesiones VALUES ('migrada', '/demo', 'vieja');
            INSERT INTO mensajes VALUES (1, 'migrada', 6, 'assistant', 'residuoviejoirrepetible FiccionX92RondaFinal');
        ''')
        c.executemany('INSERT INTO meta VALUES (?, ?)', [('version', str(version_anterior)), ('fts', '1')])
    assert b'residuoviejoirrepetible' in ruta.read_bytes()
    assert not demo.buscar('repeticionexternamigrada', todas=True)['resultados']
    assert not demo.buscar('FiccionX92RondaFinal', todas=True)['resultados']
    with sqlite3.connect(ruta) as c:
        assert c.execute("SELECT valor FROM meta WHERE clave='version'").fetchone()[0] == '4'
        assert c.execute("SELECT contaminado FROM sesiones WHERE id='migrada'").fetchone()[0] == 1
        assert c.execute("SELECT externo FROM mensajes WHERE sesion='migrada' AND posicion=5").fetchone()[0] == 1
    assert b'residuoviejoirrepetible' not in bytes_indice(demo)
    assert b'FiccionX92RondaFinal' not in bytes_indice(demo)
