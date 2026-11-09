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
        assert guardado['version'] == historial.VERSION and 'messages' not in guardado
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
        assert guardado['version'] == historial.VERSION and 'messages' not in guardado
        assert not {'checks', 'operations', 'unresolved_records', 'resolution_records'} & set(guardado)
        filas = [json.loads(x) for campo in historial.CON_FOTO
                 for x in (directorio / historial.LISTAS[campo]).read_text().splitlines()]
        assert len(filas) == 6 and all(set(c['workspace_context']) == {'$foto'} for c in filas)
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
    assert guardado['version'] == historial.VERSION and 'messages' not in guardado
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
    assert guardado['version'] == historial.VERSION and not {'messages', 'resolution_records'} & set(guardado)
    completo = historial.leer(vieja / 'session.json')
    assert completo['messages'] == mensajes and completo['resolution_records'] == []
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


def comprobar(rt, n, root, exit_code=0, argv=None):
    """Una comprobación como las de run_checks: salida al tope y la foto del espacio."""
    return rt.evidence.record_check(
        {'argv': argv or ['pytest', '-q', f'tests/test_{n}.py'], 'purpose': 'Comprobar.'},
        {'exit_code': exit_code, 'output': f'salida {n} ' + 'o' * 4000, 'workspace_context': foto(root, n),
         'workspace_stable': True, 'execution_complete': True}, rt.state.get('workspace_revision', 0))


def test_session_json_no_crece_con_las_comprobaciones(proyecto):
    """operations, checks y unresolved_records van en JSONL: session.json no crece con ellos.

    Antes cada registro (hasta 4.000 caracteres de salida) quedaba en session.json, que
    se reescribe varias veces por paso: de 0,65 a 1,3 MB en 3 turnos de 60 pasos.
    """
    m = TerminalManager('x')
    try:
        rt = NativeRuntime(m, m.task)
        root = str(proyecto.resolve())
        # Dos que fallan: dos incidencias, que luego se resuelven al pasar.
        for n in (1, 2):
            comprobar(rt, n, root, exit_code=1, argv=['pytest', '-q', f'tests/test_{n}.py'])
            rt.save()
        assert {f['status'] for f in rt.state['unresolved_records'].values()} == {'pending'}
        # El trabajo cambia: las incidencias pasan a obsoletas, cambiándolas por dentro.
        rt.state['workspace_revision'] = 1
        tamaños, diarios = [], {}
        for n in range(3, 203):
            # Una de cada dos repite la que falló: queda un intento de resolver la incidencia.
            comprobar(rt, n, root, argv=['pytest', '-q', 'tests/test_1.py'] if n % 2 else None)
            rt.save()
            if n in (22, 202):
                tamaños.append(rt.path.stat().st_size)
            if n == 102:
                diarios = {nombre: (rt.directory / nombre).read_bytes()
                           for nombre in (*historial.LISTAS.values(), historial.ARCHIVO_INCIDENCIAS)}
        assert {f['status'] for f in rt.state['unresolved_records'].values()} == {'obsolete'}
        rt.state['unresolved_records']['finding-000001']['nota'] = 'vista'
        rt.save()
        tamaños.append(rt.path.stat().st_size)
        # 200 comprobaciones más (800 KB de salida) y session.json sigue igual.
        assert tamaños[2] < tamaños[0] + 1_000 and tamaños[2] < 10_000, tamaños
        texto = rt.path.read_text()
        assert 'salida 150 ' not in texto and 'salida 1 ' not in texto
        guardado = json.loads(texto)
        assert not {'checks', 'operations', 'unresolved_records', 'resolution_records'} & set(guardado)
        assert guardado['historial']['checks']['filas'] == 202
        # Sólo se añade: lo de antes sigue igual al principio de cada JSONL.
        for nombre, antes in diarios.items():
            assert (rt.directory / nombre).read_bytes().startswith(antes), nombre
        # Una incidencia se escribe entera una vez; después, sólo lo que cambió (su estado,
        # su último intento de resolverla): una línea pequeña por cambio, no una por guardado.
        lineas = [json.loads(x) for x in
                  (rt.directory / historial.ARCHIVO_INCIDENCIAS).read_text().splitlines()]
        assert sum('=' in x for x in lineas) == 2 and len(lineas) < 110
        assert all(len(json.dumps(x)) < 200 for x in lineas if '~' in x)
        # Las fotos, una vez cada una, aunque la operación y su comprobación la compartan.
        assert len((rt.directory / historial.FOTOS).read_text().splitlines()) == 202
        # Cada comprobación relacionada con una incidencia abierta deja un intento de resolverla.
        assert len(rt.state['resolution_records']) >= 100
        en_memoria = {campo: rt.state[campo] for campo in (*historial.LISTAS, 'unresolved_records')}
        # Lo que sobra tras un corte (antes de escribir session.json) se ignora y se recorta.
        with open(rt.directory / historial.LISTAS['checks'], 'ab') as salida:
            salida.write(b'{"id": "check-a medias')
    finally:
        m.close()
    m = TerminalManager('Sigue.', resume=m.task['id'])
    try:
        rt = NativeRuntime(m, m.task)
        for campo, valor in en_memoria.items():
            assert rt.state[campo] == valor, campo
        assert rt.state['checks'][5]['workspace_context'] == foto(root, 6)
        assert rt.state['unresolved_records']['finding-000001']['nota'] == 'vista'
        comprobar(rt, 400, root)
        rt.save()
        assert b'a medias' not in (rt.directory / historial.LISTAS['checks']).read_bytes()
        assert len(historial.leer(rt.path)['checks']) == 203
        # Quien juzga la sesión desde fuera (Chiyo) no carga la conversación ni las fotos.
        evidencia = historial.leer_evidencia(rt.path)
        assert 'messages' not in evidencia and len(evidencia['operations']) == 203
        assert evidencia['checks'][0]['workspace_context'] == {'$foto': 0}
        assert evidencia['unresolved_records'] == rt.state['unresolved_records']
        assert historial.leer_evidencia(rt.path, fotos=True)['checks'][-1]['workspace_context'] == foto(root, 400)
    finally:
        m.close()


def test_anotar_un_intento_guardado_se_ve_al_reabrir(proyecto):
    """bajo_demanda anota el intento en el sandbox ya guardado: sustituye el registro."""
    from alfalfa.permisos.bajo_demanda import OnRequestPolicy
    m = TerminalManager('x')
    try:
        rt = NativeRuntime(m, m.task)
        root = str(proyecto.resolve())
        for n in range(1, 6):
            comprobar(rt, n, root)
        rt.save()
        ruta = rt.directory / historial.LISTAS['operations']
        antes = ruta.read_bytes()
        objetivo = rt.state['operations'][3]
        OnRequestPolicy._annotate(rt, {'operation_id': objetivo['id']}, reintento='rechazado')
        assert rt.state['operations'][3]['reintento'] == 'rechazado'
        # El archivo no se reescribe: se añade «vuelve a tener 3» y los dos últimos.
        despues = ruta.read_bytes()
        assert despues.startswith(antes)
        nuevas = [json.loads(x) for x in despues[len(antes):].decode().splitlines()]
        assert nuevas[0] == {'$hasta': 3} and [x['id'] for x in nuevas[1:]] == [
            rt.state['operations'][3]['id'], rt.state['operations'][4]['id']]
        operaciones = historial.leer(rt.path)['operations']
        assert len(operaciones) == 5 and operaciones[3]['reintento'] == 'rechazado'
        assert operaciones[3]['workspace_context'] == foto(root, 4)
        # Quitar también se ve.
        del rt.state['operations'][0]
        rt.save()
        assert [x['id'] for x in historial.leer(rt.path)['operations']] == [
            x['id'] for x in rt.state['operations']]
    finally:
        m.close()


def test_una_sesion_v3_se_convierte_sola(proyecto):
    """La 3 llevaba operations, checks y unresolved_records dentro de session.json."""
    m = TerminalManager('x')
    try:
        directorio = m.directory / 'native' / m.task['id']
        directorio.mkdir(parents=True, exist_ok=True)
        root = str(proyecto.resolve())
        fotos = [foto(root, n) for n in range(2)]
        lineas_fotos = [json.dumps({'n': 0, 'base': None, 'cambios': historial.diferencia({}, fotos[0])}),
                        json.dumps({'n': 1, 'base': 0, 'cambios': historial.diferencia(fotos[0], fotos[1])})]
        (directorio / historial.FOTOS).write_text(''.join(x + '\n' for x in lineas_fotos))
        mensajes = [{'role': 'user', 'content': 'Hola.'}, {'role': 'assistant', 'content': 'Hola.'}]
        (directorio / historial.MENSAJES).write_text(''.join(json.dumps(x) + '\n' for x in mensajes))
        operaciones = [{'id': f'operation-{n:06d}', 'argv': ['true'], 'cwd': root, 'purpose': 'p', 'revision': 0,
                        'exit_code': 0, 'output': 'ok' * 1000, 'session_id': m.task['id'], 'project': '',
                        'tool': 'run_checks', 'workspace_context': {'$foto': n}} for n in range(2)]
        comprobaciones = [dict(o, id=f'check-{n:06d}', operation_id=o['id']) for n, o in enumerate(operaciones)]
        incidencias = {'finding-000001': {'id': 'finding-000001', 'status': 'obsolete', 'source': 'tool',
                                          'summary': 'falló', 'original': {'cause': 'x', 'output': 'mal'}}}
        v3 = {'version': 3, 'evidence_version': 2, 'pending': [], 'plan': 'Plan.', 'changes': [],
              'primer_mensaje': {'role': 'system', 'content': 's'},
              'operations': operaciones, 'checks': comprobaciones, 'unresolved_records': incidencias,
              'resolution_records': [],
              'historial': {'archivo': historial.MENSAJES, 'mensajes': 3,
                            'bytes': (directorio / historial.MENSAJES).stat().st_size,
                            'fotos_archivo': historial.FOTOS, 'fotos': 2,
                            'fotos_bytes': (directorio / historial.FOTOS).stat().st_size}}
        (directorio / 'session.json').write_text(json.dumps(v3))
        conversacion = (directorio / historial.MENSAJES).read_bytes()
        inodo = (directorio / historial.MENSAJES).stat().st_ino
        rt = NativeRuntime(m, m.task)
        assert [c['workspace_context'] for c in rt.state['checks']] == fotos
        assert rt.state['unresolved_records']['finding-000001']['summary'] == 'falló'
        # Ya en la 4, sin esperar al primer paso; la conversación y las fotos, sin tocar.
        guardado = json.loads((directorio / 'session.json').read_text())
        assert guardado['version'] == historial.VERSION == 4
        assert not {'checks', 'operations', 'unresolved_records', 'resolution_records'} & set(guardado)
        assert (directorio / historial.MENSAJES).read_bytes() == conversacion
        assert (directorio / historial.MENSAJES).stat().st_ino == inodo
        assert len((directorio / historial.FOTOS).read_text().splitlines()) == 2
        completo = historial.leer(directorio / 'session.json')
        assert completo['messages'] == [{'role': 'system', 'content': 's'}] + mensajes
        assert [o['workspace_context'] for o in completo['operations']] == fotos
        assert completo['checks'] == [dict(c, workspace_context=fotos[n]) for n, c in enumerate(comprobaciones)]
        assert completo['unresolved_records'] == rt.state['unresolved_records']
        # Abrirla otra vez no duplica nada.
        def tamaños():
            return {n: (directorio / n).stat().st_size for n in (*historial.LISTAS.values(), historial.FOTOS,
                                                                historial.ARCHIVO_INCIDENCIAS)
                    if (directorio / n).exists()}
        antes = tamaños()
        assert len(antes) == 4  # sin resoluciones, resolution_records.jsonl ni se crea
        rt = NativeRuntime(m, m.task)
        rt.save()
        assert tamaños() == antes
    finally:
        m.close()
