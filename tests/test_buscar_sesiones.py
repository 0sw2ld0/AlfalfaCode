"""buscar_sesiones: índice de texto completo de las conversaciones guardadas.

Todas las sesiones son de demostración, en un ALFALFA_HOME temporal.
"""
import asyncio
import io
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

import pytest

from alfalfa import busqueda_sesiones as busqueda
from alfalfa.busqueda_sesiones import herramienta, lectura, redaccion
from alfalfa.proveedores.nativo import historial
from alfalfa.proveedores.nativo.herramientas import atomic_json

SISTEMA = {'role': 'system', 'content': 'Eres AlfalfaCode. Instrucciones secretas del sistema: zanahoria.'}


class Demo:
    """Un hogar de AlfalfaCode con sesiones de demostración."""

    def __init__(self, tmp_path, monkeypatch):
        self.home = tmp_path / 'alfalfa'
        self.home.mkdir()
        monkeypatch.setenv('ALFALFA_HOME', str(self.home))
        self.proyecto = tmp_path / 'proyecto'
        self.otro = tmp_path / 'otro'
        self.proyecto.mkdir()
        self.otro.mkdir()

    def nativa(self, ident):
        return self.home / 'sesiones' / ident / 'native' / ident

    def crear(self, ident, mensajes, *, cwd=None, usuarios=None, turnos=None, fecha=None, titulo=None):
        """Sesión versión 4: `mensajes` sin el de sistema; los `user` son del usuario salvo `usuarios`."""
        carpeta = self.home / 'sesiones' / ident
        self.nativa(ident).mkdir(parents=True)
        todos = [SISTEMA] + list(mensajes)
        if usuarios is None:
            usuarios = [i for i, m in enumerate(todos) if m['role'] == 'user']
        if turnos is None:
            turnos = [dict(id='t-' + ident, inicio=fecha or 1_760_000_000.0, desde=1, hasta=len(todos),
                           mensaje='x', estado='completed')]
        task = dict(id=ident, objective=titulo or 'Encargo ' + ident, cwd=str(cwd or self.proyecto),
                    status='completed', model='demo', effort='low', updated_at=fecha or time.time(), turnos=turnos)
        (carpeta / 'task.json').write_text(json.dumps(task))
        historial.escribir(self.nativa(ident), {'messages': todos, 'user_messages': usuarios, 'pending': []})
        return carpeta

    def añadir(self, ident, mensajes, *, usuario=False):
        ruta = self.nativa(ident) / 'session.json'
        h = historial.Historial(ruta.parent)
        estado, _ = h.cargar(json.loads(ruta.read_text()))
        for mensaje in mensajes:
            estado['messages'].append(mensaje)
            if usuario and mensaje['role'] == 'user':
                estado.setdefault('user_messages', []).append(len(estado['messages']) - 1)
        atomic_json(ruta, h.guardar(estado))

    def buscar(self, consulta, **kwargs):
        kwargs.setdefault('secretos', [])
        kwargs.setdefault('cwd', str(self.proyecto))
        return busqueda.buscar(consulta, home=self.home, **kwargs)

    def filas(self):
        with sqlite3.connect(busqueda.ruta_indice(self.home)) as c:
            return c.execute('SELECT sesion, posicion, rol, texto FROM mensajes ORDER BY sesion, posicion').fetchall()


@pytest.fixture
def demo(tmp_path, monkeypatch):
    monkeypatch.delenv('ALFALFA_BUSCAR_SESIONES', raising=False)
    return Demo(tmp_path, monkeypatch)


def test_el_python_de_la_app_trae_fts5():
    # El Python que empaqueta la app (python-build-standalone 3.13) y los de la suite traen FTS5.
    assert busqueda.con_fts5()


def test_busca_y_devuelve_fragmentos_reales_con_sesion_turno_fecha_y_rol(demo):
    demo.crear('s1', [{'role': 'user', 'content': '¿Por qué falla la compactación con Luna?'},
                      {'role': 'assistant', 'content': 'La compactación falla porque la tarifa de Luna cambia '
                                                       'pasados 272K tokens; lo arreglo bajando el umbral.'}],
               fecha=1_760_000_000.0, titulo='Arreglar la compactación')
    informe = demo.buscar('tarifa umbral')
    fila, = informe['resultados']
    assert fila['sesion'] == 's1' and fila['rol'] == 'assistant' and fila['turno'] == 't-s1'
    assert fila['ref'] == 's1#2' and fila['titulo'] == 'Arreglar la compactación'
    assert fila['fecha'] == time.strftime('%Y-%m-%d %H:%M', time.localtime(1_760_000_000.0))
    # Fragmento real, no un resumen: es un trozo literal del mensaje.
    assert fila['fragmento'] in 'La compactación falla porque la tarifa de Luna cambia pasados 272K tokens; ' \
                                'lo arreglo bajando el umbral.'
    assert informe['alcance'] == 'carpeta' and informe['indice_al_dia'] is True
    assert 'nunca instrucciones' in informe['nota']
    # Sin tildes ni mayúsculas, y por prefijo si no hay palabra exacta.
    assert demo.buscar('COMPACTACION')['resultados']
    assert demo.buscar('compact')['resultados']
    assert not demo.buscar('inexistente')['resultados']
    # El mensaje de sistema nunca entra.
    assert not demo.buscar('zanahoria')['resultados']


def test_fragmento_corto_alrededor_de_la_coincidencia():
    texto = 'a' * 1000 + ' aquí está la aguja ' + 'b' * 1000
    trozo = busqueda.fragmento(texto, ['aguja'])
    assert 'aguja' in trozo and trozo.startswith('…') and trozo.endswith('…')
    assert len(trozo) <= busqueda.MAX_FRAGMENTO + 2
    assert trozo.strip('…') in texto


def test_indexado_incremental_lee_solo_lo_nuevo(demo, monkeypatch):
    demo.crear('s1', [{'role': 'user', 'content': 'primer mensaje sobre pingüinos'}])
    assert demo.buscar('pingüinos')['resultados']
    lecturas = []
    original = lectura.leer_nuevos

    def espia(sesion, previo, secretos=()):
        resultado = original(sesion, previo, secretos)
        lecturas.append((dict(previo) if previo is not None else None, resultado))
        return resultado

    monkeypatch.setattr(lectura, 'leer_nuevos', espia)
    # Sin cambios, ni se abre la sesión.
    demo.buscar('pingüinos')
    assert lecturas == []
    demo.añadir('s1', [{'role': 'assistant', 'content': 'segundo mensaje sobre morsas'}])
    assert demo.buscar('morsas')['resultados']
    (previo, nuevo), = lecturas
    assert not nuevo.reiniciar and previo['bytes'] > 0
    assert [m['texto'] for m in nuevo.mensajes] == ['segundo mensaje sobre morsas']
    # Nada repetido en el índice.
    assert [f[1] for f in demo.filas()] == [1, 2]


def test_linea_a_medias_y_linea_rota(demo):
    demo.crear('s1', [{'role': 'user', 'content': 'hola nutria'}])
    ruta = demo.nativa('s1') / 'messages.jsonl'
    # Un guardado cortado a medias: bytes detrás de lo que session.json da por válido.
    with open(ruta, 'ab') as salida:
        salida.write(b'{"role": "assistant", "content": "castor a medi')
    assert not demo.buscar('castor')['resultados']
    assert demo.buscar('nutria')['resultados']
    # Una línea que no es JSON dentro de lo válido se salta; las posiciones siguen bien.
    ruta.write_bytes(ruta.read_bytes()[:ruta.read_bytes().rfind(b'\n') + 1] + b'esto no es json\n'
                     + json.dumps({'role': 'assistant', 'content': 'llega el castor'}).encode() + b'\n')
    estado = json.loads((demo.nativa('s1') / 'session.json').read_text())
    estado['historial']['bytes'] = ruta.stat().st_size
    estado['historial']['mensajes'] = 4
    (demo.nativa('s1') / 'session.json').write_text(json.dumps(estado))
    fila, = demo.buscar('castor')['resultados']
    assert fila['ref'] == 's1#3'


def test_un_messages_jsonl_reescrito_se_vuelve_a_leer_entero(demo):
    demo.crear('s1', [{'role': 'user', 'content': 'versión vieja del ornitorrinco'}])
    assert demo.buscar('ornitorrinco')['resultados']
    # Como al bifurcar o con un subagente con contexto: se reescribe de forma atómica (otro inodo).
    estado = historial.leer(demo.nativa('s1') / 'session.json')
    estado['messages'] = [SISTEMA, {'role': 'user', 'content': 'versión nueva del armadillo'}]
    historial.escribir(demo.nativa('s1'), estado)
    assert not demo.buscar('ornitorrinco')['resultados']
    assert demo.buscar('armadillo')['resultados']
    assert len(demo.filas()) == 1


def test_sesiones_viejas_v2_sin_razonamiento(demo):
    carpeta = demo.home / 'sesiones' / 'vieja'
    (carpeta / 'native' / 'vieja').mkdir(parents=True)
    (carpeta / 'task.json').write_text(json.dumps(dict(id='vieja', objective='Sesión v2', cwd=str(demo.proyecto),
                                                       status='completed', model='m', effort='low')))
    mensajes = [SISTEMA, {'role': 'user', 'content': 'pregunta sobre el quetzal'},
                {'role': 'assistant', 'content': 'respuesta del quetzal', 'thinking': 'pensamiento oculto colibrí',
                 'alfalfa_bloques': [{'type': 'thinking', 'thinking': 'firmado colibrí', 'signature': 'x'}]},
                {'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': 'bloque colibrí'},
                                                  {'type': 'redacted_thinking', 'data': 'colibrí'},
                                                  {'type': 'text', 'text': 'texto visible del tucán'}]}]
    (carpeta / 'native' / 'vieja' / 'session.json').write_text(json.dumps({'version': 2, 'messages': mensajes}))
    assert {f['ref'] for f in demo.buscar('quetzal')['resultados']} == {'vieja#1', 'vieja#2'}
    assert demo.buscar('tucán')['resultados']
    assert not demo.buscar('colibrí')['resultados']
    assert not any('colibr' in f[3] for f in demo.filas())


def test_mensajes_del_arnes_no_cuentan_como_del_usuario(demo):
    demo.crear('s1', [{'role': 'user', 'content': 'pedido real del usuario: jirafa'},
                      {'role': 'user', 'content': 'Estado verificado por el runtime: jirafa pendiente'}],
               usuarios=[1])
    resultados = demo.buscar('jirafa')['resultados']
    assert [f['ref'] for f in resultados] == ['s1#1']


def test_alrededor_de_un_resultado(demo):
    demo.crear('s1', [{'role': 'user', 'content': 'uno'}, {'role': 'assistant', 'content': 'dos'},
                      {'role': 'user', 'content': 'tres con la palabra lémur'},
                      {'role': 'assistant', 'content': 'cuatro'}, {'role': 'user', 'content': 'cinco'},
                      {'role': 'assistant', 'content': 'seis'}])
    ref = demo.buscar('lémur')['resultados'][0]['ref']
    assert ref == 's1#3'
    informe = busqueda.alrededor(ref, home=demo.home, cwd=str(demo.proyecto), cuantos=1, secretos=[])
    assert [(m['posicion'], m['texto'], m.get('buscado', False)) for m in informe['mensajes']] == [
        (2, 'dos', False), (3, 'tres con la palabra lémur', True), (4, 'cuatro', False)]
    assert informe['sesion'] == 's1'
    with pytest.raises(busqueda.ConsultaInvalida):
        busqueda.alrededor('no-es-una-ref', home=demo.home, cwd=str(demo.proyecto), secretos=[])
    # Desde otra carpeta, la sesión no se ve.
    with pytest.raises(busqueda.ConsultaInvalida):
        busqueda.alrededor(ref, home=demo.home, cwd=str(demo.otro), secretos=[])


def test_redaccion_de_secretos(demo, monkeypatch):
    clave = 'sk-clave-conocida-de-ollama-1234567890'
    monkeypatch.setattr(redaccion, 'secretos_conocidos', lambda: [clave])
    demo.crear('s1', [
        {'role': 'user', 'content': 'configura el despliegue del flamenco con ' + clave},
        {'role': 'assistant', 'content': '', 'tool_calls': [
            {'function': {'name': 'run_command', 'arguments': {'argv': ['deploy', 'flamenco'],
                                                               'api_token': 'tok-argumento-secreto'}}}]},
        {'role': 'tool', 'tool_name': 'run_command',
         'content': json.dumps({'stdout': 'flamenco listo\nexport GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123\n'
                                          'Authorization: Bearer abcdefghijklmnop.qrstu',
                                'password': 'hunter2-flamenco'})},
        {'role': 'assistant', 'content': '', 'tool_calls': [
            {'function': {'name': 'read_file', 'arguments': {'path': 'config/.env'}}}]},
        {'role': 'tool', 'tool_name': 'read_file',
         'content': json.dumps({'content': 'FLAMENCO_DB=postgres://usuario:clave-del-env@db/flamenco'})},
        {'role': 'assistant', 'content': '', 'tool_calls': [
            {'function': {'name': 'run_command', 'arguments': {'argv': ['printenv']}}}]},
        {'role': 'tool', 'tool_name': 'run_command', 'content': json.dumps({'stdout': 'HOME=/x flamenco-entorno'})},
    ])
    informe = busqueda.buscar('flamenco', home=demo.home, cwd=str(demo.proyecto), maximo=20)
    texto = json.dumps(informe, ensure_ascii=False)
    for secreto in (clave, 'tok-argumento-secreto', 'ghp_abcdefghijklmnopqrstuvwxyz0123', 'abcdefghijklmnop.qrstu',
                    'hunter2-flamenco', 'clave-del-env', 'flamenco-entorno'):
        assert secreto not in texto
    assert redaccion.REDACTADO in texto
    # Ni en el archivo del índice.
    datos = b''.join(p.read_bytes() for p in busqueda.ruta_indice(demo.home).parent.iterdir())
    for secreto in (clave, 'hunter2-flamenco', 'clave-del-env', 'flamenco-entorno'):
        assert secreto.encode() not in datos
    # La salida de leer un .env y la de printenv no se guardan: queda la marca.
    filas = {f[1]: f[3] for f in demo.filas()}
    assert filas[5] == redaccion.OMITIDO and filas[7] == redaccion.OMITIDO
    # Una clave guardada después de indexar también se tapa al devolver.
    tardia = busqueda.buscar('flamenco', home=demo.home, cwd=str(demo.proyecto), secretos=['listo'], maximo=20)
    assert 'flamenco listo' not in json.dumps(tardia, ensure_ascii=False)


def test_redaccion_de_patrones():
    assert redaccion.tapar('OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstu') == 'OPENAI_API_KEY=' + redaccion.REDACTADO
    assert redaccion.tapar('"client_secret": "abc123"') == '"client_secret": "' + redaccion.REDACTADO + '"'
    assert 'MIIE' not in redaccion.tapar('-----BEGIN RSA PRIVATE KEY-----\nMIIEabc\n-----END RSA PRIVATE KEY-----')
    # Lo que no parece un secreto se queda.
    assert redaccion.tapar('el teclado (keyboard) va bien') == 'el teclado (keyboard) va bien'
    assert redaccion.llamada_secreta('read_file', {'path': 'src/app.py'}) is False
    assert redaccion.llamada_secreta('read_file', {'path': '/Users/x/.ssh/id_rsa'}) is True
    assert redaccion.llamada_secreta('run_command', {'argv': ['cat', 'certs/server.pem']}) is True


def test_filtro_por_carpeta_y_alcance(demo, monkeypatch):
    demo.crear('aqui', [{'role': 'user', 'content': 'el koala de esta carpeta'}])
    demo.crear('alla', [{'role': 'user', 'content': 'el koala de otra carpeta'}], cwd=demo.otro)
    assert {f['sesion'] for f in demo.buscar('koala')['resultados']} == {'aqui'}
    assert {f['sesion'] for f in demo.buscar('koala', cwd=str(demo.otro))['resultados']} == {'alla'}
    assert {f['sesion'] for f in demo.buscar('koala', todas=True)['resultados']} == {'aqui', 'alla'}
    # Lo decide el usuario: variable de entorno o ajuste guardado.
    monkeypatch.setenv('ALFALFA_BUSCAR_SESIONES', 'todas')
    assert busqueda.alcance() == 'todas'
    assert {f['sesion'] for f in demo.buscar('koala')['resultados']} == {'aqui', 'alla'}
    monkeypatch.delenv('ALFALFA_BUSCAR_SESIONES')
    assert busqueda.alcance() == 'carpeta'
    busqueda.fijar_alcance('todas')
    assert busqueda.alcance() == 'todas'
    busqueda.fijar_alcance('carpeta')
    assert busqueda.alcance() == 'carpeta'


def test_sesiones_borradas_salen_del_indice(demo, monkeypatch):
    demo.crear('s1', [{'role': 'user', 'content': 'el tapir que se borra'}])
    demo.crear('s2', [{'role': 'user', 'content': 'el tapir que se queda'}])
    assert len(demo.buscar('tapir')['resultados']) == 2
    # --eliminar-sesion: a la Papelera (una de prueba) y fuera del índice.
    monkeypatch.setattr(Path, 'home', lambda: demo.home.parent)
    from alfalfa import terminal
    salida = io.StringIO()
    monkeypatch.setattr('sys.stdout', salida)
    assert terminal.main(['--eliminar-sesion', 's1']) == 0
    assert json.loads(salida.getvalue())['eliminada'] is True
    assert {f[0] for f in demo.filas()} == {'s2'}
    # Borrada a mano (Finder): la siguiente actualización la quita.
    shutil.rmtree(demo.home / 'sesiones' / 's2')
    assert demo.buscar('tapir')['resultados'] == []
    assert demo.filas() == []


def _sesiones_grandes(demo, cuantas=12, mensajes=20):
    """Unos 160 KB de texto por sesión, con palabras distintas (el índice FTS5 crece de verdad)."""
    import random
    azar = random.Random(1)
    for n in range(cuantas):
        demo.crear('s%02d' % n, [{'role': 'user' if k % 2 == 0 else 'assistant',
                                  'content': ('palabra%d ' % n) + ' '.join('w%d_%d_%d' % (n, k, azar.randint(0, 10**6))
                                                                          for _ in range(800))[:7800]}
                                 for k in range(mensajes)], fecha=1_760_000_000.0 + n)


def test_el_indice_no_crece_sin_limite_y_se_puede_borrar_entero(demo, monkeypatch):
    _sesiones_grandes(demo)
    demo.buscar('palabra0', todas=True)
    ruta = busqueda.ruta_indice(demo.home)
    assert ruta.stat().st_size > 2 * 1024 * 1024
    # Con el tope a 1 MB, la siguiente actualización suelta las más viejas y el archivo encoge de verdad.
    monkeypatch.setenv('ALFALFA_INDICE_MAX_MB', '1')
    demo.buscar('palabra11', todas=True)
    en_disco = sum(p.stat().st_size for p in ruta.parent.iterdir())
    assert en_disco <= 1024 * 1024
    with busqueda.Indice(demo.home) as indice:
        podadas = {f['id'] for f in indice.conexion.execute('SELECT id FROM sesiones WHERE podada = 1')}
    # Se sueltan las más viejas y no vuelven a indexarse solas.
    assert 's00' in podadas and 's11' not in podadas
    assert not demo.buscar('palabra0')['resultados']
    assert demo.buscar('palabra11')['resultados']
    # Una soltada que se reanuda vuelve entera.
    monkeypatch.setenv('ALFALFA_INDICE_MAX_MB', '100')
    demo.añadir('s00', [{'role': 'assistant', 'content': 'reanudada'}])
    assert demo.buscar('palabra0')['resultados'] and demo.buscar('reanudada')['resultados']
    # Borrarlo entero no pierde nada: se rehace desde las sesiones.
    assert busqueda.borrar(demo.home)
    assert not ruta.exists()
    resumen = busqueda.reindexar(demo.home, secretos=[])
    assert resumen['sesiones'] == 12 and resumen['mensajes'] == 12 * 20 + 1
    assert demo.buscar('palabra1')['resultados']


def test_mensajes_recortados_al_guardar(demo):
    demo.crear('s1', [{'role': 'tool', 'tool_name': 'read_file', 'content': json.dumps({'content': 'x' * 50000})}])
    demo.buscar('algo')
    (_, _, _, texto), = demo.filas()
    assert len(texto) <= lectura.MAX_TEXTO_HERRAMIENTA


def test_sin_fts5_busca_igual(demo, monkeypatch):
    demo.crear('s1', [{'role': 'user', 'content': 'el búho y la lechuza'},
                      {'role': 'assistant', 'content': 'el búho caza de noche'}])
    con = {f['ref'] for f in demo.buscar('BUHO')['resultados']}
    monkeypatch.setattr(busqueda, 'con_fts5', lambda: False)
    # El índice hecho con FTS5 se rehace sin él (es desechable).
    sin = {f['ref'] for f in demo.buscar('BUHO')['resultados']}
    assert con == sin == {'s1#1', 's1#2'}
    with busqueda.Indice(demo.home) as indice:
        assert indice.fts is False
    assert {f['ref'] for f in demo.buscar('lechuza noche')['resultados']} == set()
    assert {f['ref'] for f in demo.buscar('caza noche')['resultados']} == {'s1#2'}


def test_topes_de_resultados_y_de_caracteres(demo):
    for n in range(10):
        demo.crear('s' + str(n), [{'role': 'user', 'content': 'el ñandú número %d ' % n + 'texto ' * 200},
                                  {'role': 'assistant', 'content': 'otro ñandú %d ' % n + 'más ' * 200},
                                  {'role': 'user', 'content': 'tercer ñandú %d' % n},
                                  {'role': 'assistant', 'content': 'cuarto ñandú %d' % n}])
    informe = demo.buscar('ñandú', maximo=50)
    assert len(informe['resultados']) <= busqueda.MAX_RESULTADOS
    assert sum(len(f['fragmento']) for f in informe['resultados']) <= busqueda.MAX_SALIDA + busqueda.MAX_FRAGMENTO
    por_sesion = {}
    for fila in informe['resultados']:
        por_sesion[fila['sesion']] = por_sesion.get(fila['sesion'], 0) + 1
    assert max(por_sesion.values()) <= busqueda.POR_SESION
    assert len(demo.buscar('ñandú')['resultados']) <= busqueda.RESULTADOS
    with pytest.raises(busqueda.ConsultaInvalida):
        demo.buscar('')
    with pytest.raises(busqueda.ConsultaInvalida):
        demo.buscar('x' * 1000)
    # Comillas y operadores de FTS5 no rompen la consulta.
    demo.buscar('"ñandú" AND OR NEAR( * ')


def test_la_herramienta_no_incluye_la_sesion_en_curso_y_es_de_solo_lectura(demo):
    demo.crear('vieja', [{'role': 'user', 'content': 'hablamos del perezoso'}])
    demo.crear('actual', [{'role': 'user', 'content': 'ahora el perezoso otra vez'}])

    class Gestor:
        home = demo.home
        transport = None
        mcp = None

    class Runtime:
        manager = Gestor()
        task = {'id': 'actual', 'cwd': str(demo.proyecto)}

    informe = asyncio.run(herramienta.llamar(Runtime(), {'consulta': 'perezoso'}))
    assert [f['sesion'] for f in informe['resultados']] == ['vieja']
    contexto = asyncio.run(herramienta.llamar(Runtime(), {'alrededor': informe['resultados'][0]['ref'],
                                                          'mensajes': '2'}))
    assert contexto['mensajes'][0]['texto'] == 'hablamos del perezoso'
    with pytest.raises(ValueError):
        asyncio.run(herramienta.llamar(Runtime(), {}))
    vacio = asyncio.run(herramienta.llamar(Runtime(), {'consulta': 'inexistente'}))
    assert vacio['resultados'] == [] and 'Sin resultados' in vacio['nota']
    from alfalfa import terminal
    from alfalfa.proveedores.nativo.runtime import SIN_EFECTOS
    assert 'buscar_sesiones' in SIN_EFECTOS and 'buscar_sesiones' not in terminal.ESCRITURA


def test_el_agente_la_usa_en_modo_plan(tmp_path, monkeypatch):
    from unittest.mock import patch

    from alfalfa.proveedores.nativo.runtime import NativeRuntime
    from alfalfa.stdio import Transport
    from alfalfa.terminal import TerminalManager
    from tests.test_alfalfa_compactacion import Modelo, llamada

    raiz = tmp_path / 'project'
    raiz.mkdir()
    monkeypatch.chdir(raiz)
    home = tmp_path / 'alfalfa'
    monkeypatch.setenv('ALFALFA_HOME', str(home))

    def correr(modelo, objetivo, modo=None):
        m = TerminalManager(objetivo, home=home, transport=Transport(io.StringIO()), mode=modo)

        async def run():
            with patch.object(NativeRuntime, 'request', modelo.request()):
                return await m.run(objetivo)
        try:
            return asyncio.run(asyncio.wait_for(run(), 60)), m
        finally:
            m.close()

    primera = Modelo([{'role': 'assistant', 'content': 'El fallo del capibara era el umbral de 272K.'}])
    assert correr(primera, '¿Qué le pasa al capibara?')[0] == 0
    segunda = Modelo([llamada('buscar_sesiones', {'consulta': 'capibara umbral'}),
                      {'role': 'assistant', 'content': 'Ya lo vimos antes.'}])
    codigo, m = correr(segunda, 'Recuerda lo del capibara', modo='plan')
    assert codigo == 0
    nombres = [t['function']['name'] for t in segunda.payloads[0]['tools']]
    assert 'buscar_sesiones' in nombres and 'write_file' not in nombres
    respuesta = json.loads([x for x in segunda.payloads[1]['messages'] if x['role'] == 'tool'][-1]['content'])
    fila, = respuesta['resultados']
    assert 'umbral de 272K' in fila['fragmento'] and fila['rol'] == 'assistant'
    assert fila['sesion'] != m.task['id']


def test_orden_de_terminal(demo, monkeypatch, capsys):
    demo.crear('s1', [{'role': 'user', 'content': 'el alpaca come hierba'}])
    monkeypatch.chdir(demo.proyecto)
    from alfalfa import terminal
    assert terminal.main(['--buscar-sesiones', 'alpaca', '--json']) == 0
    linea = json.loads(capsys.readouterr().out)
    assert linea['tipo'] == 'busqueda_sesiones' and linea['resultados'][0]['ref'] == 's1#1'
    assert terminal.main(['--buscar-sesiones', 'alpaca']) == 0
    texto = capsys.readouterr().out
    assert 'alpaca' in texto and 's1#1' in texto
    assert terminal.main(['--ver-resultado', 's1#1', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['tipo'] == 'sesiones_alrededor'
    # Desde otra carpeta no aparece; con --todas, sí.
    monkeypatch.chdir(demo.otro)
    assert terminal.main(['--buscar-sesiones', 'alpaca', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['resultados'] == []
    assert terminal.main(['--buscar-sesiones', 'alpaca', '--json', '--todas']) == 0
    assert json.loads(capsys.readouterr().out)['resultados']
    assert terminal.main(['--reindexar', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['mensajes'] == 1
    assert terminal.main(['--borrar-indice', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['borrado'] is True
    assert terminal.main(['--alcance-busqueda', 'todas', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['alcance'] == 'todas'
    assert terminal.main(['--buscar-sesiones', '', '--json']) == 1
    assert json.loads(capsys.readouterr().out)['tipo'] == 'error'


def test_permisos_del_indice(demo):
    demo.crear('s1', [{'role': 'user', 'content': 'privado'}])
    demo.buscar('privado')
    ruta = busqueda.ruta_indice(demo.home)
    assert oct(ruta.parent.stat().st_mode & 0o777) == '0o700'
    assert oct(ruta.stat().st_mode & 0o777) == '0o600'
    assert os.path.basename(ruta.parent) == 'indice'
