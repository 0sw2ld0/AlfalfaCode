"""Prototipo del perfil `ui`: el manual de pantallas bajo demanda (ALFALFA_PACAI_UI_MANUAL=1), como una habilidad.

Sin el flag el prompt lleva el manual entero, como siempre. Con él, sólo un aviso corto y la herramienta ui_manual,
que devuelve el manual que toca a la sesión (catálogo del cliente, formato y la parte de buscar antes)."""
import asyncio
import json
from unittest.mock import patch

from alfalfa.pacai import ui_lang
from alfalfa.pacai.ui import AVISO_MANUAL, BUSCAR_ANTES, FLAG_MANUAL, Catalogo, manual_bajo_demanda
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.terminal import TerminalManager
from tests.pacai_comun import Cliente

PROPIO = {'Precio': {'description': 'Precio de un producto', 'props': {'type': 'object', 'properties': {
    'producto': {'type': 'string'}}}}}


def test_el_flag_esta_apagado_por_defecto():
    assert not manual_bajo_demanda({}) and manual_bajo_demanda({FLAG_MANUAL: '1'})
    assert not Catalogo.de(None, {}).bajo_demanda and Catalogo.de(None, {FLAG_MANUAL: '1'}).bajo_demanda


def test_sin_flag_el_prompt_lleva_el_manual_entero(monkeypatch):
    monkeypatch.delenv(FLAG_MANUAL, raising=False)
    c = Cliente(perfiles=('work', 'ui'))
    c.saludar(render={'ui': {'version': '1'}})
    assert 'Pantallas en el chat' in c.t.manual_ui() and 'Componentes:' in c.t.manual_ui()
    assert not c.t.ui_bajo_demanda()


def test_con_flag_el_prompt_lleva_solo_el_aviso_y_el_manual_va_aparte(monkeypatch):
    monkeypatch.setenv(FLAG_MANUAL, '1')
    c = Cliente(perfiles=('work', 'ui'))
    c.saludar(render={'ui': {'version': '1', 'components': PROPIO}})
    assert c.t.ui_bajo_demanda() and c.t.manual_ui() == AVISO_MANUAL
    assert len(AVISO_MANUAL) < len(c.t.manual_ui(completo=True)) / 5
    # Con buscador, buscar antes de dibujar se queda en el prompt: sin eso el modelo dice que no tiene internet
    # antes de cargar el manual (visto en el piloto con GPT-6 Luna). El manual ya no lo repite.
    assert c.t.manual_ui(web=True) == AVISO_MANUAL + BUSCAR_ANTES
    completo = c.t.manual_ui(web=True, completo=True)
    assert 'Precio' in completo and 'Componentes:' in completo and 'web_search' not in completo
    # Sin el perfil ui no hay aviso ni herramienta.
    sin = Cliente()
    sin.saludar()
    assert sin.t.manual_ui() == '' and not sin.t.ui_bajo_demanda()


def test_con_ui_lang_el_manual_bajo_demanda_es_el_compacto(monkeypatch):
    monkeypatch.setenv(FLAG_MANUAL, '1')
    monkeypatch.setenv(ui_lang.FLAG, '1')
    c = Cliente(perfiles=('work', 'ui'))
    c.saludar(render={'ui': {'version': '1'}})
    assert c.t.manual_ui() == AVISO_MANUAL and 'pacai-ui-lang' in c.t.manual_ui(completo=True)


def _sesion(tmp_path, monkeypatch, flag):
    if flag:
        monkeypatch.setenv(FLAG_MANUAL, '1')
    else:
        monkeypatch.delenv(FLAG_MANUAL, raising=False)
    monkeypatch.chdir(tmp_path)
    c = Cliente(perfiles=('work', 'ui'))
    c.saludar(render={'ui': {'version': '1'}})
    (tmp_path / 'hogar').mkdir()
    return TerminalManager('Compara tres lenguajes', home=tmp_path / 'hogar', transport=c.t)


def _correr(manager, pasos):
    payloads = []

    async def request(rt, client, endpoint, payload, **kwargs):
        if endpoint == '/api/show':
            return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
        payloads.append(json.loads(json.dumps(payload)))
        mensaje = pasos.pop(0) if pasos else {'role': 'assistant', 'content': 'Hecho.'}
        return dict(message=mensaje, done=True, done_reason='stop', prompt_eval_count=900, eval_count=10)

    async def run():
        with patch.object(NativeRuntime, 'request', request):
            return await manager.run('Compara Python, Go y Rust para un CLI.')
    asyncio.run(asyncio.wait_for(run(), 60))
    manager.close()
    return payloads


def _llamar_manual():
    return {'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'ui_manual', 'arguments': {}}}]}


def _sistema(payload):
    return next(m['content'] for m in payload['messages'] if m['role'] == 'system')


def _herramientas(payload):
    return {t['function']['name'] for t in payload.get('tools', [])}


def test_el_modelo_carga_el_manual_una_vez_y_la_segunda_recibe_un_aviso(tmp_path, monkeypatch):
    payloads = _correr(_sesion(tmp_path, monkeypatch, True), [_llamar_manual(), _llamar_manual()])
    primero = payloads[0]
    assert 'ui_manual' in _herramientas(primero)
    assert AVISO_MANUAL.strip() in _sistema(primero) and 'Componentes:' not in _sistema(primero)
    resultados = [json.loads(m['content']) for m in payloads[-1]['messages'] if m['role'] == 'tool']
    assert 'Componentes:' in resultados[0]['manual'] and resultados[0]['manual'].startswith('Pantallas en el chat')
    assert resultados[1]['ya_cargado'] is True and 'manual' not in resultados[1]


def test_sin_flag_no_hay_herramienta_y_el_manual_va_en_el_sistema(tmp_path, monkeypatch):
    payloads = _correr(_sesion(tmp_path, monkeypatch, False), [])
    assert 'ui_manual' not in _herramientas(payloads[0]) and 'Componentes:' in _sistema(payloads[0])
