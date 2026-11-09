"""Deshacer por turno: instantáneas en un git paralelo, volver con el código y rehacer."""
import hashlib
import json
import os
import subprocess
import time

import pytest

from alfalfa import deshacer, sesiones
from alfalfa.deshacer import instantaneas, irreversibles
from alfalfa.deshacer.instantaneas import Almacen
from alfalfa.proveedores.nativo import historial


def sha(texto):
    return hashlib.sha256(texto.encode()).hexdigest()


class Sesion:
    """Una sesión guardada de verdad (task.json + estado nativo) con turnos simulados."""

    def __init__(self, hogar, cwd, ident='s1'):
        self.hogar, self.cwd, self.id = hogar, cwd, ident
        self.dir = hogar / 'sesiones' / ident
        (self.dir / 'native' / ident).mkdir(parents=True)
        self.task = {'id': ident, 'cwd': str(cwd), 'status': 'completed', 'turnos': []}
        self.estado = {'messages': [{'role': 'system', 'content': 'x'}], 'changes': [], 'workspace_revision': 3,
                       'checks': [{'id': 'check-1', 'revision': 3}]}
        self.guardar()

    def guardar(self):
        (self.dir / 'task.json').write_text(json.dumps(self.task))
        historial.escribir(self.dir / 'native' / self.id, self.estado)

    def leer(self, ident=None):
        ident = ident or self.id
        carpeta = self.hogar / 'sesiones' / ident
        return (json.loads((carpeta / 'task.json').read_text()),
                historial.leer(carpeta / 'native' / ident / 'session.json'))

    def turno(self, mensaje, trabajo, llamadas=()):
        """Un turno: instantánea, el trabajo del agente (`trabajo(cwd, estado)`) e instantánea final."""
        mensajes = self.estado['messages']
        turno = sesiones.abrir_turno(self.task, mensaje, len(mensajes))
        turno['inicio'] = time.time()
        deshacer.al_empezar_turno(self.hogar, self.task, turno['id'])
        mensajes.append({'role': 'user', 'content': mensaje})
        if llamadas:
            mensajes.append({'role': 'assistant', 'tool_calls': [
                {'function': {'name': 'run_command', 'arguments': json.dumps({'argv': argv, 'purpose': 'x'})}}
                for argv in llamadas]})
        trabajo(self.cwd, self.estado)
        mensajes.append({'role': 'assistant', 'content': 'hecho'})
        sesiones.cerrar_turno(self.task, turno['id'], 'completed', len(mensajes))
        deshacer.al_terminar_turno(self.hogar, self.task, turno['id'])
        self.guardar()
        time.sleep(0.01)
        return turno['id']


def escribir(estado, cwd, ruta, texto, via=None):
    """Lo que hace write_file: el archivo y su fila en `changes`."""
    path = cwd / ruta
    antes = sha(path.read_text()) if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(texto)
    fila = {'path': ruta, 'before': antes, 'after': sha(texto)}
    if via:
        fila['via'] = via
    estado['changes'].append(fila)


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    hogar = tmp_path / 'hogar'
    cwd = tmp_path / 'trabajo' / 'proyecto'
    cwd.mkdir(parents=True)
    (cwd / 'app.py').write_text('print(1)\n')
    (cwd / 'notas.md').write_text('notas\n')
    monkeypatch.setenv('ALFALFA_HOME', str(hogar))
    return hogar, cwd


def test_carpeta_sin_git_vuelve_al_codigo_de_antes_del_mensaje(entorno):
    hogar, cwd = entorno
    sesion = Sesion(hogar, cwd)
    sesion.turno('uno', lambda c, e: escribir(e, c, 'app.py', 'print(2)\n'))
    t2 = sesion.turno('dos', lambda c, e: (escribir(e, c, 'app.py', 'print(3)\n'),
                                          escribir(e, c, 'nuevo/mod.py', 'x = 1\n')))
    assert not (cwd / '.git').exists(), 'el proyecto no se convierte en repositorio'
    datos = sesion.task['turnos'][-1]['instantanea']
    assert datos['antes'] and datos['despues']

    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t2)
    assert vista['disponible'] and not vista['conflictos']
    assert {(f['ruta'], f['accion']) for f in vista['restaurables']} == {('app.py', 'restaurar'),
                                                                         ('nuevo/mod.py', 'borrar')}
    paso = deshacer.deshacer(hogar, sesion.task, sesion.estado, t2, vista=vista)
    assert (cwd / 'app.py').read_text() == 'print(2)\n'
    assert not (cwd / 'nuevo').exists(), 'la carpeta que creó el turno se va con su archivo'
    assert sorted(paso['restaurados']) == ['app.py', 'nuevo/mod.py']
    # La evidencia: nuevas huellas para lo restaurado y ninguna comprobación vigente.
    assert sesion.estado['changes'][-1]['via'] == 'deshacer'
    assert deshacer.huella(sesion.estado)['app.py'] == sha('print(2)\n')
    assert sesion.estado['workspace_revision'] == 4


def test_no_pisa_lo_que_el_usuario_edito_despues(entorno):
    hogar, cwd = entorno
    sesion = Sesion(hogar, cwd)
    t1 = sesion.turno('uno', lambda c, e: (escribir(e, c, 'app.py', 'print(2)\n'),
                                          escribir(e, c, 'otro.py', 'y = 2\n')))
    (cwd / 'app.py').write_text('print("mío")\n')    # El usuario, después del turno.
    (cwd / 'notas.md').write_text('notas mías\n')     # Un archivo que el agente no tocó.

    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t1)
    conflictos = {c['ruta']: c['motivo'] for c in vista['conflictos']}
    assert conflictos == {'app.py': 'editado_despues', 'notas.md': 'ajeno'}
    assert [f['ruta'] for f in vista['restaurables']] == ['otro.py']
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t1, vista=vista)
    assert (cwd / 'app.py').read_text() == 'print("mío")\n'
    assert (cwd / 'notas.md').read_text() == 'notas mías\n'
    assert not (cwd / 'otro.py').exists()

    # Pedido por su ruta, sí se restaura.
    t_previo = sesion.task['deshechos'][-1]['turno']
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t_previo, forzar=['app.py'])
    assert (cwd / 'app.py').read_text() == 'print(1)\n'
    assert (cwd / 'notas.md').read_text() == 'notas mías\n'


def test_cubre_lo_que_cambian_las_ordenes(entorno):
    hogar, cwd = entorno
    sesion = Sesion(hogar, cwd)

    def orden(c, e):
        # `run_command` que crea y borra archivos sin dejar fila en `changes`.
        (c / 'generado.json').write_text('{}')
        (c / 'notas.md').unlink()
        (c / 'app.py').write_text('print("orden")\n')
        e['changes'].append({'path': 'app.py', 'before': sha('print(1)\n'), 'after': sha('print("orden")\n'),
                             'via': 'run_command'})

    t1 = sesion.turno('genera', orden, llamadas=[['python', 'gen.py']])
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t1)
    assert {(f['ruta'], f['accion']) for f in vista['restaurables']} == {
        ('app.py', 'restaurar'), ('generado.json', 'borrar'), ('notas.md', 'recrear')}
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t1, vista=vista)
    assert not (cwd / 'generado.json').exists()
    assert (cwd / 'notas.md').read_text() == 'notas\n'
    assert (cwd / 'app.py').read_text() == 'print(1)\n'


def test_rehacer_devuelve_lo_quitado_sin_pisar_lo_editado_despues(entorno):
    hogar, cwd = entorno
    sesion = Sesion(hogar, cwd)
    t1 = sesion.turno('uno', lambda c, e: (escribir(e, c, 'app.py', 'print(2)\n'),
                                          escribir(e, c, 'b.py', 'b\n'), escribir(e, c, 'c.py', 'c\n')))
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t1)
    assert not (cwd / 'b.py').exists()
    (cwd / 'c.py').write_text('c del usuario\n')    # Después de deshacer.

    vista = deshacer.vista_rehacer(hogar, sesion.task)
    assert [c['ruta'] for c in vista['conflictos']] == ['c.py']
    paso = deshacer.rehacer(hogar, sesion.task, sesion.estado, vista=vista)
    assert (cwd / 'app.py').read_text() == 'print(2)\n'
    assert (cwd / 'b.py').read_text() == 'b\n'
    assert (cwd / 'c.py').read_text() == 'c del usuario\n'
    assert paso['tipo'] == 'rehacer' and paso['conservados'] == ['c.py']
    assert sesion.task['deshechos'][0]['rehecho'] == paso['id']
    with pytest.raises(ValueError, match='nada que rehacer'):
        deshacer.vista_rehacer(hogar, sesion.task)
    # Volver otra vez al mismo mensaje: el paso de rehacer cuenta como del agente.
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t1)
    assert {f['ruta'] for f in vista['restaurables']} == {'app.py', 'b.py'}
    assert {c['ruta'] for c in vista['conflictos']} == {'c.py'}


def test_respeta_gitignore_cachés_y_tope_de_tamaño(entorno, monkeypatch):
    hogar, cwd = entorno
    monkeypatch.setattr(instantaneas, 'TOPE_ARCHIVO', 1000)
    (cwd / '.gitignore').write_text('*.log\n')
    (cwd / 'x.log').write_text('log')
    (cwd / '__pycache__').mkdir()
    (cwd / '__pycache__' / 'a.pyc').write_text('pyc')
    (cwd / 'node_modules' / 'p').mkdir(parents=True)
    (cwd / 'node_modules' / 'p' / 'i.js').write_text('js')
    (cwd / '.tmp-alfalfa').mkdir()
    (cwd / '.tmp-alfalfa' / 't').write_text('t')
    (cwd / 'grande.bin').write_bytes(b'x' * 2000)
    almacen = Almacen(hogar, cwd)
    fila = almacen.guardar('s1', 'prueba')
    assert sorted(almacen.arbol(fila['commit'])) == ['.gitignore', 'app.py', 'notas.md']
    assert fila['grandes'] == ['grande.bin']
    assert str(hogar / 'instantaneas') in str(almacen.dir)


def test_proyecto_git_no_se_toca(entorno):
    hogar, cwd = entorno
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    for orden in (['init', '-q'], ['add', '-A'], ['-c', 'user.name=x', '-c', 'user.email=x@x', 'commit', '-qm', 'x']):
        subprocess.run(['git', *orden], cwd=cwd, check=True, env=env)
    (cwd / 'sin_seguir.txt').write_text('nuevo')
    estado_git = lambda: subprocess.run(['git', 'status', '--porcelain'], cwd=cwd, capture_output=True,  # noqa: E731
                                        text=True, env=env).stdout
    antes = estado_git()
    git_antes = {p: p.read_bytes() for p in (cwd / '.git').rglob('*') if p.is_file()}
    sesion = Sesion(hogar, cwd)
    t1 = sesion.turno('uno', lambda c, e: escribir(e, c, 'app.py', 'print(9)\n'))
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t1)
    assert {p: p.read_bytes() for p in (cwd / '.git').rglob('*') if p.is_file()} == git_antes
    assert estado_git() == antes
    assert not list((cwd / '.git').glob('refs/alfalfa*'))


def test_turno_sin_instantanea_lo_dice(entorno):
    hogar, cwd = entorno
    sesion = Sesion(hogar, cwd)
    turno = sesiones.abrir_turno(sesion.task, 'viejo', 1)
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, turno['id'])
    assert vista['disponible'] is False and 'instantánea' in vista['motivo']
    with pytest.raises(ValueError):
        deshacer.deshacer(hogar, sesion.task, sesion.estado, turno['id'])


def test_un_fallo_de_git_no_para_el_turno(entorno, monkeypatch):
    hogar, cwd = entorno

    def roto(*args, **kwargs):
        raise subprocess.CalledProcessError(1, 'git')
    monkeypatch.setattr(instantaneas.subprocess, 'run', roto)
    sesion = Sesion(hogar, cwd)
    sesion.turno('uno', lambda c, e: escribir(e, c, 'app.py', 'print(2)\n'))
    datos = sesion.task['turnos'][0]['instantanea']
    assert set(datos['errores']) == {'antes', 'despues'} and 'antes' not in datos
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, sesion.task['turnos'][0]['id'])
    assert vista['disponible'] is False and 'No se guardó' in vista['motivo']


def test_apagadas_con_la_variable(entorno, monkeypatch):
    hogar, cwd = entorno
    monkeypatch.setenv('ALFALFA_INSTANTANEAS', '0')
    sesion = Sesion(hogar, cwd)
    sesion.turno('uno', lambda c, e: None)
    assert 'instantanea' not in sesion.task['turnos'][0]
    assert not (hogar / 'instantaneas').exists()


def test_limpieza_de_instantaneas_viejas(entorno):
    hogar, cwd = entorno
    almacen = Almacen(hogar, cwd)
    almacen.guardar('s1', 'vieja')
    assert almacen.limpiar(dias=30) == 0
    assert almacen.limpiar(dias=30, ahora=time.time() + 31 * 86400) == 1
    otra = almacen.guardar('s1', 'otra')
    assert almacen.arbol(otra['commit']), 'tras la limpieza el índice no apunta a objetos borrados'
    assert almacen.limpiar(tope=1) == 1, 'por encima del tope se borran las más viejas'
    # Un almacén de una carpeta que ya no existe se borra entero.
    otra = cwd.parent / 'borrada'
    otra.mkdir()
    Almacen(hogar, otra).guardar('s2', 'x')
    otra.rmdir()
    instantaneas.limpiar_todo(hogar)
    assert not Almacen(hogar, otra).dir.exists()


def test_avisa_de_lo_que_no_se_deshace(tmp_path):
    cwd = tmp_path / 'p'
    cwd.mkdir()
    tipos = lambda argv: {t for t, _ in irreversibles.clasificar(argv, cwd)}  # noqa: E731
    assert tipos(['git', 'push', 'origin', 'main']) == {'publicacion'}
    assert tipos(['git', 'commit', '-m', 'x']) == {'git'}
    assert tipos(['git', 'status']) == set()
    assert tipos(['npm', 'install', 'left-pad']) == {'instalacion'}
    assert tipos(['brew', 'install', 'jq']) == {'instalacion'}
    assert tipos(['rm', '-rf', str(tmp_path / 'fuera')]) == {'fuera_de_la_carpeta'}
    assert tipos(['curl', '-X', 'POST', 'https://ejemplo.com/api']) == {'red'}
    assert tipos(['bash', '-lc', 'cd x && git push']) == {'publicacion'}
    assert tipos(['python', '-m', 'pytest']) == set()
    assert tipos(['sudo', 'rm', '/etc/x']) == {'sistema', 'fuera_de_la_carpeta'}
    mensajes = [{'role': 'assistant', 'tool_calls': [
        {'function': {'name': 'run_command', 'arguments': {'argv': ['git', 'push']}}},
        {'function': {'name': 'mcp__github__create_issue', 'arguments': '{}'}},
        {'function': {'name': 'write_file', 'arguments': '{}'}}]}]
    avisos = irreversibles.de_mensajes(mensajes, cwd)
    assert [a['tipo'] for a in avisos] == ['publicacion', 'herramienta_externa']
    assert avisos[0]['ordenes'] == ['git push']


# MARK: - Terminal y --json (lo que usa la app)

def _orden(capsys, *argv):
    from alfalfa.terminal import main
    codigo = main(list(argv))
    salida = capsys.readouterr().out
    return codigo, [json.loads(linea) for linea in salida.splitlines() if linea.startswith('{')]


def test_volver_solo_codigo_por_json(entorno, capsys):
    hogar, cwd = entorno
    sesion = Sesion(hogar, cwd)
    t1 = sesion.turno('uno', lambda c, e: escribir(e, c, 'app.py', 'print(2)\n'), llamadas=[['git', 'push']])
    codigo, filas = _orden(capsys, '--volver', 's1', '--turno', t1, '--alcance', 'codigo', '--vista-previa', '--json')
    assert codigo == 0 and filas[0]['tipo'] == 'deshacer_vista'
    assert filas[0]['restaurables'] == [{'ruta': 'app.py', 'accion': 'restaurar'}]
    assert filas[0]['irreversibles'][0]['tipo'] == 'publicacion'
    assert not any(k.startswith('_') for k in filas[0])
    assert (cwd / 'app.py').read_text() == 'print(2)\n', 'la vista previa no toca nada'

    codigo, filas = _orden(capsys, '--volver', 's1', '--turno', t1, '--alcance', 'codigo', '--json')
    assert codigo == 0 and filas[0]['tipo'] == 'deshecho' and filas[0]['sesion'] == 's1'
    assert (cwd / 'app.py').read_text() == 'print(1)\n'
    task, estado = sesion.leer()
    assert task['deshechos'][0]['restaurados'] == ['app.py']
    assert len(task['turnos']) == 1, 'sólo el código: la conversación sigue igual'
    assert estado['workspace_revision'] == 4
    from alfalfa.stdio import history
    marca = [e for e in history('s1')['eventos'] if 'deshacer' in e]
    assert marca and marca[0]['deshacer']['paso'] == 'deshacer' and 'git push' in str(marca[0]['deshacer'])

    codigo, filas = _orden(capsys, '--rehacer', 's1', '--json')
    assert codigo == 0 and filas[0]['tipo'] == 'deshecho' and filas[0]['de'] == task['deshechos'][0]['id']
    assert (cwd / 'app.py').read_text() == 'print(2)\n'


def test_volver_con_conversacion_y_codigo_bifurca(entorno, capsys):
    hogar, cwd = entorno
    sesion = Sesion(hogar, cwd)
    t1 = sesion.turno('uno', lambda c, e: escribir(e, c, 'app.py', 'print(2)\n'))
    t2 = sesion.turno('dos', lambda c, e: escribir(e, c, 'app.py', 'print(3)\n'))
    codigo, filas = _orden(capsys, '--volver', 's1', '--turno', t2, '--json')
    assert codigo == 0
    assert [f['tipo'] for f in filas] == ['deshecho', 'sesion_cambiada']
    nueva = filas[1]['id']
    assert filas[0]['sesion'] == nueva and filas[1]['bifurcada_de'] == 's1'
    assert (cwd / 'app.py').read_text() == 'print(2)\n'
    task, estado = sesion.leer(nueva)
    assert [t['id'] for t in task['turnos']] == [t1]
    assert task['deshechos'][-1]['turno'] == t2
    assert estado['changes'][-1]['via'] == 'deshacer'
    original, _ = sesion.leer()
    assert 'deshechos' not in original, 'la sesión de origen no cambia'
    # Rehacer desde la bifurcada.
    codigo, _ = _orden(capsys, '--rehacer', nueva, '--json')
    assert codigo == 0 and (cwd / 'app.py').read_text() == 'print(3)\n'


def test_volver_al_primer_mensaje_y_solo_conversacion(entorno, capsys):
    hogar, cwd = entorno
    sesion = Sesion(hogar, cwd)
    t1 = sesion.turno('uno', lambda c, e: escribir(e, c, 'app.py', 'print(2)\n'))
    t2 = sesion.turno('dos', lambda c, e: escribir(e, c, 'app.py', 'print(3)\n'))
    codigo, filas = _orden(capsys, '--volver', 's1', '--turno', t2, '--alcance', 'conversacion', '--json')
    assert [f['tipo'] for f in filas] == ['sesion_cambiada']
    assert (cwd / 'app.py').read_text() == 'print(3)\n', 'sólo la conversación: los archivos no cambian'
    codigo, filas = _orden(capsys, '--volver', 's1', '--turno', t1, '--json')
    assert codigo == 0 and filas[0]['tipo'] == 'deshecho' and filas[0]['nueva_conversacion'] is True
    assert (cwd / 'app.py').read_text() == 'print(1)\n'


def test_volver_en_la_terminal_pregunta_y_falla_si_corre(entorno, capsys, monkeypatch):
    import fcntl
    hogar, cwd = entorno
    sesion = Sesion(hogar, cwd)
    t1 = sesion.turno('uno', lambda c, e: (escribir(e, c, 'app.py', 'print(2)\n'), escribir(e, c, 'b.py', 'b\n')))
    (cwd / 'app.py').write_text('mío\n')
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda texto: 's')
    from alfalfa.terminal import main
    assert main(['--volver', 's1', '--turno', t1, '--alcance', 'codigo']) == 0
    salida = capsys.readouterr().out
    assert 'app.py — se queda: lo cambiaste después del turno' in salida
    assert (cwd / 'app.py').read_text() == 'print(1)\n', 'contestó que sí'
    with open(sesion.dir / 'lock', 'w') as cerrojo:
        fcntl.flock(cerrojo, fcntl.LOCK_EX)
        assert main(['--rehacer', 's1']) == 1
    assert 'en ejecución' in capsys.readouterr().out


def test_un_turno_de_verdad_guarda_sus_dos_instantaneas(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import patch

    from alfalfa.proveedores.nativo.runtime import NativeRuntime
    from alfalfa.terminal import TerminalManager
    proyecto = tmp_path / 'proyecto'
    proyecto.mkdir()
    (proyecto / 'a.txt').write_text('a')
    monkeypatch.chdir(proyecto)
    monkeypatch.setenv('ALFALFA_MANTENER_DESPIERTO', '0')

    async def request(rt, client, endpoint, payload, **kwargs):
        if endpoint == '/api/show':
            return {'capabilities': ['tools']}
        (proyecto / 'a.txt').write_text('cambiado por una orden')
        return dict(message={'role': 'assistant', 'content': 'Hecho.'}, done=True, done_reason='stop',
                    prompt_eval_count=1, eval_count=1)

    m = TerminalManager('x', home=tmp_path / 'alfalfa')
    try:
        async def run():
            with patch.object(NativeRuntime, 'request', request):
                return await m.run('Hola.')
        asyncio.run(asyncio.wait_for(run(), 60))
        datos = m.task['turnos'][0]['instantanea']
        almacen = Almacen(tmp_path / 'alfalfa', proyecto)
        assert datos['repo'] == almacen.clave
        assert almacen.cambios(datos['antes'], datos['despues']) == {
            'a.txt': ('100644', almacen.git('hash-object', 'a.txt').decode().strip())}
        guardada = json.loads((tmp_path / 'alfalfa' / 'sesiones' / m.task['id'] / 'task.json').read_text())
        assert guardada['turnos'][0]['instantanea']['despues'] == datos['despues']
    finally:
        m.close()
