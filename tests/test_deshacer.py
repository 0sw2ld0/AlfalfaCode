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
        deshacer.al_empezar_turno(self.hogar, self.task, turno['id'], self.estado)
        mensajes.append({'role': 'user', 'content': mensaje})
        if llamadas:
            mensajes.append({'role': 'assistant', 'tool_calls': [
                {'function': {'name': 'run_command', 'arguments': json.dumps({'argv': argv, 'purpose': 'x'})}}
                for argv in llamadas]})
        trabajo(self.cwd, self.estado)
        mensajes.append({'role': 'assistant', 'content': 'hecho'})
        sesiones.cerrar_turno(self.task, turno['id'], 'completed', len(mensajes))
        deshacer.al_terminar_turno(self.hogar, self.task, turno['id'], self.estado)
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
    # Todo lo hizo la orden: lo que creó se propone; app.py lo adoptó (`via: 'run_command'`) y no se propone.
    assert vista['restaurables'] == []
    assert {(c['ruta'], c['accion'], c['motivo']) for c in vista['conflictos']} == {
        ('app.py', 'restaurar', 'adoptado'), ('generado.json', 'borrar', 'orden'), ('notas.md', 'recrear', 'orden')}
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t1, forzar=['app.py', 'generado.json', 'notas.md'],
                      vista=vista)
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
    almacen.guardar('s1', 'deshacer-x-antes')
    assert almacen.limpiar(tope=1) == 0, 'las recientes no se borran aunque se pase del tope'
    # Pasados dos días, por encima del tope se borran las más viejas, salvo las de deshacer.
    assert almacen.limpiar(tope=1, ahora=time.time() + 3 * 86400) == 1
    salida = almacen.git('for-each-ref', '--format=%(refname)', 'refs/alfalfa/').decode()
    assert salida.strip() == 'refs/alfalfa/s1/deshacer-x-antes'
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
        # La instantánea de antes corre mientras el modelo responde: lo que cambia archivos la espera,
        # como cualquier herramienta que no sólo lee.
        await rt.antes_de_cambiar()
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


# MARK: - Lo que deshacer nunca debe estropear (revisión del PR 22)

def escribir_bytes(estado, cwd, ruta, datos):
    """write_file con bytes: el archivo y su fila en `changes`."""
    path = cwd / ruta
    antes = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    path.write_bytes(datos)
    estado['changes'].append({'path': ruta, 'before': antes, 'after': hashlib.sha256(datos).hexdigest()})


def _resumen(vista):
    return ({(f['ruta'], f['accion']) for f in vista['restaurables']},
            {(c['ruta'], c['motivo']) for c in vista['conflictos']},
            {(n['ruta'], n['motivo']) for n in vista['no_cubiertos']})


def test_no_borra_un_ignorado_que_el_turno_deja_de_ignorar(entorno):
    hogar, cwd = entorno
    (cwd / '.gitignore').write_text('.env\n')
    (cwd / '.env').write_text('SECRET=1\n')
    sesion = Sesion(hogar, cwd)
    t1 = sesion.turno('uno', lambda c, e: escribir(e, c, '.gitignore', '# limpio\n'))
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t1)
    assert _resumen(vista) == ({('.gitignore', 'restaurar')}, set(), {('.env', 'ignorado')})
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t1, forzar=['.env'], vista=vista)
    assert (cwd / '.env').read_text() == 'SECRET=1\n'
    assert (cwd / '.gitignore').read_text() == '.env\n'


def test_no_borra_un_archivo_grande_que_el_agente_achica(entorno, monkeypatch):
    hogar, cwd = entorno
    monkeypatch.setattr(instantaneas, 'TOPE_ARCHIVO', 1000)
    (cwd / 'datos.csv').write_bytes(b'x' * 2000)
    sesion = Sesion(hogar, cwd)
    t1 = sesion.turno('uno', lambda c, e: escribir(e, c, 'datos.csv', 'a,b\n'))
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t1)
    assert _resumen(vista) == (set(), set(), {('datos.csv', 'grande')})
    assert vista['grandes'] == ['datos.csv']
    assert vista['irreversibles'][-1]['tipo'] == 'no_cubierto'
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t1, vista=vista)
    assert (cwd / 'datos.csv').read_text() == 'a,b\n'


def test_no_pisa_lo_que_cambia_otro_durante_un_turno_sin_ordenes(entorno):
    hogar, cwd = entorno
    sesion = Sesion(hogar, cwd)

    def t1(c, e):
        escribir(e, c, 'app.py', 'print(2)\n')
        (c / 'notas.md').write_text('escritas por el usuario mientras el agente trabajaba\n')
    t = sesion.turno('uno', t1)
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
    assert _resumen(vista)[:2] == ({('app.py', 'restaurar')}, {('notas.md', 'ajeno')})
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t, vista=vista)
    assert (cwd / 'notas.md').read_text().startswith('escritas por el usuario')


def test_conserva_permisos_y_bytes_crlf_y_utf16(entorno):
    import stat
    hogar, cwd = entorno
    (cwd / '.gitattributes').write_text('* text=auto\n*.u16 working-tree-encoding=UTF-16\n*.id ident\n')
    (cwd / 'clave.pem').write_text('privada\n')
    os.chmod(cwd / 'clave.pem', 0o600)
    (cwd / 'win.txt').write_bytes(b'a\r\nb\r\n')
    original16 = 'hola\n'.encode('utf-16')
    (cwd / 'u.u16').write_bytes(original16)
    (cwd / 'v.id').write_bytes(b'$Id$\n')
    (cwd / 'run.sh').write_text('echo 1\n')
    os.chmod(cwd / 'run.sh', 0o750)
    sesion = Sesion(hogar, cwd)

    def t1(c, e):
        escribir(e, c, 'clave.pem', 'otra\n')
        escribir_bytes(e, c, 'win.txt', b'cambiado\n')
        escribir_bytes(e, c, 'u.u16', 'adios\n'.encode('utf-16'))
        escribir_bytes(e, c, 'v.id', b'nada\n')
        escribir(e, c, 'run.sh', 'echo 2\n')
        os.chmod(c / 'run.sh', 0o640)
    t = sesion.turno('uno', t1)
    paso = deshacer.deshacer(hogar, sesion.task, sesion.estado, t)
    assert not paso['fallos']
    assert stat.S_IMODE(os.stat(cwd / 'clave.pem').st_mode) == 0o600
    assert (cwd / 'clave.pem').read_text() == 'privada\n'
    assert (cwd / 'win.txt').read_bytes() == b'a\r\nb\r\n'
    assert (cwd / 'u.u16').read_bytes() == original16
    assert (cwd / 'v.id').read_bytes() == b'$Id$\n'
    assert stat.S_IMODE(os.stat(cwd / 'run.sh').st_mode) == 0o750, 'sólo vuelve el bit x, con los permisos de ahora'


def test_no_escribe_fuera_por_un_enlace(entorno, tmp_path):
    import shutil
    hogar, cwd = entorno
    fuera, fuera2 = tmp_path / 'fuera', tmp_path / 'fuera2'
    fuera.mkdir()
    fuera2.mkdir()
    (cwd / 'sub').mkdir()
    (cwd / 'sub' / 'a.txt').write_text('a\n')
    sesion = Sesion(hogar, cwd)

    def t1(c, e):
        shutil.rmtree(c / 'sub')
        os.symlink(fuera, c / 'sub')
    t = sesion.turno('uno', t1, llamadas=[['ln', '-s', str(fuera), 'sub']])
    os.unlink(cwd / 'sub')
    os.symlink(fuera2, cwd / 'sub')  # El usuario cambia el destino después.
    paso = deshacer.deshacer(hogar, sesion.task, sesion.estado, t, forzar=['sub/a.txt'])
    assert not list(fuera2.iterdir()) and not list(fuera.iterdir())
    assert [f['ruta'] for f in paso['fallos']] == ['sub/a.txt']
    assert 'enlace' in paso['fallos'][0]['error']


def test_un_enlace_a_un_archivo_de_fuera_vuelve_sin_seguirlo(entorno, tmp_path):
    hogar, cwd = entorno
    fuera = tmp_path / 'secreto.txt'
    fuera.write_text('original\n')
    os.symlink(fuera, cwd / 'enlace.txt')
    sesion = Sesion(hogar, cwd)

    def t1(c, e):
        os.unlink(c / 'enlace.txt')
        escribir(e, c, 'enlace.txt', 'agente\n')
    t = sesion.turno('uno', t1)
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t)
    assert os.path.islink(cwd / 'enlace.txt') and os.readlink(cwd / 'enlace.txt') == str(fuera)
    assert fuera.read_text() == 'original\n'


def test_misma_fecha_y_tamano_no_engana(entorno):
    hogar, cwd = entorno
    sesion = Sesion(hogar, cwd)
    t = sesion.turno('uno', lambda c, e: escribir(e, c, 'app.py', 'print(2)\n'))
    time.sleep(1.1)
    previo = os.stat(cwd / 'app.py')
    (cwd / 'app.py').write_text('print(9)\n')
    os.utime(cwd / 'app.py', ns=(previo.st_atime_ns, previo.st_mtime_ns))
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t)
    assert (cwd / 'app.py').read_text() == 'print(9)\n'


def _repo(path, con_commit):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    subprocess.run(['git', 'init', '-q', str(path)], check=True, env=env)
    (path / 'x.py').write_text('1\n')
    if con_commit:
        for orden in (['add', '.'], ['commit', '-qm', 'x']):
            subprocess.run(['git', '-C', str(path), '-c', 'user.name=a', '-c', 'user.email=a@a', *orden],
                           check=True, env=env)


@pytest.mark.parametrize('con_commit', [False, True])
def test_un_repo_anidado_no_rompe_las_instantaneas_y_se_avisa(entorno, con_commit):
    hogar, cwd = entorno
    (cwd / 'lib').mkdir()
    _repo(cwd / 'lib', con_commit)
    sesion = Sesion(hogar, cwd)
    t = sesion.turno('uno', lambda c, e: (escribir(e, c, 'lib/x.py', '2\n'), escribir(e, c, 'app.py', 'print(2)\n')))
    datos = sesion.task['turnos'][0]['instantanea']
    assert datos['antes'] and datos['despues'] and 'errores' not in datos
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
    assert _resumen(vista) == ({('app.py', 'restaurar')}, set(), {('lib/x.py', 'repo_anidado')})
    assert 'lib/x.py' in vista['irreversibles'][-1]['ordenes']
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t, vista=vista)
    assert (cwd / 'lib' / 'x.py').read_text() == '2\n'
    assert (cwd / 'app.py').read_text() == 'print(1)\n'


def test_un_repo_vacio_creado_por_el_agente_no_apaga_las_siguientes(entorno):
    hogar, cwd = entorno
    sesion = Sesion(hogar, cwd)
    sesion.turno('uno', lambda c, e: subprocess.run(['git', 'init', '-q', str(c / 'nuevo')], check=True))
    sesion.turno('dos', lambda c, e: escribir(e, c, 'app.py', 'print(5)\n'))
    for turno in sesion.task['turnos']:
        assert turno['instantanea']['antes'] and turno['instantanea']['despues']


def test_quitar_el_git_de_un_repo_anidado_no_borra_su_contenido(entorno):
    import shutil
    hogar, cwd = entorno
    (cwd / 'lib').mkdir()
    _repo(cwd / 'lib', True)
    (cwd / 'lib' / 'mucho.py').write_text('trabajo del usuario\n')
    sesion = Sesion(hogar, cwd)
    t = sesion.turno('uno', lambda c, e: shutil.rmtree(c / 'lib' / '.git'), llamadas=[['rm', '-rf', 'lib/.git']])
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
    assert {n['motivo'] for n in vista['no_cubiertos']} == {'repo_anidado'}
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t, forzar=['lib/mucho.py', 'lib/x.py'], vista=vista)
    assert (cwd / 'lib' / 'mucho.py').read_text() == 'trabajo del usuario\n'


def test_avisa_de_un_ignorado_que_edito_el_agente(entorno):
    hogar, cwd = entorno
    (cwd / '.gitignore').write_text('.env\n')
    (cwd / '.env').write_text('A=1\n')
    sesion = Sesion(hogar, cwd)
    t = sesion.turno('uno', lambda c, e: escribir(e, c, '.env', 'A=2\n'))
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
    assert vista['no_cubiertos'] == [{'ruta': '.env', 'motivo': 'ignorado'}]
    assert vista['irreversibles'] == [{'tipo': 'no_cubierto', 'titulo': irreversibles.TITULOS['no_cubierto'],
                                       'ordenes': ['.env']}]


@pytest.mark.skipif(os.geteuid() == 0, reason='root lee cualquier archivo')
def test_un_archivo_ilegible_no_apaga_las_instantaneas(entorno):
    hogar, cwd = entorno
    privado = cwd / 'privado.txt'
    privado.write_text('x')
    os.chmod(privado, 0)
    try:
        sesion = Sesion(hogar, cwd)
        sesion.turno('uno', lambda c, e: escribir(e, c, 'app.py', 'print(5)\n'))
        datos = sesion.task['turnos'][0]['instantanea']
        assert datos['antes'] and datos['despues']
        assert Almacen(hogar, cwd).excluidos(datos['antes'])['ilegibles'] == ['privado.txt']
    finally:
        os.chmod(privado, 0o600)


def test_carpetas_vacias_que_ya_estaban_se_quedan(entorno):
    import sys
    hogar, cwd = entorno
    (cwd / 'uploads').mkdir()
    sesion = Sesion(hogar, cwd)
    t = sesion.turno('uno', lambda c, e: (escribir(e, c, 'uploads/a.txt', 'x\n'), escribir(e, c, 'nueva/b.txt', 'y\n')))
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t)
    assert (cwd / 'uploads').is_dir() and not (cwd / 'uploads' / 'a.txt').exists()
    if sys.platform == 'darwin':
        assert not (cwd / 'nueva').exists(), 'la carpeta que creó el turno se va (macOS sabe cuándo nació)'


def test_dos_instantaneas_a_la_vez_y_un_cerrojo_viejo(entorno):
    import threading
    hogar, cwd = entorno
    for n in range(300):
        (cwd / f'f{n}.txt').write_text('x' * 500 + str(n))
    almacen = Almacen(hogar, cwd)
    almacen.preparar()
    errores = []

    def guardar(nombre):
        try:
            Almacen(hogar, cwd).guardar('s1', nombre)
        except instantaneas.SinInstantanea as exc:
            errores.append(str(exc))
    hilos = [threading.Thread(target=guardar, args=('n' + str(i),)) for i in range(3)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert errores == []
    # Un git que murió por tiempo deja su .lock: la siguiente instantánea lo quita.
    (almacen.dir / 'indices' / 's1.lock').write_text('')
    assert almacen.guardar('s1', 'otra')['commit']


def test_una_sesion_nueva_parte_del_indice_mas_reciente(entorno):
    hogar, cwd = entorno
    almacen = Almacen(hogar, cwd)
    almacen.guardar('s1', 'uno')
    assert not (almacen.dir / 'indices' / 's2').exists()
    almacen.guardar('s2', 'uno')
    assert (almacen.dir / 'indices' / 's2').stat().st_size > 0


def test_un_archivo_llamado_como_una_cache_se_guarda(entorno):
    hogar, cwd = entorno
    (cwd / 'coverage').write_text('no es la carpeta de cobertura\n')
    (cwd / '.coverage').write_text('sqlite')
    almacen = Almacen(hogar, cwd)
    arbol = almacen.arbol(almacen.guardar('s1', 'uno')['commit'])
    assert 'coverage' in arbol and '.coverage' not in arbol


def test_avisa_del_git_push_de_un_subagente(entorno):
    hogar, cwd = entorno
    sesion = Sesion(hogar, cwd)

    def t1(c, e):
        carpeta = sesion.dir / 'subagentes'
        carpeta.mkdir()
        argumentos = {'argv': ['git', 'push', 'origin', 'main'], 'purpose': 'publicar'}
        filas = [{'role': 'tool', 'time': time.time(), 'text': 'Inicio run_command · ' + str(argumentos)},
                 {'role': 'tool', 'time': time.time(), 'text': "Inicio run_command · {'argv': ['curl', 'x"}]
        (carpeta / 'a1.jsonl').write_text(''.join(json.dumps(f) + '\n' for f in filas))
    t = sesion.turno('uno', t1)
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
    assert {a['tipo'] for a in vista['irreversibles']} == {'publicacion', 'subagente'}


# MARK: - Segunda revisión del PR 22

@pytest.mark.parametrize('como', ['ignorado', 'grande'])
def test_rehacer_no_pisa_un_archivo_que_el_usuario_crea_despues(entorno, monkeypatch, como):
    hogar, cwd = entorno
    monkeypatch.setattr(instantaneas, 'TOPE_ARCHIVO', 1000)
    sesion = Sesion(hogar, cwd)
    t = sesion.turno('uno', lambda c, e: escribir(e, c, 'config.json', '{"agente": 1}\n'))
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t)
    assert not (cwd / 'config.json').exists()
    if como == 'ignorado':
        (cwd / '.gitignore').write_text('config.json\n')
        propio = b'{"usuario": 1}\n'
    else:
        propio = b'x' * 2000
    (cwd / 'config.json').write_bytes(propio)
    vista = deshacer.vista_rehacer(hogar, sesion.task)
    assert ('config.json', como) in {(c['ruta'], c['motivo']) for c in vista['conflictos']}
    deshacer.rehacer(hogar, sesion.task, sesion.estado, vista=vista)
    assert (cwd / 'config.json').read_bytes() == propio


def test_con_la_lista_de_ignorados_recortada_no_borra_lo_que_pasa_del_tope(entorno, monkeypatch):
    hogar, cwd = entorno
    monkeypatch.setattr(instantaneas, 'TOPE_EXCLUIDOS', 3)
    (cwd / '.gitignore').write_text('*.log\n')
    for n in range(6):
        (cwd / f'{n}.log').write_text('registro\n')
    sesion = Sesion(hogar, cwd)
    t = sesion.turno('uno', lambda c, e: (escribir(e, c, '.gitignore', '# nada\n'), escribir(e, c, 'nuevo.py', 'x\n')),
                     llamadas=[['sed', '-i', '', '/log/d', '.gitignore']])
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
    no_cubiertos = {n['ruta'] for n in vista['no_cubiertos']}
    assert {f'{n}.log' for n in range(6)} <= no_cubiertos
    assert not any(c['ruta'].endswith('.log') for c in vista['conflictos'] + vista['restaurables'])
    assert ('nuevo.py', 'borrar') in {(f['ruta'], f['accion']) for f in vista['restaurables']}, \
        'lo que el agente dijo crear sí se borra'
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t, vista=vista)
    assert all((cwd / f'{n}.log').exists() for n in range(6))


@pytest.mark.skipif(os.geteuid() == 0, reason='root lee cualquier carpeta')
def test_una_carpeta_ilegible_es_un_prefijo_y_no_se_borra_su_contenido(entorno):
    hogar, cwd = entorno
    (cwd / 'priv').mkdir()
    (cwd / 'priv' / 'f.txt').write_text('mío\n')
    os.chmod(cwd / 'priv', 0)
    try:
        sesion = Sesion(hogar, cwd)
        t = sesion.turno('uno', lambda c, e: os.chmod(c / 'priv', 0o755), llamadas=[['chmod', '755', 'priv']])
        datos = sesion.task['turnos'][0]['instantanea']
        assert Almacen(hogar, cwd).excluidos(datos['antes'])['ilegibles'] == ['priv/']
        vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
        assert {(n['ruta'], n['motivo']) for n in vista['no_cubiertos']} == {('priv/f.txt', 'ilegible')}
        deshacer.deshacer(hogar, sesion.task, sesion.estado, t, forzar=['priv/f.txt'], vista=vista)
        assert (cwd / 'priv' / 'f.txt').read_text() == 'mío\n'
    finally:
        os.chmod(cwd / 'priv', 0o755)


@pytest.mark.parametrize('como', ['borrado', 'atomico'])
def test_un_0600_que_el_agente_reemplaza_vuelve_0600(entorno, como):
    import stat
    hogar, cwd = entorno
    clave = cwd / 'clave.pem'
    clave.write_text('privada\n')
    os.chmod(clave, 0o600)
    sesion = Sesion(hogar, cwd)

    def t1(c, e):
        if como == 'borrado':
            (c / 'clave.pem').unlink()
            e['changes'].append({'path': 'clave.pem', 'before': sha('privada\n'), 'after': 'DELETED'})
        else:
            (c / 'tmp.pem').write_text('otra\n')
            os.chmod(c / 'tmp.pem', 0o644)
            os.replace(c / 'tmp.pem', c / 'clave.pem')
            e['changes'].append({'path': 'clave.pem', 'before': sha('privada\n'), 'after': sha('otra\n')})
    t = sesion.turno('uno', t1)
    paso = deshacer.deshacer(hogar, sesion.task, sesion.estado, t)
    assert paso['restaurados'] == ['clave.pem']
    assert clave.read_text() == 'privada\n'
    assert stat.S_IMODE(os.stat(clave).st_mode) == 0o600


def test_un_lock_de_ref_viejo_no_bloquea_deshacer(entorno):
    hogar, cwd = entorno
    sesion = Sesion(hogar, cwd)
    t = sesion.turno('uno', lambda c, e: escribir(e, c, 'app.py', 'print(2)\n'))
    almacen = Almacen(hogar, cwd)
    viejo = almacen.dir / 'refs' / 'alfalfa' / 's1' / 'ahora.lock'
    viejo.parent.mkdir(parents=True, exist_ok=True)
    viejo.write_text('')
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
    assert vista['disponible'], vista.get('motivo')
    assert not viejo.exists()


def test_una_edicion_del_usuario_durante_el_turno_en_el_mismo_archivo_no_se_pisa(entorno):
    hogar, cwd = entorno
    sesion = Sesion(hogar, cwd)

    def t1(c, e):
        escribir(e, c, 'app.py', 'print(2)\n')
        (c / 'app.py').write_text('print(2)  # y lo mío\n')   # El usuario, mientras el agente sigue.
    t = sesion.turno('uno', t1)
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
    assert {(c['ruta'], c['motivo']) for c in vista['conflictos']} == {('app.py', 'editado_despues')}
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t, vista=vista)
    assert (cwd / 'app.py').read_text() == 'print(2)  # y lo mío\n'


# MARK: - Tercera revisión del PR 22

@pytest.mark.skipif(os.geteuid() == 0, reason='root lee cualquier carpeta')
def test_una_carpeta_ilegible_con_apostrofo(entorno):
    hogar, cwd = entorno
    carpeta = cwd / "it's"
    carpeta.mkdir()
    (carpeta / 'f.txt').write_text('mío\n')
    os.chmod(carpeta, 0)
    try:
        sesion = Sesion(hogar, cwd)
        t = sesion.turno('uno', lambda c, e: os.chmod(c / "it's", 0o755), llamadas=[['chmod', '755', "it's"]])
        datos = sesion.task['turnos'][0]['instantanea']
        assert Almacen(hogar, cwd).excluidos(datos['antes'])['ilegibles'] == ["it's/"]
        vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
        assert {(n['ruta'], n['motivo']) for n in vista['no_cubiertos']} == {("it's/f.txt", 'ilegible')}
        deshacer.deshacer(hogar, sesion.task, sesion.estado, t, forzar=["it's/f.txt"], vista=vista)
        assert (carpeta / 'f.txt').read_text() == 'mío\n'
    finally:
        os.chmod(carpeta, 0o755)


def test_los_avisos_de_git_con_comillas_y_apostrofos():
    from alfalfa.deshacer.instantaneas import _ilegible
    assert _ilegible("warning: could not open directory 'it's/': Permission denied") == "it's/"
    assert _ilegible('error: open("it\'s \'a\'.txt"): Permission denied') == "it's 'a'.txt"
    assert _ilegible("error: unable to index file 'it's/x'") == "it's/x"
    assert _ilegible('error: algo distinto') is None


def test_con_ignorados_recortados_borra_lo_que_el_runtime_apunta_como_nuevo(entorno, monkeypatch):
    hogar, cwd = entorno
    monkeypatch.setattr(instantaneas, 'TOPE_EXCLUIDOS', 0)
    (cwd / '.gitignore').write_text('*.log\n')
    (cwd / 'a.log').write_text('x')
    sesion = Sesion(hogar, cwd)

    def t1(c, e):
        (c / 'nuevo.py').write_text('x\n')
        e['changes'].append({'path': 'nuevo.py', 'before': 'NEW', 'after': sha('x\n')})  # como write_file
    t = sesion.turno('uno', t1)
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
    assert ('nuevo.py', 'borrar') in {(f['ruta'], f['accion']) for f in vista['restaurables']}


def test_un_chmod_sin_cambiar_el_contenido_se_deshace(entorno):
    import stat
    hogar, cwd = entorno
    clave = cwd / 'clave.pem'
    clave.write_text('privada\n')
    os.chmod(clave, 0o600)
    sesion = Sesion(hogar, cwd)
    t = sesion.turno('uno', lambda c, e: os.chmod(c / 'clave.pem', 0o644), llamadas=[['chmod', '644', 'clave.pem']])
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
    assert {(c['ruta'], c['accion'], c['motivo']) for c in vista['conflictos']} == {
        ('clave.pem', 'permisos', 'orden')}
    paso = deshacer.deshacer(hogar, sesion.task, sesion.estado, t, forzar=['clave.pem'], vista=vista)
    assert paso['restaurados'] == ['clave.pem']
    assert stat.S_IMODE(os.stat(clave).st_mode) == 0o600 and clave.read_text() == 'privada\n'


def test_un_archivo_0600_nunca_se_recrea_con_mas_permisos(entorno, monkeypatch):
    hogar, cwd = entorno
    clave = cwd / 'clave.pem'
    clave.write_text('privada\n')
    os.chmod(clave, 0o600)
    sesion = Sesion(hogar, cwd)

    def t1(c, e):
        (c / 'clave.pem').unlink()
        e['changes'].append({'path': 'clave.pem', 'before': sha('privada\n'), 'after': 'DELETED'})
    t = sesion.turno('uno', t1)
    abiertos = []
    original = os.open

    def vigilado(path, flags, mode=0o777, *args, **kwargs):
        if str(path).endswith('clave.pem') and flags & os.O_CREAT:
            abiertos.append(mode)
        return original(path, flags, mode, *args, **kwargs)
    monkeypatch.setattr(instantaneas.os, 'open', vigilado)
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t)
    assert abiertos == [0o600]


def test_una_carpeta_0700_que_borra_el_agente_vuelve_0700(entorno):
    import shutil
    import stat
    hogar, cwd = entorno
    (cwd / 'secretos').mkdir(mode=0o700)
    os.chmod(cwd / 'secretos', 0o700)
    (cwd / 'secretos' / 'a.txt').write_text('a\n')
    sesion = Sesion(hogar, cwd)

    def t1(c, e):
        shutil.rmtree(c / 'secretos')
        e['changes'].append({'path': 'secretos/a.txt', 'before': sha('a\n'), 'after': 'DELETED'})
    t = sesion.turno('uno', t1)
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t)
    assert stat.S_IMODE(os.stat(cwd / 'secretos').st_mode) == 0o700
    assert (cwd / 'secretos' / 'a.txt').read_text() == 'a\n'


def test_guardar_ref_espera_al_cerrojo(entorno):
    import fcntl
    import threading
    hogar, cwd = entorno
    almacen = Almacen(hogar, cwd)
    commit = almacen.guardar('s1', 'uno')['commit']
    fd = os.open(almacen.dir / 'cerrojo', os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    hilo = threading.Thread(target=almacen.guardar_ref, args=(commit, 's1', 'otra'))
    hilo.start()
    time.sleep(0.3)
    assert hilo.is_alive(), 'update-ref no entra mientras otro tiene el cerrojo'
    os.close(fd)
    hilo.join(5)
    assert 'refs/alfalfa/s1/otra' in almacen.git('for-each-ref', '--format=%(refname)').decode()


def test_lo_que_adopta_una_orden_no_se_restaura_sin_preguntar(entorno):
    hogar, cwd = entorno
    sesion = Sesion(hogar, cwd)

    def t1(c, e):
        escribir(e, c, 'app.py', 'print(2)\n')
        # Mientras corre una orden del agente, el usuario edita app.py; la orden lo adopta como suyo.
        (c / 'app.py').write_text('print(2)  # del usuario\n')
        e['changes'].append({'path': 'app.py', 'before': sha('print(2)\n'),
                             'after': sha('print(2)  # del usuario\n'), 'via': 'run_command'})
    t = sesion.turno('uno', t1, llamadas=[['make']])
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
    assert {(c['ruta'], c['motivo']) for c in vista['conflictos']} == {('app.py', 'adoptado')}
    from alfalfa.deshacer.orden import PROPUESTOS
    assert 'adoptado' not in PROPUESTOS, 'ni la app la trae marcada ni la terminal propone [S/n]'
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t, vista=vista)
    assert (cwd / 'app.py').read_text() == 'print(2)  # del usuario\n'


# MARK: - Cuarta revisión del PR 22

@pytest.mark.skipif(os.geteuid() == 0, reason='root lee cualquier carpeta')
@pytest.mark.parametrize('nombre', ['salto\nx', "it's\nx"])
def test_una_carpeta_ilegible_con_salto_de_linea(entorno, nombre):
    hogar, cwd = entorno
    carpeta = cwd / nombre
    carpeta.mkdir()
    (carpeta / 'f.txt').write_text('mío\n')
    os.chmod(carpeta, 0)
    try:
        sesion = Sesion(hogar, cwd)
        t = sesion.turno('uno', lambda c, e: os.chmod(c / nombre, 0o755), llamadas=[['chmod', '755', nombre]])
        datos = sesion.task['turnos'][0]['instantanea']
        assert Almacen(hogar, cwd).excluidos(datos['antes'])['ilegibles'] == [nombre + '/']
        vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
        assert {(n['ruta'], n['motivo']) for n in vista['no_cubiertos']} == {(nombre + '/f.txt', 'ilegible')}
        deshacer.deshacer(hogar, sesion.task, sesion.estado, t, forzar=[nombre + '/f.txt'], vista=vista)
        assert (carpeta / 'f.txt').read_text() == 'mío\n'
    finally:
        os.chmod(carpeta, 0o755)


def test_un_aviso_de_git_que_no_se_entiende_deja_la_lista_incompleta(entorno, monkeypatch):
    hogar, cwd = entorno
    (cwd / 'previo.txt').write_text('ya estaba\n')
    almacen = Almacen(hogar, cwd)
    original = Almacen.git

    def con_aviso(self, *args, **kwargs):
        salida = original(self, *args, **kwargs)
        if args[:1] == ('ls-files',) and kwargs.get('tolerar') == (0,):
            return salida[0], 'warning: algo que no sé leer\n'
        return salida
    monkeypatch.setattr(Almacen, 'git', con_aviso)
    datos = almacen.guardar('s1', 'uno')
    assert almacen.excluidos(datos['commit'])['incompleta'] is True


def test_con_la_lista_incompleta_no_borra_lo_que_no_estaba(entorno):
    hogar, cwd = entorno
    sesion = Sesion(hogar, cwd)
    t = sesion.turno('uno', lambda c, e: (c / 'generado.txt').write_text('x'), llamadas=[['make']])
    almacen = Almacen(hogar, cwd)
    archivo = almacen.dir / 'excluidos' / sesion.task['turnos'][0]['instantanea']['antes']
    excluidos = json.loads(archivo.read_text())
    archivo.write_text(json.dumps(dict(excluidos, incompleta=True)))
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
    assert {(n['ruta'], n['motivo']) for n in vista['no_cubiertos']} == {('generado.txt', 'sin_registro')}
    assert vista['conflictos'] == []


def test_un_chmod_de_una_carpeta_sola_se_deshace(entorno):
    import stat
    hogar, cwd = entorno
    (cwd / 'secretos').mkdir()
    os.chmod(cwd / 'secretos', 0o700)
    (cwd / 'secretos' / 'a.txt').write_text('a\n')
    sesion = Sesion(hogar, cwd)
    t = sesion.turno('uno', lambda c, e: os.chmod(c / 'secretos', 0o755), llamadas=[['chmod', '755', 'secretos']])
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
    assert {(c['ruta'], c['accion'], c['motivo']) for c in vista['conflictos']} == {
        ('secretos/', 'permisos', 'orden')}
    paso = deshacer.deshacer(hogar, sesion.task, sesion.estado, t, forzar=['secretos/'], vista=vista)
    assert paso['restaurados'] == ['secretos/']
    assert stat.S_IMODE(os.stat(cwd / 'secretos').st_mode) == 0o700


def test_avisa_si_cambian_los_permisos_de_un_ignorado(entorno):
    hogar, cwd = entorno
    (cwd / '.gitignore').write_text('.env\n')
    (cwd / '.env').write_text('A=1\n')
    os.chmod(cwd / '.env', 0o600)
    sesion = Sesion(hogar, cwd)
    t = sesion.turno('uno', lambda c, e: os.chmod(c / '.env', 0o644), llamadas=[['chmod', '644', '.env']])
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
    assert {(n['ruta'], n['motivo']) for n in vista['no_cubiertos']} == {('.env', 'ignorado')}
    assert vista['irreversibles'][-1]['tipo'] == 'no_cubierto'


def test_una_carpeta_0555_que_borra_el_agente_vuelve_con_su_contenido(entorno):
    import shutil
    import stat
    hogar, cwd = entorno
    (cwd / 'solo').mkdir()
    (cwd / 'solo' / 'f.txt').write_text('f\n')
    os.chmod(cwd / 'solo', 0o555)
    sesion = Sesion(hogar, cwd)

    def t1(c, e):
        os.chmod(c / 'solo', 0o755)
        shutil.rmtree(c / 'solo')
        e['changes'].append({'path': 'solo/f.txt', 'before': sha('f\n'), 'after': 'DELETED'})
    t = sesion.turno('uno', t1)
    try:
        paso = deshacer.deshacer(hogar, sesion.task, sesion.estado, t)
        assert not paso['fallos']
        assert (cwd / 'solo' / 'f.txt').read_text() == 'f\n'
        assert stat.S_IMODE(os.stat(cwd / 'solo').st_mode) == 0o555
    finally:
        if (cwd / 'solo').exists():
            os.chmod(cwd / 'solo', 0o755)


# MARK: - Seguimiento del PR 22

@pytest.mark.skipif(os.geteuid() == 0, reason='root atraviesa cualquier carpeta')
@pytest.mark.parametrize('modo', [0o400, 0o600])
def test_una_carpeta_legible_pero_no_atravesable_no_deja_sin_instantanea(entorno, modo):
    hogar, cwd = entorno
    (cwd / 'nx').mkdir()
    (cwd / 'nx' / 'f.txt').write_text('mío\n')
    os.chmod(cwd / 'nx', modo)
    try:
        sesion = Sesion(hogar, cwd)
        sesion.turno('uno', lambda c, e: escribir(e, c, 'app.py', 'print(2)\n'))
        datos = sesion.task['turnos'][0]['instantanea']
        assert datos['antes'] and datos['despues'] and 'errores' not in datos
        assert Almacen(hogar, cwd).excluidos(datos['antes'])['ilegibles'] == ['nx/']
    finally:
        os.chmod(cwd / 'nx', 0o755)


def test_lo_adoptado_sigue_adoptado_aunque_el_agente_lo_escriba_despues(entorno):
    hogar, cwd = entorno
    sesion = Sesion(hogar, cwd)

    def t1(c, e):
        escribir(e, c, 'app.py', 'print(2)\n')
        (c / 'app.py').write_text('print(2)  # del usuario\n')
        e['changes'].append({'path': 'app.py', 'before': sha('print(2)\n'),
                             'after': sha('print(2)  # del usuario\n'), 'via': 'run_command'})
    t = sesion.turno('uno', t1, llamadas=[['make']])
    sesion.turno('dos', lambda c, e: escribir(e, c, 'app.py', 'print(2)  # del usuario\nprint(3)\n'))
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
    assert {(c['ruta'], c['motivo']) for c in vista['conflictos']} == {('app.py', 'adoptado')}


def test_avisa_de_los_permisos_de_un_ignorado_dentro_de_una_carpeta_ignorada(entorno):
    hogar, cwd = entorno
    (cwd / '.gitignore').write_text('secretos/\n')
    (cwd / 'secretos').mkdir()
    (cwd / 'secretos' / '.env').write_text('A=1\n')
    os.chmod(cwd / 'secretos' / '.env', 0o600)
    sesion = Sesion(hogar, cwd)
    t = sesion.turno('uno', lambda c, e: os.chmod(c / 'secretos' / '.env', 0o644),
                     llamadas=[['chmod', '644', 'secretos/.env']])
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
    assert ('secretos/.env', 'ignorado') in {(n['ruta'], n['motivo']) for n in vista['no_cubiertos']}


def test_el_chmod_de_una_carpeta_vacia_se_deshace(entorno):
    import stat
    hogar, cwd = entorno
    (cwd / 'vacia').mkdir()
    os.chmod(cwd / 'vacia', 0o700)
    sesion = Sesion(hogar, cwd)
    t = sesion.turno('uno', lambda c, e: os.chmod(c / 'vacia', 0o755), llamadas=[['chmod', '755', 'vacia']])
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
    assert {(c['ruta'], c['accion']) for c in vista['conflictos']} == {('vacia/', 'permisos')}
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t, forzar=['vacia/'], vista=vista)
    assert stat.S_IMODE(os.stat(cwd / 'vacia').st_mode) == 0o700


@pytest.mark.skipif(os.geteuid() == 0, reason='root lee cualquier carpeta')
def test_un_chmod_000_de_una_carpeta_con_archivos_se_deshace(entorno):
    import stat
    hogar, cwd = entorno
    (cwd / 'datos').mkdir()
    (cwd / 'datos' / 'a.txt').write_text('a\n')
    sesion = Sesion(hogar, cwd)
    t = sesion.turno('uno', lambda c, e: os.chmod(c / 'datos', 0), llamadas=[['chmod', '000', 'datos']])
    try:
        vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
        assert {(c['ruta'], c['accion'], c['motivo']) for c in vista['conflictos']} == {
            ('datos/', 'permisos', 'orden')}
        assert vista['restaurables'] == [] and vista['no_cubiertos'] == []
        deshacer.deshacer(hogar, sesion.task, sesion.estado, t, forzar=['datos/'], vista=vista)
        assert stat.S_IMODE(os.stat(cwd / 'datos').st_mode) == 0o755
        assert (cwd / 'datos' / 'a.txt').read_text() == 'a\n'
    finally:
        os.chmod(cwd / 'datos', 0o755)


def test_restaura_dentro_de_una_carpeta_0555_que_sigue_existiendo(entorno):
    import shutil
    import stat
    hogar, cwd = entorno
    (cwd / 'solo' / 'sub').mkdir(parents=True)
    (cwd / 'solo' / 'sub' / 'f.txt').write_text('f\n')
    os.chmod(cwd / 'solo', 0o555)
    sesion = Sesion(hogar, cwd)

    def t1(c, e):
        os.chmod(c / 'solo', 0o755)
        shutil.rmtree(c / 'solo' / 'sub')
        os.chmod(c / 'solo', 0o555)
        e['changes'].append({'path': 'solo/sub/f.txt', 'before': sha('f\n'), 'after': 'DELETED'})
    t = sesion.turno('uno', t1)
    try:
        paso = deshacer.deshacer(hogar, sesion.task, sesion.estado, t)
        assert not paso['fallos'], paso['fallos']
        assert (cwd / 'solo' / 'sub' / 'f.txt').read_text() == 'f\n'
        assert stat.S_IMODE(os.stat(cwd / 'solo').st_mode) == 0o555, 'vuelve a su modo al terminar'
    finally:
        os.chmod(cwd / 'solo', 0o755)
