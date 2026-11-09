"""La sesión nativa en disco: conversación en un JSONL al que sólo se añade.

Una sesión larga llegó a 384 MB de session.json y 190 MB de handoff.json, que se
reescribían enteros tras cada herramienta. Ahora la conversación y las fotos del
espacio de trabajo van en JSONL a los que sólo se añade, session.json queda
pequeño y handoff.json lleva lo último de cada lista con el total aparte.
"""
import asyncio
import fcntl
import json
import os
import subprocess
from unittest.mock import patch

import pytest

from alfalfa.evidencia import registro
from alfalfa.proveedores.nativo import herramientas, historial
from alfalfa.proveedores.nativo.historial import Mensajes
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.terminal import TerminalManager


@pytest.fixture
def proyecto(tmp_path, monkeypatch):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    subprocess.run(['git', 'init', '-q'], cwd=raiz, check=True)
    (raiz / 'grande.txt').write_text(''.join(f'línea {i} ' + 'x' * 70 + '\n' for i in range(3000)))
    monkeypatch.chdir(raiz)
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path / 'alfalfa'))
    return raiz


LEIDOS = {'n': 0}


def guion(pasos):
    """El modelo lee `grande.txt` `pasos` veces (siempre otro trozo) y termina el turno."""
    estado = {'n': 0}

    async def request(self, client, endpoint, payload, **kwargs):
        if endpoint == '/api/show':
            return {'capabilities': ['tools'], 'model_info': {'x.context_length': 1048576}}
        estado['n'] += 1
        if estado['n'] > pasos:
            estado['n'] = 0
            mensaje = {'role': 'assistant', 'content': 'Leído.'}
        else:
            LEIDOS['n'] += 1  # otro trozo cada vez: repetir la misma lectura sería un bucle
            mensaje = {'role': 'assistant', 'content': '', 'tool_calls': [{'function': {
                'name': 'read_file', 'arguments': {'path': 'grande.txt', 'offset': LEIDOS['n'] * 7, 'limit': 150}}}]}
        return dict(message=mensaje, done=True, done_reason='stop', prompt_eval_count=900, eval_count=10)
    return request


def correr(m, prompt, pasos):
    with patch.object(NativeRuntime, 'request', guion(pasos)):
        asyncio.run(asyncio.wait_for(m.run(prompt), 120))


def test_una_sesion_larga_crece_en_linea_y_no_se_reescribe(proyecto, monkeypatch):
    escrito = {}
    original = herramientas.atomic_write

    def contar(path, data, mode=0o644):
        escrito[path.name] = escrito.get(path.name, 0) + len(data)
        return original(path, data, mode)
    monkeypatch.setattr(herramientas, 'atomic_write', contar)
    monkeypatch.setattr(historial, 'atomic_write', contar)
    m = TerminalManager('Lee el archivo.')
    try:
        carpeta = m.directory / 'native' / m.task['id']
        diario = carpeta / historial.MENSAJES
        anterior, inodo, crecimientos, por_turno, sesion = b'', None, [], [], []
        for turno in range(4):
            antes = dict(escrito)
            correr(m, f'Turno {turno}.', 25)
            contenido = diario.read_bytes()
            # Sólo se añade: lo de antes sigue igual al principio, en el mismo archivo.
            assert contenido.startswith(anterior) and len(contenido) > len(anterior)
            assert inodo is None or diario.stat().st_ino == inodo
            inodo = diario.stat().st_ino
            crecimientos.append(len(contenido) - len(anterior))
            anterior = contenido
            por_turno.append(escrito.get('session.json', 0) - antes.get('session.json', 0))
            sesion.append((carpeta / 'session.json').stat().st_size)
            assert escrito.get(historial.MENSAJES, 0) == 0  # nunca se reescribió
        # Cada turno añade lo mismo: el disco crece en línea con la conversación.
        assert max(crecimientos) < 1.2 * min(crecimientos)
        # session.json no lleva la conversación: no crece con ella, ni lo que se escribe de él por turno.
        assert sesion[-1] < sesion[0] + 20_000 and sesion[-1] < len(anterior) / 10
        # (Antes cada guardado escribía la conversación entera: el turno 4 escribía 4 veces el 1.)
        assert por_turno[-1] < 2 * por_turno[0]
        guardado = json.loads((carpeta / 'session.json').read_text())
        assert guardado['version'] == 3 and 'messages' not in guardado
        assert 'línea 1000 ' not in (carpeta / 'session.json').read_text()
        assert guardado['historial']['bytes'] == len(anterior)
        estado = historial.leer(carpeta / 'session.json')
        assert len(estado['messages']) == guardado['historial']['mensajes']
        assert sum(m['role'] == 'tool' for m in estado['messages']) == 100
        # Reanudar: el modelo vuelve a ver la conversación entera, leída del JSONL.
        vistos = []

        async def request(self, client, endpoint, payload, **kwargs):
            if endpoint == '/api/show':
                return {'capabilities': ['tools'], 'model_info': {'x.context_length': 1048576}}
            vistos.append(payload['messages'])
            return dict(message={'role': 'assistant', 'content': 'Sigo.'}, done=True, done_reason='stop',
                        prompt_eval_count=900, eval_count=10)
    finally:
        m.close()
    m = TerminalManager('Sigue.', resume=m.task['id'])
    try:
        with patch.object(NativeRuntime, 'request', request):
            asyncio.run(asyncio.wait_for(m.run('Sigue.'), 60))
        textos = [x['content'] for x in vistos[0] if x['role'] == 'user']
        assert textos[:4] == ['Turno 0.', 'Turno 1.', 'Turno 2.', 'Turno 3.']
        assert sum(x['role'] == 'tool' for x in vistos[0]) == 100
    finally:
        m.close()


def test_cambiar_un_mensaje_guardado_reescribe_el_historial(proyecto):
    m = TerminalManager('x')
    try:
        rt = NativeRuntime(m, m.task)
        for texto in ('sistema', 'uno', 'dos', 'tres'):
            rt.state['messages'].append({'role': 'user', 'content': texto, 'thinking': 'secreto'})
        rt.save()
        ruta = rt.directory / historial.MENSAJES
        assert b'secreto' not in ruta.read_bytes()
        # El de sistema se sustituye en cada turno: va en session.json y no toca el JSONL.
        antes = ruta.read_bytes()
        rt.state['messages'][0] = {'role': 'system', 'content': 'nuevo'}
        rt.save()
        assert ruta.read_bytes() == antes
        rt.state['messages'][2] = {'role': 'user', 'content': 'DOS'}
        del rt.state['messages'][3]
        rt.save()
        textos = [x['content'] for x in historial.leer(rt.path)['messages']]
        assert textos == ['nuevo', 'uno', 'DOS']
        # Una lista nueva (un subagente con contexto) se adopta y se escribe entera.
        rt.state['messages'] = [{'role': 'system', 'content': ''}, {'role': 'user', 'content': 'otra'}]
        rt.save()
        assert isinstance(rt.state['messages'], Mensajes)
        rt.state['messages'].append({'role': 'assistant', 'content': 'vale'})
        rt.save()
        assert [x['content'] for x in historial.leer(rt.path)['messages']] == ['', 'otra', 'vale']
    finally:
        m.close()


def test_lo_que_sobra_tras_un_corte_se_ignora_y_se_recorta(proyecto):
    m = TerminalManager('x')
    try:
        rt = NativeRuntime(m, m.task)
        rt.state['messages'] += [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'uno'}]
        rt.save()
        ruta = rt.directory / historial.MENSAJES
        with open(ruta, 'ab') as salida:
            salida.write(b'{"role": "user", "content": "a medias')  # se cortó antes de session.json
        rt = NativeRuntime(m, m.task)
        assert [x['content'] for x in rt.state['messages']] == ['s', 'uno']
        rt.state['messages'].append({'role': 'assistant', 'content': 'dos'})
        rt.save()
        assert [x['content'] for x in historial.leer(rt.path)['messages']] == ['s', 'uno', 'dos']
        assert b'a medias' not in ruta.read_bytes()
        # Lo contrario (falta lo que session.json dice que se escribió) no se disimula.
        with open(ruta, 'r+b') as salida:
            salida.truncate(5)
        with pytest.raises(historial.HistorialIncompleto):
            NativeRuntime(m, m.task)
    finally:
        m.close()


def foto(root, cambio=0):
    """Una foto del espacio de trabajo con forma real (cientos de archivos)."""
    archivos = {f'{root}/src/m{i}.py': [f'{i + (cambio if i == 7 else 0):064x}', 33188, i, 1, 1]
                for i in range(300)}
    return {'root': root, 'git': {'head': None, 'status': ''},
            'watch': {'files': archivos, 'shared': {}, 'directories': {root: sorted(archivos)}, 'ignored': []},
            'sources': dict(archivos)}


def test_las_sesiones_viejas_se_convierten_sin_perder_nada(proyecto):
    m = TerminalManager('x')
    try:
        directorio = m.directory / 'native' / m.task['id']
        directorio.mkdir(parents=True, exist_ok=True)
        root = str(proyecto.resolve())
        fotos = [foto(root, n) for n in range(3)]
        operaciones = [{'id': f'operation-{n:06d}', 'argv': ['true'], 'cwd': root, 'purpose': 'p', 'revision': 0,
                        'exit_code': 0, 'output': 'ok', 'session_id': m.task['id'], 'project': '',
                        'tool': 'run_checks', 'workspace_context': fotos[n]} for n in range(3)]
        comprobaciones = [dict(o, id=f'check-{n:06d}', operation_id=o['id']) for n, o in enumerate(operaciones)]
        no_relevantes = [{'id': f'resolution-{n:06d}', 'finding_id': 'finding-000001', 'status': 'not_relevant',
                          'check_evidence': {'output': 'y' * 100}} for n in range(1, 41)]
        resuelta = {'id': 'resolution-000041', 'finding_id': 'finding-000001', 'status': 'pending',
                    'check_evidence': {'output': 'z'}}
        mensajes = [{'role': 'system', 'content': 'viejo'}, {'role': 'user', 'content': 'Hola.'},
                    {'role': 'assistant', 'content': 'Hola, ¿qué hago?'},
                    {'role': 'tool', 'tool_name': 'read_file', 'content': 'w' * 5000}]
        viejo = {'version': 2, 'evidence_version': 2, 'messages': mensajes, 'pending': [], 'plan': 'Plan.',
                 'changes': [], 'operations': operaciones, 'checks': comprobaciones,
                 'resolution_records': no_relevantes + [resuelta], 'checkpoint': {'through': 2, 'text': 'r'}}
        (directorio / 'session.json').write_text(json.dumps(viejo))
        tamaño_viejo = (directorio / 'session.json').stat().st_size
        rt = NativeRuntime(m, m.task)
        # En memoria, todo como antes.
        assert rt.state['messages'] == mensajes and rt.state['plan'] == 'Plan.'
        assert [c['workspace_context'] for c in rt.state['checks']] == fotos
        assert rt.state['resolution_records'] == [resuelta]
        conteo = rt.evidence.report()['resolution_counts']
        assert conteo['not_relevant'] == 40 and conteo['pending'] == 1
        # En disco, ya en el formato nuevo y sin esperar al primer paso.
        guardado = json.loads((directorio / 'session.json').read_text())
        assert guardado['version'] == 3 and 'messages' not in guardado
        assert all(set(c['workspace_context']) == {'$foto'} for c in guardado['checks'] + guardado['operations'])
        assert guardado['checkpoint'] == {'through': 2, 'text': 'r'}
        assert (directorio / 'session.json').stat().st_size < tamaño_viejo / 3
        # Cada foto ocupa en fotos.jsonl lo que cambió respecto de la anterior.
        lineas = (directorio / historial.FOTOS).read_text().splitlines()
        assert all(len(linea) < len(lineas[0]) / 20 for linea in lineas[1:])
        archivadas = [json.loads(x) for x in (directorio / registro.ARCHIVO_NO_RELEVANTES).read_text().splitlines()]
        assert archivadas == no_relevantes
        completo = historial.leer(directorio / 'session.json')
        assert completo['messages'] == mensajes
        assert [o['workspace_context'] for o in completo['operations']] == fotos
        # Abrirla otra vez no duplica nada.
        rt = NativeRuntime(m, m.task)
        assert rt.evidence.report()['resolution_counts']['not_relevant'] == 40
        assert len((directorio / registro.ARCHIVO_NO_RELEVANTES).read_text().splitlines()) == 40
        assert len((directorio / historial.FOTOS).read_text().splitlines()) == len(lineas)
        rt.save()
        assert len((directorio / historial.FOTOS).read_text().splitlines()) == len(lineas)
    finally:
        m.close()


def test_handoff_acota_todo_lo_que_crece(proyecto):
    m = TerminalManager('x')
    try:
        rt = NativeRuntime(m, m.task)
        root = str(proyecto.resolve())
        incidencias, bloqueos, resoluciones, comprobaciones = {}, {}, [], []
        for n in range(1, 61):
            ident = f'finding-{n:06d}'
            incidencias[ident] = {'id': ident, 'status': 'pending', 'source': 'check', 'summary': f'fallo {n}',
                                  'original': {'cause': 'nonzero_exit', 'output': 'x' * 2000}}
            if n % 2:
                bloqueos[ident] = f'fallo {n}'
            resoluciones.append({'id': f'resolution-{n:06d}', 'finding_id': ident, 'status': 'pending'})
            comprobaciones.append({'id': f'check-{n:06d}', 'argv': ['false', str(n)], 'cwd': root, 'exit_code': 1,
                                   'output': 'mal', 'revision': 0, 'workspace_context': foto(root)})
        rt.state.update(checks=comprobaciones, operations=list(comprobaciones), resolution_records=resoluciones,
                        resolutions_not_relevant=500)
        with patch.object(registro.RunEvidence, '_refresh_incidents', lambda self: incidencias):
            rt.state.update(unresolved_records=incidencias, unresolved=bloqueos)
            rt.evidence.handoff()
        valor = json.loads((rt.directory / 'handoff.json').read_text())
        for clave, total in (('checks', 60), ('operations', 60), ('blockers', 30),
                             ('unresolved_records', 60), ('resolution_records', 560)):
            assert len(valor[clave]) == 20 and valor[clave + '_total'] == total, clave
        assert list(valor['blockers'])[-1] == 'finding-000059'
        # Las incidencias que se enseñan como bloqueos están; el resto, las más recientes.
        assert set(valor['blockers']) <= set(valor['unresolved_records'])
        informe = valor['verification']
        assert len(informe['check_failures']) == 20 and informe['check_failures_total'] >= 60
        texto = json.dumps(valor)
        assert 'workspace_context' not in texto and len(texto) < 200_000
    finally:
        m.close()


def test_una_comprobacion_sin_relacion_solo_se_cuenta(proyecto):
    m = TerminalManager('x')
    try:
        rt = NativeRuntime(m, m.task)
        root = str(proyecto.resolve())
        rt.state['unresolved_records'] = {
            f'finding-{n:06d}': {'id': f'finding-{n:06d}', 'status': 'pending', 'source': 'check',
                                 'session_id': m.task['id'], 'project': '', 'cwd': root, 'argv': ['false', str(n)],
                                 'revision': 0, 'original': {'cause': 'nonzero_exit'}, 'summary': 'x'}
            for n in range(1, 11)}
        check = {'id': 'check-000001', 'session_id': m.task['id'], 'project': '', 'cwd': root, 'argv': ['true'],
                 'exit_code': 0, 'output': 'ok' * 1000, 'revision': 0}
        rt.evidence._record_resolution_attempts(check)
        assert rt.state['resolution_records'] == [] and rt.state['resolutions_not_relevant'] == 10
        assert rt.evidence.report()['resolution_counts']['not_relevant'] == 10
        # Una relacionada que no la resuelve no copia la salida: la tiene su comprobación.
        rt.evidence._record_resolution_attempts(dict(check, id='check-000002', argv=['false', '3'], exit_code=1))
        fila, = rt.state['resolution_records']
        assert fila['finding_id'] == 'finding-000003' and fila['status'] != 'resolved'
        assert 'output' not in fila['check_evidence'] and len(fila['check_evidence']['output_sha256']) == 64
    finally:
        m.close()


def test_bifurcar_e_importar_escriben_el_formato_nuevo(proyecto, tmp_path):
    from alfalfa.sesiones import bifurcar
    m = TerminalManager('x')
    try:
        correr(m, 'Primero.', 3)
        correr(m, 'Segundo.', 3)
    finally:
        m.close()
    base = tmp_path / 'alfalfa' / 'sesiones'
    t1 = m.task['turnos'][0]
    nuevo = bifurcar(base, m.task['id'], t1['id'])
    carpeta = base / nuevo / 'native' / nuevo
    guardado = json.loads((carpeta / 'session.json').read_text())
    assert guardado['version'] == 3 and 'messages' not in guardado
    estado = historial.leer(carpeta / 'session.json')
    assert len(estado['messages']) == t1['hasta']
    assert [x['content'] for x in estado['messages'] if x['role'] == 'user'] == ['Primero.']
    assert oct(os.stat(carpeta / historial.MENSAJES).st_mode & 0o777) == '0o600'


def sesion_vieja(carpeta, root):
    """Una sesión v2 terminada, como las dejaba AlfalfaCode 0.99.0, con su handoff.json."""
    directorio = carpeta / 'native' / carpeta.name
    directorio.mkdir(parents=True)
    (carpeta / 'task.json').write_text(json.dumps({'id': carpeta.name, 'status': 'completed'}))
    comprobaciones = [{'id': f'check-{n:06d}', 'argv': ['false', str(n)], 'cwd': root, 'exit_code': 1,
                       'output': 'mal ' * 500, 'revision': 0, 'workspace_context': foto(root, n)} for n in range(60)]
    no_relevantes = [{'id': f'resolution-{n:06d}', 'finding_id': 'finding-000001', 'status': 'not_relevant',
                      'check_evidence': {'output': 'y' * 2000}} for n in range(300)]
    mensajes = [{'role': 'system', 'content': 's'}] + [{'role': 'tool', 'tool_name': 'read_file',
                                                        'content': 'w' * 3000} for _ in range(50)]
    (directorio / 'session.json').write_text(json.dumps({
        'version': 2, 'evidence_version': 2, 'messages': mensajes, 'pending': [], 'plan': '', 'changes': [],
        'operations': comprobaciones, 'checks': comprobaciones, 'resolution_records': no_relevantes}))
    (directorio / 'handoff.json').write_text(json.dumps({
        'version': 1, 'checks': comprobaciones[-20:], 'checks_total': 60, 'operations': comprobaciones[-20:],
        'operations_total': 60, 'blockers': {f'finding-{n:06d}': 'fallo' for n in range(80)},
        'unresolved_records': {f'finding-{n:06d}': {'id': f'finding-{n:06d}', 'original': {'output': 'x' * 2000}}
                               for n in range(80)},
        'resolution_records': no_relevantes, 'verification': {'status': 'failed', 'check_failures': comprobaciones}}))
    return directorio, mensajes, no_relevantes


def test_aligerar_todas_las_sesiones_de_una_vez(proyecto, tmp_path, capsys):
    from alfalfa.terminal import main
    root = str(proyecto.resolve())
    sesiones = tmp_path / 'alfalfa' / 'sesiones'
    vieja, mensajes, no_relevantes = sesion_vieja(sesiones / 'vieja', root)
    abierta, _, _ = sesion_vieja(sesiones / 'abierta', root)
    antes = sum(p.stat().st_size for p in vieja.iterdir())
    # Una sesión en marcha tiene su cerrojo tomado (como TerminalManager): no se toca.
    en_marcha = os.open(sesiones / 'abierta' / 'lock', os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(en_marcha, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        intacta = (abierta / 'session.json').read_bytes()
        assert main(['--aligerar-sesiones']) == 0
        salida = capsys.readouterr().out
        assert 'abierta  en marcha: no se toca' in salida and 'vieja  ' in salida and 'se liberan' in salida
        assert 'resoluciones sin relación guardadas aparte ocupan' in salida
        assert (abierta / 'session.json').read_bytes() == intacta
    finally:
        os.close(en_marcha)
    guardado = json.loads((vieja / 'session.json').read_text())
    assert guardado['version'] == 3 and 'messages' not in guardado and guardado['resolution_records'] == []
    assert historial.leer(vieja / 'session.json')['messages'] == mensajes
    archivadas = (vieja / registro.ARCHIVO_NO_RELEVANTES).read_text().splitlines()
    assert [json.loads(x) for x in archivadas] == no_relevantes
    entrega = json.loads((vieja / 'handoff.json').read_text())
    assert len(entrega['blockers']) == 20 and entrega['blockers_total'] == 80
    assert len(entrega['resolution_records']) == 20 and entrega['resolution_records_total'] == 300
    assert entrega['checks_total'] == 60 and entrega['verification']['check_failures_total'] == 60
    assert 'workspace_context' not in json.dumps(entrega)
    despues = sum(p.stat().st_size for p in vieja.iterdir() if p.name != registro.ARCHIVO_NO_RELEVANTES)
    assert despues < antes / 5
    # Otra vez: ya está todo ligero y no cambia nada.
    copia = {p.name: p.read_bytes() for p in vieja.iterdir()}
    assert main(['--aligerar-sesiones', '--json']) == 0
    filas = {f['id']: f for f in json.loads(capsys.readouterr().out)['sesiones']}
    assert filas['vieja']['antes'] == filas['vieja']['despues'] and filas['abierta']['estado'] == 'aligerada'
    assert {p.name: p.read_bytes() for p in vieja.iterdir()} == copia
    # Lo guardado aparte no lo lee nadie: se puede descartar, y el número se conserva.
    assert main(['--aligerar-sesiones', '--descartar-no-relevantes']) == 0
    assert not (vieja / registro.ARCHIVO_NO_RELEVANTES).exists()
    assert json.loads((vieja / 'session.json').read_text())['resolutions_not_relevant'] == 300
    with pytest.raises(SystemExit):
        main(['--descartar-no-relevantes'])
