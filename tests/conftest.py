import os

# La suite no debe heredar el entorno de quien la corre: una sesión de
# AlfalfaCode exporta su hogar, su Ollama y sus referencias.
_PREFIJOS = ('ALFALFA_', 'OLLAMA_', 'OPENAI_', 'ANTHROPIC_', 'OPENROUTER_', 'OPENCODE_')
for _nombre in [n for n in os.environ if n.startswith(_PREFIJOS)]:
    del os.environ[_nombre]
# Ni los procesos hijos de la suite (`alfalfa --stdio` de verdad) mantienen el Mac despierto:
# test_mantener_despierto lo enciende donde lo prueba.
os.environ['ALFALFA_MANTENER_DESPIERTO'] = '0'


import pytest  # noqa: E402  (después de limpiar el entorno)


@pytest.fixture(autouse=True)
def _sin_llavero_ni_proveedores_del_usuario(tmp_path_factory, monkeypatch):
    """Ni el Llavero real ni el proveedores.json de quien corre la suite entran."""
    from alfalfa.proveedores import configuracion, llavero
    monkeypatch.setattr(llavero, '_almacen', {})
    monkeypatch.setattr(llavero, '_leidos', {})
    monkeypatch.setattr(configuracion, '_casa', tmp_path_factory.mktemp('alfalfa'))


@pytest.fixture(autouse=True)
def _catalogo_sin_red(monkeypatch):
    """El catálogo de models.dev nunca se baja en la suite: sin copia guardada vale la del paquete.
    Las pruebas del catálogo ponen su propio `_descargar` con un catálogo falso."""
    from alfalfa import catalogo

    def sin_red():
        raise OSError('sin red en las pruebas')

    monkeypatch.setattr(catalogo, '_descargar', sin_red)
    catalogo.olvidar()
    yield
    catalogo.olvidar()


@pytest.fixture(autouse=True)
def _sin_habilidades_del_usuario(tmp_path_factory, monkeypatch):
    """Las habilidades sueltas de ~/.claude/skills y ~/.codex/skills de quien corre la suite no entran."""
    from alfalfa.complementos import cargador
    monkeypatch.setattr(cargador, 'CASA', tmp_path_factory.mktemp('casa'))


@pytest.fixture(autouse=True)
def _sin_internet_en_la_web(monkeypatch):
    """web_search y read_web nunca salen a internet en la suite: un motor o una lectura de
    verdad fallan con «sin red en las pruebas». Las pruebas de la web usan motores falsos."""
    from cuysearch import ErrorDeLectura
    from cuysearch.extractor import PageExtractor
    from cuysearch.motores.base import ErrorDeMotor, Motor

    from alfalfa.proveedores.nativo import web

    async def sin_red(self, *args, **kwargs):
        raise ErrorDeMotor(self.nombre + ': sin red en las pruebas')

    async def sin_lectura(self, url):
        from cuysearch.security import UnsafeUrlError, validate_public_url
        try:
            validate_public_url(url)  # las URL locales se rechazan igual que fuera de las pruebas
        except UnsafeUrlError as exc:
            raise ErrorDeLectura(str(exc)) from exc
        raise ErrorDeLectura('sin red en las pruebas')

    monkeypatch.setattr(Motor, '_pedir', sin_red)
    monkeypatch.setattr(PageExtractor, '_extract_limited', lambda self, client, url: sin_lectura(self, url))
    monkeypatch.setattr(web, '_buscadores', {})


@pytest.fixture(autouse=True)
def _sin_asercion_de_energia(monkeypatch):
    """Los turnos de la suite no toman la aserción de verdad: test_mantener_despierto la prueba aparte."""
    from alfalfa import despierto
    monkeypatch.setattr(despierto, '_asercion_del_sistema', lambda: None)


@pytest.fixture(autouse=True)
def _como_con_ollama(monkeypatch):
    """La suite hace como si este Mac tuviera Ollama: un encargo sin --modelo va al proveedor de
    siempre y no a la bienvenida de la primera vez, tenga o no Ollama quien la corre.
    test_primera_vez lo quita donde prueba la bienvenida."""
    from alfalfa import primera_vez
    monkeypatch.setattr(primera_vez, 'ollama_instalado', lambda: True)
