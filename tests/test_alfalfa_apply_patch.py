"""apply_patch completo en AlfalfaCode.

Los escenarios de fixtures/apply_patch_codex son los portables de Codex
(codex-rs/apply-patch/tests/fixtures/scenarios, Apache-2.0, NOTICE junto a
ellos): input/, patch.txt y expected/. Se aplican con la herramienta real del
motor sobre un TerminalManager real.
"""
import asyncio
import json
import re
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from alfalfa import apply_patch as modulo
from alfalfa.apply_patch import ParchInvalido, leer
from alfalfa.proveedores.nativo.herramientas import ON_REQUEST_TOOLS, TOOLS, digest
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.terminal import TerminalManager

ESCENARIOS = Path(__file__).with_name('fixtures') / 'apply_patch_codex'
FALLAN = {'005_rejects_empty_patch', '006_rejects_missing_context', '007_rejects_missing_file_delete',
          '008_rejects_empty_update_hunk', '009_requires_existing_file_for_update', '012_delete_directory_fails',
          '013_rejects_invalid_hunk_header',
          # Diferencia deliberada: AlfalfaCode es todo o nada; Codex deja created.txt.
          '015_failure_after_partial_success_leaves_changes'}


def arbol(raiz):
    if not raiz.exists():
        return {}
    return {str(p.relative_to(raiz)): p.read_bytes() for p in sorted(raiz.rglob('*')) if p.is_file()}


@pytest.fixture
def espacio(tmp_path, monkeypatch):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    monkeypatch.chdir(raiz)
    m = TerminalManager('Edita.', home=tmp_path / 'alfalfa')
    rt = m.runtime()
    yield raiz, rt
    m.close()


def aplicar(rt, texto):
    return asyncio.run(rt.tools.call('apply_patch', {'input': texto}))


@pytest.mark.parametrize('nombre', sorted(p.name for p in ESCENARIOS.iterdir() if p.is_dir()))
def test_escenarios_portables_de_codex(espacio, nombre):
    raiz, rt = espacio
    escenario = ESCENARIOS / nombre
    if (escenario / 'input').exists():
        shutil.copytree(escenario / 'input', raiz, dirs_exist_ok=True)
    antes = arbol(raiz)
    texto = (escenario / 'patch.txt').read_text()
    if nombre in FALLAN:
        with pytest.raises(ValueError):
            aplicar(rt, texto)
        assert arbol(raiz) == antes, 'un parche que falla no escribe nada'
    else:
        resultado = aplicar(rt, texto)
        assert resultado['summary'].startswith('Parche aplicado')
        assert arbol(raiz) == arbol(escenario / 'expected')


def test_lectura_de_la_gramatica():
    bloques = leer('*** Begin Patch\n*** Add File: a/b.py\n+x\n+y\n*** Delete File: c.py\n'
                   '*** Update File: d.py\n*** Move to: e.py\n@@ def f():\n-    pass\n+    return 1\n*** End Patch')
    assert [(b.tipo, b.ruta, b.destino) for b in bloques] == [('add', 'a/b.py', None), ('delete', 'c.py', None),
                                                              ('update', 'd.py', 'e.py')]
    assert bloques[0].contenido == 'x\ny\n'
    trozo, = bloques[2].trozos
    assert (trozo.contexto, trozo.viejas, trozo.nuevas) == ('def f():', ['    pass'], ['    return 1'])
    # Heredoc tolerado, como el modo Lenient de Codex.
    assert leer("<<'EOF'\n*** Begin Patch\n*** Delete File: x\n*** End Patch\nEOF")[0].ruta == 'x'
    assert leer('*** Begin Patch\n*** End Patch') == []
    for malo, texto in [('bad', 'primera línea'), ('*** Begin Patch\nbad', 'última línea'),
                        ('*** Begin Patch\n*** Update File: t.py\n*** End Patch', 'está vacío'),
                        ('*** Begin Patch\n*** Frobnicate File: x\n*** End Patch', 'no es una cabecera'),
                        ('*** Begin Patch\n*** Update File: t.py\n@@\nsin prefijo\n*** End Patch', 'Línea inesperada')]:
        with pytest.raises(ParchInvalido, match=texto):
            leer(malo)


def test_varios_bloques_sobre_el_mismo_archivo_y_finales_de_linea(espacio):
    raiz, rt = espacio
    (raiz / 'a.txt').write_bytes(b'uno\r\ndos\r\ntres\r\n')
    aplicar(rt, '*** Begin Patch\n*** Update File: a.txt\n@@\n-uno\n+UNO\n*** Update File: a.txt\n@@\n-tres\n+TRES\n'
                '*** End Patch')
    assert (raiz / 'a.txt').read_bytes() == b'UNO\r\ndos\r\nTRES\r\n'


def test_tolerancia_de_espacios_y_puntuacion(espacio):
    raiz, rt = espacio
    (raiz / 'a.py').write_text('def f():\n    return “hola”   \n')
    aplicar(rt, '*** Begin Patch\n*** Update File: a.py\n@@ def f():\n-    return "hola"\n+    return "adiós"\n'
                '*** End Patch')
    assert (raiz / 'a.py').read_text() == 'def f():\n    return "adiós"\n'


@pytest.mark.parametrize('ruta', ['../fuera.txt', '/etc/hosts', '.git/config', '.env', 'sub/../../x', 'enlace/x.txt'])
def test_rutas_fuera_privadas_o_por_enlaces_no_se_tocan(espacio, tmp_path, ruta):
    raiz, rt = espacio
    (raiz / '.git').mkdir()
    fuera = tmp_path / 'externo'
    fuera.mkdir()
    (raiz / 'enlace').symlink_to(fuera)
    (raiz / 'bien.txt').write_text('a\n')
    antes = arbol(raiz)
    with pytest.raises(ValueError):
        aplicar(rt, '*** Begin Patch\n*** Add File: bien2.txt\n+b\n*** Add File: ' + ruta + '\n+x\n*** End Patch')
    assert arbol(raiz) == antes and not any(fuera.iterdir()) and not (tmp_path / 'fuera.txt').exists()


def test_un_cambio_ajeno_durante_el_calculo_no_se_pisa(espacio):
    raiz, rt = espacio
    (raiz / 'a.txt').write_text('a\n')
    original = modulo.planificar

    def con_cambio(bloques, leer_texto):
        plan = original(bloques, leer_texto)
        (raiz / 'a.txt').write_text('ajeno\n')
        return plan
    with patch.object(modulo, 'planificar', con_cambio):
        with pytest.raises(ValueError, match='cambió durante el parche'):
            aplicar(rt, '*** Begin Patch\n*** Update File: a.txt\n@@\n-a\n+b\n*** End Patch')
    assert (raiz / 'a.txt').read_text() == 'ajeno\n'


def test_evidencia_copias_y_revision(espacio):
    raiz, rt = espacio
    (raiz / 'm.txt').write_text('viejo\n')
    (raiz / 'd.txt').write_text('borrar\n')
    revision = rt.state.get('workspace_revision', 0)
    resultado = aplicar(rt, '*** Begin Patch\n*** Update File: m.txt\n*** Move to: n/m2.txt\n@@\n-viejo\n+nuevo\n'
                            '*** Delete File: d.txt\n*** Add File: a.txt\n+hola\n*** End Patch')
    acciones = {c['path']: (c['action'], c['before'], c['after']) for c in resultado['changes']}
    assert acciones == {'n/m2.txt': ('A', 'NEW', digest(b'nuevo\n')),
                        'm.txt': ('D', digest(b'viejo\n'), 'DELETED'),
                        'd.txt': ('D', digest(b'borrar\n'), 'DELETED'),
                        'a.txt': ('A', 'NEW', digest(b'hola\n'))}
    assert rt.state['workspace_revision'] == revision + 1
    assert (rt.directory / ('before-' + digest(b'viejo\n'))).read_bytes() == b'viejo\n'
    assert rt.evidence.integrity()['mismatches'] == []


def test_solo_alfalfa_ofrece_apply_patch(espacio, tmp_path, monkeypatch):
    raiz, rt = espacio
    assert 'apply_patch' in [t['function']['name'] for t in rt.tool_specs]
    assert 'apply_patch' in [t['function']['name'] for t in ON_REQUEST_TOOLS]
    assert 'apply_patch' not in [t['function']['name'] for t in TOOLS]
    integrador = NativeRuntime(rt.manager, dict(rt.task))
    assert 'apply_patch' not in [t['function']['name'] for t in integrador.tool_specs]
    with pytest.raises(ValueError):
        asyncio.run(integrador.tools.call('apply_patch',
                                          {'input': '*** Begin Patch\n*** Add File: x\n+x\n*** End Patch'}))
    assert not (raiz / 'x').exists()
    sololectura = TerminalManager('x', home=tmp_path / 'otro', read_only=True)
    try:
        assert 'apply_patch' in sololectura.runtime().excluded_tools
    finally:
        sololectura.close()


def test_el_modelo_usa_apply_patch_en_una_sesion(espacio):
    raiz, rt = espacio
    (raiz / 'a.txt').write_text('uno\n')
    payloads = []
    pasos = [{'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'apply_patch', 'arguments': {
        'input': '*** Begin Patch\n*** Update File: a.txt\n@@\n-uno\n+dos\n'
                 '*** Add File: b.txt\n+tres\n*** End Patch'}}}]},
             {'role': 'assistant', 'content': 'Hecho.'}]

    async def request(self, client, endpoint, payload, **kwargs):
        if endpoint == '/api/show':
            return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
        payloads.append(json.loads(json.dumps(payload)))
        return dict(message=pasos.pop(0), done=True, done_reason='stop', prompt_eval_count=900, eval_count=10)
    with patch.object(NativeRuntime, 'request', request):
        assert asyncio.run(rt.manager.run('Edita.')) == 0
    assert (raiz / 'a.txt').read_text() == 'dos\n' and (raiz / 'b.txt').read_text() == 'tres\n'
    resultado = json.loads([m for m in payloads[1]['messages'] if m['role'] == 'tool'][-1]['content'])
    assert resultado['summary'] == 'Parche aplicado: M a.txt, A b.txt.'
    assert 'apply_patch' in payloads[0]['messages'][0]['content']


def test_licencia_y_procedencia():
    carpeta = Path(modulo.__file__).resolve().parent
    assert re.search(r'Copyright\s2025\sOpenAI', (carpeta / 'NOTICE').read_text())
    assert re.search(r'Copyright\s2025\sOpenAI', (ESCENARIOS / 'NOTICE').read_text())
    assert 'scenarios' in (carpeta / 'PROCEDENCIA.md').read_text()
