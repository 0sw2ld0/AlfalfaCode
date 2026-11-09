"""Compactación del contexto al estilo de Codex para AlfalfaCode.

Traducción y adaptación de codex-rs/core/src/compact.rs y de los prompts
prompts/templates/compact/{prompt,summary_prefix}.md (OpenAI Codex,
Apache-2.0; ver NOTICE, LICENSE y PROCEDENCIA.md de esta carpeta):

- Se dispara al llegar al 90 % de la ventana del modelo o al límite que elija el
  usuario, si es menor (`auto_compact_token_limit`, openai_models.rs), o a mano.
  Propio de AlfalfaCode: con un modelo que cobra más toda la petición pasado un
  umbral (Luna a 272K, Haiku 5.5 a 100K), el modo Ahorro compacta también al 90 %
  de ese umbral, avisa al 80 % y recorta lo que lo pasaría en una sola petición.
- Se pide al mismo modelo un resumen de traspaso con PROMPT, sin herramientas.
- El contexto nuevo es: instrucciones iniciales + los últimos mensajes reales
  del usuario hasta MAX_TOKENS_USUARIO (el que no cabe entero se recorta por
  el medio y ahí se para) + el resumen con PREFIJO. Los anteriores al último
  van juntos y marcados como HISTORIA; el contexto del arnés (archivos que
  cambió el agente) va justo antes del último, como hace Codex.
- Los tokens se aproximan como Codex: bytes UTF-8 / 4, redondeando hacia arriba.

El historial completo se conserva en la sesión: compactar sólo cambia lo que se
envía al modelo.
"""
from __future__ import annotations

from pathlib import Path

_AQUI = Path(__file__).resolve().parent
PROMPT = (_AQUI / 'prompt.md').read_text(encoding='utf-8').strip()
PREFIJO = (_AQUI / 'summary_prefix.md').read_text(encoding='utf-8').strip()
# COMPACT_USER_MESSAGE_MAX_TOKENS de compact.rs.
MAX_TOKENS_USUARIO = 20000
# auto_compact_token_limit: 9/10 de la ventana.
UMBRAL_NUMERADOR, UMBRAL_DENOMINADOR = 9, 10
BYTES_POR_TOKEN = 4
# Resumen como mucho: lo que quepa en esto (un traspaso, no otra transcripción).
MAX_RESUMEN = 32000


def tokens_aprox(texto):
    """approx_token_count de Codex: bytes UTF-8 / 4 redondeado hacia arriba."""
    return (len(texto.encode('utf-8')) + BYTES_POR_TOKEN - 1) // BYTES_POR_TOKEN


def limite(ventana, configurado=None, tarifa=None):
    """El 90 % de la ventana, o el límite del usuario si es menor (openai_models.rs).

    `tarifa` es el umbral de tokens a partir del cual el modelo cobra más toda la
    petición (modo Ahorro): entonces también como mucho el 90 % de ese umbral."""
    propio = ventana * UMBRAL_NUMERADOR // UMBRAL_DENOMINADOR if ventana else None
    tope = min(configurado, propio) if propio and configurado else propio or configurado
    if tarifa:
        barato = tarifa * UMBRAL_NUMERADOR // UMBRAL_DENOMINADOR
        tope = min(tope, barato) if tope else barato
    return tope


# Modos de contexto de una sesión con un modelo que cobra más por contexto largo:
# «ahorro» compacta antes del umbral de tarifa; «completo» usa la ventana entera.
AHORRO, COMPLETO = 'ahorro', 'completo'
MODOS_CONTEXTO = (AHORRO, COMPLETO)
# El aviso sale al 80 % del umbral de tarifa.
AVISO_NUMERADOR, AVISO_DENOMINADOR = 8, 10
# En Ahorro ninguna petición pasa del 95 % del umbral: el resto es margen para lo que
# la estimación por bytes no ve (el proveedor cuenta sus propios tokens).
RECORTE_NUMERADOR, RECORTE_DENOMINADOR = 95, 100


def aviso_en(tarifa):
    return tarifa * AVISO_NUMERADOR // AVISO_DENOMINADOR


def tope_de_peticion(tarifa):
    return tarifa * RECORTE_NUMERADOR // RECORTE_DENOMINADOR


def tokens_cortos(tokens):
    """«100K», «1M», «1,05M», «850»: para los avisos."""
    if tokens >= 1_000_000:
        valor = round(tokens / 1_000_000, 2)
        return (str(int(valor)) if valor == int(valor) else str(valor).replace('.', ',')) + 'M'
    if tokens >= 1000:
        return str(round(tokens / 1000)) + 'K'
    return str(tokens)


def recortar_medio(texto, max_tokens):
    """truncate_middle_with_token_budget: conserva principio y final con una marca."""
    datos = texto.encode('utf-8')
    maximo = max_tokens * BYTES_POR_TOKEN
    if len(datos) <= maximo:
        return texto
    izquierda = maximo // 2
    derecha = maximo - izquierda
    quitados = (len(datos) - maximo + BYTES_POR_TOKEN - 1) // BYTES_POR_TOKEN
    cabeza = datos[:izquierda].decode('utf-8', errors='ignore')
    cola = datos[len(datos) - derecha:].decode('utf-8', errors='ignore')
    return cabeza + '…' + str(quitados) + ' tokens recortados…' + cola


MAX_INSTRUCCIONES = 2000


def prompt_de(instrucciones=None):
    """El PROMPT, más lo que pidió el usuario al compactar a mano (`/compact` de Claude Code)."""
    if not isinstance(instrucciones, str) or not instrucciones.strip():
        return PROMPT
    return (PROMPT + '\n\nAdemás, el usuario pide para este resumen (respétalo sin perder lo esencial): '
            + instrucciones.strip()[:MAX_INSTRUCCIONES])


def es_resumen(texto):
    return isinstance(texto, str) and texto.startswith(PREFIJO + '\n')


def seleccionar_usuario(mensajes, max_tokens=MAX_TOKENS_USUARIO):
    """build_compacted_history_with_limit: los más recientes primero hasta el tope.

    Devuelve los textos en orden cronológico. Un resumen anterior nunca cuenta
    como mensaje del usuario.
    """
    elegidos, resto = [], max_tokens
    for texto in reversed([m for m in mensajes if isinstance(m, str) and not es_resumen(m)]):
        if resto <= 0:
            break
        tokens = tokens_aprox(texto)
        if tokens <= resto:
            elegidos.append(texto)
            resto -= tokens
        else:
            elegidos.append(recortar_medio(texto, resto))
            break
    return list(reversed(elegidos))


def mensaje_resumen(resumen):
    return {'role': 'user', 'content': PREFIJO + '\n' + resumen}


HISTORIA = ('Mensajes anteriores del usuario en esta sesión, en orden. Ya los atendiste: son historia, '
            'no encargos nuevos; lo que siga pendiente lo dice el resumen.')


def historial_compactado(sistema, usuario, resumen, contexto=(), ultimo_es_encargo=True):
    """Instrucciones + historia del usuario + contexto del arnés + su último mensaje + resumen.

    Codex pasa cada mensaje antiguo suelto y un modelo pequeño los tomaba por
    encargos nuevos (rehacía commits ya hechos). Como en la compactación de
    Claude Code, los anteriores quedan como historia y sólo el último es un
    mensaje del usuario. Al compactar entre turnos (`ultimo_es_encargo` falso) el
    último también está contestado y va con los demás."""
    mensajes = [sistema]
    anteriores, ultimo = (list(usuario[:-1]), list(usuario[-1:])) if ultimo_es_encargo else (list(usuario), [])
    if anteriores:
        mensajes.append({'role': 'user', 'content': HISTORIA + '\n\n' + '\n\n'.join(
            str(n) + '. ' + texto for n, texto in enumerate(anteriores, 1))})
    mensajes += list(contexto)
    mensajes += [{'role': 'user', 'content': t} for t in ultimo]
    return mensajes + [mensaje_resumen(resumen)]
