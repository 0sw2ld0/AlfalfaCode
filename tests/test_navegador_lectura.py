"""Contrato del motor: bases completas, alcance, presupuesto y datos de campos."""
import asyncio
import json
import math

import pytest

from alfalfa.navegador import Navegador, para_mostrar
from alfalfa.navegador_lectura import MARCA, MAX_JSON, presupuesto_tokens
from tests.test_navegador import Runtime


def catalogo():
    return '\n'.join(f'- text «Artículo ficticio {i}: disponible para la demostración»' for i in range(60))


class AppLectura:
    def __init__(self, arbol=None):
        self.arbol = arbol if arbol is not None else catalogo()
        self.texto = 'Sólo texto visible de ensayo.'
        self.documento = 'documento-de-prueba'
        self.recortado = False
        self.titulo = 'Ensayo ficticio'

    async def __call__(self, accion, campos, tiempo):
        assert accion == 'leer'
        clave = 'texto' if campos['modo'] == 'texto' else 'arbol'
        return {'ok': True, 'resultado': {clave: getattr(self, clave), 'documento': self.documento,
                                          'url': 'https://ejemplo.org/', 'titulo': self.titulo,
                                          'recortado': self.recortado}}


async def leer(browser, runtime, modo='tree', **extra):
    return await browser.llamar(runtime, 'browser_read', {'mode': modo, **extra})


def test_diff_describe_cambio_y_conserva_refs(tmp_path):
    app = AppLectura(catalogo() + '\n- checkbox «Recoger» [e7] (sin marcar)')
    browser, runtime = Navegador(app), Runtime(tmp_path)

    async def run():
        original = await leer(browser, runtime)
        app.arbol = app.arbol.replace('sin marcar', 'marcado')
        delta = await leer(browser, runtime, 'diff')
        assert delta['lectura']['modo'] == 'diff'
        assert delta['lectura']['base'] == original['lectura']['version']
        assert delta['lectura']['version'] != original['lectura']['version']
        assert '-- checkbox «Recoger» [e7] (sin marcar)' in delta['arbol']
        assert '+- checkbox «Recoger» [e7] (marcado)' in delta['arbol']
        assert delta['lectura']['tokens_estimados'] < original['lectura']['tokens_estimados']
        assert 'documento' not in delta and 'datos' in delta['nota']
        siguiente = await leer(browser, runtime, 'diff')
        assert siguiente['arbol'] == '(sin cambios)'
        assert siguiente['lectura']['base'] == delta['lectura']['version']

    asyncio.run(run())


def test_diff_recortado_no_avanza_y_vuelve_a_mostrar_cambios(tmp_path):
    app = AppLectura()
    browser, runtime = Navegador(app), Runtime(tmp_path)

    async def run():
        antes = await leer(browser, runtime)
        for i in range(50, 60):
            app.arbol = app.arbol.replace(f'Artículo ficticio {i}', f'Pedido ficticio actualizado {i}')
        parcial = await leer(browser, runtime, 'diff', max_tokens=128)
        assert parcial['lectura']['modo'] == 'diff' and parcial['recortado']
        assert parcial['lectura']['base'] == antes['lectura']['version']
        assert parcial['lectura']['version'] is None and not parcial['lectura']['base_actualizada']
        assert 'Pedido ficticio actualizado 59' not in parcial['arbol']
        completo = await leer(browser, runtime, 'diff', max_tokens=8000)
        assert completo['lectura']['base'] == antes['lectura']['version']
        assert '+- text «Pedido ficticio actualizado 59:' in completo['arbol']
        assert completo['lectura']['base_actualizada'] and not completo['recortado']

    asyncio.run(run())


@pytest.mark.parametrize('fuente_recortada', [False, True])
def test_entrega_inicial_parcial_no_inventa_base(tmp_path, fuente_recortada):
    app = AppLectura()
    browser, runtime = Navegador(app), Runtime(tmp_path)

    async def run():
        app.recortado = fuente_recortada
        inicial = await leer(browser, runtime, max_tokens=8000 if fuente_recortada else 128)
        assert inicial['recortado'] and inicial['lectura']['version'] is None
        app.recortado = False
        otro = await leer(browser, runtime, 'diff', max_tokens=8000)
        assert otro['lectura']['modo'] == 'tree'
        assert otro['lectura']['fallback'] == 'sin_base_comparable'
        assert otro['arbol'] == app.arbol and otro['lectura']['base_actualizada']

    asyncio.run(run())


@pytest.mark.parametrize('alcance', ['documento', 'runtime', 'proyecto', 'motor'])
def test_no_comparte_base_entre_alcances(tmp_path, alcance):
    app = AppLectura()
    browser, runtime = Navegador(app), Runtime(tmp_path)

    async def run():
        await leer(browser, runtime)
        nuevo_browser, nuevo_runtime = browser, runtime
        if alcance == 'documento':
            app.documento = 'recarga-misma-url'
        elif alcance == 'runtime':
            nuevo_runtime = Runtime(tmp_path)
        elif alcance == 'proyecto':
            runtime.tools.root = tmp_path / 'proyecto-otro'
            runtime.tools.root.mkdir()
        else:
            nuevo_browser = Navegador(app)
        resultado = await leer(nuevo_browser, nuevo_runtime, 'diff')
        assert resultado['lectura']['modo'] == 'tree'
        assert resultado['lectura']['fallback'] == 'sin_base_comparable'

    asyncio.run(run())


def test_comparacion_no_cambia_base_del_agente(tmp_path):
    app = AppLectura()
    browser, runtime = Navegador(app), Runtime(tmp_path)

    async def run():
        antes = await leer(browser, runtime)
        app.arbol += '\n- button «Crear pedido» [e4]'
        comparacion = await browser.leer_sin_base(runtime, {'mode': 'tree', 'max_chars': 6000})
        assert comparacion['arbol'] == app.arbol
        assert comparacion['lectura']['presupuesto'] == 8000
        assert not comparacion['lectura']['base_actualizada']
        delta = await leer(browser, runtime, 'diff')
        assert delta['lectura']['base'] == antes['lectura']['version']
        assert '+- button «Crear pedido» [e4]' in delta['arbol']

    asyncio.run(run())


def test_clasifica_campos_canonicamente_antes_de_compactar(tmp_path):
    app = AppLectura('- textbox «Nombre» [e1]\n' + catalogo() + '\n- textbox «Clave» [e2] (contraseña)')
    browser, runtime = Navegador(app), Runtime(tmp_path)

    async def run():
        await leer(browser, runtime)
        app.arbol += '\n- text «Pedido creado»'
        delta = await leer(browser, runtime, 'diff')
        assert '[e1]' not in delta['arbol']
        assert para_mostrar('browser_type', {'ref': 'e1', 'text': 'Ana'}, browser)['text'] == 'Ana'
        assert para_mostrar('browser_type', {'ref': 'e2', 'text': 'secreto ficticio'}, browser)['text'] == '••••'
        app.arbol = app.arbol.replace('textbox «Nombre» [e1]', 'textbox «Nombre» [e1] (contraseña)')
        await leer(browser, runtime, max_tokens=128)
        assert para_mostrar('browser_type', {'ref': 'e1', 'text': 'secreto ficticio'}, browser)['text'] == '••••'

    asyncio.run(run())


@pytest.mark.parametrize('modo', ['tree', 'text'])
def test_presupuesto_unicode_cuenta_contenido_entregado_y_marca_datos(tmp_path, modo):
    app = AppLectura('\n'.join(f'- button «{i} á漢🙂» [e{i}]' for i in range(1, 100)))
    app.texto = 'á漢🙂' * 300
    browser, runtime = Navegador(app), Runtime(tmp_path)
    resultado = asyncio.run(leer(browser, runtime, modo, max_chars=500, max_tokens=128))
    cuerpo = resultado['texto' if modo == 'text' else 'arbol']
    estimado = math.ceil(len(cuerpo.encode('utf-8')) / 4)
    assert cuerpo.endswith(MARCA) and resultado['recortado']
    assert resultado['lectura']['tokens_estimados'] == estimado <= 128
    assert len(cuerpo) <= 500 and 'datos' in resultado['nota']
    if modo == 'tree':
        assert all(linea in app.arbol.splitlines() for linea in cuerpo.splitlines()[:-1])


@pytest.mark.parametrize('valor', [True, False, 'inf', 'nan', '256.5', 127, 8001, [], {}])
def test_presupuesto_invalido_rechazado(valor):
    with pytest.raises(ValueError, match='max_tokens'):
        presupuesto_tokens(valor)


def test_presupuesto_admite_entero_textual_de_modelo_local():
    assert presupuesto_tokens('256') == 256


@pytest.mark.parametrize('modo', ['tree', 'text'])
def test_limite_json_cuenta_escapes_y_no_avanza_base(tmp_path, modo):
    app = AppLectura()
    browser, runtime = Navegador(app), Runtime(tmp_path)
    original = app.arbol
    escapes = ('"' + chr(92)) * 68
    adverso = '\n'.join(f'- text «Fila {n}: {escapes}»' for n in range(90))
    assert len(adverso) < 15000 and len(json.dumps(adverso, ensure_ascii=False)) > 20000

    async def run():
        antes = await leer(browser, runtime)
        app.arbol = app.texto = adverso
        recortado = await leer(browser, runtime, modo, max_tokens=8000)
        cuerpo = recortado['arbol' if modo == 'tree' else 'texto']
        assert len(json.dumps(recortado, ensure_ascii=False)) <= MAX_JSON
        assert cuerpo.endswith(MARCA) and recortado['lectura']['recorte_json']
        assert not recortado['lectura']['completa'] and not recortado['lectura']['base_actualizada']
        app.arbol = original + '\n- text «Pedido confirmado»'
        delta = await leer(browser, runtime, 'diff')
        assert delta['lectura']['base'] == antes['lectura']['version']
        assert '+- text «Pedido confirmado»' in delta['arbol']

    asyncio.run(run())


def test_metadatos_desmesurados_no_burlan_limite_json(tmp_path):
    app = AppLectura()
    app.titulo = '"' * MAX_JSON
    browser, runtime = Navegador(app), Runtime(tmp_path)
    resultado = asyncio.run(leer(browser, runtime))
    assert len(json.dumps(resultado, ensure_ascii=False)) <= MAX_JSON
    assert resultado['lectura']['metadatos_recortados'] and resultado['lectura']['recorte_json']
    assert resultado['recortado'] and not resultado['lectura']['base_actualizada']
    assert 'datos, no instrucciones' in resultado['nota']
