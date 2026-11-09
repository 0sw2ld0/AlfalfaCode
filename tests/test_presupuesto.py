"""Presupuesto con tope duro y ahorro de la caché de prompts.

Con los proveedores de verdad (Anthropic y OpenAI) y un transporte simulado: nada sale a
la red. El hogar de AlfalfaCode es uno de prueba en cada test.
"""
import asyncio
import io
import json
from unittest.mock import patch

import httpx
import pytest

from alfalfa import presupuesto
from alfalfa.presupuesto import GastoMensual, Presupuesto, PresupuestoAgotado, importe
from alfalfa.proveedores import anthropic, llavero, openai
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.stdio import Transport, gasto
from alfalfa.terminal import TerminalManager, main, texto_del_gasto
from tests.test_proveedor_anthropic import mensaje as mensaje_claude
from tests.test_proveedor_anthropic import sse

# Claude Opus 5.5: 4 $ la entrada, 20 $ la salida, 0,20 $ la caché leída y 5 $ la escrita (por millón).
OPUS = 'anthropic/claude-opus-5-5'


def proyecto(tmp_path, monkeypatch, archivos=20):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    for n in range(archivos):
        (raiz / ('f' + str(n) + '.txt')).write_text('contenido ' + str(n) + '\n')
    monkeypatch.chdir(raiz)
    return raiz


class Claude:
    """Anthropic simulado: lee un archivo por respuesta hasta `lecturas` y termina. Cada
    petición declara `entrada` tokens sin caché, `leida` leídos y `escrita` escritos."""

    def __init__(self, lecturas=20, entrada=100_000, leida=0, escrita=0, salida=1000):
        self.lecturas, self.peticiones = lecturas, 0
        self.entrada, self.leida, self.escrita, self.salida = entrada, leida, escrita, salida

    def instalar(self, monkeypatch):
        def api(request):
            self.peticiones += 1
            n = self.peticiones
            bloques = ([('tool_use', 'toolu_' + str(n), 'read_file', {'path': 'f' + str(n % 20) + '.txt'})]
                       if n <= self.lecturas else [('text', 'Leídos.')])
            eventos = mensaje_claude(*bloques, stop='tool_use' if n <= self.lecturas else 'end_turn',
                                     entrada=self.entrada, salida=self.salida, leida=self.leida,
                                     escrita=self.escrita)
            return httpx.Response(200, content=sse(*eventos), headers={'content-type': 'text/event-stream'})

        real = anthropic.Transporte.__init__

        def init(transporte, proveedor, url, clave, tarea=None, timeout=None, http=None):
            real(transporte, proveedor, url, clave, tarea, timeout, httpx.MockTransport(api))
        monkeypatch.setattr(anthropic.Transporte, '__init__', init)
        llavero.guardar('anthropic', 'sk-ant-prueba-123456')


def correr(m, prompt='Lee los archivos.'):
    try:
        return asyncio.run(asyncio.wait_for(m.run(prompt), 60))
    finally:
        m.close()


def eventos(salida):
    return [json.loads(linea) for linea in salida.getvalue().splitlines() if linea.strip()]


def coste_opus(entrada, salida, leida=0, escrita=0):
    return (entrada * 4.0 + leida * 0.20 + escrita * 5.0 + salida * 20.0) / 1_000_000


# ---------------------------------------------------------------- piezas

def test_importe_de_un_tope():
    assert importe('5') == 5.0 and importe('12,50') == 12.5 and importe('3 $') == 3.0 and importe(2) == 2.0
    for nada in (None, '', '0', 0, 'no'):
        assert importe(nada) is None
    for malo in ('mucho', -1, True, float('inf')):
        with pytest.raises(ValueError):
            importe(malo)
    assert presupuesto.dolares(0.4213) == '0,42 $' and presupuesto.dolares(0.004) == '< 0,01 $'


def test_el_gasto_del_mes_se_lee_por_el_final_y_ve_otras_sesiones(tmp_path):
    una, otra = GastoMensual(tmp_path), GastoMensual(tmp_path)
    una.apuntar('s1', 0.25)
    assert otra.total() == pytest.approx(0.25)
    otra.apuntar('s2', 0.5)
    una.apuntar('s1', 0.0)  # sin coste no se apunta
    assert una.total() == pytest.approx(0.75) and otra.total() == pytest.approx(0.75)
    archivo = tmp_path / 'gasto' / (presupuesto.mes_actual() + '.jsonl')
    assert len(archivo.read_text().splitlines()) == 2
    # Una línea a medio escribir por otra sesión cuenta cuando se termina.
    with archivo.open('a') as f:
        f.write('{"t": 1, "s": "s3", "usd": 1.')
    assert una.total() == pytest.approx(0.75)
    with archivo.open('a') as f:
        f.write('0}\nbasura\n')
    assert una.total() == pytest.approx(1.75)
    # Cada mes empieza de cero: el del mes pasado no cuenta.
    assert una.total('1999-01') == 0


def test_topes_del_entorno_y_explicitos(tmp_path, monkeypatch):
    monkeypatch.setenv('ALFALFA_PRESUPUESTO', '3')
    monkeypatch.setenv('ALFALFA_PRESUPUESTO_MES', '40,5')
    p = Presupuesto(tmp_path, {'id': 's'})
    assert (p.sesion, p.mes) == (3.0, 40.5)
    # Un --presupuesto 0 quita el del entorno.
    assert Presupuesto(tmp_path, {'id': 's'}, sesion='0', mes='9').sesion is None
    p.cambiar(sesion=None)
    assert p.sesion is None and p.mes == 40.5
    monkeypatch.setenv('ALFALFA_PRESUPUESTO', 'mucho')
    with pytest.raises(ValueError, match='ALFALFA_PRESUPUESTO'):
        Presupuesto(tmp_path, {'id': 's'})


def test_un_modelo_sin_precio_no_se_para(tmp_path):
    """Local, plan o cortesía: no gasta, así que sigue aunque el tope se haya alcanzado."""
    task = {'id': 's', 'coste_usd': 9.0}

    class Gestor:
        presupuesto = Presupuesto(tmp_path, task, sesion=5)
    rt = NativeRuntime.__new__(NativeRuntime)
    rt.manager, rt.task, rt._precio = Gestor(), task, None
    rt._comprobar_presupuesto()
    rt._precio = {'entrada': 1, 'salida': 1}
    with pytest.raises(PresupuestoAgotado, match='9,00 \\$ de 5,00 \\$'):
        rt._comprobar_presupuesto()


def test_la_terminal_valida_el_tope(capsys):
    with pytest.raises(SystemExit):
        main(['--presupuesto', 'mucho', 'Hola'])
    assert '--presupuesto' in capsys.readouterr().err


# ---------------------------------------------------------------- tope de la sesión

def test_el_turno_se_para_al_llegar_al_tope_de_la_sesion(tmp_path, monkeypatch):
    proyecto(tmp_path, monkeypatch)
    claude = Claude(entrada=100_000, salida=1000)   # 0,42 $ por petición
    claude.instalar(monkeypatch)
    salida = io.StringIO()
    transport = Transport(salida)
    m = TerminalManager('Lee los archivos.', home=tmp_path / 'home', model=OPUS, auto=True,
                        transport=transport, budget='1')
    assert correr(m) == 1
    # 0,42 → 0,84 (aviso del 80 %) → 1,26: la cuarta petición ya no sale.
    assert claude.peticiones == 3
    assert m.task['status'] == 'blocked'
    assert m.task['coste_usd'] == pytest.approx(3 * coste_opus(100_000, 1000))
    assert 'se agotó' in m.task['summary'] and '1,26 $ de 1,00 $' in m.task['summary']
    assert '--presupuesto' in m.task['summary'] and 'Ajustes' in m.task['summary']
    salidas = eventos(salida)
    textos = [e['texto'] for e in salidas if e['tipo'] == 'evento' and e['rol'] == 'system']
    assert any(t.startswith('Llevas 0,84 $ de 1,00 $ del presupuesto de esta sesión (84 %)') for t in textos)
    assert sum('se agotó' in t for t in textos) == 1
    avisos = [e for e in salidas if e['tipo'] == 'presupuesto']
    assert avisos[0]['aviso'] == 'sesion' and avisos[-1]['agotado'] == 'sesion'
    assert avisos[-1]['sesion'] == {'tope': 1.0, 'gastado': pytest.approx(1.26)}
    # El gasto del mes se apuntó fuera de la sesión: una línea por petición.
    registro = tmp_path / 'home' / 'gasto' / (presupuesto.mes_actual() + '.jsonl')
    assert len(registro.read_text().splitlines()) == 3
    assert not any('gasto' in k for k in json.loads((m.directory / 'task.json').read_text()))
    # El turno queda cerrado con su coste.
    assert m.task['turnos'][-1]['coste'] == pytest.approx(1.26)


def test_subir_el_tope_deja_seguir_y_el_aviso_vuelve(tmp_path, monkeypatch):
    proyecto(tmp_path, monkeypatch)
    claude = Claude(lecturas=4, entrada=100_000, salida=1000)
    claude.instalar(monkeypatch)
    m = TerminalManager('Lee.', home=tmp_path / 'home', model=OPUS, auto=True, budget='1')
    try:
        assert asyncio.run(m.run('Lee.')) == 1 and claude.peticiones == 3
        m.cambiar_presupuesto(sesion=5)
        assert asyncio.run(m.run('Sigue.')) == 0, m.task['summary']
        assert claude.peticiones == 5
    finally:
        m.close()


# ---------------------------------------------------------------- tope del mes

def test_el_tope_del_mes_cuenta_las_demas_sesiones(tmp_path, monkeypatch):
    proyecto(tmp_path, monkeypatch)
    home = tmp_path / 'home'
    GastoMensual(home).apuntar('otra-sesion', 9.85)
    claude = Claude(entrada=50_000, salida=0)   # 0,20 $ por petición
    claude.instalar(monkeypatch)
    monkeypatch.setenv('ALFALFA_PRESUPUESTO_MES', '10')
    m = TerminalManager('Lee.', home=home, model=OPUS, auto=True)
    assert correr(m, 'Lee.') == 1
    assert claude.peticiones == 1
    assert 'presupuesto del mes en este Mac se agotó' in m.task['summary']
    assert '10,05 $ de 10,00 $' in m.task['summary'] and '--presupuesto-mes' in m.task['summary']
    assert GastoMensual(home).total() == pytest.approx(10.05)


# ---------------------------------------------------------------- caché

def test_anthropic_cache_leida_y_escrita(tmp_path, monkeypatch):
    proyecto(tmp_path, monkeypatch)
    claude = Claude(lecturas=1, entrada=1000, leida=80_000, escrita=10_000, salida=100)
    claude.instalar(monkeypatch)
    salida = io.StringIO()
    m = TerminalManager('Lee.', home=tmp_path / 'home', model=OPUS, auto=True, transport=Transport(salida))
    assert correr(m, 'Lee.') == 0, m.task['summary']
    prompt = 1000 + 80_000 + 10_000
    con = 2 * coste_opus(1000, 100, leida=80_000, escrita=10_000)
    sin = 2 * coste_opus(prompt, 100)
    assert m.task['coste_usd'] == pytest.approx(con)
    cache = presupuesto.resumen_cache(m.task)
    assert cache['leidos'] == 160_000 and cache['escritos'] == 20_000
    assert cache['sin_cache'] == pytest.approx(sin) and cache['con_cache'] == pytest.approx(con)
    assert cache['ahorro'] == pytest.approx(sin - con) and cache['ahorro'] > 0
    assert gasto(m.task)['cache']['ahorro'] == pytest.approx(sin - con)
    assert texto_del_gasto(m.task).endswith('Caché: ahorró ' + presupuesto.dolares(sin - con)
                                            + ' (160.000 tokens leídos)')


def test_openai_cached_tokens(tmp_path, monkeypatch):
    proyecto(tmp_path, monkeypatch)
    peticiones = []

    def api(request):
        peticiones.append(1)
        uso = {'input_tokens': 100_000, 'output_tokens': 50, 'input_tokens_details': {'cached_tokens': 60_000}}
        salidas = ([{'type': 'function_call', 'call_id': 'call_1', 'name': 'read_file',
                     'arguments': json.dumps({'path': 'f1.txt'})}] if len(peticiones) == 1 else
                   [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Leído.'}]}])
        return httpx.Response(200, json={'status': 'completed', 'usage': uso, 'output': salidas})

    real = openai.Transporte.__init__

    def init(self, url, clave, tarea=None, timeout=None, transporte=None):
        real(self, url, clave, tarea, timeout, httpx.MockTransport(api))
    monkeypatch.setattr(openai.Transporte, '__init__', init)
    llavero.guardar('openai', 'sk-prueba-123')
    m = TerminalManager('Lee.', home=tmp_path / 'home', model='openai/gpt-4.1', auto=True)
    assert correr(m, 'Lee.') == 0, m.task['summary']
    # gpt-4.1: 2 $ la entrada y 0,50 $ la caché leída por millón.
    ahorro = 2 * 60_000 * (2 - 0.5) / 1_000_000
    cache = presupuesto.resumen_cache(m.task)
    assert cache['leidos'] == 120_000 and cache['escritos'] == 0
    assert cache['ahorro'] == pytest.approx(ahorro)


def test_ollama_no_tiene_cache_que_ensenar():
    task = {'id': 's', 'total_tokens': 10}
    assert presupuesto.resumen_cache(task) is None and 'cache' not in gasto(task)
    assert texto_del_gasto(task) == ''
    # Escribir más de lo que se ha leído aún sale caro, y se dice.
    assert presupuesto.texto_cache({'leidos': 0, 'escritos': 10, 'ahorro': -0.02}).startswith(
        'Caché: aún cuesta 0,02 $ más')


# ---------------------------------------------------------------- subagentes

def test_lo_que_gasta_un_subagente_cuenta_para_la_sesion(tmp_path, monkeypatch):
    from tests.test_alfalfa_hooks import Sesion, llamada
    from tests.test_subagentes import Guion
    from tests.test_subagentes import correr as correr_con_subagentes
    s = Sesion(tmp_path, monkeypatch, None)
    s.m.cambiar_presupuesto(sesion=1.0)
    precio = {'entrada': 4.0, 'salida': 20.0, 'cache_lectura': 0.2}
    real = NativeRuntime._cliente

    def con_precio(rt, timeout):
        resultado = real(rt, timeout)
        rt._precio = precio
        return resultado
    lecturas = [llamada('read_file', {'path': 'notas.txt'}) for _ in range(10)]
    (s.root / 'notas.txt').write_text('hola\n')
    guion = Guion(principal=[llamada('spawn_agent', {'message': 'Lee notas.txt muchas veces.',
                                                     'description': 'Leer'}),
                             {'role': 'assistant', 'content': 'Sigo yo.'},
                             {'role': 'assistant', 'content': 'Más.'}],
                  por_agente={'a1': lecturas})
    # prompt_eval_count=900 y eval_count=10 por petición: 0,0038 $; con 300.000 de entrada, 1,2 $.
    peticion = guion.request()

    async def cara(rt, client, endpoint, payload, **kwargs):
        respuesta = await peticion(rt, client, endpoint, payload, **kwargs)
        if endpoint != '/api/show':
            respuesta['prompt_eval_count'] = 100_000
        return respuesta
    guion.request = lambda: cara
    with patch.object(NativeRuntime, '_cliente', con_precio):
        codigo = correr_con_subagentes(s, guion)
    assert codigo == 1 and s.m.task['status'] == 'blocked'
    # El principal pidió una vez (0,40 $); el subagente, dos (0,80 $) y la tercera ya no salió.
    assert len(guion.payloads['principal']) == 1
    assert len(guion.payloads['a1']) == 2
    assert s.m.task['coste_usd'] == pytest.approx(3 * (100_000 * 4 + 10 * 20) / 1e6)
    sub = s.m.subagentes.agentes['a1']
    assert sub.estado == 'fallido' and 'se agotó' in sub.error
    # El aviso del corte sale una sola vez, en la conversación principal.
    cortes = [e for e in eventos(s.salida) if e['tipo'] == 'evento'
              and e.get('texto', '').startswith('El presupuesto de esta sesión se agotó')]
    assert len(cortes) == 1


# ---------------------------------------------------------------- --stdio

def test_stdio_cambia_los_topes_a_mitad_de_sesion(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    salida = io.StringIO()
    transport = Transport(salida)
    m = TerminalManager('x', home=tmp_path / 'home', transport=transport, budget='2')
    try:
        assert m.presupuesto.para_el_cliente()['sesion'] == {'tope': 2.0, 'gastado': 0.0}
        transport.budget = m.cambiar_presupuesto
        transport.receive(json.dumps({'tipo': 'presupuesto', 'sesion': None, 'mes': '30'}))
        assert m.presupuesto.sesion is None and m.presupuesto.mes == 30.0
        ultimo = eventos(salida)[-1]
        assert ultimo['tipo'] == 'presupuesto' and ultimo['mes']['tope'] == 30.0 and ultimo['sesion']['tope'] is None
        # Sin la clave, ese tope no cambia.
        transport.receive(json.dumps({'tipo': 'presupuesto', 'sesion': 4}))
        assert (m.presupuesto.sesion, m.presupuesto.mes) == (4.0, 30.0)
        for malo in ({'tipo': 'presupuesto', 'sesion': 'mucho'}, {'tipo': 'presupuesto'}):
            transport.receive(json.dumps(malo))
            assert eventos(salida)[-1]['tipo'] == 'error'
        assert (m.presupuesto.sesion, m.presupuesto.mes) == (4.0, 30.0)
    finally:
        m.close()


def test_sin_topes_ni_gasto_no_hay_nada_que_decir(tmp_path):
    p = Presupuesto(tmp_path, {'id': 's'})
    assert p.para_el_cliente() is None
    GastoMensual(tmp_path).apuntar('otra', 0.5)
    assert p.para_el_cliente()['mes']['gastado'] == pytest.approx(0.5)
