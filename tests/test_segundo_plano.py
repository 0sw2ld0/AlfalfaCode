"""Órdenes en segundo plano, como los background terminals de Codex."""
import asyncio
import io
import json
import os
import sys
import time

import pytest

from alfalfa import segundo_plano
from alfalfa.stdio import Transport
from alfalfa.terminal import TerminalManager

PY = [sys.executable, '-I', '-B', '-c']
SERVIDOR = ('import time, sys\nprint("listo", flush=True)\nwhile True:\n'
            '    line = sys.stdin.readline() if False else None\n    time.sleep(0.2)\n')


class Si:
    denied = 0

    async def __call__(self, args):
        return None


@pytest.fixture
def motor(tmp_path, monkeypatch):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    monkeypatch.chdir(raiz)
    monkeypatch.setattr(segundo_plano, 'ESPERA_INICIAL', 1.0)
    salida = io.StringIO()
    m = TerminalManager('Lanza el juego', home=tmp_path / 'alfalfa', transport=Transport(salida))
    rt = m.runtime()
    rt.approval_policy = Si()

    def llamar(nombre, **argumentos):
        return asyncio.get_event_loop().run_until_complete(rt.tools.call(nombre, argumentos))

    asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        yield m, rt, llamar, salida
    finally:
        asyncio.get_event_loop().run_until_complete(m.fondo.detener_todos())
        m.close()


def filas(salida, tipo):
    return [json.loads(linea) for linea in salida.getvalue().splitlines() if json.loads(linea)['tipo'] == tipo]


def test_un_programa_que_sigue_abierto_vuelve_con_su_id_y_no_bloquea(motor):
    m, rt, llamar, salida = motor
    resultado = llamar('run_command', argv=PY + [SERVIDOR], purpose='Abrir el juego', background=True)
    assert resultado['en_segundo_plano'] is True and resultado['proceso'] == 'p1'
    assert resultado['exit_code'] is None and 'listo' in resultado['output']
    assert resultado['classification']['status'] == 'running'
    assert rt.state.get('unresolved') == {}  # seguir vivo no es un fallo
    procesos = filas(salida, 'procesos')[-1]['procesos']
    assert procesos[0]['id'] == 'p1' and procesos[0]['vivo'] is True and procesos[0]['ultimas'] == ['listo']
    estado = llamar('command_status', process='p1')
    assert estado['vivo'] is True and estado['salida_nueva'] == ''
    parado = llamar('command_status', process='p1', stop=True)
    assert parado['vivo'] is False and parado['detenido'] is True
    assert filas(salida, 'procesos')[-1]['procesos'][0]['vivo'] is False
    assert any('Proceso p1 detenido' in f['texto'] for f in filas(salida, 'evento'))


def test_lo_que_termina_pronto_devuelve_su_codigo(motor):
    m, rt, llamar, salida = motor
    resultado = llamar('run_command', argv=PY + ['print("hola"); raise SystemExit(3)'], purpose='Probar',
                       background=True)
    assert resultado['exit_code'] == 3 and 'en_segundo_plano' not in resultado and 'hola' in resultado['output']
    assert rt.state['unresolved']  # un fallo de verdad sigue contando


def test_command_status_espera_salida_nueva_y_avisa_al_terminar(motor):
    m, rt, llamar, salida = motor
    codigo = 'import time\nprint("a", flush=True)\ntime.sleep(1.5)\nprint("b", flush=True)\ntime.sleep(0.5)\n'
    llamar('run_command', argv=PY + [codigo], purpose='Probar', background=True)
    estado = llamar('command_status', process='p1', wait_seconds=5)
    assert 'b' in estado['salida_nueva']
    llamar('command_status', process='p1', wait_seconds=3)
    asyncio.get_event_loop().run_until_complete(asyncio.sleep(1))
    final = llamar('command_status', process='p1')
    assert final['vivo'] is False and final['exit_code'] == 0
    assert any('terminó con código 0' in f['texto'] for f in filas(salida, 'evento'))
    with pytest.raises(ValueError, match='No hay un proceso'):
        llamar('command_status', process='p9')


def test_el_cliente_puede_detener_procesos(motor):
    m, rt, llamar, salida = motor
    llamar('run_command', argv=PY + [SERVIDOR], purpose='Abrir', background=True)
    t = m.transport
    t.stop_process = lambda ident: asyncio.ensure_future(m.fondo.detener(ident))
    t.receive(json.dumps({'tipo': 'detener_proceso', 'id': 'p1'}))
    asyncio.get_event_loop().run_until_complete(asyncio.sleep(0.5))
    assert not m.fondo.vivos()
    t.receive(json.dumps({'tipo': 'detener_proceso', 'id': 7}))
    assert 'inválido' in filas(salida, 'error')[-1]['texto']


def test_command_status_y_background_estan_en_el_contrato():
    from alfalfa.proveedores.nativo.herramientas import ON_REQUEST_TOOLS, TOOLS
    nombres = [t['function']['name'] for t in ON_REQUEST_TOOLS]
    assert 'command_status' in nombres and 'command_status' not in [t['function']['name'] for t in TOOLS]
    run = next(t for t in ON_REQUEST_TOOLS if t['function']['name'] == 'run_command')['function']
    checks = next(t for t in ON_REQUEST_TOOLS if t['function']['name'] == 'run_checks')['function']
    assert 'background' in run['parameters']['properties'] and 'background' not in checks['parameters']['properties']


COMPILACION = ('import time\nprint("compilando 1/3", flush=True)\ntime.sleep(1.5)\n'
               'print("compilando 2/3", flush=True)\ntime.sleep(1.5)\nprint("Build complete!", flush=True)\n')


def test_una_orden_que_agota_su_tiempo_pasa_a_segundo_plano_y_termina(motor):
    """Como Codex: una compilación larga no se corta; el modelo espera su final."""
    m, rt, llamar, salida = motor
    resultado = llamar('run_command', argv=PY + [COMPILACION], purpose='Compilar en release', timeout_seconds=1)
    assert resultado['en_segundo_plano'] is True and resultado['proceso'] == 'p1'
    assert 'timed_out' not in resultado and 'compilando 1/3' in resultado['output']
    assert resultado['classification']['status'] == 'running'
    assert 'wait_for_exit' in resultado['nota'] and 'no es un bloqueo' in resultado['nota']
    assert rt.state.get('unresolved') == {}
    assert any('pasó a segundo plano como p1' in f['texto'] for f in filas(salida, 'evento'))
    final = llamar('command_status', process='p1', wait_seconds=30, wait_for_exit=True)
    assert final['vivo'] is False and final['exit_code'] == 0
    assert 'compilando 1/3' not in final['salida_nueva']  # sólo lo nuevo
    assert 'compilando 2/3' in final['salida_nueva'] and 'Build complete!' in final['salida_nueva']


def test_run_checks_sigue_cortandose_al_agotar_su_tiempo(motor):
    m, rt, llamar, salida = motor
    resultado = llamar('run_checks', argv=PY + [COMPILACION], purpose='Pruebas', timeout_seconds=1)
    assert resultado.get('timed_out') is True and 'en_segundo_plano' not in resultado
    assert not m.fondo.procesos


def test_sin_sitio_en_segundo_plano_se_corta_como_antes(motor, monkeypatch):
    m, rt, llamar, salida = motor
    monkeypatch.setattr(segundo_plano, 'MAX_PROCESOS', 0)
    resultado = llamar('run_command', argv=PY + [COMPILACION], purpose='Compilar', timeout_seconds=1)
    assert resultado.get('timed_out') is True and 'en_segundo_plano' not in resultado


# La salida con tope: el arnés lee la tubería y guarda sólo lo último.
LINEA = 'linea %06d ' + '.' * 40 + '\n'
MUCHO = ('import sys, time\nprint("arranca", flush=True)\ntime.sleep(1.5)\n'
         'for i in range(40000):\n    sys.stdout.write(' + repr(LINEA) + ' % i)\n'
         'sys.stdout.flush()\nsys.stderr.write("FIN\\n")\n')
TOPE = 64 * 1024


def test_un_proceso_que_escribe_mucho_guarda_solo_lo_ultimo_con_la_marca(motor, monkeypatch):
    monkeypatch.setattr(segundo_plano, 'TOPE_REGISTRO', TOPE, raising=False)
    m, rt, llamar, salida = motor
    resultado = llamar('run_command', argv=PY + [MUCHO], purpose='Escribir mucho', background=True)
    assert resultado['en_segundo_plano'] is True and resultado['output'] == 'arranca\n'
    final = llamar('command_status', process='p1', wait_seconds=60, wait_for_exit=True)
    assert final['vivo'] is False and final['exit_code'] == 0
    # command_status: la marca primero, luego lo último, con stderr detrás de stdout como se escribió.
    total = len('arranca\n') + sum(len(LINEA % i) for i in range(40000)) + len('FIN\n')
    nueva = final['salida_nueva']
    primera, resto = nueva.split('\n', 1)
    assert primera.startswith('[… ') and primera.endswith(' bytes anteriores recortados]')
    assert int(primera.split()[1]) == total - len('arranca\n') - len(resto.encode())
    assert resto.endswith(LINEA % 39999 + 'FIN\n') and len(resto) == segundo_plano.MAX_SALIDA
    assert final['truncated'] is True
    # El registro en disco no pasa del tope, conserva lo último y empieza con la marca.
    registro = m.directory / 'procesos' / 'p1.log'
    contenido = registro.read_bytes()
    assert len(contenido) <= TOPE and registro.stat().st_blocks * 512 <= TOPE + 64 * 1024  # sin huecos
    cabecera, datos = contenido.split(b'\n', 1)
    assert cabecera.decode().startswith('[… ') and cabecera.decode().endswith(' bytes anteriores recortados]')
    assert int(cabecera.split()[1]) + len(datos) == total
    assert datos.endswith((LINEA % 39999 + 'FIN\n').encode()) and len(datos) >= TOPE // 2 - 100
    # La app sigue viendo sus últimas líneas, sin la marca.
    assert m.fondo.lista()[0]['ultimas'][-2:] == [(LINEA % 39999).rstrip('\n'), 'FIN']
    # Lo ya visto no se repite.
    assert llamar('command_status', process='p1')['salida_nueva'] == ''


RAPIDO = ('import sys, time\ntime.sleep(1.5)\nbloque = b"x" * 65535 + b"\\n"\n'
          'for _ in range(320):\n    sys.stdout.buffer.write(bloque)\n'
          'sys.stdout.buffer.flush()\nprint("hecho", flush=True)\n')


def test_un_proceso_que_escribe_mucho_y_rapido_no_se_bloquea(motor, monkeypatch):
    """20 MB de golpe sin que nadie mire: el arnés vacía la tubería y el proceso termina."""
    monkeypatch.setattr(segundo_plano, 'TOPE_REGISTRO', 256 * 1024, raising=False)
    m, rt, llamar, salida = motor
    assert llamar('run_command', argv=PY + [RAPIDO], purpose='Escribir rápido', background=True)['en_segundo_plano']
    proceso = m.fondo.procesos['p1']
    limite = time.monotonic() + 60
    while proceso.vivo and time.monotonic() < limite:
        asyncio.get_event_loop().run_until_complete(asyncio.sleep(0.1))
    assert proceso.proc.returncode == 0
    final = llamar('command_status', process='p1')
    assert final['salida_nueva'].endswith('hecho\n')
    assert (m.directory / 'procesos' / 'p1.log').stat().st_size <= 256 * 1024


def test_el_registro_de_un_terminado_se_guarda_hasta_cerrar_la_sesion(motor):
    m, rt, llamar, salida = motor
    carpeta = m.directory / 'procesos'
    corto = llamar('run_command', argv=PY + ['print("corto")'], purpose='Probar', background=True)
    assert corto['exit_code'] == 0 and corto['output'] == 'corto\n'
    llamar('run_command', argv=PY + [SERVIDOR], purpose='Abrir', background=True)
    assert (carpeta / 'p1.log').exists() and (carpeta / 'p2.log').exists()
    llamar('command_status', process='p2', stop=True)
    # Terminados, pero command_status todavía puede pedirlos: no se borran.
    assert (carpeta / 'p1.log').exists() and (carpeta / 'p2.log').exists()
    assert m.fondo.lista()[0]['ultimas'] == ['corto']
    asyncio.get_event_loop().run_until_complete(m.fondo.detener_todos())
    m.close()
    assert not carpeta.exists()


def test_solo_se_pueden_pedir_los_ultimos_terminados(motor, monkeypatch):
    monkeypatch.setattr(segundo_plano, 'MAX_TERMINADOS', 2, raising=False)
    m, rt, llamar, salida = motor
    for n in range(4):
        assert llamar('run_command', argv=PY + ['print(' + str(n) + ')'], purpose='Probar',
                      background=True)['exit_code'] == 0
    asyncio.get_event_loop().run_until_complete(asyncio.sleep(0.5))
    carpeta = m.directory / 'procesos'
    assert sorted(p.name for p in carpeta.glob('p*.log')) == ['p3.log', 'p4.log']
    assert [f['id'] for f in m.fondo.lista()] == ['p3', 'p4']
    with pytest.raises(ValueError, match='ya se borró'):
        llamar('command_status', process='p1')


def test_tras_un_cierre_brusco_al_reabrir_se_recogen_sus_procesos_y_registros(tmp_path, monkeypatch):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    monkeypatch.chdir(raiz)
    monkeypatch.setattr(segundo_plano, 'ESPERA_INICIAL', 1.0)
    bucle = asyncio.new_event_loop()
    asyncio.set_event_loop(bucle)
    m = TerminalManager('Lanza el juego', home=tmp_path / 'alfalfa', transport=Transport(io.StringIO()))
    rt = m.runtime()
    rt.approval_policy = Si()
    bucle.run_until_complete(rt.tools.call('run_command', dict(argv=PY + [SERVIDOR], purpose='Abrir',
                                                               background=True)))
    proc = m.fondo.procesos['p1'].proc
    carpeta = m.directory / 'procesos'
    assert json.loads((carpeta / 'grupos.json').read_text())[0]['pgid'] == proc.pid
    # Como un kill -9: el cerrojo se suelta sin detener nada ni limpiar.
    os.close(m._lock)
    m._lock = None
    otro = TerminalManager(resume=m.task['id'], home=tmp_path / 'alfalfa', transport=Transport(io.StringIO()))
    try:
        assert not (carpeta / 'p1.log').exists() and not (carpeta / 'grupos.json').exists()
        from alfalfa.procesos import esperar_salida
        assert bucle.run_until_complete(esperar_salida(proc, 10)) is not None
    finally:
        otro.close()
        for tarea in asyncio.all_tasks(bucle):
            tarea.cancel()
        bucle.run_until_complete(asyncio.sleep(0.1))
        bucle.close()


ESCRIBE_SIEMPRE = ('import sys, time\nprint("listo", flush=True)\ntime.sleep(1.5)\n'
                   'while True:\n    sys.stdout.write("x" * 4096 + "\\n")\n    sys.stdout.flush()\n')


def test_sin_lector_el_proceso_no_se_queda_colgado_escribiendo(motor):
    """Como si el arnés muriera: la tubería se queda sin lector y el proceso recibe SIGPIPE (o EPIPE si
    lo ignora, como Python, que sale con BrokenPipeError): termina solo, sin bloquearse ni matarlo."""
    import signal

    from alfalfa.procesos import esperar_salida
    m, rt, llamar, salida = motor
    assert llamar('run_command', argv=PY + [ESCRIBE_SIEMPRE], purpose='Escribir', background=True)['en_segundo_plano']
    proceso = m.fondo.procesos['p1']
    proceso.bomba.cancel()
    # Sólo el extremo de lectura, como cuando el arnés muere (cerrar el transporte entero lo mataría).
    proceso.proc._transport.get_pipe_transport(1).close()
    codigo = asyncio.get_event_loop().run_until_complete(esperar_salida(proceso.proc, 10))
    assert codigo is not None and codigo != -signal.SIGKILL


NIETO = ('import subprocess, sys\n'
         'subprocess.Popen([sys.executable, "-I", "-B", "-c", "import os, time\\nprint(os.getpid(), flush=True)\\n'
         'while True:\\n    print(1, flush=True)\\n    time.sleep(0.2)\\n"])\n')


def test_detener_todos_recoge_al_nieto_que_retiene_la_tuberia(motor):
    """`servidor &`: la orden sale y el nieto sigue escribiendo en su grupo; detener todos lo cierra."""
    from alfalfa.procesos import grupo_vivo
    m, rt, llamar, salida = motor
    if getattr(rt.process_sandbox, 'etiqueta', None) == 'bwrap':
        # Con bwrap cada orden tiene su espacio de procesos y el nieto muere al salir ella (test_bwrap.py).
        # Lo que se prueba aquí es la recogida del arnés, así que la orden sale del sandbox, como permite Si.
        rt.process_sandbox = None
    resultado = llamar('run_command', argv=PY + [NIETO], purpose='Abrir', background=True)
    assert resultado['exit_code'] == 0
    proceso = m.fondo.procesos['p1']
    assert proceso.bombeando and json.loads((m.directory / 'procesos' / 'grupos.json').read_text())
    nieto = int(proceso.registro.leer(0)[0].split()[0])
    asyncio.get_event_loop().run_until_complete(m.fondo.detener_todos())
    assert not proceso.bombeando and not grupo_vivo(proceso.proc.pid)
    try:
        os.kill(nieto, 0)
        vivo = True
    except ProcessLookupError:
        vivo = False
    assert not vivo
    assert not (m.directory / 'procesos' / 'grupos.json').exists()
