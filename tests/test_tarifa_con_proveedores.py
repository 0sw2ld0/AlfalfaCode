"""Tarifa por contexto largo con los proveedores de verdad (OpenAI y Anthropic), sin salir a la red.

El transporte simulado mide cada petición por sus bytes, como haría el proveedor con sus
tokens, y lo devuelve como uso: así se comprueba de verdad que en Ahorro ninguna petición
pasa del umbral, sin un `alfalfa_tarifa` falso ni usos inventados.
"""
import asyncio
import json

import httpx
import pytest

from alfalfa import catalogo
from alfalfa.compactacion import tokens_aprox
from alfalfa.proveedores import anthropic, llavero, openai
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from tests.test_proveedor_anthropic import mensaje as mensaje_claude
from tests.test_proveedor_anthropic import sse

LINEA = 'linea {n:05d} del archivo {f}: texto que el modelo lee para seguir con el encargo.\n'


def proyecto(tmp_path, monkeypatch, archivos):
    """Archivos de unos 24 KB: cada lectura añade unos 4.800 tokens (read_file da hasta 19.000 caracteres)."""
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    for f in range(archivos):
        (raiz / ('f' + str(f) + '.txt')).write_text(''.join(LINEA.format(n=n, f=f) for n in range(300)))
    monkeypatch.chdir(raiz)
    return raiz


class Guion:
    """El modelo lee `lecturas` archivos distintos, uno por respuesta, y termina. Las peticiones
    sin herramientas (o con salida JSON) son resúmenes de compactación."""

    def __init__(self, lecturas):
        self.lecturas, self.leidos = lecturas, 0
        self.medidas, self.resumenes = [], []

    def es_resumen(self, cuerpo):
        formato = ((cuerpo.get('text') or {}).get('format') or {}).get('type')
        return not cuerpo.get('tools') or formato == 'json_object'

    def medir(self, cuerpo):
        """Lo que mide el proveedor: aquí, los bytes de la petición entre 4 (como tokens_aprox)."""
        return tokens_aprox(json.dumps(cuerpo, ensure_ascii=False))

    def siguiente(self):
        if self.leidos < self.lecturas:
            self.leidos += 1
            return 'f' + str(self.leidos - 1) + '.txt'
        return None


def openai_falso(monkeypatch, guion, salida=40):
    def api(request):
        cuerpo = json.loads(request.content)
        medida = guion.medir(cuerpo)
        uso = {'input_tokens': medida, 'output_tokens': salida}
        if guion.es_resumen(cuerpo):
            guion.resumenes.append(medida)
            texto = (json.dumps({'objective': 'leer', 'decisions': [], 'completed': ['lecturas'], 'pending': [],
                                 'blockers': []}) if cuerpo.get('text') else 'Resumen: quedan lecturas.')
            return httpx.Response(200, json={'status': 'completed', 'usage': uso, 'output': [
                {'type': 'message', 'content': [{'type': 'output_text', 'text': texto}]}]})
        guion.medidas.append(medida)
        archivo = guion.siguiente()
        salidas = ([{'type': 'function_call', 'call_id': 'call_' + str(guion.leidos), 'name': 'read_file',
                     'arguments': json.dumps({'path': archivo})}] if archivo else
                   [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Leídos.'}]}])
        return httpx.Response(200, json={'status': 'completed', 'usage': uso, 'output': salidas})

    real = openai.Transporte.__init__

    def init(self, url, clave, tarea=None, timeout=None, transporte=None):
        real(self, url, clave, tarea, timeout, httpx.MockTransport(api))
    monkeypatch.setattr(openai.Transporte, '__init__', init)
    llavero.guardar('openai', 'sk-prueba-123')


def anthropic_falso(monkeypatch, guion):
    def api(request):
        cuerpo = json.loads(request.content)
        medida = guion.medir(cuerpo)
        if guion.es_resumen(cuerpo):
            guion.resumenes.append(medida)
            eventos = mensaje_claude(('text', 'Resumen: quedan lecturas.'), entrada=medida, salida=30)
        else:
            guion.medidas.append(medida)
            archivo = guion.siguiente()
            # Con razonamiento firmado, que vuelve en la petición siguiente.
            bloques = [('thinking', 'Sigo leyendo.', 'firma-' + str(guion.leidos))]
            bloques.append(('tool_use', 'toolu_' + str(guion.leidos), 'read_file', {'path': archivo}) if archivo
                           else ('text', 'Leídos.'))
            eventos = mensaje_claude(*bloques, stop='tool_use' if archivo else 'end_turn', entrada=medida, salida=60)
        return httpx.Response(200, content=sse(*eventos), headers={'content-type': 'text/event-stream'})

    real = anthropic.Transporte.__init__

    def init(self, proveedor, url, clave, tarea=None, timeout=None, transporte=None):
        real(self, proveedor, url, clave, tarea, timeout, httpx.MockTransport(api))
    monkeypatch.setattr(anthropic.Transporte, '__init__', init)
    llavero.guardar('anthropic', 'sk-ant-prueba-123456')


def correr(m, prompt):
    try:
        return asyncio.run(asyncio.wait_for(m.run(prompt), 120))
    finally:
        m.close()


def test_haiku_con_anthropic_en_ahorro_ninguna_peticion_pasa_de_100k(tmp_path, monkeypatch):
    """Haiku 5.5 tiene 1M de ventana: sin el modo Ahorro leería hasta pasar con creces los 100K."""
    from alfalfa.terminal import TerminalManager
    proyecto(tmp_path, monkeypatch, 30)
    guion = Guion(30)
    anthropic_falso(monkeypatch, guion)
    m = TerminalManager('Lee los archivos.', home=tmp_path / 'home', model='anthropic/claude-haiku-5-5', auto=True)
    assert correr(m, 'Lee los archivos.') == 0, m.task.get('summary')
    assert guion.leidos == 30
    # Compactó antes de la tarifa cara y ninguna petición la pasó (ni la del resumen).
    assert guion.resumenes and m.task['runtime_compactions'] >= 1
    assert max(guion.medidas + guion.resumenes) < 100_000
    # Con la ventana real (1M), sin recortar las salidas viejas por falta de sitio: sin el Ahorro
    # el contexto habría llegado a unos 140K.
    assert m.task['context_limit'] == 1_000_000 and not m.task.get('runtime_pruned_outputs')
    assert 30 * 4_500 > 100_000
    assert m.task['context_tarifa'] == {'desde': 100_000, 'entrada': 5.0, 'salida': 5.0, 'modo': 'ahorro'}


def test_haiku_con_anthropic_en_contexto_completo_pasa_el_umbral_y_cobra_el_tramo(tmp_path, monkeypatch):
    from alfalfa.terminal import TerminalManager
    proyecto(tmp_path, monkeypatch, 25)
    guion = Guion(25)
    anthropic_falso(monkeypatch, guion)
    m = TerminalManager('Lee los archivos.', home=tmp_path / 'home', model='anthropic/claude-haiku-5-5', auto=True)
    m.elegir_contexto('completo')
    assert correr(m, 'Lee los archivos.') == 0, m.task.get('summary')
    assert guion.resumenes == [] and max(guion.medidas) > 100_000
    # Cada petición pagada a su tarifa: las de más de 100K, todo a 5×.
    precio = anthropic.CATALOGO['claude-haiku-5-5']['precio']
    esperado = sum(catalogo.coste(precio, medida, 60) for medida in guion.medidas)
    assert m.task['coste_usd'] == pytest.approx(esperado)
    assert esperado > sum(medida * 0.10 + 60 * 0.50 for medida in guion.medidas) / 1e6


def test_luna_con_openai_cobra_el_tramo_aunque_models_dev_no_lo_traiga(tmp_path, monkeypatch):
    """300K de entrada y 10K de salida en contexto completo: 0,0675 $, no 0,035 $."""
    from alfalfa.terminal import TerminalManager
    assert 'tramo' not in catalogo.ficha('openai', 'gpt-6-luna')['precio']
    monkeypatch.chdir(tmp_path)
    llavero.guardar('openai', 'sk-prueba-123')

    def api(request):
        return httpx.Response(200, json={'status': 'completed', 'usage': {'input_tokens': 300_000,
                                                                          'output_tokens': 10_000},
                                         'output': [{'type': 'message', 'content': [
                                             {'type': 'output_text', 'text': 'Hecho.'}]}]})
    real = openai.Transporte.__init__
    monkeypatch.setattr(openai.Transporte, '__init__', lambda self, url, clave, tarea=None, timeout=None,
                        transporte=None: real(self, url, clave, tarea, timeout, httpx.MockTransport(api)))
    m = TerminalManager('Hola', home=tmp_path / 'home', model='openai/gpt-6-luna', auto=True)
    m.elegir_contexto('completo')
    assert correr(m, 'Hola') == 0, m.task.get('summary')
    assert m.task['coste_usd'] == pytest.approx(0.0675)
    assert catalogo.coste(catalogo.con_tramo({'entrada': 0.1, 'salida': 0.5, 'cache_lectura': 0.01},
                                             'openai', 'gpt-6-luna'), 300_000, 10_000, 100_000) \
        == pytest.approx((200_000 * 0.2 + 100_000 * 0.02 + 10_000 * 0.75) / 1e6)
    # Quien no tiene tramo conocido sigue igual.
    assert catalogo.con_tramo({'entrada': 1, 'salida': 2}, 'openrouter', 'gpt-6-luna') == {'entrada': 1, 'salida': 2}


def test_luna_con_openai_y_la_compactacion_del_integrador_tampoco_pasa_de_272k(tmp_path, monkeypatch):
    """Como lo construye Chiyo: NativeRuntime(manager, task), sin compactación «codex». Con la ventana
    de 1.050.000, el 60 % de context_chars quedaba en unos 465K tokens."""
    from alfalfa.terminal import TerminalManager
    proyecto(tmp_path, monkeypatch, 70)
    guion = Guion(70)
    openai_falso(monkeypatch, guion)
    m = TerminalManager('Lee los archivos.', home=tmp_path / 'home', model='openai/gpt-6-luna', auto=True)
    try:
        rt = NativeRuntime(m, m.task)
        assert rt.compaction is None
        codigo = asyncio.run(asyncio.wait_for(rt.run('Lee los archivos.'), 300))
    finally:
        m.close()
    assert codigo is None or codigo == 0
    assert guion.leidos == 70
    assert guion.resumenes and m.task['runtime_compactions'] >= 1
    assert max(guion.medidas + guion.resumenes) < 272_000
    assert 70 * 4_500 > 272_000
    assert rt.context_chars * 60 // 100 // 4 > 400_000  # el tope de siempre no lo habría parado
