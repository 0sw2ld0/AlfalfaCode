"""Los linters y servidores de lenguaje que el arnés lanza tras una edición no ven las claves del usuario.

ESLint ejecuta el `eslint.config.js` del proyecto, que es código del repositorio. Antes el linter
recibía el entorno entero: con OPENAI_API_KEY o GITHUB_TOKEN en él, ese código los veía y podía
devolverlos en un diagnóstico, que entra en el historial y va al modelo. Las órdenes del agente ya
recibían sólo PATH, HOME, TMPDIR, LANG y DEVELOPER_DIR.
"""
import asyncio
import os

from alfalfa.diagnosticos import Diagnosticos, entorno_base


def test_un_linter_no_recibe_las_claves(tmp_path, monkeypatch):
    proyecto, binarios = tmp_path / 'proyecto', tmp_path / 'bin-global'
    (proyecto / 'src').mkdir(parents=True)
    binarios.mkdir()
    (proyecto / 'eslint.config.js').write_text('export default [];\n')
    (proyecto / 'src' / 'a.js').write_text('const a = 1;\n')
    eslint = binarios / 'eslint'
    eslint.write_text('''#!/bin/sh
cat > /dev/null
printf '[{"messages":[{"line":1,"column":1,"severity":2,"ruleId":"demo","message":"OPENAI=%s GITHUB=%s DB=%s"}]}]' \\
  "${OPENAI_API_KEY:-NO}" "${GITHUB_TOKEN:-NO}" "${DB_PASS:-NO}"
exit 1
''')
    eslint.chmod(0o755)
    monkeypatch.setenv('PATH', str(binarios) + os.pathsep + '/usr/bin:/bin')
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-PRUEBA-0000')
    monkeypatch.setenv('GITHUB_TOKEN', 'ghp_PRUEBA0000')
    monkeypatch.setenv('DB_PASS', 'clave-PRUEBA-0000')
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS', '1')  # la suite los apaga (conftest)

    async def editar():
        diagnosticos = Diagnosticos(str(proyecto), str(tmp_path / 'alfalfa'))
        try:
            return await diagnosticos.tras_editar(proyecto, [{'path': 'src/a.js', 'before': 'NEW', 'after': 'x'}],
                                                  lambda sha: None, pedir=None, modo='auto')
        finally:
            if hasattr(diagnosticos, 'cerrar'):
                await diagnosticos.cerrar()

    informe = asyncio.run(editar())
    texto = ' '.join(informe['nuevos'])
    assert 'eslint demo' in texto, informe  # el linter corrió de verdad
    assert 'OPENAI=NO GITHUB=NO DB=NO' in texto, texto
    for secreto in ('sk-PRUEBA-0000', 'ghp_PRUEBA0000', 'clave-PRUEBA-0000'):
        assert secreto not in texto


def test_el_entorno_base_es_el_de_las_ordenes(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-x')
    monkeypatch.setenv('LANG', 'es_ES.UTF-8')
    base = entorno_base()
    assert 'OPENAI_API_KEY' not in base and base['LANG'] == 'es_ES.UTF-8' and 'PATH' in base
