"""Los `arguments` de un `tool_start` son los argumentos reales de la llamada del modelo, sin secretos, y `--stdio`
v2 no cambia en nada por ello.

La misma sesión (una orden con un argumento de 3000 caracteres y un secreto, y un archivo de 5000) corre de verdad
con `--stdio` y con `--pacai`. Los eventos v2 de esas llamadas se comparan con los que daba `origin/main` antes del
cambio (`tests/fixtures/stdio_v2_argumentos_largos.json`, grabados con `REGRABAR_V2=1`)."""
import json
import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from alfalfa.pacai.jcs import digest
from alfalfa.seatbelt import AISLADAS
from tests.pacai_comun import SESION as SESION_PACAI
from tests.pacai_comun import problemas

REFERENCIA = Path(__file__).parent / 'fixtures' / 'stdio_v2_argumentos_largos.json'
SECRETO = 'sk-secreto-0123456789abcdef'
LARGO = 'x' * 3000
CONTENIDO = 'y' * 5000

SESION = '''
import sys
from unittest.mock import patch
from alfalfa.terminal import main
from alfalfa.proveedores.nativo.runtime import NativeRuntime
SECRETO, LARGO, CONTENIDO = %r, %r, %r
async def inference(self, client, endpoint, payload, **kwargs):
    if endpoint == '/api/show':
        return {'capabilities': ['tools']}
    calls = []
    if not any(m['role'] == 'tool' for m in self.state['messages']):
        calls = [dict(function=dict(name='run_command', arguments=dict(
                     argv=[sys.executable, '-c', 'print(len("' + LARGO + '"))', SECRETO], purpose='Contar',
                     description='Contar una cadena larga', needs_network=True))),
                 dict(function=dict(name='write_file', arguments=dict(path='largo.txt', content=CONTENIDO + SECRETO,
                                                                         expected_sha256='NEW')))]
    return dict(message=dict(role='assistant', content='' if calls else 'Hecho.', tool_calls=calls),
                done=True, done_reason='stop', prompt_eval_count=1, eval_count=1)
with patch.object(NativeRuntime, 'request', inference), patch('alfalfa.proveedores.secretos', return_value=[SECRETO]):
    sys.exit(main(sys.argv[1:]))
''' % (SECRETO, LARGO, CONTENIDO)


def _lanzar(tmp_path, *args):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), ALFALFA_HOME=str(tmp_path / 'home'))
    return subprocess.Popen([sys.executable, '-c', SESION, *args], cwd=tmp_path, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _conversar(proc, contestar, fin):
    """Lee líneas JSON; `contestar(fila)` devuelve lo que hay que mandar (o None); para en `fin(fila)`."""
    cola = queue.Queue()

    def leer():
        for linea in proc.stdout:
            cola.put(linea)
        cola.put(None)
    threading.Thread(target=leer, daemon=True).start()
    filas = []
    try:
        while True:
            try:
                linea = cola.get(timeout=30)
            except queue.Empty:
                raise AssertionError('el proceso no contestó: ' + str(filas[-3:])) from None
            assert linea is not None, 'el proceso cerró stdout: ' + proc.stderr.read()
            fila = json.loads(linea)
            filas.append(fila)
            respuesta = contestar(fila)
            if respuesta is not None:
                proc.stdin.write(json.dumps(respuesta) + '\n')
                proc.stdin.flush()
            if fin(fila):
                break
        proc.stdin.close()
        proc.wait(timeout=30)
        while (linea := cola.get(timeout=10)) is not None:
            filas.append(json.loads(linea))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    return filas


def _normalizar(valor, tmp_path):
    """Lo que cambia de una ejecución a otra (horas, ids, el intérprete y la carpeta) fuera."""
    if isinstance(valor, dict):
        return {k: _normalizar(v, tmp_path) for k, v in valor.items() if k not in ('hora', 'turno', 'id', 'intento')}
    if isinstance(valor, list):
        return [_normalizar(v, tmp_path) for v in valor]
    if isinstance(valor, str):
        for real, marca in ((sys.executable, '<python>'), (str(tmp_path.resolve()), '<carpeta>'),
                            (str(tmp_path), '<carpeta>')):
            valor = valor.replace(real, marca)
        # Seatbelt en macOS y bwrap en Linux: la misma orden en el sandbox del sistema.
        for etiqueta in AISLADAS:
            valor = valor.replace('"sandbox": "' + etiqueta + '"', '"sandbox": "<sandbox>"')
        # El texto «Inicio …» se recorta con la ruta del intérprete dentro: cuántas x del relleno caben
        # depende de lo larga que sea esa ruta (/opt/venv/bin/python frente a la de un venv del Mac).
        valor = re.sub(r'([xy])\1{19,}', r'<\1…>', valor)
        return re.sub(r'("intento\\*": \\*")[0-9a-f]+', r'\1<intento>', valor)
    return valor


def _de_las_llamadas(filas, tmp_path):
    """Los eventos v2 de las dos llamadas: sus `evento` de herramienta y sus permisos."""
    return [_normalizar(f, tmp_path) for f in filas
            if (f['tipo'] == 'evento' and f.get('rol') == 'tool') or f['tipo'] in ('permiso', 'permiso_resuelto')]


def test_los_eventos_v2_de_una_llamada_no_cambian(tmp_path):
    proc = _lanzar(tmp_path, '--stdio', 'Cuenta y escribe')
    filas = _conversar(proc, lambda f: dict(tipo='permiso', id=f['id'], aprobado=True) if f['tipo'] == 'permiso'
                       else None, lambda f: f['tipo'] == 'turno_terminado')
    eventos = _de_las_llamadas(filas, tmp_path)
    assert SECRETO not in json.dumps(filas)
    if os.environ.get('REGRABAR_V2'):
        REFERENCIA.write_text(json.dumps(eventos, ensure_ascii=False, indent=1) + '\n')
    assert eventos == _normalizar(json.loads(REFERENCIA.read_text()), tmp_path)


def _mensaje(seq, tipo, payload, **sobre):
    return dict(pacai='0.3', id='c_%08d' % seq, type=tipo, session=SESION_PACAI, sender='client', seq=seq,
                time='2026-11-15T10:00:00Z', payload=payload, **sobre)


def sesion_pacai(tmp_path, *opciones):
    """Una sesión real de `alfalfa --pacai`: saludo, un `input`, aprobar lo que pida y EOF tras la ejecución.
    Devuelve la transcripción de los dos lados."""
    proc = _lanzar(tmp_path, '--pacai', *opciones)
    enviados = [_mensaje(1, 'hello', {'versions': ['0.3'], 'profiles': ['work'], 'extensions': ['alfalfa']})]
    proc.stdin.write(json.dumps(enviados[0]) + '\n')
    proc.stdin.flush()

    def contestar(m):
        if m['type'] == 'session_started':
            enviados.append(_mensaje(2, 'input', {'blocks': [{'type': 'text', 'text': 'Cuenta y escribe'}]}))
        elif m['type'] == 'approval_request':
            enviados.append(_mensaje(len(enviados) + 1, 'approval_result', {'decision': 'approved', 'scope': 'once'},
                                     reply_to=m['id']))
        else:
            return None
        return enviados[-1]
    recibidos = _conversar(proc, contestar, lambda m: m['type'] == 'run_finished')
    # La transcripción en orden: lo enviado va justo después de lo que lo provocó.
    log, pendientes = [enviados[0]], list(enviados[1:])
    for m in recibidos:
        log.append(m)
        if pendientes and (pendientes[0].get('reply_to') == m['id'] or
                           (m['type'] == 'session_started' and pendientes[0]['type'] == 'input')):
            log.append(pendientes.pop(0))
    return log


def test_los_argumentos_del_tool_start_son_los_de_la_llamada_sin_secretos(tmp_path):
    log = sesion_pacai(tmp_path)
    assert problemas(log) == []
    inicios = {m['payload']['name']: m for m in log if m['type'] == 'tool_start'}
    orden = inicios['run_command']['payload']
    assert orden['arguments'] == {'argv': [sys.executable, '-c', 'print(len("' + LARGO + '"))', '[redacted]'],
                                  'purpose': 'Contar', 'description': 'Contar una cadena larga', 'needs_network': True}
    # La extensión sólo añade lo de v2 que el núcleo no lleva (el texto del evento, `herramienta`, el turno).
    assert orden['title'] == 'Contar una cadena larga' and set(orden.get('alfalfa', {})) <= {'text', 'tool', 'turn'}
    archivo = inicios['write_file']['payload']
    assert archivo['arguments'] == {'path': 'largo.txt', 'content': CONTENIDO + '[redacted]',
                                    'expected_sha256': 'NEW'}
    assert archivo['title'] == 'Crear largo.txt'
    # La aprobación cubre esos mismos argumentos, completos.
    peticion = next(m for m in log if m['type'] == 'approval_request')
    assert peticion['payload']['arguments'] == orden['arguments']
    assert peticion['payload']['digest'] == digest(orden['arguments'])
    assert SECRETO not in json.dumps(log)
    assert (tmp_path / 'largo.txt').read_text().startswith(CONTENIDO)        # se ejecutó la llamada entera


def test_unos_argumentos_que_no_caben_se_recortan_y_se_dice():
    from alfalfa.pacai.sobre import Emisor
    from alfalfa.pacai.traductor import Traductor
    t = Traductor(Emisor('agent', SESION_PACAI), perfiles=('work',), extensiones=('alfalfa',), max_argumentos=4000)
    t.empezar_turno('c_00000001')
    args = {'path': 'a.txt', 'content': 'z' * 20000, 'otros': ['w' * 3000, 'corto']}
    inicio, = t._tool_start(t.raiz, 'Inicio write_file · …', {'nombre': 'write_file'}, args)
    p = inicio['payload']
    assert len(json.dumps(p['arguments'])) <= 4000 and p['truncated'] is True and 'alfalfa' not in p
    assert p['arguments']['path'] == 'a.txt' and p['arguments']['otros'][1] == 'corto'
    assert p['arguments']['content'].startswith('zzz') and p['arguments']['content'].endswith('… [recortado]')
    assert args['content'] == 'z' * 20000                               # el original no se toca
    # `truncated` es del núcleo (0.3): sale también sin la extensión.
    sin = Traductor(Emisor('agent', SESION_PACAI), max_argumentos=4000)
    sin.empezar_turno('c_00000002')
    assert sin._tool_start(sin.raiz, 'Inicio x · …', {'nombre': 'x'}, args)[0]['payload']['truncated'] is True


@pytest.mark.parametrize('args', [None, {'a': float('nan')}, ['no', 'es', 'un', 'objeto']])
def test_sin_argumentos_estructurados_validos_sale_lo_de_antes(args):
    from alfalfa.pacai.sobre import Emisor
    from alfalfa.pacai.traductor import Traductor
    t = Traductor(Emisor('agent', SESION_PACAI))
    t.empezar_turno('c_00000001')
    inicio, = t._tool_start(t.raiz, "Inicio read_file · {'path': 'a.py'}", {'nombre': 'read_file'}, args)
    assert inicio['payload']['arguments'] == {'path': 'a.py'}


async def test_los_argumentos_van_al_inicio_de_su_herramienta_tambien_en_un_subagente_y_no_a_otro():
    from tests.pacai_comun import Cliente, abrir
    c = Cliente()
    await abrir(c)
    args = {'path': 'a.py', 'texto': 'z' * 2000}
    c.t.argumentos_herramienta('read_file', args)
    c.t.emit('subagente_evento', agente='a1', rol='tool', texto='Inicio read_file · ' + str(args)[:1000], hora=1.0)
    inicio = [m for m in c.drenar() if m['type'] == 'tool_start'][0]
    assert inicio['payload']['arguments'] == args and inicio['payload']['run_id'] != c.t.t.raiz
    # Unos argumentos que no llegan a su evento (otro evento en medio) no se cuelan en la herramienta siguiente.
    c.t.argumentos_herramienta('edit_file', {'path': 'b.py'})
    c.t.emit('evento', rol='system', texto='aviso', hora=1.0)
    c.t.emit('evento', rol='tool', texto="Inicio edit_file · {'path': 'c.py'}", hora=1.0,
             herramienta={'nombre': 'edit_file', 'fase': 'inicio'})
    assert [m for m in c.drenar() if m['type'] == 'tool_start'][0]['payload']['arguments'] == {'path': 'c.py'}
    c.t._cerrar_sesion()


async def test_una_llamada_truncada_no_se_puede_aprobar_y_termina_en_error():
    import asyncio

    from tests.pacai_comun import Cliente, cerrar, fin_de_herramienta, reposo
    c = Cliente()
    c.saludar(limits={'max_message_bytes': 4096})        # los argumentos caben en 1024 bytes
    c.entrada('haz algo')
    c.t.turno_empieza(await asyncio.wait_for(c.t.next_message(), 1))
    args = {'argv': ['python3', '-c', 'x' * 3000], 'purpose': 'Larga'}
    c.t.argumentos_herramienta('run_command', args)
    c.t.emit('evento', rol='tool', texto='Inicio run_command · …', hora=1.0,
             herramienta={'nombre': 'run_command', 'fase': 'inicio'})
    inicio = [m for m in c.drenar() if m['type'] == 'tool_start'][0]
    assert inicio['payload']['truncated'] is True
    tarea = asyncio.create_task(c.t.approval('.', False)(dict(args)))
    await reposo()
    with pytest.raises(ValueError, match='rechazado'):
        await asyncio.wait_for(tarea, 1)                 # se niega sin preguntar
    assert not c.del_agente('approval_request')
    fin = fin_de_herramienta(c, 'run_command', {'error': 'Comando rechazado; no se ejecutó.'})
    assert fin[0]['payload']['status'] == 'error'
    await cerrar(c, 'blocked', resumen='No se pudo aprobar')
    c.comprobar()
