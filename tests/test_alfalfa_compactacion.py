"""Compactación del contexto al estilo de Codex en AlfalfaCode.

TerminalManager y NativeRuntime reales; sólo se sustituye la inferencia
(NativeRuntime.request). Las peticiones de resumen se reconocen porque su
último mensaje es el PROMPT de compactación y no declaran herramientas.
"""
import asyncio
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from alfalfa import compactacion
from alfalfa.compactacion import (MAX_TOKENS_USUARIO, PREFIJO, PROMPT, es_resumen, limite, recortar_medio,
                                    seleccionar_usuario, tokens_aprox)
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.stdio import Transport
from alfalfa.terminal import TerminalManager, main

VENTANA = 131072
CARPETA = Path(compactacion.__file__).resolve().parent


# ---------------------------------------------------------------- algoritmo

def test_tokens_aproximados_como_codex():
    assert tokens_aprox('') == 0 and tokens_aprox('a') == 1 and tokens_aprox('abcd') == 1
    assert tokens_aprox('abcde') == 2 and tokens_aprox('ñ' * 4) == 2  # bytes UTF-8, no caracteres


def test_limite_es_el_90_por_ciento_o_el_del_usuario_si_es_menor():
    assert limite(VENTANA) == VENTANA * 9 // 10
    assert limite(VENTANA, 5000) == 5000
    assert limite(10000, 50000) == 9000
    assert limite(None, 4000) == 4000 and limite(None) is None


def test_recorte_por_el_medio_conserva_principio_y_final():
    texto = 'A' * 1000 + 'B' * 1000
    recortado = recortar_medio(texto, 100)
    assert recortado.startswith('A' * 200) and recortado.endswith('B' * 200)
    assert '…400 tokens recortados…' in recortado
    assert recortar_medio('corto', 100) == 'corto'
    # Nunca parte un carácter de varios bytes.
    assert recortar_medio('ñ' * 1000, 10).encode('utf-8')


def test_seleccion_de_mensajes_del_usuario():
    viejo, medio, nuevo = 'v' * 8000, 'm' * 60000, 'n' * 20000  # 2000, 15000 y 5000 tokens
    elegidos = seleccionar_usuario([viejo, medio, nuevo])
    # Del más nuevo al más viejo: n (5000) + m (15000) llenan los 20 000; v queda fuera.
    assert elegidos == [medio, nuevo]
    elegidos = seleccionar_usuario([viejo, 'x' * 100000, nuevo])
    assert elegidos[-1] == nuevo and len(elegidos) == 2 and 'tokens recortados' in elegidos[0]
    assert sum(tokens_aprox(t) for t in elegidos) <= MAX_TOKENS_USUARIO + 10
    resumen = PREFIJO + '\nantes'
    assert es_resumen(resumen) and seleccionar_usuario(['a', resumen, 'b']) == ['a', 'b']


def test_prompts_traducidos_y_avisos_de_licencia():
    assert PROMPT.startswith('Estás haciendo una COMPACTACIÓN DEL CONTEXTO')
    assert 'resumen de traspaso' in PROMPT
    # Apartados como la compactación de Claude Code: el siguiente paso sólo sigue el encargo actual.
    for apartado in ('Encargo actual', 'Pedidos anteriores', 'Pendiente', 'Siguiente paso'):
        assert apartado in PROMPT
    assert 'ni retomes pedidos antiguos' in PROMPT and 'no retomes pedidos anteriores' in PREFIJO
    assert PREFIJO.startswith('Otro modelo de lenguaje empezó a resolver este problema')
    assert 'Copyright 2025 OpenAI' in (CARPETA / 'NOTICE').read_text()
    assert 'Apache License' in (CARPETA / 'LICENSE').read_text()
    procedencia = (CARPETA / 'PROCEDENCIA.md').read_text()
    assert 'faeb379646bdbad016e11f7c9ca91113cad4bc9d' in procedencia and 'compact.rs' in procedencia


# ---------------------------------------------------------------- sesiones

class Modelo:
    """Guion de respuestas de chat; los resúmenes se contestan aparte."""

    def __init__(self, pasos, *, resumen='Resumen: falta el paso 3.', usos=None):
        self.pasos, self.payloads, self.resumenes = list(pasos), [], []
        self.resumen = resumen
        self.usos = list(usos or [])

    def request(self):
        modelo = self

        async def request(rt, client, endpoint, payload, **kwargs):
            if endpoint == '/api/show':
                return {'capabilities': ['tools', 'thinking'], 'model_info': {'x.context_length': VENTANA}}
            copia = json.loads(json.dumps(payload))
            if payload['messages'][-1]['content'] == PROMPT:
                modelo.resumenes.append(copia)
                respuesta = modelo.resumen(copia) if callable(modelo.resumen) else modelo.resumen
                if isinstance(respuesta, dict):
                    return respuesta
                return dict(message={'role': 'assistant', 'content': respuesta}, done=True, done_reason='stop',
                            prompt_eval_count=1000, eval_count=50)
            modelo.payloads.append(copia)
            paso = modelo.pasos.pop(0) if modelo.pasos else None
            mensaje = paso(copia) if callable(paso) else (paso or {'role': 'assistant', 'content': 'Hecho.'})
            uso = modelo.usos.pop(0) if modelo.usos else 900
            return dict(message=mensaje, done=True, done_reason='stop', prompt_eval_count=uso, eval_count=10)
        return request


def llamada(nombre, argumentos):
    return {'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': nombre, 'arguments': argumentos}}]}


def leer(path='a.txt'):
    return llamada('read_file', {'path': path})


class Sesion:
    def __init__(self, tmp_path, monkeypatch, *, resume=None, compact_limit=None, objetivo='Haz tres pasos.'):
        self.root = tmp_path / 'project'
        self.root.mkdir(exist_ok=True)
        for nombre in 'abcdefgh':
            (self.root / (nombre + '.txt')).write_text('contenido ' + nombre + '\n')
        monkeypatch.chdir(self.root)
        self.salida = io.StringIO()
        self.transport = Transport(self.salida)
        self.m = TerminalManager(objetivo, home=tmp_path / 'alfalfa', transport=self.transport, resume=resume,
                                 compact_limit=compact_limit)

    def correr(self, modelo, prompt='Haz tres pasos.', antes=None, cerrar=True):
        async def run():
            if antes:
                antes(self)
            with patch.object(NativeRuntime, 'request', modelo.request()):
                return await self.m.run(prompt)
        codigo = asyncio.run(asyncio.wait_for(run(), 60))
        if cerrar:
            self.m.close()
        return codigo

    def estado(self):
        return json.loads((self.m.directory / 'native' / self.m.task['id'] / 'session.json').read_text())

    def filas(self, tipo):
        return [json.loads(l) for l in self.salida.getvalue().splitlines() if json.loads(l)['tipo'] == tipo]


def test_se_compacta_al_90_por_ciento_y_sigue_con_el_contexto_nuevo(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch)
    # La segunda respuesta mide 118 000 tokens de contexto: supera 117 964 (90 %).
    modelo = Modelo([leer(), leer(), {'role': 'assistant', 'content': 'Terminado.'}],
                    usos=[900, VENTANA * 9 // 10 - 10 + 1, 900])
    assert s.correr(modelo) == 0
    resumen, = modelo.resumenes
    # El resumen lo pide el mismo modelo, sin herramientas y con el prompt de Codex al final.
    assert 'tools' not in resumen and resumen['model'] == 'deepseek-v4.1-flash:cloud'
    assert resumen['think'] is False and resumen['options']['num_ctx'] == VENTANA
    assert [m['role'] for m in resumen['messages']][:2] == ['system', 'user']
    # Después: instrucciones + mensajes reales del usuario + resumen; nada del historial antiguo.
    tercero = modelo.payloads[2]['messages']
    assert tercero[0]['role'] == 'system' and tercero[1] == {'role': 'user', 'content': 'Haz tres pasos.'}
    assert tercero[2] == {'role': 'user', 'content': PREFIJO + '\nResumen: falta el paso 3.'}
    assert len(tercero) == 3 and not any(m['role'] == 'tool' for m in tercero)
    # El historial completo sigue en la sesión.
    estado = s.estado()
    assert sum(m['role'] == 'tool' for m in estado['messages']) == 2
    punto = estado['checkpoint']
    assert punto['estilo'] == 'codex' and punto['motivo'] == 'auto' and punto['usuario'] == ['Haz tres pasos.']
    assert punto['through'] == len(estado['messages']) - 1 and s.m.task['runtime_compactions'] == 1
    textos = [f['texto'] for f in s.filas('evento')]
    assert any(t.startswith('Contexto compactado (auto)') for t in textos)
    contexto = s.filas('contexto')
    # Reemitido tras compactar: instrucciones y herramientas se quedan, los mensajes pasan
    # a ser el resumen y el total baja del umbral.
    tras = contexto[-2]
    assert tras['limite'] == VENTANA and tras['desglose']['mensajes'] < 1000
    assert tras['tokens'] < VENTANA * 9 // 10 and tras['estimado'] == tras['tokens']


def test_bajo_el_limite_no_se_compacta(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch)
    modelo = Modelo([leer(), leer()], usos=[VENTANA * 9 // 10 - 200, VENTANA * 9 // 10 - 200])
    assert s.correr(modelo) == 0
    assert modelo.resumenes == [] and 'checkpoint' not in s.estado()


def test_limite_configurado_por_el_usuario(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch, compact_limit=5000)
    modelo = Modelo([leer(), leer()], usos=[5200, 900])
    assert s.correr(modelo) == 0
    assert len(modelo.resumenes) == 1
    with pytest.raises(ValueError, match='al menos 1000'):
        TerminalManager('x', home=tmp_path / 'otro', compact_limit=999)


def test_compactar_a_mano_desde_el_cliente(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch)

    def pedir(payload):
        s.transport.receive(json.dumps({'tipo': 'compactar'}))
        return leer()
    modelo = Modelo([pedir, {'role': 'assistant', 'content': 'Fin.'}])
    assert s.correr(modelo) == 0
    assert len(modelo.resumenes) == 1 and s.estado()['checkpoint']['motivo'] == 'manual'
    assert any('Compactación pedida' in f['texto'] for f in s.filas('evento'))
    assert s.transport.take_compact() is False  # se consumió una sola vez


def test_solo_se_conservan_mensajes_reales_del_usuario(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch)

    def mensaje_a_mitad(payload):
        s.transport.message('Usa también b.txt')
        return leer()
    modelo = Modelo([mensaje_a_mitad, leer(), {'role': 'assistant', 'content': 'Fin.'}],
                    usos=[900, VENTANA, 900])
    assert s.correr(modelo) == 0
    punto = s.estado()['checkpoint']
    assert punto['usuario'] == ['Haz tres pasos.', 'Usa también b.txt']
    # El encargo y el mensaje del cliente; no los resultados ni los avisos del arnés.
    assert s.estado()['user_messages'] == [1, 4]


def test_segunda_compactacion_no_guarda_el_resumen_anterior_como_usuario(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch)
    resumenes = iter(['Primer resumen.', 'Segundo resumen.'])
    modelo = Modelo([leer(n + '.txt') for n in 'abcdefg'] + [{'role': 'assistant', 'content': 'Fin'}],
                    resumen=lambda p: next(resumenes), usos=[VENTANA, 900, 900, 900, 900, VENTANA, 900, 900])
    assert s.correr(modelo) == 0
    assert len(modelo.resumenes) == 2
    # El segundo resumen ve el primero dentro del contexto que resume…
    assert any(m['content'] == PREFIJO + '\nPrimer resumen.' for m in modelo.resumenes[1]['messages'])
    # …pero el contexto nuevo sólo lleva el segundo, y el usuario sin resúmenes.
    final = modelo.payloads[-1]['messages']
    assert [m['content'] for m in final if es_resumen(m['content'])] == [PREFIJO + '\nSegundo resumen.']
    assert s.estado()['checkpoint']['usuario'] == ['Haz tres pasos.']


@pytest.mark.parametrize('respuesta', [
    dict(message={'role': 'assistant', 'content': '   '}, done=True, done_reason='stop'),
    dict(message={'role': 'assistant', 'content': 'Resumen a medias'}, done=True, done_reason='length'),
    dict(message={'role': 'assistant', 'content': 'x'}, done=False),
])
def test_resumen_fallido_conserva_el_historial_y_sigue(tmp_path, monkeypatch, respuesta):
    s = Sesion(tmp_path, monkeypatch)
    modelo = Modelo([leer(), leer(), {'role': 'assistant', 'content': 'Fin'}], resumen=respuesta,
                    usos=[VENTANA, 900, 900])
    assert s.correr(modelo) == 0
    assert 'checkpoint' not in s.estado()
    assert any(m['role'] == 'tool' for m in modelo.payloads[-1]['messages'])


def test_reanudar_reconstruye_el_contexto_desde_el_punto_de_control(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch)
    modelo = Modelo([leer(), leer(), {'role': 'assistant', 'content': ''}, {'role': 'assistant', 'content': ''},
                     {'role': 'assistant', 'content': ''}], usos=[VENTANA, 900, 900, 900, 900])
    s.correr(modelo)  # termina sin respuesta: la sesión queda reanudable
    ident = s.m.task['id']
    s2 = Sesion(tmp_path, monkeypatch, resume=ident)
    modelo2 = Modelo([])
    s2.correr(modelo2, prompt='Sigue.')
    mensajes = modelo2.payloads[0]['messages']
    assert mensajes[1]['content'] == 'Haz tres pasos.' and es_resumen(mensajes[2]['content'])
    assert {'role': 'user', 'content': 'Sigue.'} in mensajes


def test_los_agentes_del_integrador_conservan_su_compactacion(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch)
    try:
        rt = NativeRuntime(s.m, dict(s.m.task))
        assert rt.compaction is None and rt.compact_request is None
        rt.user_message('hola')
        assert 'user_messages' not in rt.state
        assert s.m.runtime().compaction == 'codex'
    finally:
        s.m.close()


def test_opcion_de_linea_de_ordenes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path / 'h'))
    monkeypatch.chdir(tmp_path)
    assert main(['--compactar-en', '10', 'x']) == 1
    assert 'al menos 1000' in capsys.readouterr().err
