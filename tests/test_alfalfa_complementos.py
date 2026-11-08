"""Complementos compatibles con Codex, Claude Code, Cursor y Agent Plugins.

Cubre los cuatro manifiestos, la precedencia contrastada con Codex y la
selección determinista sin carga duplicada, los diagnósticos y campos
opcionales, que sólo se lean raíces autorizadas, habilidades y comandos, MCP
(confianza y conflictos), `.app.json`, `--complementos --json`, la procedencia
y que el fixture quede intacto.

Los fixtures viven en `tests/fixtures/complementos/`; las pruebas que los
modifican trabajan sobre una copia en `tmp_path`.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

from alfalfa import complementos
from alfalfa.complementos import CargadorComplementos, SCHEMA
from alfalfa.complementos.cargador import (Diagnostico, ErrorComplemento, _cuerpo, _estado_esquema,
                                             _front_matter)

FIXTURES = Path(__file__).with_name('fixtures') / 'complementos'
REPO = Path(__file__).resolve().parents[1]


def copiar(tmp_path, nombre, destino='plugin'):
    """Copia un fixture a tmp_path para poder mutarlo sin tocar el repositorio."""
    origen = FIXTURES / nombre
    destino = tmp_path / destino
    shutil.copytree(origen, destino)
    return destino


def configurar(home, raices, politicas=None):
    home.mkdir(parents=True, exist_ok=True)
    (home / 'complementos.json').write_text(json.dumps(
        {'raices': [{'raiz': str(r)} for r in raices], 'politicas': politicas or {}}))
    return home


def cargar(tmp_path, raices, cwd=None, politicas=None):
    home = configurar(tmp_path / 'home', raices, politicas)
    return CargadorComplementos(home, cwd or (tmp_path / 'obra')).cargar()


def codigos(cargador):
    return [d.codigo for d in cargador.diagnosticos]


# ---------------------------------------------------------------- los cuatro formatos

@pytest.mark.parametrize('fixture,formato,manifiesto', [
    ('codex', 'codex', '.codex-plugin/plugin.json'),
    ('claude', 'claude', '.claude-plugin/plugin.json'),
    ('cursor', 'cursor', '.cursor-plugin/plugin.json'),
    ('agent-plugins', 'agent-plugins', 'plugin.json'),
])
def test_cada_formato_se_reconoce_con_su_propio_fixture(tmp_path, fixture, formato, manifiesto):
    cargador = cargar(tmp_path, [FIXTURES / fixture])
    assert cargador.diagnosticos == []
    assert len(cargador.complementos) == 1
    plugin = cargador.complementos[0]
    assert (plugin.formato, plugin.manifiesto) == (formato, manifiesto)
    assert plugin.raiz == (FIXTURES / fixture).resolve()
    # El nombre del complemento es el del manifiesto, no el de la carpeta.
    assert plugin.identidad == {'codex': 'demo-codex', 'claude': 'demo-claude',
                                'cursor': 'demo-cursor', 'agent-plugins': 'demo-agent-plugins'}[fixture]


def test_los_fixtures_son_independientes(tmp_path):
    """Cada caso tiene su propia raíz y su único manifiesto; ninguno se apoya en otro."""
    for nombre, relativa in (('codex', '.codex-plugin/plugin.json'),
                             ('claude', '.claude-plugin/plugin.json'),
                             ('cursor', '.cursor-plugin/plugin.json'),
                             ('agent-plugins', 'plugin.json')):
        raiz = FIXTURES / nombre
        assert (raiz / relativa).is_file()
        assert sorted(p.name for p in raiz.rglob('plugin.json')) == ['plugin.json']
        # Cada fixture cargado por separado produce exactamente un complemento.
        cargador = CargadorComplementos(configurar(tmp_path / ('home-' + nombre), [raiz]),
                                        tmp_path / 'obra').cargar()
        assert len(cargador.complementos) == 1
        assert cargador.complementos[0].manifiesto == relativa


# ---------------------------------------------------------------- precedencia

def test_precedencia_codex_antes_que_claude_y_cursor(tmp_path):
    """DISCOVERABLE_PLUGIN_MANIFEST_PATHS: gana el primero que existe, no se suman."""
    raiz = copiar(tmp_path, 'codex')
    shutil.copytree(FIXTURES / 'claude' / '.claude-plugin', raiz / '.claude-plugin')
    shutil.copytree(FIXTURES / 'cursor' / '.cursor-plugin', raiz / '.cursor-plugin')
    cargador = cargar(tmp_path, [raiz])
    assert len(cargador.complementos) == 1
    assert cargador.complementos[0].formato == 'codex'
    assert cargador.complementos[0].manifiesto == '.codex-plugin/plugin.json'
    # El segundo manifiesto no produce un segundo complemento ni diagnóstico de duplicado.
    assert 'COMP-ID-DUPLICADO' not in codigos(cargador)


def test_claude_gana_a_cursor_si_no_hay_codex(tmp_path):
    raiz = copiar(tmp_path, 'claude')
    shutil.copytree(FIXTURES / 'cursor' / '.cursor-plugin', raiz / '.cursor-plugin')
    cargador = cargar(tmp_path, [raiz])
    assert cargador.complementos[0].formato == 'claude'


def test_portable_raiz_gana_a_los_manifiestos_de_cliente(tmp_path):
    raiz = copiar(tmp_path, 'agent-plugins')
    shutil.copytree(FIXTURES / 'codex' / '.codex-plugin', raiz / '.codex-plugin')
    cargador = cargar(tmp_path, [raiz])
    assert [p.formato for p in cargador.complementos] == ['agent-plugins']


def test_portable_raiz_sin_esquema_se_ignora_y_sigue_el_de_cliente(tmp_path):
    """plugin_namespace.rs: sin $schema de agent-plugins, el raíz no es portable."""
    raiz = copiar(tmp_path, 'codex')
    (raiz / 'plugin.json').write_text(json.dumps({'name': 'no-portable', 'version': '1'}))
    cargador = cargar(tmp_path, [raiz])
    assert [p.formato for p in cargador.complementos] == ['codex']
    assert cargador.complementos[0].identidad == 'demo-codex'


def test_esquema_de_agent_plugins_no_soportado_se_rechaza(tmp_path):
    raiz = copiar(tmp_path, 'codex')
    (raiz / 'plugin.json').write_text(json.dumps(
        {'$schema': 'https://agent-plugins.org/schemas/9.9.9/plugin.schema.json', 'name': 'futuro'}))
    cargador = cargar(tmp_path, [raiz])
    assert cargador.complementos == []
    assert codigos(cargador) == ['COMP-SCHEMA']
    assert 'no soportada' in cargador.diagnosticos[0].texto
    # No se degrada al manifiesto de cliente ni se ejecuta nada.
    assert 'demo-codex' not in str(cargador.informe())


def test_seleccion_determinista_sin_carga_duplicada(tmp_path):
    """La misma raíz dos veces y el orden de la configuración no cambian el informe."""
    raiz = copiar(tmp_path, 'codex')
    otra = copiar(tmp_path, 'claude', destino='otra')
    uno = cargar(tmp_path / 'a', [raiz, otra])
    dos = cargar(tmp_path / 'b', [otra, raiz])
    # Mismo contenido salvo la raíz de la configuración (el complemento no la cita).
    assert uno.informe() == dos.informe()
    assert [p.identidad for p in uno.complementos] == ['demo-claude', 'demo-codex']
    repetida = cargar(tmp_path / 'c', [raiz, raiz])
    assert [p.identidad for p in repetida.complementos] == ['demo-codex']
    assert 'COMP-RAIZ-DUPLICADA' in codigos(repetida)


def test_dos_raices_con_la_misma_identidad_no_se_sobrescriben(tmp_path):
    primera = copiar(tmp_path, 'codex', destino='primera')
    segunda = copiar(tmp_path, 'claude', destino='segunda')
    manifiesto = json.loads((segunda / '.claude-plugin' / 'plugin.json').read_text())
    manifiesto['name'] = 'demo-codex'
    (segunda / '.claude-plugin' / 'plugin.json').write_text(json.dumps(manifiesto))
    cargador = cargar(tmp_path, [primera, segunda])
    assert [p.raiz for p in cargador.complementos] == [primera.resolve()]
    assert 'COMP-ID-DUPLICADO' in codigos(cargador)
    assert any('Identidad duplicada' in d.texto for d in cargador.diagnosticos)


# ---------------------------------------------------------------- manifiestos inválidos

@pytest.mark.parametrize('nombre,contenido,codigo', [
    ('json roto', '{', 'COMP-JSON'),
    ('no objeto', '[]', 'COMP-MANIFIESTO'),
    ('sin nombre', json.dumps({'version': '1.0.0'}), 'COMP-MANIFIESTO'),
    ('nombre inválido', json.dumps({'name': 'Mayúsculas y espacios'}), 'COMP-MANIFIESTO'),
    ('nombre con ..', json.dumps({'name': 'a..b'}), 'COMP-MANIFIESTO'),
    ('versión numérica', json.dumps({'name': 'demo', 'version': 3}), 'COMP-MANIFIESTO'),
    ('author texto', json.dumps({'name': 'demo', 'author': 'yo'}), 'COMP-MANIFIESTO'),
    ('esquema ajeno', json.dumps({'name': 'demo', '$schema': 'https://ejemplo.invalid/x.json'}), 'COMP-SCHEMA'),
])
def test_manifiesto_invalido_da_diagnostico_sin_ejecutar_nada(tmp_path, nombre, contenido, codigo):
    raiz = copiar(tmp_path, 'codex')
    (raiz / '.codex-plugin' / 'plugin.json').write_text(contenido)
    (raiz / 'hooks').mkdir()
    (raiz / 'hooks' / 'hook.sh').write_text('#!/bin/sh\ntouch EJECUTADO\n')
    cargador = cargar(tmp_path, [raiz])
    assert cargador.complementos == []
    assert codigos(cargador) == [codigo]
    assert cargador.diagnosticos[0].grave and cargador.diagnosticos[0].texto
    assert not (tmp_path / 'EJECUTADO').exists()
    assert cargador.servidores_mcp() == []


def test_claude_plugin_root_se_resuelve_como_plugin_root(tmp_path):
    """Los .mcp.json de Claude Code usan ${CLAUDE_PLUGIN_ROOT}; antes el servidor no se iniciaba."""
    raiz = tmp_path / 'plug'
    (raiz / '.claude-plugin').mkdir(parents=True)
    (raiz / '.claude-plugin' / 'plugin.json').write_text(json.dumps({'name': 'demo-raiz'}))
    (raiz / 'servidor.sh').write_text('#!/bin/sh\n')
    (raiz / 'servidor.sh').chmod(0o755)
    (raiz / '.mcp.json').write_text(json.dumps({'mcpServers': {'s': {
        'command': '${CLAUDE_PLUGIN_ROOT}/servidor.sh', 'args': ['${CLAUDE_PLUGIN_ROOT}/datos', '${PLUGIN_ROOT}/x'],
        'env': {'RAIZ': '${CLAUDE_PLUGIN_ROOT}'}}}}))
    cargador = cargar(tmp_path, [raiz])
    servidor, = cargador.servidores_mcp()
    real = str(raiz.resolve())
    assert servidor.orden == real + '/servidor.sh'
    assert servidor.argumentos == [real + '/datos', real + '/x'] and servidor.entorno == {'RAIZ': real}
    (raiz / '.mcp.json').write_text(json.dumps({'mcpServers': {'s': {'command': '${HOME}/x'}}}))
    cargador = cargar(tmp_path, [raiz])
    assert cargador.servidores_mcp() == []
    assert 'COMP-MCP-ENTORNO' in [d.codigo for d in cargador.complementos[0].diagnosticos]


@pytest.mark.parametrize('campo,valor', [('version', None), ('keywords', [7]), ('keywords', 'texto'),
                                         ('homepage', 7), ('repository', None), ('license', ['MIT']),
                                         ('description', 3)])
def test_agent_plugins_con_tipos_imposibles_no_carga_ni_registra_servidores(tmp_path, campo, valor):
    """`version` null, keywords [7] u homepage 7 se aceptaban sin
    diagnóstico y el servidor MCP del complemento quedaba registrado."""
    raiz = copiar(tmp_path, 'agent-plugins')
    manifiesto = json.loads((raiz / 'plugin.json').read_text())
    manifiesto[campo] = valor
    (raiz / 'plugin.json').write_text(json.dumps(manifiesto))
    cargador = cargar(tmp_path, [raiz])
    assert cargador.complementos == [] and codigos(cargador) == ['COMP-MANIFIESTO']
    assert cargador.servidores_mcp() == []


def test_campos_opcionales_desconocidos_no_impiden_la_carga(tmp_path):
    raiz = copiar(tmp_path, 'claude')
    manifiesto = json.loads((raiz / '.claude-plugin' / 'plugin.json').read_text())
    manifiesto.update({'campoDelFuturo': {'a': 1}, 'otroOpcional': [1, 2]})
    (raiz / '.claude-plugin' / 'plugin.json').write_text(json.dumps(manifiesto))
    cargador = cargar(tmp_path, [raiz])
    assert [p.identidad for p in cargador.complementos] == ['demo-claude']
    avisos = [d for d in cargador.complementos[0].diagnosticos if d.codigo == 'COMP-CAMPO-DESCONOCIDO']
    assert [a.capacidad for a in avisos] == ['campoDelFuturo', 'otroOpcional']
    assert all(not a.grave for a in avisos)
    # Las capacidades compatibles siguen disponibles.
    assert 'demo-claude:revisar-claude' in cargador.complementos[0].habilidades


def test_manifiesto_portable_ignora_campos_fuera_del_esquema(tmp_path):
    """Un campo opcional desconocido no invalida Agent Plugins 1.0.0.

    `agent_plugin_manifest.rs` avisa e ignora los campos fuera de
    AGENT_PLUGIN_FIELDS; aquí se conserva la carga y las capacidades compatibles,
    mientras el campo queda enumerado como ignorado.
    """
    raiz = copiar(tmp_path, 'agent-plugins')
    manifiesto = json.loads((raiz / 'plugin.json').read_text())
    manifiesto['campoDelFuturo'] = {}
    (raiz / 'plugin.json').write_text(json.dumps(manifiesto))
    cargador = cargar(tmp_path, [raiz])
    plugin, = cargador.complementos
    assert plugin.formato == 'agent-plugins'
    avisos = [d for d in plugin.diagnosticos if d.codigo == 'COMP-CAMPO-DESCONOCIDO']
    assert [a.capacidad for a in avisos] == ['campoDelFuturo']
    assert all(not a.grave for a in avisos)
    # Las capacidades compatibles siguen ahí: skill portable y servidor MCP.
    assert 'demo-agent-plugins:portable' in plugin.habilidades
    assert [s.procedencia for s in cargador.servidores_mcp()] == ['mcp.json']


def test_manifiesto_portable_no_interpreta_rutas_fuera_del_contrato(tmp_path):
    """Un campo de ruta legado en Agent Plugins se avisa y no se interpreta."""
    raiz = copiar(tmp_path, 'agent-plugins')
    manifiesto = json.loads((raiz / 'plugin.json').read_text())
    manifiesto['mcpServers'] = './mcp.json'
    (raiz / 'plugin.json').write_text(json.dumps(manifiesto))
    cargador = cargar(tmp_path, [raiz])
    plugin, = cargador.complementos
    assert 'mcpServers' in [d.capacidad for d in plugin.diagnosticos
                            if d.codigo == 'COMP-CAMPO-DESCONOCIDO']
    # El servidor declarado a mano no se lee; sólo el predeterminado del formato.
    servidor, = cargador.servidores_mcp()
    assert servidor.procedencia == 'mcp.json' and servidor.nombre_original == 'portable'


def test_estado_de_esquema_traduce_la_referencia(tmp_path):
    ruta = tmp_path / 'plugin.json'
    ruta.write_text(json.dumps({'name': 'x', '$schema': SCHEMA}))
    assert _estado_esquema(ruta)[0] == 'soportado'
    ruta.write_text(json.dumps({'name': 'x', '$schema': 'https://agent-plugins.org/schemas/2.0.0/plugin.schema.json'}))
    assert _estado_esquema(ruta)[0] == 'no-soportado'
    ruta.write_text(json.dumps({'name': 'x', '$schema': 'https://ejemplo.invalid/plugin.schema.json'}))
    assert _estado_esquema(ruta)[0] == 'ajeno'
    ruta.write_text('{')
    assert _estado_esquema(ruta)[0] == 'ajeno'
    enlace = tmp_path / 'enlace.json'
    enlace.symlink_to(ruta)
    assert _estado_esquema(enlace)[0] == 'ajeno'


# ---------------------------------------------------------------- autorización

def test_sin_configuracion_no_se_carga_nada(tmp_path):
    assert CargadorComplementos(tmp_path / 'vacio', tmp_path / 'obra').cargar().informe()['complementos'] == []


def test_presencia_en_un_marketplace_o_en_claude_no_activa(tmp_path, monkeypatch):
    """ni ~/.claude/plugins ni un marketplace conceden autorización."""
    hogar = tmp_path / 'usuario'
    cache = hogar / '.claude' / 'plugins' / 'cache' / 'runpod' / 'runpod' / '1.7.4'
    shutil.copytree(FIXTURES / 'runpod-1.7.4' / 'claude', cache)
    marketplace = hogar / '.claude' / 'plugins' / 'marketplace.json'
    marketplace.write_text(json.dumps({'plugins': [{'name': 'runpod', 'path': str(cache)}]}))
    monkeypatch.setenv('HOME', str(hogar))
    home = tmp_path / 'home'
    home.mkdir()
    cargador = CargadorComplementos(home, tmp_path / 'obra').cargar()
    assert cargador.complementos == []
    assert cargador.servidores_mcp() == []
    # Con una raíz explícita sí, y sigue sin arrancar nada.
    explicito = cargar(tmp_path / 'otro', [cache])
    assert [p.identidad for p in explicito.complementos] == ['runpod']


def test_raiz_desactivada_no_se_carga(tmp_path):
    raiz = copiar(tmp_path, 'claude')
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'complementos.json').write_text(json.dumps(
        {'raices': [{'raiz': str(raiz), 'activo': False}]}))
    assert CargadorComplementos(home, tmp_path / 'obra').cargar().complementos == []


def test_configuracion_invalida_no_carga_ni_rompe(tmp_path):
    raiz = copiar(tmp_path, 'claude')
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'complementos.json').write_text(json.dumps({'raices': [{'raiz': str(raiz)}], 'extra': 1}))
    cargador = CargadorComplementos(home, tmp_path / 'obra').cargar()
    assert cargador.complementos == []
    assert codigos(cargador)[0] == 'COMP-CONFIG'


def test_carpeta_de_trabajo_con_manifiesto_no_se_autoriza_sola(tmp_path):
    """el cwd no se activa por su contenido, aunque tenga todo."""
    obra = tmp_path / 'obra'
    shutil.copytree(FIXTURES / 'codex', obra)
    cargador = cargar(tmp_path, [obra], cwd=obra)
    assert cargador.complementos == []
    assert 'COMP-CWD-NO-AUTORIZADO' in codigos(cargador)
    # Tampoco una raíz que contiene al cwd.
    padre = cargar(tmp_path / 'otro', [tmp_path], cwd=obra)
    assert padre.complementos == []


@pytest.mark.parametrize('valor', ['../fuera', '/etc', './../fuera', 'skills/../../fuera'])
def test_rutas_declaradas_que_escapan_no_se_leen(tmp_path, valor):
    raiz = copiar(tmp_path, 'claude')
    manifiesto = json.loads((raiz / '.claude-plugin' / 'plugin.json').read_text())
    manifiesto['skills'] = valor
    (raiz / '.claude-plugin' / 'plugin.json').write_text(json.dumps(manifiesto))
    fuera = tmp_path / 'fuera' / 'habilidad'
    fuera.mkdir(parents=True)
    (fuera / 'SKILL.md').write_text('---\nname: robada\ndescription: no\n---\nNO AUTORIZADA\n')
    cargador = cargar(tmp_path, [raiz])
    assert cargador.complementos[0].habilidades == {}
    assert any(d.codigo == 'COMP-RUTA' for d in cargador.complementos[0].diagnosticos)
    assert 'NO AUTORIZADA' not in json.dumps(cargador.informe())


def test_enlaces_simbolicos_internos_si_y_externos_no(tmp_path):
    raiz = copiar(tmp_path, 'claude')
    fuera = tmp_path / 'fuera'
    fuera.mkdir()
    (fuera / 'SKILL.md').write_text('---\nname: externa\ndescription: fuera\n---\n')
    (raiz / 'skills' / 'enlazada').symlink_to(fuera, target_is_directory=True)
    interno = raiz / 'skills' / 'real'
    (raiz / 'skills' / 'copia').symlink_to(interno, target_is_directory=True)
    cargador = cargar(tmp_path, [raiz])
    habilidades = cargador.complementos[0].habilidades
    assert set(habilidades) == {'demo-claude:revisar-claude'}
    assert 'externa' not in json.dumps(cargador.informe())


def test_raiz_que_es_enlace_si_se_resuelve_y_se_contiene(tmp_path):
    real = copiar(tmp_path, 'claude', destino='real')
    enlace = tmp_path / 'instalado'
    enlace.symlink_to(real, target_is_directory=True)
    cargador = cargar(tmp_path, [enlace])
    assert [p.identidad for p in cargador.complementos] == ['demo-claude']
    assert cargador.complementos[0].raiz == real.resolve()


# ---------------------------------------------------------------- habilidades bajo demanda

def test_descubrimiento_guarda_solo_metadatos_y_apertura_lee_el_cuerpo(tmp_path):
    raiz = copiar(tmp_path, 'claude')
    (raiz / 'skills' / 'revisar' / 'SKILL.md').write_text(
        '---\nname: revisar-claude\ndescription: Revisa un archivo.\n---\n'
        'CUERPO-COMPLETO-QUE-NO-DEBE-APARECER-EN-EL-DESCUBRIMIENTO\n')
    cargador = cargar(tmp_path, [raiz])
    habilidad = cargador.complementos[0].habilidades['demo-claude:revisar-claude']
    informe = json.dumps(cargador.informe(), ensure_ascii=False)
    assert 'CUERPO-COMPLETO' not in informe
    assert habilidad.descripcion == 'Revisa un archivo.'
    assert habilidad.referencia if hasattr(habilidad, 'referencia') else True
    assert habilidad.ruta == 'skills/revisar/SKILL.md'
    # La instrucción del modelo cita una referencia identificable, no el contenido.
    instrucciones = cargador.instrucciones()
    assert 'demo-claude:revisar-claude' in instrucciones
    assert habilidad.sha256[:12] in instrucciones
    assert 'CUERPO-COMPLETO' not in instrucciones
    abierta = cargador.abrir_habilidad('demo-claude:revisar-claude')
    assert abierta['contenido'].endswith('CUERPO-COMPLETO-QUE-NO-DEBE-APARECER-EN-EL-DESCUBRIMIENTO\n')
    assert abierta['sha256'] == habilidad.sha256


def test_habilidad_cambiada_tras_el_descubrimiento_se_detecta(tmp_path):
    raiz = copiar(tmp_path, 'claude')
    cargador = cargar(tmp_path, [raiz])
    (raiz / 'skills' / 'revisar' / 'SKILL.md').write_text('---\nname: revisar-claude\n---\notra\n')
    with pytest.raises(ErrorComplemento) as fallo:
        cargador.abrir_habilidad('demo-claude:revisar-claude')
    assert fallo.value.codigo == 'COMP-SKILL-CAMBIADA'


@pytest.mark.parametrize('identidad', ['', 'desconocida:x', 'demo-claude:no-existe'])
def test_habilidad_desconocida_no_se_abre(tmp_path, identidad):
    cargador = cargar(tmp_path, [FIXTURES / 'claude'])
    with pytest.raises(ErrorComplemento) as fallo:
        cargador.abrir_habilidad(identidad)
    assert fallo.value.codigo == 'COMP-SKILL-DESCONOCIDA'


def test_habilidades_anidadas_se_descubren_recursivamente(tmp_path):
    raiz = copiar(tmp_path, 'runpod-1.7.4/claude')
    cargador = cargar(tmp_path, [raiz])
    assert set(cargador.complementos[0].habilidades) == {'runpod:pod-doctor', 'runpod:runpod-mcp'}


# ---------------------------------------------------------------- comandos

def test_comandos_con_namespace_estable_y_sustitucion(tmp_path):
    cargador = cargar(tmp_path, [FIXTURES / 'codex'])
    plugin = cargador.complementos[0]
    assert list(plugin.comandos) == ['demo-codex:resumir']
    pedido = cargador.invocar_comando('demo-codex:resumir', 'notas.md y el resto')
    assert pedido['ejecucion'] is False
    assert 'notas.md' in pedido['texto']
    assert 'Resume el archivo notas.md con este encargo: notas.md y el resto' in pedido['texto']
    # El front matter no se cuela en la plantilla.
    assert 'description:' not in pedido['texto']
    assert 'No se ejecutó' not in pedido['nota'] and 'no se ejecutó' in pedido['nota']


def test_invocar_un_comando_no_interpreta_ni_amplia_permisos(tmp_path):
    raiz = copiar(tmp_path, 'claude')
    (raiz / 'commands' / 'peligro.md').write_text(
        '---\ndescription: prueba\n---\nEjecuta esto: rm -rf / y $(whoami) y `id`\n')
    cargador = cargar(tmp_path, [raiz])
    pedido = cargador.invocar_comando('demo-claude:peligro', '$(touch PWNED)')
    assert pedido['ejecucion'] is False
    assert 'rm -rf /' in pedido['texto'] and '$(whoami)' in pedido['texto']
    assert not (tmp_path / 'PWNED').exists()
    with pytest.raises(ErrorComplemento) as fallo:
        cargador.invocar_comando('demo-claude:peligro', 'comilla " sin cerrar')
    assert fallo.value.codigo == 'COMP-ARGUMENTOS'
    with pytest.raises(ErrorComplemento):
        cargador.invocar_comando('demo-claude:inexistente')


def test_cuerpo_quita_solo_el_front_matter():
    assert _cuerpo('---\na: b\n---\ncuerpo\n') == 'cuerpo\n'
    assert _cuerpo('sin front matter\n') == 'sin front matter\n'
    assert _cuerpo('---\nsin cierre\n') == ''
    assert _front_matter('---\nname: x\ndescription: "y"\n---\n') == {'name': 'x', 'description': 'y'}


# ---------------------------------------------------------------- MCP normalizado

def test_mcp_stdio_resuelve_plugin_root_y_conserva_procedencia(tmp_path):
    cargador = cargar(tmp_path, [FIXTURES / 'codex'])
    servidores = cargador.servidores_mcp()
    assert len(servidores) == 1
    servidor = servidores[0]
    raiz = (FIXTURES / 'codex').resolve()
    assert servidor.transporte == 'stdio'
    assert servidor.orden == str(raiz / 'servidor.sh')
    assert servidor.argumentos == ['--modo', 'prueba']
    assert servidor.cwd == str(raiz)
    assert (servidor.procedencia, servidor.complemento, servidor.nombre_original) == (
        '.mcp.json', 'demo-codex', 'local')
    # Sin nada de esto en el manifiesto: diferidas por defecto, ninguna directa.
    assert servidor.directas == () and servidor.confianza == frozenset()
    assert servidor.habilitadas is None and servidor.red is False


def test_mcp_http_remoto_del_portable(tmp_path):
    cargador = cargar(tmp_path, [FIXTURES / 'agent-plugins'])
    servidor, = cargador.servidores_mcp()
    assert servidor.transporte == 'http'
    assert servidor.procedencia == 'mcp.json' and servidor.complemento == 'demo-agent-plugins'
    assert servidor.nombre_original == 'portable'


def test_servidores_de_dos_raices_no_comparten_identificador(tmp_path):
    uno = copiar(tmp_path, 'claude', destino='uno')
    dos = copiar(tmp_path, 'codex', destino='dos')
    cargador = cargar(tmp_path, [uno, dos])
    ids = [s.id for s in cargador.servidores_mcp()]
    assert len(ids) == len(set(ids)) == 2
    # Cada uno conserva su nombre original y su procedencia.
    assert {(s.complemento, s.nombre_original) for s in cargador.servidores_mcp()} == {
        ('demo-claude', 'revision'), ('demo-codex', 'local')}


def test_servidor_repetido_no_sobrescribe(tmp_path):
    raiz = copiar(tmp_path, 'claude')
    (raiz / '.mcp.json').write_text(json.dumps({'mcpServers': {
        'Revision': {'type': 'http', 'url': 'https://a.example.invalid/mcp'},
        'revision': {'type': 'http', 'url': 'https://b.example.invalid/mcp'}}}))
    cargador = cargar(tmp_path, [raiz])
    avisos = [d for d in cargador.complementos[0].diagnosticos if d.codigo == 'COMP-MCP-ID-DUPLICADO']
    assert len(avisos) == 1
    servidores = cargador.servidores_mcp()
    assert len(servidores) == 1
    assert servidores[0].url == 'https://a.example.invalid/mcp'


@pytest.mark.parametrize('entrada,codigo', [
    ({'type': 'sse', 'url': 'https://a.example.invalid/mcp'}, 'COMP-MCP-TRANSPORTE'),
    ({'type': 'websocket', 'url': 'https://a.example.invalid/mcp'}, 'COMP-MCP-TRANSPORTE'),
    ({'type': 'stdio'}, 'COMP-MCP'),
    ({'type': 'stdio', 'command': '${SECRETO}'}, 'COMP-MCP-ENTORNO'),
    ({'type': 'stdio', 'command': 'echo', 'args': ['${PLUGIN_DATA}/x']}, 'COMP-MCP-ENTORNO'),
    ({'type': 'stdio', 'command': 'echo', 'env': {'CLAVE': '${MI_TOKEN}'}}, 'COMP-MCP-ENTORNO'),
    ({'type': 'http', 'url': 'https://a.example.invalid/mcp', 'headers': {'X': '${TOKEN}'}}, 'COMP-MCP-ENTORNO'),
    ({'type': 'stdio', 'command': 'echo', 'cwd': '/etc'}, 'COMP-MCP-RUTA'),
    ({'type': 'stdio', 'command': '../fuera.sh'}, 'COMP-MCP-RUTA'),
    ('texto', 'COMP-MCP'),
])
def test_servidor_mcp_invalido_no_se_inicia(tmp_path, entrada, codigo):
    raiz = copiar(tmp_path, 'claude')
    (raiz / '.mcp.json').write_text(json.dumps({'mcpServers': {'malo': entrada}}))
    cargador = cargar(tmp_path, [raiz])
    assert cargador.servidores_mcp() == []
    assert codigo in [d.codigo for d in cargador.complementos[0].diagnosticos]
    assert any('no se inicia' in d.texto.lower() or 'no se carga' in d.texto.lower()
               for d in cargador.complementos[0].diagnosticos)


def test_el_complemento_no_puede_conceder_confianza_ni_directas(tmp_path):
    """sólo la configuración del usuario concede excepciones."""
    raiz = copiar(tmp_path, 'claude')
    (raiz / '.mcp.json').write_text(json.dumps({'mcpServers': {'revision': {
        'type': 'http', 'url': 'https://a.example.invalid/mcp',
        'directas': ['leer'], 'confianza': ['leer'], 'entorno_heredado': ['GITHUB_TOKEN'],
        'habilitadas': ['leer'], 'max_bytes_esquema': 999999, 'red': True}}}))
    cargador = cargar(tmp_path, [raiz])
    servidor, = cargador.servidores_mcp()
    assert servidor.directas == () and servidor.confianza == frozenset()
    assert servidor.habilitadas is None
    assert servidor.max_bytes_esquema == 16384 and servidor.red is False
    avisos = [d for d in cargador.complementos[0].diagnosticos if d.codigo == 'COMP-POLITICA-IGNORADA']
    assert {a.capacidad for a in avisos} >= {'directas', 'confianza', 'entorno_heredado',
                                             'habilitadas', 'max_bytes_esquema', 'red'}
    assert all(not a.grave for a in avisos)


def test_la_configuracion_del_usuario_si_concede_directas_y_confianza(tmp_path):
    raiz = copiar(tmp_path, 'claude')
    politicas = {'demo-claude': {'mcp': {'revision': {
        'directas': ['leer'], 'confianza': ['leer'], 'max_bytes_esquema': 4096}}}}
    cargador = cargar(tmp_path, [raiz], politicas=politicas)
    servidor, = cargador.servidores_mcp()
    assert servidor.directas == ('leer',) and servidor.confianza == frozenset({'leer'})
    assert servidor.max_bytes_esquema == 4096


def test_politica_del_usuario_no_puede_traer_campos_ajenos(tmp_path):
    raiz = copiar(tmp_path, 'claude')
    politicas = {'demo-claude': {'mcp': {'revision': {'orden': '/bin/malo', 'confianza': ['leer']}}}}
    cargador = cargar(tmp_path, [raiz], politicas=politicas)
    servidor, = cargador.servidores_mcp()
    assert servidor.orden is None and servidor.transporte == 'http'
    assert servidor.confianza == frozenset({'leer'})
    assert any(d.codigo == 'COMP-POLITICA-MCP' for d in cargador.complementos[0].diagnosticos)


def test_conflicto_entre_usuario_y_complemento_es_restrictivo(tmp_path):
    """deshabilitadas gana sobre habilitadas y sobre directas."""
    raiz = copiar(tmp_path, 'claude')
    politicas = {'demo-claude': {'mcp': {'revision': {'habilitadas': ['leer'],
                                                      'deshabilitadas': ['leer'],
                                                      'directas': ['leer']}}}}
    cargador = cargar(tmp_path, [raiz], politicas=politicas)
    servidor, = cargador.servidores_mcp()
    assert not servidor.habilitada('leer')
    conflictos = [d for d in cargador.complementos[0].diagnosticos if d.codigo == 'MCP-CONFLICTO']
    assert conflictos and all(not c.grave for c in conflictos)


def test_conflicto_de_politica_no_impide_arrancar_la_sesion(tmp_path, monkeypatch):
    """Los avisos MCP-CONFLICTO se mezclaban como otro tipo de
    diagnóstico y diagnosticos_mcp() lanzaba AttributeError: TerminalManager no arrancaba."""
    from alfalfa.terminal import TerminalManager
    raiz = copiar(tmp_path, 'claude')
    politicas = {'demo-claude': {'mcp': {'revision': {'habilitadas': ['leer'], 'deshabilitadas': ['leer']}}}}
    cargador = cargar(tmp_path, [raiz], politicas=politicas)
    conflicto, = [d for d in cargador.diagnosticos_mcp() if d.codigo == 'MCP-CONFLICTO']
    assert conflicto.servidor == 'demo-claude-revision' and not conflicto.grave
    json.dumps(cargador.informe())
    proyecto = tmp_path / 'proyecto'
    proyecto.mkdir()
    monkeypatch.chdir(proyecto)
    m = TerminalManager('x', home=tmp_path / 'home')
    try:
        assert m.mcp is not None and not m.mcp.servidores['demo-claude-revision'].habilitada('leer')
        assert any(d.codigo == 'MCP-CONFLICTO' for d in m.mcp.diagnosticos)
    finally:
        m.close()


@pytest.mark.parametrize('fixture,ruta', [('agent-plugins', 'plugin.json'), ('claude', '.claude-plugin/plugin.json')])
def test_author_null_es_un_manifiesto_invalido(tmp_path, fixture, ruta):
    """`author: null` se aceptaba como si faltara."""
    raiz = copiar(tmp_path, fixture)
    manifiesto = json.loads((raiz / ruta).read_text())
    manifiesto['author'] = None
    (raiz / ruta).write_text(json.dumps(manifiesto))
    cargador = cargar(tmp_path, [raiz])
    assert cargador.complementos == [] and codigos(cargador) == ['COMP-MANIFIESTO']
    assert cargador.servidores_mcp() == []


# ---------------------------------------------------------------- .app.json

def test_app_json_se_ignora_sin_abrir_conectores(tmp_path):
    raiz = copiar(tmp_path, 'claude')
    (raiz / '.app.json').write_text(json.dumps({'apps': {'sample': {'id': 'connector_sample'}}}))
    cargador = cargar(tmp_path, [raiz])
    assert cargador.complementos[0].ignoradas == ['.app.json']
    servidores = cargador.servidores_mcp()
    assert [s.procedencia for s in servidores] == ['.mcp.json']
    # El conector no aparece por ningún lado del informe.
    assert 'connector_sample' not in json.dumps(cargador.informe())


def test_app_json_ilegible_no_rompe_ni_produce_conectores(tmp_path):
    raiz = copiar(tmp_path, 'claude')
    (raiz / '.app.json').write_text('{ no es json')
    cargador = cargar(tmp_path, [raiz])
    assert cargador.complementos[0].ignoradas == ['.app.json']
    assert all(s.complemento == 'demo-claude' for s in cargador.servidores_mcp())


# ---------------------------------------------------------------- CLI JSON

def test_cli_complementos_json_es_valido_y_determinista(tmp_path, monkeypatch, capsys):
    from alfalfa.stdio import entry
    from types import SimpleNamespace
    raiz = copiar(tmp_path, 'codex')
    home = configurar(tmp_path / 'alfalfa', [raiz])
    monkeypatch.setenv('ALFALFA_HOME', str(home))
    monkeypatch.chdir(tmp_path)
    assert entry(SimpleNamespace(complementos=True)) == 0
    primero = capsys.readouterr().out
    fila = json.loads(primero)
    assert (fila['version'], fila['tipo'], fila['esquema']) == (2, 'complementos', 1)
    assert [p['identidad'] for p in fila['complementos']] == ['demo-codex']
    plugin = fila['complementos'][0]
    assert (plugin['formato'], plugin['manifiesto'], plugin['version_disponible']) == (
        'codex', '.codex-plugin/plugin.json', '2.1.0')
    assert plugin['raiz'] == str(raiz.resolve())
    assert plugin['capacidades']['servidores_mcp']
    assert set(plugin['capacidades']['servidores'][0]) == {'id', 'original', 'procedencia', 'transporte'}
    assert entry(SimpleNamespace(complementos=True)) == 0
    segundo = capsys.readouterr().out
    assert primero == segundo
    # No se arranca nada para listar.
    assert fila['ejecucion'].startswith('ninguna')


def test_cli_complementos_no_revela_secretos(tmp_path, monkeypatch, capsys):
    from alfalfa.stdio import entry
    from types import SimpleNamespace
    raiz = copiar(tmp_path, 'claude')
    (raiz / '.mcp.json').write_text(json.dumps({'mcpServers': {'revision': {
        'type': 'http', 'url': 'https://a.example.invalid/mcp',
        'headers': {'Authorization': 'Bearer CENTINELA-CLI-3f4a2b'}}}}))
    home = configurar(tmp_path / 'alfalfa', [raiz])
    monkeypatch.setenv('ALFALFA_HOME', str(home))
    monkeypatch.chdir(tmp_path)
    assert entry(SimpleNamespace(complementos=True)) == 0
    salida = capsys.readouterr().out
    assert 'CENTINELA-CLI' not in salida
    assert 'Authorization' not in salida and 'headers' not in salida
    json.loads(salida)


# ---------------------------------------------------------------- sólo TerminalManager

def test_native_runtime_no_hereda_complementos_de_otro_gestor(tmp_path):
    """el gestor del integrador no puede colar complementos por un atributo homónimo."""
    from alfalfa.proveedores.nativo.runtime import NativeRuntime

    class GestorIntegrador:
        """Imita a AgentController: mismo atributo, nada que ver con AlfalfaCode."""
        def __init__(self):
            self.directory = tmp_path / 'integrador'
            self.directory.mkdir(parents=True, exist_ok=True)
            self.complementos = cargar(tmp_path, [FIXTURES / 'claude'])

    rt = NativeRuntime(GestorIntegrador(), {'id': 't1', 'objective': 'x', 'cwd': str(tmp_path)})
    assert rt.complementos is None
    sistema = rt.system_instructions('prueba')
    assert 'Complementos autorizados' not in sistema
    assert 'demo-claude' not in sistema


def test_terminal_manager_si_expone_los_complementos_cargados(tmp_path, monkeypatch):
    """la sesión de TerminalManager sí recibe el cargador y sus instrucciones."""
    from alfalfa.terminal import TerminalManager
    monkeypatch.chdir(tmp_path)
    raiz = FIXTURES / 'codex'
    home = configurar(tmp_path / 'alfalfa', [raiz])
    m = TerminalManager('Lista.', home=home, read_only=True)
    try:
        assert [p.identidad for p in m.complementos.complementos] == ['demo-codex']
        instrucciones = m.runtime().complementos.instrucciones()
        assert 'demo-codex:resumir' in instrucciones
        # En solo lectura no se arranca ningún servidor del complemento.
        assert m.mcp is None
        assert m.complementos.servidores_mcp() and m.complementos is not None
    finally:
        m.close()


def test_solo_lectura_no_arranca_los_servidores_del_complemento(tmp_path, monkeypatch):
    """listar el complemento no inicia MCP; en solo lectura tampoco se conecta."""
    from alfalfa.terminal import TerminalManager
    monkeypatch.chdir(tmp_path)
    home = configurar(tmp_path / 'alfalfa', [FIXTURES / 'codex'])
    m = TerminalManager('Lista.', home=home, read_only=True)
    try:
        assert m.mcp is None
        assert len(m.complementos.servidores_mcp()) == 1
    finally:
        m.close()


# ---------------------------------------------------------------- Runpod 1.7.4

CAPACIDADES_RUNPOD_CLAUDE = {'habilidades': {'runpod:pod-doctor', 'runpod:runpod-mcp'},
                             'comandos': {'runpod:migrate'},
                             'servidores': {'runpod-runpod'}}


def test_el_mismo_runpod_se_reconoce_desde_sus_dos_manifiestos(tmp_path):
    """Codex y Claude describen el mismo complemento sin convertir su contenido."""
    desde_claude = cargar(tmp_path / 'a', [FIXTURES / 'runpod-1.7.4' / 'claude']).complementos[0]
    desde_codex = cargar(tmp_path / 'b', [FIXTURES / 'runpod-1.7.4' / 'codex']).complementos[0]
    assert (desde_claude.identidad, desde_codex.identidad) == ('runpod', 'runpod')
    assert (desde_claude.version, desde_codex.version) == ('1.7.4', '1.7.4')
    assert (desde_claude.formato, desde_codex.formato) == ('claude', 'codex')
    # Mismos metadatos del autor: no hay traducción de campos entre formatos.
    assert desde_claude.descripcion == desde_codex.descripcion
    assert desde_claude.nombre == desde_codex.nombre == 'runpod'


def test_capacidades_compatibles_y_no_soportadas_de_runpod(tmp_path):
    """el informe enumera lo compatible y lo ignorado; no declara equivalencia total."""
    cargador = cargar(tmp_path, [FIXTURES / 'runpod-1.7.4' / 'claude'])
    informe = cargador.informe()['complementos'][0]
    assert {h['id'] for h in informe['capacidades']['habilidades']} == CAPACIDADES_RUNPOD_CLAUDE['habilidades']
    assert [c['id'] for c in informe['capacidades']['comandos']] == ['runpod:migrate']
    assert informe['capacidades']['servidores_mcp'] == ['runpod-runpod']
    # Lo no soportado no se finge: el formato de Claude no trae interfaz, así que no hay
    # capacidades de interfaz y los campos de ecosistema se avisan como desconocidos.
    assert 'interface' not in informe['capacidades']
    # El mismo complemento desde Codex declara `interface`, que aquí no se interpreta.
    codex = cargar(tmp_path / 'b', [FIXTURES / 'runpod-1.7.4' / 'codex']).informe()['complementos'][0]
    assert 'interface' not in codex['capacidades']


def test_el_fixture_de_runpod_no_trae_credenciales_ni_conecta(tmp_path, monkeypatch):
    """carga real del fixture; la conexión externa es doble y no se abre."""
    llamadas = []

    def sin_red(*args, **kwargs):
        llamadas.append(args)
        raise AssertionError('No debe abrirse ninguna conexión externa al cargar el complemento.')

    monkeypatch.setattr('httpx.AsyncClient.send', sin_red, raising=False)
    monkeypatch.setattr('httpx.Client.send', sin_red, raising=False)
    cargador = cargar(tmp_path, [FIXTURES / 'runpod-1.7.4' / 'claude'])
    servidor, = cargador.servidores_mcp()
    assert (servidor.url, servidor.transporte, servidor.procedencia) == (
        'https://mcp.getrunpod.io/', 'http', '.mcp.json')
    assert servidor.complemento == 'runpod' and servidor.nombre_original == 'runpod'
    # Diferido y sin confianza por defecto: el complemento no se autoconcede nada.
    assert servidor.directas == () and servidor.confianza == frozenset() and servidor.red is False
    assert llamadas == []
    texto = json.dumps(cargador.informe(), ensure_ascii=False)
    for prohibido in ('Authorization', 'api_key', 'Bearer', 'headers'):
        assert prohibido not in texto


# ---------------------------------------------------------------- catálogo grande diferido

def test_catalogo_mcp_grande_no_entra_al_contexto_y_se_descubre(tmp_path):
    """300 herramientas con esquema no engordan el prompt; buscar_herramientas las halla.

    La conexión se sustituye por un doble: no se abre ningún proceso ni socket.
    """
    import asyncio
    from types import SimpleNamespace
    from alfalfa.mcp_cliente.gestor import BUSCAR, McpManager

    # complemento → cargador → servidor normalizado del cliente MCP (sin ejecutar nada)
    raiz = copiar(tmp_path, 'claude')
    (raiz / '.mcp.json').write_text(json.dumps({'mcpServers': {'catalogo': {
        'type': 'http', 'url': 'https://mcp.example.invalid/catalogo'}}}))
    cargador = cargar(tmp_path, [raiz])
    servidor, = cargador.servidores_mcp()
    assert servidor.url == 'https://mcp.example.invalid/catalogo'
    # Diferido por defecto: el complemento no pide exponer nada al modelo.
    assert servidor.directas == () and servidor.habilitadas is None

    class ConexionDoble:
        def __init__(self, servidor):
            self.servidor = servidor
            self.herramientas = [SimpleNamespace(name='herramienta_%03d' % i,
                                                 description='Consulta registros número %d' % i,
                                                 inputSchema={'type': 'object', 'properties': {
                                                     'consulta': {'type': 'string'}}})
                                 for i in range(300)]

    gestor = McpManager(SimpleNamespace(task={'id': 't'}, event=lambda *a, **k: None,
                                       context_mcp=lambda *a, **k: None),
                        [servidor], [], str(raiz))

    async def preparar():
        # Se indexa el catálogo sin conectar: el camino de _indexar es el mismo del inicio real.
        gestor.conexiones[servidor.id] = ConexionDoble(servidor)
        gestor._indexar(gestor.conexiones[servidor.id])
        return gestor

    gestor = asyncio.run(preparar())
    assert len(gestor.catalogo) == 300
    # Contexto inicial: sólo buscar_herramientas, ninguna de las 300 declaradas.
    declaradas = gestor.declaraciones()
    assert [d['function']['name'] for d in declaradas] == [BUSCAR]
    assert gestor.expuestas() == [] and gestor.cargadas == []
    instrucciones = gestor.instrucciones()
    assert '300 herramientas' in instrucciones
    assert 'herramienta_000' not in instrucciones
    # Ningún esquema del catálogo aparece en el prompt inicial.
    for nombre in ('herramienta_000', 'herramienta_299'):
        assert nombre not in json.dumps(declaradas)
    # Y la herramienta se descubre con buscar_herramientas y se puede cargar bajo demanda.
    hallazgo = gestor.buscar({'consulta': 'herramienta_299'})
    assert [r['nombre'] for r in hallazgo['resultados']][:1] == ['mcp__demo-claude-catalogo__herramienta_299']
    assert hallazgo['resultados'][0]['cargada'] is False
    gestor.buscar({'consulta': 'select:mcp__demo-claude-catalogo__herramienta_299'})
    assert gestor.expuestas() == ['mcp__demo-claude-catalogo__herramienta_299']
    assert len(gestor.declaraciones()) == 2


# ---------------------------------------------------------------- regresiones de la revisión

def test_h001_enlace_de_carpeta_del_manifiesto_no_autoriza(tmp_path):
    """.codex-plugin enlazado a una carpeta externa no aporta manifiesto."""
    externo = tmp_path / 'externo' / '.codex-plugin'
    externo.mkdir(parents=True)
    (externo / 'plugin.json').write_text(json.dumps({'name': 'ajeno', 'version': '1.0.0'}))
    raiz = tmp_path / 'instalado'
    raiz.mkdir()
    (raiz / '.codex-plugin').symlink_to(externo, target_is_directory=True)
    cargador = cargar(tmp_path / 'a', [raiz])
    assert cargador.complementos == []
    assert 'COMP-RUTA' in codigos(cargador)
    assert 'ajeno' not in json.dumps(cargador.informe())
    # El control positivo: el mismo manifiesto dentro de la raíz sí carga.
    propia = copiar(tmp_path, 'codex', destino='propia')
    assert [p.identidad for p in cargar(tmp_path / 'b', [propia]).complementos] == ['demo-codex']


def test_h002_servidor_del_usuario_no_se_sustituye_por_el_del_complemento(tmp_path, monkeypatch):
    """El identificador repetido conserva el servidor y el filtro del usuario."""
    from alfalfa.terminal import TerminalManager
    monkeypatch.chdir(tmp_path)
    raiz = copiar(tmp_path, 'claude')
    home = configurar(tmp_path / 'alfalfa', [raiz])
    (home / 'mcp.json').write_text(json.dumps({'servidores': {'demo-claude-revision': {
        'url': 'https://usuario.example.invalid/mcp', 'deshabilitadas': ['leer']}}}))
    m = TerminalManager('Lista.', home=home)
    try:
        servidores = m.mcp.servidores
        assert list(servidores) == ['demo-claude-revision']
        servidor = servidores['demo-claude-revision']
        # Gana el del usuario: su URL y su filtro siguen intactos.
        assert servidor.url == 'https://usuario.example.invalid/mcp'
        assert servidor.deshabilitadas == frozenset({'leer'})
        assert servidor.complemento is None
        assert any('configuración del usuario' in d.texto for d in m.mcp.diagnosticos)
    finally:
        m.close()


def test_h003_servidor_desactivado_por_el_usuario_no_se_registra(tmp_path):
    """`activo: false` de la política impide que el servidor llegue al gestor."""
    raiz = copiar(tmp_path, 'claude')
    politicas = {'demo-claude': {'mcp': {'revision': {'activo': False}}}}
    cargador = cargar(tmp_path, [raiz], politicas=politicas)
    assert cargador.servidores_mcp() == []
    avisos = [d for d in cargador.complementos[0].diagnosticos if d.codigo == 'COMP-POLITICA-MCP']
    assert any('desactivado' in a.texto for a in avisos)
    # El control positivo: sin la política el mismo servidor sí se registra.
    assert len(cargar(tmp_path / 'otro', [raiz]).servidores_mcp()) == 1


def test_h003_activo_no_booleano_no_registra_el_servidor(tmp_path):
    raiz = copiar(tmp_path, 'claude')
    cargador = cargar(tmp_path, [raiz], politicas={'demo-claude': {'mcp': {'revision': {'activo': 'no'}}}})
    assert cargador.servidores_mcp() == []
    assert any(d.codigo == 'COMP-POLITICA-MCP' for d in cargador.complementos[0].diagnosticos)


def test_h004_ruta_declarada_no_suma_la_predeterminada(tmp_path):
    """Con ruta declarada sólo se lee esa; el predeterminado no se añade."""
    raiz = copiar(tmp_path, 'claude')
    # El manifiesto declara ./mcp-extra.json y el directorio también tiene .mcp.json.
    manifiesto = json.loads((raiz / '.claude-plugin' / 'plugin.json').read_text())
    manifiesto['mcpServers'] = './mcp-extra.json'
    (raiz / '.claude-plugin' / 'plugin.json').write_text(json.dumps(manifiesto))
    (raiz / 'mcp-extra.json').write_text(json.dumps({'mcpServers': {'extra': {
        'type': 'http', 'url': 'https://extra.example.invalid/mcp'}}}))
    cargador = cargar(tmp_path, [raiz])
    servidores = cargador.servidores_mcp()
    assert [s.procedencia for s in servidores] == ['mcp-extra.json']
    assert [s.nombre_original for s in servidores] == ['extra']
    assert 'COMP-MCP-ID-DUPLICADO' not in codigos(cargador)


def test_h005_mapa_inline_simple_produce_servidores(tmp_path):
    """{servidor: entrada} se acepta igual que {mcpServers: {...}}."""
    raiz = copiar(tmp_path, 'claude')
    manifiesto = json.loads((raiz / '.claude-plugin' / 'plugin.json').read_text())
    manifiesto['mcpServers'] = {'docs': {'type': 'http', 'url': 'https://docs.example.invalid/mcp'}}
    (raiz / '.claude-plugin' / 'plugin.json').write_text(json.dumps(manifiesto))
    cargador = cargar(tmp_path, [raiz])
    servidor, = cargador.servidores_mcp()
    assert servidor.nombre_original == 'docs'
    assert servidor.url == 'https://docs.example.invalid/mcp'
    assert servidor.procedencia == '<manifiesto:mcpServers>'


def test_h005_mapa_inline_envuelto_sigue_funcionando(tmp_path):
    raiz = copiar(tmp_path, 'claude')
    manifiesto = json.loads((raiz / '.claude-plugin' / 'plugin.json').read_text())
    manifiesto['mcpServers'] = {'mcpServers': {'docs': {
        'type': 'http', 'url': 'https://docs.example.invalid/mcp'}}}
    (raiz / '.claude-plugin' / 'plugin.json').write_text(json.dumps(manifiesto))
    cargador = cargar(tmp_path, [raiz])
    servidor, = cargador.servidores_mcp()
    assert (servidor.nombre_original, servidor.url) == ('docs', 'https://docs.example.invalid/mcp')


def test_h006_herramientas_de_sesion_resuelven_habilidad_y_comando(tmp_path, monkeypatch):
    """abrir_habilidad e invocar_comando son invocables desde el runtime."""
    import asyncio
    from alfalfa.terminal import TerminalManager
    monkeypatch.chdir(tmp_path)
    raiz = copiar(tmp_path, 'codex')
    home = configurar(tmp_path / 'alfalfa', [raiz])
    m = TerminalManager('Lee la habilidad.', home=home, read_only=True)
    try:
        rt = m.runtime()
        ofrecidas = {t['function']['name'] for t in rt.complementos_tools}
        assert ofrecidas == {'abrir_habilidad', 'invocar_comando'}
        abierta = asyncio.run(rt.tools.call('abrir_habilidad', {'habilidad': 'demo-codex:resumen-codex'}))
        assert abierta['id'] == 'demo-codex:resumen-codex'
        assert abierta['contenido'] and abierta['sha256']
        pedido = asyncio.run(rt.tools.call('invocar_comando', {'comando': 'demo-codex:resumir',
                                                              'argumentos': 'notas.md'}))
        assert pedido['ejecucion'] is False and 'notas.md' in pedido['texto']
        # Una referencia desconocida no abre nada.
        with pytest.raises(Exception):
            asyncio.run(rt.tools.call('abrir_habilidad', {'habilidad': 'demo-codex:no-existe'}))
    finally:
        m.close()


def test_h006_los_agentes_del_integrador_no_tienen_las_herramientas_de_complementos(tmp_path):
    from alfalfa.proveedores.nativo.runtime import NativeRuntime

    class GestorIntegrador:
        def __init__(self):
            self.directory = tmp_path / 'integrador'
            self.directory.mkdir(parents=True, exist_ok=True)

    rt = NativeRuntime(GestorIntegrador(), {'id': 't1', 'objective': 'x', 'cwd': str(tmp_path)})
    assert rt.complementos_tools == ()
    assert 'abrir_habilidad' not in {t['function']['name'] for t in rt.tool_specs}


def test_h007_la_sustitucion_no_reprocesa_los_argumentos_insertados(tmp_path):
    """$ARGUMENTS con un literal «$1» no se sustituye otra vez."""
    raiz = copiar(tmp_path, 'codex')
    (raiz / 'commands' / 'plantilla.md').write_text(
        '---\ndescription: plantilla\n---\nCuerpo: $ARGUMENTS y luego $1.\n')
    cargador = cargar(tmp_path, [raiz])
    literal = cargador.invocar_comando('demo-codex:plantilla', '$1')
    assert literal['texto'].strip() == 'Cuerpo: $1 y luego $1.'
    doble = cargador.invocar_comando('demo-codex:plantilla', '$ARGUMENTS')
    assert doble['texto'].strip() == 'Cuerpo: $ARGUMENTS y luego $ARGUMENTS.'
    # Varias palabras: $1 es la primera y $ARGUMENTS conserva el texto completo.
    varia = cargador.invocar_comando('demo-codex:resumir', 'notas.md resto')
    assert 'Resume el archivo notas.md con este encargo: notas.md resto' in varia['texto']


def test_h008_campos_conocidos_sin_capacidad_se_informan_como_ignorados(tmp_path):
    """`interface` y `agents` no se callan; aparecen como ignorados con aviso."""
    raiz = copiar(tmp_path, 'runpod-1.7.4/claude')
    manifiesto = json.loads((raiz / '.claude-plugin' / 'plugin.json').read_text())
    manifiesto.update({'interface': {'displayName': 'Runpod'}, 'agents': './agents/'})
    (raiz / '.claude-plugin' / 'plugin.json').write_text(json.dumps(manifiesto))
    cargador = cargar(tmp_path, [raiz])
    plugin = cargador.complementos[0]
    assert {'interface', 'agents'} <= set(plugin.ignoradas)
    avisos = {d.capacidad for d in plugin.diagnosticos if d.codigo == 'COMP-CAMPO-NO-SOPORTADO'}
    assert {'interface', 'agents'} <= avisos
    assert all(not d.grave for d in plugin.diagnosticos if d.codigo == 'COMP-CAMPO-NO-SOPORTADO')
    # Sigue sin fingir capacidad: no hay clave de interfaz en las capacidades.
    assert 'interface' not in plugin.como_dict()['capacidades']
    # El fixture real de Runpod Codex también deja constancia de su interface.
    codex = cargar(tmp_path / 'b', [FIXTURES / 'runpod-1.7.4' / 'codex']).complementos[0]
    assert 'interface' in codex.ignoradas


def test_escape_inexistente_sigue_siendo_escape_no_ruta_ausente(tmp_path):
    """Una ruta que sale de la raíz no se degrada a COMP-RUTA-AUSENTE."""
    raiz = copiar(tmp_path, 'claude')
    manifiesto = json.loads((raiz / '.claude-plugin' / 'plugin.json').read_text())
    manifiesto['skills'] = './../../tmp/inexistente-x/skills'
    (raiz / '.claude-plugin' / 'plugin.json').write_text(json.dumps(manifiesto))
    cargador = cargar(tmp_path, [raiz])
    codigos_capacidad = {(d.codigo, d.capacidad) for d in cargador.complementos[0].diagnosticos}
    assert ('COMP-RUTA', 'skills') in codigos_capacidad
    assert ('COMP-RUTA-AUSENTE', 'skills') not in codigos_capacidad


def test_ruta_declarada_ausente_dentro_de_la_raiz_no_es_grave(tmp_path):
    raiz = copiar(tmp_path, 'claude')
    manifiesto = json.loads((raiz / '.claude-plugin' / 'plugin.json').read_text())
    manifiesto['commands'] = './commands-que-no-existen'
    (raiz / '.claude-plugin' / 'plugin.json').write_text(json.dumps(manifiesto))
    cargador = cargar(tmp_path, [raiz])
    avisos = [d for d in cargador.complementos[0].diagnosticos if d.codigo == 'COMP-RUTA-AUSENTE']
    assert avisos and all(not a.grave for a in avisos)
    assert cargador.complementos[0].comandos == {}


# ------------------------------------- regresiones de la segunda revisión

@pytest.mark.parametrize('cwd', [7, {}, [], True])
def test_h001_cwd_no_textual_no_rompe_el_resto_de_complementos(tmp_path, cwd):
    """Un cwd no textual se diagnostica; los demás servidores siguen."""
    raiz = copiar(tmp_path, 'claude')
    (raiz / '.mcp.json').write_text(json.dumps({'mcpServers': {
        'roto': {'type': 'stdio', 'command': 'python', 'cwd': cwd},
        'sano': {'type': 'http', 'url': 'https://sano.example.invalid/mcp'}}}))
    cargador = cargar(tmp_path, [raiz])
    servidores = cargador.servidores_mcp()
    assert [s.nombre_original for s in servidores] == ['sano']
    avisos = [d for d in cargador.complementos[0].diagnosticos if d.capacidad == 'roto']
    assert any(d.codigo == 'COMP-MCP-RUTA' for d in avisos)
    assert all('no se inicia' in d.texto for d in avisos)


def test_h001_el_listado_termina_con_json_aunque_el_cwd_sea_invalido(tmp_path, monkeypatch, capsys):
    """`entry()` emitía nada porque el TypeError abortaba el listado."""
    from alfalfa.stdio import entry
    from types import SimpleNamespace
    raiz = copiar(tmp_path, 'claude')
    (raiz / '.mcp.json').write_text(json.dumps({'mcpServers': {'roto': {
        'type': 'stdio', 'command': 'python', 'cwd': 7}}}))
    home = configurar(tmp_path / 'alfalfa', [raiz])
    monkeypatch.setenv('ALFALFA_HOME', str(home))
    monkeypatch.chdir(tmp_path)
    assert entry(SimpleNamespace(complementos=True)) == 0
    fila = json.loads(capsys.readouterr().out)
    plugin, = fila['complementos']
    assert plugin['identidad'] == 'demo-claude'
    assert any(d['codigo'] == 'COMP-MCP-RUTA' for d in plugin['diagnosticos'])
    # Ningún servidor inválido llega al gestor de sesión.
    assert plugin['capacidades']['servidores'] == []


@pytest.mark.parametrize('valor', ['./skills/../skills', './', './.', '././', './skills/..'])
def test_h003_raiz_y_punto_punto_se_rechazan_como_en_codex(tmp_path, valor):
    """`./skills/../skills` y `./` no son rutas declaradas válidas."""
    raiz = copiar(tmp_path, 'claude')
    manifiesto = json.loads((raiz / '.claude-plugin' / 'plugin.json').read_text())
    manifiesto['skills'] = valor
    (raiz / '.claude-plugin' / 'plugin.json').write_text(json.dumps(manifiesto))
    cargador = cargar(tmp_path, [raiz])
    plugin, = cargador.complementos
    assert plugin.habilidades == {}
    assert any(d.codigo == 'COMP-RUTA' and d.capacidad == 'skills' for d in plugin.diagnosticos)


@pytest.mark.parametrize('valor', ['mcp.json', './mcp.json'])
def test_h003_ruta_mcp_declarada_exige_prefijo(tmp_path, valor):
    """La ruta MCP declarada también sigue `resolve_manifest_path`."""
    raiz = copiar(tmp_path, 'claude')
    manifiesto = json.loads((raiz / '.claude-plugin' / 'plugin.json').read_text())
    manifiesto['mcpServers'] = valor
    (raiz / '.claude-plugin' / 'plugin.json').write_text(json.dumps(manifiesto))
    (raiz / 'mcp.json').write_text(json.dumps({'mcpServers': {'declarado': {
        'type': 'http', 'url': 'https://declarado.example.invalid/mcp'}}}))
    cargador = cargar(tmp_path, [raiz])
    plugin, = cargador.complementos
    if valor.startswith('./'):
        # Control positivo: con el prefijo exigido se lee el archivo declarado.
        servidor, = cargador.servidores_mcp()
        assert servidor.nombre_original == 'declarado'
    else:
        assert cargador.servidores_mcp() == []
        assert any(d.codigo == 'COMP-RUTA' and d.capacidad == 'mcp' for d in plugin.diagnosticos)


def test_h004_un_rechazo_estructural_no_deja_capacidades_visibles(tmp_path):
    """Con COMP-LIMITE no quedan el complemento ni sus habilidades."""
    raiz = copiar(tmp_path, 'claude')
    manifiesto = json.loads((raiz / '.claude-plugin' / 'plugin.json').read_text())
    manifiesto['commands'] = ['./commands'] * 257
    (raiz / '.claude-plugin' / 'plugin.json').write_text(json.dumps(manifiesto))
    cargador = cargar(tmp_path, [raiz])
    assert cargador.complementos == []
    assert cargador.informe()['complementos'] == []
    assert 'COMP-LIMITE' in codigos(cargador)
    with pytest.raises(ErrorComplemento):
        cargador.abrir_habilidad('demo-claude:revisar-claude')
    assert cargador.instrucciones() == ''


def test_h004_un_rechazo_estructural_no_reserva_identidad_ni_servidor(tmp_path):
    """El complemento rechazado no bloquea una segunda raíz válida."""
    rota = copiar(tmp_path, 'claude', destino='rota')
    manifiesto = json.loads((rota / '.claude-plugin' / 'plugin.json').read_text())
    manifiesto['skills'] = ['./skills'] * 257
    (rota / '.claude-plugin' / 'plugin.json').write_text(json.dumps(manifiesto))
    cargador = cargar(tmp_path, [rota])
    assert cargador.complementos == []
    # La identidad no quedó tomada: la misma raíz, corregida, carga sin COMP-ID-DUPLICADO.
    (rota / '.claude-plugin' / 'plugin.json').write_text(json.dumps(
        {k: v for k, v in manifiesto.items() if k != 'skills'}))
    sano = cargar(tmp_path / 'b', [rota])
    assert [p.identidad for p in sano.complementos] == ['demo-claude']
    assert 'COMP-ID-DUPLICADO' not in codigos(sano)