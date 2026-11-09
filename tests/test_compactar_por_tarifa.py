"""Modelos que cobran más por contexto largo (Luna pasados 272K, Haiku 5.5 pasados 100K).

La sesión elige entre «ahorro» (por defecto: compacta antes del umbral de tarifa, avisa al
80 % y recorta lo que lo pasaría en una sola petición) y «completo» (la ventana entera, con
aviso del coste). TerminalManager y NativeRuntime reales; sólo se sustituye la inferencia.
"""
import asyncio
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from alfalfa import catalogo
from alfalfa.compactacion import PREFIJO, PROMPT, limite, tokens_aprox, tokens_cortos
from alfalfa.proveedores.nativo import historial
from alfalfa.proveedores.nativo.runtime import NativeRuntime, _tarifa_valida
from alfalfa.stdio import CONTINUAR, Transport
from alfalfa.terminal import TerminalManager, desglose_de

VENTANA = 1_000_000
TARIFA = {'desde': 100_000, 'entrada': 5.0, 'salida': 5.0}


# ---------------------------------------------------------------- catálogo

def test_el_catalogo_lee_los_tramos_de_models_dev():
    crudo = {'anthropic': {'name': 'Anthropic', 'models': {
        'claude-haiku-5-5': {'name': 'Claude Haiku 5.5', 'tool_call': True, 'limit': {'context': 1_000_000},
                             'cost': {'input': 0.1, 'output': 0.5, 'cache_read': 0.01, 'tiers': [
                                 {'input': 0.5, 'output': 2.5, 'cache_read': 0.05,
                                  'tier': {'type': 'context', 'size': 100_000}}]}},
        # El formato antiguo sólo trae `context_over_200k`: el tramo empieza en 200K.
        'viejo': {'tool_call': True, 'limit': {'context': 400_000},
                  'cost': {'input': 1, 'output': 4, 'context_over_200k': {'input': 2, 'output': 6}}},
        'plano': {'tool_call': True, 'limit': {'context': 128_000}, 'cost': {'input': 1, 'output': 2}}}}}
    modelos = catalogo.validar(crudo)['anthropic']['modelos']
    haiku = modelos['claude-haiku-5-5']
    assert haiku['precio']['tramo']['desde'] == 100_000
    assert catalogo.tarifa(haiku['precio']) == TARIFA
    assert catalogo.tarifa(modelos['viejo']['precio']) == {'desde': 200_000, 'entrada': 2.0, 'salida': 1.5}
    assert catalogo.tarifa(modelos['plano']['precio']) is None
    resumen = catalogo.resumen(haiku)
    assert resumen['ventana'] == 1_000_000 and resumen['barato_hasta'] == 100_000
    assert resumen['tarifa_larga'] == {'entrada': 5.0, 'salida': 5.0}
    # El coste de una petición larga va entero al precio del tramo.
    assert catalogo.coste(haiku['precio'], 150_000, 0) == pytest.approx(150_000 * 0.5 / 1e6)
    assert catalogo.coste(haiku['precio'], 50_000, 0) == pytest.approx(50_000 * 0.1 / 1e6)
    assert catalogo.veces({'entrada': 2.0, 'salida': 1.5}) == '2× la entrada y 1,5× la salida'


def test_tabla_propia_si_el_catalogo_aun_no_trae_el_tramo():
    precio = {'entrada': 0.1, 'salida': 0.5}
    assert catalogo.tarifa(precio, 'openai', 'gpt-6-luna') == {'desde': 272_000, 'entrada': 2.0, 'salida': 1.5}
    assert catalogo.tarifa(precio, 'anthropic', 'claude-haiku-5-5') == TARIFA
    # Quien no cobra por token (sin precio) no tiene tarifa que cuidar.
    assert catalogo.tarifa(None, 'openai', 'gpt-6-luna') is None
    assert catalogo.tarifa(precio, 'openrouter', 'gpt-6-luna') is None


def test_una_copia_guardada_sin_tramos_se_renueva_con_red(tmp_path, monkeypatch):
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path))
    catalogo.olvidar()
    destino = catalogo.ruta()
    destino.parent.mkdir(parents=True)
    vieja = {'fuente': catalogo.URL, 'fecha': 4e9, 'proveedores': {'openai': {'id': 'openai', 'nombre': 'OpenAI',
             'modelos': {'gpt-6-luna': {'id': 'gpt-6-luna', 'herramientas': True, 'ventana': 1_050_000}}}}}
    destino.write_text(json.dumps(vieja))
    assert catalogo._leer(destino)['fecha'] == 0
    destino.write_text(json.dumps(dict(vieja, formato=catalogo.FORMATO)))
    assert catalogo._leer(destino)['fecha'] == 4e9
    catalogo.olvidar()


def test_limite_y_tarifa_saneada():
    assert limite(VENTANA, None, 100_000) == 90_000
    assert limite(VENTANA, 50_000, 100_000) == 50_000
    assert limite(VENTANA) == 900_000
    assert tokens_cortos(1_050_000) == '1,05M' and tokens_cortos(100_000) == '100K'
    assert _tarifa_valida(TARIFA) == TARIFA
    assert _tarifa_valida({'desde': 100_000, 'entrada': 1, 'salida': 1}) is None
    assert _tarifa_valida({'desde': 'mucho'}) is None and _tarifa_valida({'desde': 5}) is None


def test_selector_de_openai_y_alfalfa_dicen_ventana_y_umbral():
    from alfalfa.proveedores.alfalfa import _ventanas
    assert _ventanas({'ventana': 1_000_000, 'barato_hasta': 100_000}) == {'ventana': 1_000_000,
                                                                          'barato_hasta': 100_000}
    # Sin contexto completo el servicio anuncia el umbral como ventana: no hay tramo que enseñar.
    assert _ventanas({'ventana': 100_000}) == {'ventana': 100_000}
    assert _ventanas({'ventana': True, 'barato_hasta': -1}) == {}


# ---------------------------------------------------------------- motor

class Modelo:
    """Ventana de 1M y tramo caro a partir de 100K, como Haiku 5.5 en Alfalfa Cloud."""

    def __init__(self, pasos=(), usos=(), tarifa=TARIFA, salida=10):
        self.pasos, self.usos, self.payloads, self.resumenes = list(pasos), list(usos), [], []
        self.tarifa, self.salida = tarifa, salida

    def request(self):
        modelo = self

        async def request(rt, client, endpoint, payload, **kwargs):
            if endpoint == '/api/show':
                info = {'capabilities': ['tools'], 'model_info': {'x.context_length': VENTANA}}
                if modelo.tarifa:
                    info['alfalfa_tarifa'] = modelo.tarifa
                return info
            copia = json.loads(json.dumps(payload))
            if payload['messages'][-1]['content'].startswith(PROMPT):
                modelo.resumenes.append(copia)
                return dict(message={'role': 'assistant', 'content': 'Resumen: falta el paso 3.'}, done=True,
                            done_reason='stop', prompt_eval_count=1000, eval_count=50)
            modelo.payloads.append(copia)
            paso = modelo.pasos.pop(0) if modelo.pasos else None
            uso = modelo.usos.pop(0) if modelo.usos else 900
            return dict(message=paso or {'role': 'assistant', 'content': 'Hecho.'}, done=True, done_reason='stop',
                        prompt_eval_count=uso, eval_count=modelo.salida)
        return request


def leer(path='a.txt'):
    return {'role': 'assistant', 'content': '',
            'tool_calls': [{'function': {'name': 'read_file', 'arguments': {'path': path}}}]}


class Sesion:
    def __init__(self, tmp_path, monkeypatch, *, contexto=None, resume=None, objetivo='Haz tres pasos.'):
        self.root = tmp_path / 'project'
        self.root.mkdir(exist_ok=True)
        (self.root / 'a.txt').write_text('contenido\n')
        monkeypatch.chdir(self.root)
        self.salida = io.StringIO()
        self.transport = Transport(self.salida)
        self.m = TerminalManager(objetivo, home=tmp_path / 'alfalfa', transport=self.transport, resume=resume)
        if contexto:
            self.m.elegir_contexto(contexto)

    def correr(self, modelo, prompt='Haz tres pasos.'):
        async def run():
            with patch.object(NativeRuntime, 'request', modelo.request()):
                return await self.m.run(prompt)
        return asyncio.run(asyncio.wait_for(run(), 60))

    def filas(self, tipo):
        return [json.loads(linea) for linea in self.salida.getvalue().splitlines() if json.loads(linea)['tipo'] == tipo]

    def avisos(self):
        return [f for f in self.filas('evento') if isinstance(f.get('tarifa'), dict)]


def test_ahorro_avisa_al_80_por_ciento_y_compacta_antes_del_umbral(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch)
    # 85K: pasa el aviso (80K) sin llegar a compactar (90K); luego 91K compacta.
    modelo = Modelo([leer(), leer(), leer()], usos=[900, 85_000, 91_000, 900])
    assert s.correr(modelo) == 0
    aviso, = s.avisos()
    assert aviso['tarifa']['modo'] == 'ahorro' and aviso['tarifa']['desde'] == 100_000
    assert aviso['tarifa']['compactar_en'] == 90_000 and 0 < aviso['tarifa']['faltan'] < 5_000
    assert aviso['texto'].startswith('A partir de 100K tokens, ') and 'cobra 5× toda la petición' in aviso['texto']
    assert 'Compactaré en unos' in aviso['texto']
    # La compactación llegó antes de los 100K y ninguna petición los pasó.
    assert len(modelo.resumenes) == 1 and s.m.task['runtime_compactions'] == 1
    contexto = s.filas('contexto')[-1]
    assert contexto['umbral'] == 90_000 and contexto['tarifa'] == dict(TARIFA, modo='ahorro')
    # Se guarda en la transcripción para reabrir la sesión con su aviso.
    filas = [json.loads(linea) for linea in (s.m.directory / (s.m.task['id'] + '.jsonl')).read_text().splitlines()]
    assert any(isinstance(f.get('tarifa'), dict) for f in filas)
    s.m.close()


def test_contexto_completo_usa_la_ventana_y_avisa_al_pasar_el_umbral(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, contexto='completo')
    modelo = Modelo([leer(), leer()], usos=[900, 101_000, 900])
    assert s.correr(modelo) == 0
    assert modelo.resumenes == []
    aviso, = s.avisos()
    assert aviso['tarifa']['modo'] == 'completo' and 'Contexto completo' in aviso['texto']
    assert s.filas('contexto')[-1]['umbral'] == VENTANA * 9 // 10
    assert s.m.task['contexto_modo'] == 'completo'
    s.m.close()


def test_sin_tarifa_nada_cambia(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch)
    modelo = Modelo([leer(), leer()], usos=[900, 150_000, 900], tarifa=None)
    assert s.correr(modelo) == 0
    assert modelo.resumenes == [] and s.avisos() == []
    assert 'tarifa' not in s.filas('contexto')[-1]
    s.m.close()


def test_cambiar_de_modo_vale_ya_y_se_guarda(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch)
    assert s.correr(Modelo([leer()], usos=[900, 900])) == 0
    s.transport.context_mode = s.m.cambiar_contexto
    s.transport.receive(json.dumps({'tipo': 'contexto', 'modo': 'completo'}))
    sesion = s.filas('sesion')[-1]
    assert sesion['contexto'] == 'completo'
    assert s.filas('contexto')[-1]['umbral'] == VENTANA * 9 // 10
    assert json.loads((s.m.directory / 'task.json').read_text())['contexto_modo'] == 'completo'
    assert any(f['texto'].startswith('Contexto completo: uso la ventana entera; a partir de 100K')
               for f in s.filas('evento'))
    s.transport.receive(json.dumps({'tipo': 'contexto', 'modo': 'barato'}))
    assert s.filas('error')
    s.m.close()
    otro = TerminalManager('x', home=tmp_path / 'otro')
    with pytest.raises(ValueError, match='Modo de contexto'):
        otro.elegir_contexto('gratis')
    otro.close()


def test_una_salida_enorme_se_recorta_antes_de_pasar_el_umbral(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch)
    assert s.correr(Modelo([leer()], usos=[900, 900])) == 0
    rt = s.m.runtime()
    rt.context_tokens, rt._tarifa = VENTANA, dict(TARIFA)
    enorme = 'línea de salida\n' * 30_000
    rt.state['messages'].append({'role': 'tool', 'tool_name': 'run_command', 'content': enorme})
    contexto = rt._context_messages()
    assert tokens_aprox(json.dumps(contexto, ensure_ascii=False)) > 100_000
    recortado = rt._recortar_por_tarifa(contexto)
    assert tokens_aprox(json.dumps(recortado, ensure_ascii=False)) <= 95_000
    salida = recortado[-1]['content']
    assert salida.startswith('línea de salida') and 'Salida recortada para no pasar de 100K tokens' in salida
    # El historial completo no cambia y la siguiente petición manda el mismo recorte (caché).
    assert rt.state['messages'][-1]['content'] == enorme
    assert rt._context_messages()[-1]['content'] == salida
    # session.json sólo guarda hasta cuántos tokens, no el texto recortado.
    tope, = rt.state['recortes_tarifa'].values()
    assert type(tope) is int and len(json.dumps(rt.state['recortes_tarifa'])) < 200
    assert any(f['texto'].startswith('Recorté una salida') for f in s.filas('evento'))
    # En contexto completo no se recorta.
    s.m.task['contexto_modo'] = 'completo'
    rt.state.pop('recortes_tarifa')
    assert rt._recortar_por_tarifa(rt._context_messages())[-1]['content'] == enorme
    s.m.close()


def test_seguir_en_una_sesion_nueva_con_el_resumen(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, contexto='completo')
    modelo = Modelo([leer()], usos=[900, 900])
    assert s.correr(modelo) == 0

    async def continuar():
        with patch.object(NativeRuntime, 'request', modelo.request()):
            return await s.m.continuar_en_nueva()
    nueva = asyncio.run(continuar())
    assert nueva and len(modelo.resumenes) == 1
    s.m.close()
    carpeta = tmp_path / 'alfalfa' / 'sesiones' / nueva
    task = json.loads((carpeta / 'task.json').read_text())
    assert task['continuada_de'] == {'sesion': s.m.task['id']} and task['contexto_modo'] == 'completo'
    assert task['status'] == 'created' and task['turnos'] == []
    estado = historial.leer(carpeta / 'native' / nueva / 'session.json')
    assert [m['role'] for m in estado['messages']] == ['system']
    assert estado['checkpoint']['through'] == 1 and estado['checkpoint']['text'] == 'Resumen: falta el paso 3.'
    fila, = [json.loads(linea) for linea in (carpeta / (nueva + '.jsonl')).read_text().splitlines()]
    assert fila['compactacion']['motivo'] == 'continuacion'
    # Reanudarla manda sólo instrucciones, la historia del usuario, el resumen y el mensaje nuevo.
    otra = Sesion(tmp_path, monkeypatch, resume=nueva)
    siguiente = Modelo(usos=[900])
    assert otra.correr(siguiente, 'Sigue.') == 0
    mensajes = siguiente.payloads[0]['messages']
    assert mensajes[-2] == {'role': 'user', 'content': PREFIJO + '\nResumen: falta el paso 3.'}
    assert mensajes[-1] == {'role': 'user', 'content': 'Sigue.'}
    assert not any(m['role'] in ('tool', 'assistant') for m in mensajes)
    otra.m.close()


def test_continuar_desde_el_cliente_corta_el_turno_y_se_cierra():
    transport = Transport(io.StringIO())
    transport.in_turn = False
    transport.receive(json.dumps({'tipo': 'continuar_en_nueva'}))

    async def siguiente():
        return await transport.next_message()
    assert asyncio.run(siguiente()) == (CONTINUAR, None)


def test_desglose_lleva_la_tarifa():
    assert desglose_de({'context_tarifa': dict(TARIFA, modo='ahorro')})['tarifa'] == dict(TARIFA, modo='ahorro')
    assert 'tarifa' not in desglose_de({})


def test_procedencia_documenta_el_cambio():
    texto = (Path(catalogo.__file__).resolve().parents[1] / 'compactacion' / 'PROCEDENCIA.md').read_text()
    assert 'Ahorro' in texto and 'Contexto completo' in texto


def test_seguir_en_una_sesion_nueva_desde_la_app(tmp_path):
    """El proceso de producción: el aviso pide continuar, compacta, dice la sesión nueva y se cierra."""
    from tests.test_compactar_sesion import Cliente, compactacion
    cliente = Cliente(tmp_path, 'Haz tres pasos.')
    try:
        assert cliente.tipo('sesion')['contexto'] == 'ahorro'
        assert cliente.tipo('turno_terminado')['estado'] == 'completed'
        cliente.enviar(tipo='continuar_en_nueva')
        assert cliente.hasta(compactacion)['compactacion']['estado'] == 'hecha'
        cambio = cliente.tipo('sesion_cambiada')
        assert cambio['continuada_de'] and cambio['id'] != cambio['continuada_de']
        cliente.proc.wait(timeout=15)
        assert cliente.proc.returncode == 0, cliente.proc.stderr.read()
        assert (tmp_path / 'home' / 'sesiones' / cambio['id'] / 'task.json').is_file()
    finally:
        cliente.matar()


# ---------------------------------------------------------------- arreglos de la revisión

def salida_de(letra, tokens):
    """Una salida de herramienta de unos `tokens` tokens, distinta para cada letra."""
    return (letra * 99 + '\n') * (tokens * 4 // 100)


def test_el_recorte_empieza_por_lo_que_aun_no_esta_en_cache(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch)
    assert s.correr(Modelo([leer()], usos=[900, 900])) == 0
    rt = s.m.runtime()
    rt.context_tokens, rt._tarifa = VENTANA, dict(TARIFA)
    viejas = [salida_de(c, 19_000) for c in 'abcd']
    for texto in viejas:
        rt.state['messages'].append({'role': 'tool', 'tool_name': 'read_file', 'content': texto})
    # La última petición midió todo eso: está en la caché del proveedor.
    medido = tokens_aprox(json.dumps(rt._context_messages(), ensure_ascii=False))
    rt.state['context_usage'] = {'tokens': medido, 'messages': len(rt.state['messages'])}
    nuevas = [salida_de(c, 19_000) for c in 'xy']
    for texto in nuevas:
        rt.state['messages'].append({'role': 'tool', 'tool_name': 'read_file', 'content': texto})
    contexto = rt._recortar_por_tarifa(rt._context_messages())
    assert tokens_aprox(json.dumps(contexto, ensure_ascii=False)) <= 95_000
    textos = [m['content'] for m in contexto if m.get('role') == 'tool']
    # Lo que ya estaba en caché llega igual; se recorta lo nuevo, primero lo más nuevo.
    assert textos[-6:-2] == viejas
    x, y = textos[-2:]
    assert x != nuevas[0] and y != nuevas[1]
    assert tokens_aprox(y) < tokens_aprox(x)
    s.m.close()


def test_los_recortes_se_purgan_al_compactar(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch)
    modelo = Modelo([leer()], usos=[900, 900])
    assert s.correr(modelo) == 0
    rt = s.m.runtime()
    rt.context_tokens, rt._tarifa = VENTANA, dict(TARIFA)
    rt.state['messages'].append({'role': 'tool', 'tool_name': 'run_command', 'content': salida_de('z', 120_000)})
    rt._recortar_por_tarifa(rt._context_messages())
    assert rt.state['recortes_tarifa']
    rt.save()

    async def compactar():
        with patch.object(NativeRuntime, 'request', modelo.request()):
            await rt.compact_now()
    asyncio.run(compactar())
    assert 'recortes_tarifa' not in rt.state
    s.m.close()


def test_el_razonamiento_que_no_se_reenvia_no_adelanta_la_compactacion(tmp_path, monkeypatch):
    """Luna en xhigh: 80K de prompt y 30K de salida casi toda razonamiento. La siguiente petición
    lleva unos 80K (el razonamiento no vuelve), por debajo de los 90K de Ahorro."""
    s = Sesion(tmp_path, monkeypatch)
    modelo = Modelo([leer(), leer()], usos=[900, 80_000, 80_500], salida=30_000)
    assert s.correr(modelo) == 0
    assert modelo.resumenes == [] and 'checkpoint' not in s.m.runtime().state
    s.m.close()


def test_pasar_de_completo_a_ahorro_conserva_el_resumen_anterior(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, contexto='completo')
    modelo = Modelo([leer()], usos=[900, 900])
    assert s.correr(modelo) == 0
    rt = s.m.runtime()

    async def compactar():
        with patch.object(NativeRuntime, 'request', modelo.request()):
            await rt.compact_now()
    asyncio.run(compactar())
    resumen = {'role': 'user', 'content': PREFIJO + '\nResumen: falta el paso 3.'}
    # Siguió en contexto completo hasta unos 120K y pasa a Ahorro: el resumen se pide por debajo
    # de los 95K, sin perder el anterior.
    for letra in 'abcdef':
        rt.state['messages'].append({'role': 'tool', 'tool_name': 'read_file', 'content': salida_de(letra, 20_000)})
    rt.save()
    s.m.task['contexto_modo'] = 'ahorro'
    asyncio.run(compactar())
    pedido = modelo.resumenes[-1]['messages']
    assert resumen in pedido and pedido[-1]['content'].startswith(PROMPT)
    assert tokens_aprox(json.dumps(pedido, ensure_ascii=False)) <= 95_000
    # Se quitaron las salidas más antiguas tras el resumen; la última sigue.
    salidas = [m['content'] for m in pedido if m.get('role') == 'tool']
    assert salidas and salidas[-1] == salida_de('f', 20_000) and salida_de('a', 20_000) not in salidas
    s.m.close()


def test_si_lo_fijo_no_cabe_bajo_el_umbral_no_compacta_en_bucle(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch)
    for n in range(14):
        (s.root / ('f' + str(n) + '.txt')).write_text('archivo ' + str(n) + '\n')
    # Un umbral de 5K: las instrucciones y las herramientas ya pasan del 90 %.
    modelo = Modelo([leer('f' + str(n) + '.txt') for n in range(14)], usos=[6_000] * 20,
                    tarifa={'desde': 5_000, 'entrada': 5, 'salida': 5})
    assert s.correr(modelo) == 0
    assert len(modelo.resumenes) == 1
    avisos = [f['texto'] for f in s.filas('evento') if 'No volveré a compactar sola' in f['texto']]
    assert len(avisos) == 1
    s.m.close()


def test_el_historial_reenvia_el_aviso_de_tarifa(tmp_path, monkeypatch):
    from alfalfa.stdio import history
    s = Sesion(tmp_path, monkeypatch)
    modelo = Modelo([leer(), leer(), leer()], usos=[900, 85_000, 91_000, 900])
    assert s.correr(modelo) == 0
    s.m.close()
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path / 'alfalfa'))
    datos = history(s.m.task['id'])
    aviso, = [e for e in datos['eventos'] if isinstance(e.get('tarifa'), dict)]
    assert aviso['tarifa']['modo'] == 'ahorro' and aviso['tarifa']['desde'] == 100_000
    assert datos['contexto']['tarifa'] == dict(TARIFA, modo='ahorro')
    # Lo que la app de Mac prueba (PriceTierTests) tiene la misma forma que lo que manda el motor.
    fixture = Path(__file__).resolve().parents[1] / 'alfalfa-mac/Tests/AlfalfaKitTests/Fixtures/historial_tarifa.jsonl'
    guardado, = [json.loads(linea) for linea in fixture.read_text().splitlines()]
    salida = io.StringIO()
    Transport(salida).emit('historial', sesion=s.m.task['id'], **datos)
    real = json.loads(salida.getvalue())
    assert set(guardado) == set(real)
    suyo, = [e for e in guardado['eventos'] if 'tarifa' in e]
    assert set(suyo) == set(aviso) and set(suyo['tarifa']) == set(aviso['tarifa'])
    assert set(guardado['contexto']['tarifa']) == set(real['contexto']['tarifa'])


def test_una_sesion_continuada_no_hereda_incidencias_sin_contexto(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch)
    modelo = Modelo([leer()], usos=[900, 900])
    assert s.correr(modelo) == 0
    rt = s.m.runtime()
    rt.state['changes'] = [{'path': 'a.txt', 'after': 'abc'}]
    rt.evidence.record_tool_error('edit_file', {'path': 'a.txt'}, 'No se encontró el texto.')
    assert rt.state['unresolved_records'] and rt.state['unresolved']
    rt.save()

    async def continuar():
        with patch.object(NativeRuntime, 'request', modelo.request()):
            return await s.m.continuar_en_nueva()
    nueva = asyncio.run(continuar())
    s.m.close()
    estado = historial.leer(tmp_path / 'alfalfa' / 'sesiones' / nueva / 'native' / nueva / 'session.json')
    assert not estado.get('unresolved_records') and not estado.get('unresolved')
    assert estado['changes'] == [{'path': 'a.txt', 'after': 'abc'}]


def test_los_subagentes_heredan_el_modo_de_contexto(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, contexto='completo')
    capturadas = []

    def empezar(self, sub, mensaje):
        capturadas.append(sub.task)
    from alfalfa import subagentes
    monkeypatch.setattr(subagentes.Subagentes, '_empezar', empezar)
    rt = s.m.runtime()
    asyncio.run(s.m.subagentes.lanzar(rt, {'message': 'Mira a.txt', 'background': True}))
    assert capturadas[0]['contexto_modo'] == 'completo'
    s.m.close()


def test_seguir_en_una_sesion_nueva_espera_a_que_termine_el_turno():
    salida = io.StringIO()
    transport = Transport(salida)
    transport.in_turn = True

    class Trabajo:
        cancelado = False

        def done(self):
            return False

        def cancel(self):
            self.cancelado = True
    transport.worker = Trabajo()
    transport.receive(json.dumps({'tipo': 'continuar_en_nueva'}))
    assert not transport.worker.cancelado and transport.continue_new
    assert 'cuando termine este turno' in salida.getvalue()
    transport.in_turn = False

    async def siguiente():
        return await transport.next_message()
    assert asyncio.run(siguiente()) == (CONTINUAR, None)
