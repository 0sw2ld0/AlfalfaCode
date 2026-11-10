"""Demo reproducible con el aislamiento de tests/conftest.py, sin proveedor ni Llavero reales.

Desde la raíz, después de `cd alfalfa-mac && swift build`:
PYTHONPATH=. python -m pytest -p tests.conftest docs/demos/interfaz-a13/demo.py -q
"""
import asyncio
import io
import json
import shutil
import subprocess
from pathlib import Path

from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.stdio import Transport
from alfalfa.terminal import TerminalManager

ROOT = Path(__file__).resolve().parents[3]
DEMO = Path(__file__).resolve().parent
HTML = '''<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Demo A13</title>
<style>body{background:#f6f6f2;font:20px system-ui;padding:36px;color:#26332a}
button{background:COLOR;border:0;border-radius:12px;padding:16px 28px;color:white;font:600 20px system-ui}
p{color:#657269;font-size:16px}</style></head><body><h1>Una interfaz comprobable</h1>
<p>Demostración propia de AlfalfaCode · 760 × 400</p><button>Guardar cambios</button></body></html>'''


async def test_demo(tmp_path, monkeypatch):
    build = ROOT / 'alfalfa-mac/.build/arm64-apple-macosx/debug'
    executable = tmp_path / 'InterfazDemo'
    sources = ROOT / 'alfalfa-mac/Sources/AlfalfaCode'
    subprocess.run(['swiftc', '-parse-as-library', '-I', str(build / 'Modules'), str(DEMO / 'Demo.swift'),
                    str(sources / 'Theme.swift'), str(sources / 'Session/MediaGallery.swift'),
                    str(sources / 'Session/InterfaceProofCard.swift'),
                    *map(str, sorted((build / 'AlfalfaKit.build').glob('*.swift.o'))), '-o', str(executable)],
                   check=True)
    stderr = (tmp_path / 'demo-stderr.log').open('w')
    process = await asyncio.create_subprocess_exec(str(executable), stdin=asyncio.subprocess.PIPE,
                                                   stdout=asyncio.subprocess.PIPE, stderr=stderr)

    async def pedir(accion, campos, tiempo=10):
        process.stdin.write((json.dumps({'accion': accion, 'campos': campos}) + '\n').encode())
        await process.stdin.drain()
        return json.loads(await asyncio.wait_for(process.stdout.readline(), tiempo))

    root = tmp_path / 'proyecto'
    root.mkdir()
    before, after = HTML.replace('COLOR', '#c53835'), HTML.replace('COLOR', '#278348')
    (root / 'index.html').write_text(before)
    monkeypatch.chdir(root)
    m = TerminalManager('Cambia el botón.', home=tmp_path / 'hogar', model='demo',
                        transport=Transport(io.StringIO()), navegador=True)
    m.navegador.pedir = pedir

    async def run(rt, prompt):
        sitios = m.navegador.sitios(rt)
        sitios.guardar({'permitidos': ['https://demo.alfalfa.test:443'], 'bloqueados': []})
        assert (await pedir('demo_cargar', {'html': before, 'sitios': sitios.politica()}))['ok']
        await rt.tools.call('browser_read', {})
        (root / 'index.html').write_text(after)
        assert (await pedir('demo_cargar', {'html': after, 'sitios': sitios.politica()}))['ok']
        await rt.tools.call('browser_read', {})
        await rt.tools.call('wrap_up', {'recap': 'Botón actualizado de rojo a verde.'})
        rt.task['status'] = 'completed'

    monkeypatch.setattr(NativeRuntime, 'run', run)
    try:
        assert await m.run('Cambia el botón de rojo a verde.') == 0
        proof = m.task['cierre']['prueba_interfaz']
        assert proof['estado'] == 'completa'
        pair, = proof['comparaciones']
        assert Path(pair['antes']).read_bytes() != Path(pair['despues']).read_bytes()
        assert (await pedir('demo_tarjeta', {'prueba': proof, 'ruta': str(DEMO / 'tarjeta.png')}))['ok']
        for key, filename in [('antes', 'antes.png'), ('despues', 'despues.png')]:
            shutil.copyfile(pair[key], DEMO / filename)
            pair[key] = filename
        (DEMO / 'cierre.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n')
        (DEMO / 'antes.html').write_text(before)
        (DEMO / 'despues.html').write_text(after)
    finally:
        m.close()
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 10)
        stderr.close()
