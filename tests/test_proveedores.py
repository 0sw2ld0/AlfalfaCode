"""El contrato de proveedores, Ollama sin cambios y la configuración en el Llavero."""
import io
import json
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from alfalfa import ajustes, proveedores
from alfalfa.proveedores import configuracion, llavero, ollama
from alfalfa.proveedores import orden as orden_de_proveedores


def test_el_prefijo_dice_el_proveedor_y_sin_prefijo_es_ollama():
    assert proveedores.separar('openai/gpt-6-luna') == ('openai', 'gpt-6-luna')
    assert proveedores.separar('alfalfa/glm-5.3-flash') == ('alfalfa', 'glm-5.3-flash')
    assert proveedores.separar('ollama/glm-5.3:cloud') == ('ollama', 'glm-5.3:cloud')
    assert proveedores.separar('deepseek-v4.1-flash:cloud') == ('ollama', 'deepseek-v4.1-flash:cloud')
    # Un espacio de nombres de Ollama no es un proveedor.
    assert proveedores.separar('frob/deepseek-v4.1-flash') == ('ollama', 'frob/deepseek-v4.1-flash')
    assert proveedores.con_prefijo('ollama', 'glm-5.3:cloud') == 'glm-5.3:cloud'
    assert proveedores.con_prefijo('openai', 'gpt-6-luna') == 'openai/gpt-6-luna'


def test_ollama_valida_la_url_como_antes(monkeypatch):
    monkeypatch.setenv('ALFALFA_OLLAMA_URL', 'http://ollama.example.com')
    with pytest.raises(ValueError, match='requiere HTTPS'):
        ollama.Ollama().url()
    monkeypatch.setenv('ALFALFA_OLLAMA_URL', 'https://u:p@ollama.example.com')
    with pytest.raises(ValueError, match='sin credenciales ni ruta'):
        ollama.Ollama().url()
    monkeypatch.setenv('ALFALFA_OLLAMA_URL', 'https://ollama.com/')
    assert ollama.Ollama().url() == 'https://ollama.com'


def test_ollama_da_el_cliente_de_siempre(monkeypatch):
    monkeypatch.setenv('OLLAMA_API_KEY', 'clave-de-ollama-123')
    cliente = ollama.Ollama().cliente(httpx.Timeout(5))
    try:
        assert str(cliente.base_url).rstrip('/') == 'http://127.0.0.1:11434'
        assert cliente.headers['Authorization'] == 'Bearer clave-de-ollama-123'
    finally:
        import asyncio
        asyncio.run(cliente.aclose())


def test_la_clave_de_ollama_sale_del_entorno_o_del_llavero(monkeypatch):
    assert ajustes.ollama_clave() is None
    llavero.guardar('ollama', 'del-llavero-123')
    assert ajustes.ollama_clave() == 'del-llavero-123'
    monkeypatch.setenv('OLLAMA_API_KEY', 'del-entorno-123')
    assert ajustes.ollama_clave() == 'del-entorno-123'


def test_la_clave_de_openai_sale_del_entorno_o_del_llavero(monkeypatch):
    assert ajustes.openai_clave() is None
    llavero.guardar('openai', 'sk-del-llavero')
    assert ajustes.openai_clave() == 'sk-del-llavero'
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-del-entorno')
    assert ajustes.openai_clave() == 'sk-del-entorno'


def test_las_urls_salen_del_entorno_o_de_proveedores_json(monkeypatch):
    assert ajustes.ollama_url() == 'http://127.0.0.1:11434'
    assert ajustes.alfalfa_servicio_url() is None
    configuracion.cambiar('ollama', 'url', 'https://ollama.com')
    configuracion.cambiar('alfalfa', 'url', 'https://alfalfa.example.com')
    assert ajustes.ollama_url() == 'https://ollama.com'
    assert ajustes.alfalfa_servicio_url() == 'https://alfalfa.example.com'
    monkeypatch.setenv('ALFALFA_OLLAMA_URL', 'http://localhost:11434')
    monkeypatch.setenv('ALFALFA_SERVICIO_URL', 'https://otro.example.com')
    assert ajustes.ollama_url() == 'http://localhost:11434'
    assert ajustes.alfalfa_servicio_url() == 'https://otro.example.com'


def test_proveedores_json_no_acepta_secretos():
    with pytest.raises(ValueError, match='Llavero'):
        configuracion.escribir({'openai': {'clave': 'sk-nunca-aqui'}})
    assert not configuracion.ruta().exists()


def test_proveedores_json_es_privado():
    configuracion.cambiar(None, 'por_defecto', 'openai')
    assert configuracion.ruta().stat().st_mode & 0o777 == 0o600
    assert configuracion.por_defecto() == 'openai'
    with pytest.raises(ValueError):
        configuracion.cambiar(None, 'por_defecto', 'otro')


def test_el_llavero_rechaza_claves_raras():
    for mala in ('', 'corta', 'con espacio dentro', 'con"comillas"largas', 'barra\\dentro-larga'):
        with pytest.raises(ValueError):
            llavero.guardar('openai', mala)
    with pytest.raises(ValueError):
        llavero.guardar('otro', 'sk-valida-123')


def test_el_llavero_real_no_pone_el_secreto_en_los_argumentos(monkeypatch):
    monkeypatch.setattr(llavero, '_almacen', None)
    monkeypatch.setattr(llavero, '_security', lambda: '/usr/bin/security')
    vistas = []

    def run(argv, **kwargs):
        vistas.append((argv, kwargs.get('input')))
        if 'find-generic-password' in argv:
            return subprocess.CompletedProcess(argv, 0, 'sk-guardada-123\n', '')
        return subprocess.CompletedProcess(argv, 0, '', '')

    monkeypatch.setattr(llavero.subprocess, 'run', run)
    llavero.guardar('openai', 'sk-guardada-123')
    argv, entrada = vistas[-1]
    assert argv == ['/usr/bin/security', '-i']
    assert 'sk-guardada-123' in entrada and '-s AlfalfaCode -a openai' in entrada
    assert llavero.leer('openai') == 'sk-guardada-123'
    # Lo leído se recuerda: el protocolo sanea cada línea sin lanzar `security`.
    antes = len(vistas)
    assert llavero.leer('openai') == 'sk-guardada-123'
    assert len(vistas) == antes
    assert all('sk-guardada-123' not in ' '.join(a) for a, _ in vistas)


def test_fuera_de_macos_no_hay_llavero(monkeypatch):
    monkeypatch.setattr(llavero, '_almacen', None)
    monkeypatch.setattr(llavero.sys, 'platform', 'linux')
    assert llavero.leer('openai') is None
    with pytest.raises(llavero.LlaveroNoDisponible):
        llavero.guardar('openai', 'sk-valida-123')


def _tags(filas):
    def manejar(request):
        assert request.url.path == '/api/tags'
        return httpx.Response(200, json={'models': filas})
    return lambda: httpx.Client(transport=httpx.MockTransport(manejar))


def test_ollama_lista_sus_modelos_con_su_proveedor(monkeypatch):
    monkeypatch.setattr(ollama, 'cliente_de_lista', _tags([{'name': 'glm-5.3-flash:cloud', 'size': 1}]))
    filas = ollama.Ollama().modelos()
    assert filas == [{'name': 'glm-5.3-flash:cloud', 'size': 1, 'proveedor': 'ollama',
                      'nombre': 'glm-5.3-flash:cloud'}]


class _Falla(proveedores.Proveedor):
    id, nombre = 'openai', 'OpenAI'

    def modelos(self):
        raise httpx.ConnectError('sk-que-no-debe-salir')


def test_un_proveedor_que_falla_no_esconde_a_los_demas(monkeypatch):
    monkeypatch.setattr(ollama, 'cliente_de_lista', _tags([{'name': 'm'}]))
    monkeypatch.setattr(proveedores, 'disponibles', lambda: [ollama.Ollama(), _Falla()])
    modelos, fallos = proveedores.listar_modelos()
    assert [m['name'] for m in modelos] == ['m']
    assert fallos == {'openai': 'No se pudo consultar OpenAI.'}


def test_la_lista_del_protocolo_lleva_el_proveedor(monkeypatch, capsys):
    from alfalfa.stdio import entry
    monkeypatch.setattr(ollama, 'cliente_de_lista', _tags([{'name': 'm'}]))
    assert entry(SimpleNamespace(sesiones=False, modelos=True)) == 0
    fila = json.loads(capsys.readouterr().out)
    assert fila['tipo'] == 'modelos' and fila['modelos'][0]['proveedor'] == 'ollama'
    assert 'fallos' not in fila


def test_el_protocolo_sanea_las_claves_de_todos_los_proveedores(monkeypatch):
    from alfalfa.stdio import Transport
    llavero.guardar('openai', 'sk-secreto-de-openai')
    monkeypatch.setenv('OLLAMA_API_KEY', 'clave-de-ollama-123')
    salida = io.StringIO()
    Transport(salida).emit('evento', texto='sk-secreto-de-openai y clave-de-ollama-123')
    linea = salida.getvalue()
    assert 'sk-secreto' not in linea and 'clave-de-ollama' not in linea
    assert linea.count('[REDACTADO]') == 2


def test_la_orden_guarda_la_clave_por_la_entrada_y_no_la_enseña(capsys):
    args = SimpleNamespace(json=True, guardar_clave='openai', borrar_clave=None,
                           proveedor_por_defecto=None, fijar_url=None, proveedores=False)
    assert orden_de_proveedores.orden(args, entrada=io.StringIO('sk-por-la-entrada\n')) == 0
    salida = capsys.readouterr().out
    assert 'sk-por-la-entrada' not in salida
    assert ajustes.openai_clave() == 'sk-por-la-entrada'
    assert json.loads(salida)['tipo'] == 'proveedores'


def test_la_orden_fija_y_quita_la_url_del_servicio(capsys):
    base = dict(json=False, guardar_clave=None, borrar_clave=None, proveedor_por_defecto=None,
                proveedores=False)
    assert orden_de_proveedores.orden(SimpleNamespace(**base, fijar_url=('alfalfa', 'http://x.example.com'))) == 1
    assert orden_de_proveedores.orden(SimpleNamespace(**base, fijar_url=('alfalfa', 'https://x.example.com'))) == 0
    assert ajustes.alfalfa_servicio_url() == 'https://x.example.com'
    assert orden_de_proveedores.orden(SimpleNamespace(**base, fijar_url=('alfalfa', '-'))) == 0
    assert ajustes.alfalfa_servicio_url() is None


def test_la_terminal_atiende_las_ordenes_de_proveedores(capsys):
    from alfalfa.terminal import main
    assert main(['--proveedores', '--json']) == 0
    datos = json.loads(capsys.readouterr().out)
    assert datos['por_defecto'] == 'ollama'
    assert [p['id'] for p in datos['proveedores']][0] == 'ollama'


def test_el_motor_pide_el_cliente_al_proveedor_del_modelo(tmp_path, monkeypatch):
    from alfalfa.proveedores.nativo.runtime import NativeRuntime
    from alfalfa.terminal import TerminalManager
    monkeypatch.chdir(tmp_path)
    manager = TerminalManager('x', home=tmp_path / 'home', model='glm-5.3-flash:cloud')
    try:
        assert manager.task['provider'] == 'ollama'
        rt = NativeRuntime(manager, manager.task)
        proveedor, modelo, cliente = rt._cliente(httpx.Timeout(5))
        import asyncio
        asyncio.run(cliente.aclose())
        assert (proveedor.id, modelo) == ('ollama', 'glm-5.3-flash:cloud')
        manager.task['model'] = 'ollama/glm-5.3-flash:cloud'
        assert rt._cliente(httpx.Timeout(5))[1] == 'glm-5.3-flash:cloud'
    finally:
        manager.close()


def test_leer_el_llavero_desde_una_orden_siempre_pregunta():
    from alfalfa.permisos.peligrosos import dangerous_command_match
    assert dangerous_command_match(['security', 'find-generic-password', '-s', 'AlfalfaCode', '-w'])
    assert dangerous_command_match(['bash', '-c', 'security dump-keychain'])
    assert dangerous_command_match(['ls', '-la']) is None


def test_la_clave_de_openai_nunca_llega_a_un_servidor_mcp():
    from alfalfa.mcp_cliente.configuracion import VARIABLES_PROHIBIDAS
    assert {'OLLAMA_API_KEY', 'OPENAI_API_KEY'} <= VARIABLES_PROHIBIDAS


def test_cualquier_carpeta_tmp_de_primer_nivel_es_de_temporales():
    from alfalfa.evidencia.espacio import temporal
    assert temporal('.tmp-alfalfa/x.log') and temporal('.tmp-chiyo/dd/a') and temporal('.temp-app')
    assert not temporal('src/.tmp-x/a') and not temporal('tmp/a') and not temporal('.tmpx/a')


def test_una_clave_de_proveedor_no_llega_a_mcp_ni_con_otro_nombre(monkeypatch):
    from alfalfa.mcp_cliente import configuracion as mcp
    monkeypatch.setattr(ajustes, 'ollama_clave', lambda: 'clave-del-integrador-123')
    with pytest.raises(ValueError, match='clave de proveedor'):
        mcp._heredada('APP_OLLAMA_API_KEY', 'entorno', {'APP_OLLAMA_API_KEY': 'clave-del-integrador-123'})
    assert mcp._heredada('OTRA', 'entorno', {'OTRA': 'valor-normal'}) == 'valor-normal'
