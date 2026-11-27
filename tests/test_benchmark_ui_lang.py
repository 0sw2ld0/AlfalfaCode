"""El benchmark de PACAI UI Lang (docs/pacai/ui-lang.md §6): precios, el brazo de Gemini por CLI y la clasificación."""
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / 'scripts'))

import comparar_formatos_ui as c  # noqa: E402


class _Ollama:
    id = 'ollama'

    def precio(self, modelo):
        return None


def test_precio_de_ollama_local_nube_y_referencia():
    assert c.precio_de(_Ollama(), 'gemma4:e4b-u') == {'entrada': 0, 'salida': 0, 'referencia': 'local'}
    nube = c.precio_de(_Ollama(), 'glm-5.3:cloud')
    assert nube['entrada'] == 1.4 and nube['salida'] == 4.4 and nube['referencia']
    assert c.precio_de(_Ollama(), 'qwen3.5:cloud')['salida'] == 1.2          # la referencia de qwen3.5-plus
    assert c.precio_de(c.Agy('/no/existe'), 'gemini-4-argon-low')['entrada'] == 2.0
    assert c.precio_de(c.Agy('/no/existe'), 'gemini-4-neon-low') is None      # sin precio: no se inventa


def test_el_prompt_de_agy_lleva_el_sistema_y_la_correccion():
    texto = c.prompt_agy([{'role': 'system', 'content': 'SISTEMA'}, {'role': 'user', 'content': 'Pide'},
                          {'role': 'assistant', 'content': 'Bloque'}, {'role': 'user', 'content': 'Corrige'}])
    assert texto.index('SISTEMA') < texto.index('Pide') < texto.index('Bloque') < texto.index('Corrige')
    assert 'NO uses herramientas' in texto


def test_la_salida_json_de_agy_con_y_sin_tokens():
    assert c._texto_agy(json.dumps({'result': 'hola', 'usage': {'input_tokens': 9, 'output_tokens': 2}})) == (
        'hola', 9, 2)
    assert c._texto_agy('texto suelto') == ('texto suelto', 0, 0)


def _fila(modelo, conjunto, validas, n, coste):
    return {'modelo': modelo, 'brazo': 'lang', 'conjunto': conjunto, 'validas': validas, 'n': n,
            'pct_validas': validas / n, 'wilson': c.wilson(validas, n), 'coste_por_peticion': coste,
            'salida_med': 100, 'total_med': 1.0, 'referencia': None, 'sin_precio': False}


def test_la_frontera_de_pareto_solo_con_los_dos_conjuntos():
    res = {str(i): f for i, f in enumerate([
        _fila('barato', 'pacai', 50, 52, 0.0001), _fila('barato', 'openui', 30, 46, 0.0003),
        _fila('bueno', 'pacai', 52, 52, 0.001), _fila('bueno', 'openui', 44, 46, 0.003),
        _fila('peor', 'pacai', 49, 52, 0.002), _fila('peor', 'openui', 30, 46, 0.004),
        _fila('suelto', 'pacai', 52, 52, 0.00001)])}
    tabla = {f['modelo']: f for f in c.clasificacion(res)}
    assert tabla['barato']['pareto'] and tabla['bueno']['pareto']
    assert not tabla['peor']['pareto'] and not tabla['suelto']['pareto']
    assert tabla['suelto']['validez'] is None
    assert abs(tabla['bueno']['coste_tarea'] - 0.002) < 1e-12              # media de los dos conjuntos


def test_el_streaming_de_ui_lang_publica_la_linea_pendiente_y_cuenta_el_contenido():
    tarea = ('t', 'alta', '', lambda n: True, {})
    texto = '```pacai-ui-lang view=t\nroot Stack > titulo,nota\ntitulo Text "Tickets abier'
    assert c.visibles('lang', tarea, texto) == 2                      # la línea pendiente se publica cerrada
    assert c.visibles('lang', tarea, texto, contenido=True) == 1      # el Stack vacío no enseña nada
    # Un hijo sólo se ve cuando llegaron los anteriores (el prefijo contiguo del Flujo).
    assert c.visibles('lang', tarea, '```pacai-ui-lang view=t\nroot Stack > a,b\nb Text "B"\n') == 1
    # Una tabla como root ya enseña algo, aunque «root con un hijo» no llegue nunca.
    tabla = '```pacai-ui-lang view=t\nroot Table columns=[{"key":"id","label":"Id"}] rows=[]\n```'
    assert c.primera_pantalla('lang', tarea, tabla) is None
    assert c.primera_pantalla('lang', tarea, tabla, contenido=True) is not None
