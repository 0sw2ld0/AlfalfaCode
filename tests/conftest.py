import os
import tempfile

# La suite no debe heredar el entorno de quien la corre: una sesión de
# AlfalfaCode exporta su hogar, su Ollama y sus referencias.
_PREFIJOS = ('ALFALFA_', 'OLLAMA_', 'OPENAI_', 'ANTHROPIC_', 'OPENROUTER_', 'OPENCODE_')
for _nombre in [n for n in os.environ if n.startswith(_PREFIJOS)]:
    del os.environ[_nombre]
# Ni los procesos hijos de la suite (`alfalfa --stdio` de verdad) mantienen el Mac despierto:
# test_mantener_despierto lo enciende donde lo prueba.
os.environ['ALFALFA_MANTENER_DESPIERTO'] = '0'
# Ni las configuraciones de OpenCode, Codex y Claude Code de quien corre la suite (alfalfa/importadas),
# tampoco en los procesos hijos.
os.environ['ALFALFA_IMPORTAR_CASA'] = tempfile.mkdtemp(prefix='casa-ajena-')
# Ni lanzan servidores de lenguaje ni linters tras cada edición: test_diagnosticos los enciende.
os.environ['ALFALFA_DIAGNOSTICOS'] = '0'
# Ni la revisión de aprendizaje al cerrar el turno pide nada al modelo falso: test_aprendizaje la enciende.
os.environ['ALFALFA_APRENDER_REVISION'] = '0'
# Ni su ~/.gitconfig: con `commit.gpgsign = true` (o un hook global) fallaba el primer `git commit` de
# una docena de archivos. Git usa una configuración propia de la suite, con un nombre y un correo de
# prueba, también en los procesos hijos; y nada de un GIT_DIR o un GIT_INDEX_FILE heredados.
for _nombre in [n for n in os.environ if n.startswith('GIT_')]:
    del os.environ[_nombre]
_GITCONFIG = os.path.join(tempfile.mkdtemp(prefix='git-de-la-suite-'), 'gitconfig')
with open(_GITCONFIG, 'w') as _archivo:
    _archivo.write('[user]\n\tname = Prueba\n\temail = prueba@example.com\n'
                   '[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n')
os.environ.update(GIT_CONFIG_GLOBAL=_GITCONFIG, GIT_CONFIG_NOSYSTEM='1')


import pytest  # noqa: E402  (después de limpiar el entorno)

from tests.aislamiento_python import preparar  # noqa: E402

preparar()


@pytest.fixture(autouse=True)
def _nunca_el_hogar_real(monkeypatch):
    """Ninguna prueba crea carpetas en el ~/.alfalfa de verdad: un TerminalManager sin `home=` dejaba sesiones
    «Cola de encargos» de cada pasada en la app de quien corría la suite. Falla en el acto y dice dónde."""
    from pathlib import Path

    import tests.casa as casa
    from alfalfa import terminal
    original = terminal.private_directory

    def vigilado(path):
        ruta = Path(path).resolve()
        for hogar in casa.hogares_reales():
            if ruta == hogar or hogar in ruta.parents:
                raise AssertionError('Esta prueba iba a escribir en el hogar real de AlfalfaCode (' + str(ruta)
                                     + '). Dale un hogar temporal: home=tmp_path / "hogar" o ALFALFA_HOME.')
        return original(path)
    monkeypatch.setattr(terminal, 'private_directory', vigilado)


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
def _lista_de_modelos_sin_red(monkeypatch):
    """`GET /v1/models` de OpenAI y Anthropic nunca sale a la red: sin copia guardada, models.dev.
    Las pruebas de la lista ponen su propio transporte falso."""
    import httpx

    from alfalfa.proveedores import lista_api

    def sin_red(peticion):
        raise httpx.ConnectError('sin red en las pruebas', request=peticion)

    monkeypatch.setattr(lista_api, '_transporte', httpx.MockTransport(sin_red))
    lista_api.olvidar()
    yield
    lista_api.olvidar()


@pytest.fixture(autouse=True)
def _sin_habilidades_del_usuario(tmp_path_factory, monkeypatch):
    """Las habilidades sueltas de ~/.claude/skills y ~/.codex/skills de quien corre la suite no entran."""
    from alfalfa.complementos import cargador
    monkeypatch.setattr(cargador, 'CASA', tmp_path_factory.mktemp('casa'))


@pytest.fixture(autouse=True)
def _sin_agentes_del_usuario(tmp_path_factory, monkeypatch):
    """Los agentes de ~/.claude/agents de quien corre la suite no entran: un `explorador.md` propio
    tapaba al integrado y cambiaba lo que prueban las de subagentes."""
    from alfalfa import subagentes
    monkeypatch.setattr(subagentes, 'CASA', tmp_path_factory.mktemp('casa-agentes'))


@pytest.fixture
def fuera_del_sandbox():
    """Una carpeta vacía en la casa real del usuario (no la de $HOME), fuera de $TMPDIR, /private/tmp y
    la carpeta de trabajo: el sandbox no deja escribir ahí. Si también la casa cuelga de un temporal,
    no hay dónde probarlo y la prueba se salta con el motivo."""
    import shutil
    from pathlib import Path

    from tests.casa import PREFIJO_FUERA_DEL_SANDBOX, base_fuera_del_sandbox
    base = base_fuera_del_sandbox()
    if base is None:
        pytest.skip('la casa del usuario cuelga de una carpeta temporal: no hay un sitio fuera del sandbox')
    ruta = Path(tempfile.mkdtemp(prefix=PREFIJO_FUERA_DEL_SANDBOX, dir=base))
    try:
        yield ruta
    finally:
        shutil.rmtree(ruta, ignore_errors=True)


@pytest.fixture(autouse=True)
def _sin_configuraciones_ajenas_del_usuario(tmp_path_factory, monkeypatch):
    """~/.codex/config.toml, ~/.config/opencode y ~/.claude/commands de quien corre la suite no entran."""
    from alfalfa import importadas
    monkeypatch.setattr(importadas, 'CASA', tmp_path_factory.mktemp('casa-ajena'))


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


@pytest.fixture(autouse=True)
def _sourcekit_en_un_temporal(tmp_path_factory, monkeypatch):
    """Ninguna prueba compila en el ~/Library/Caches/alfalfa/sourcekit de quien corre la suite.

    La carpeta temporal tiene la misma forma que la de verdad (…/Library/Caches/alfalfa/sourcekit), así
    que el TMPDIR de sourcekit-lsp contiene «alfalfa» como en el Mac del usuario: un manifiesto que mire
    eso se porta en las pruebas como se portaría de verdad. Una sola para toda la suite, para que la caché
    de módulos del SDK se haga una vez.
    """
    import hashlib

    from alfalfa import diagnosticos
    base = tmp_path_factory.getbasetemp() / 'Library' / 'Caches' / 'alfalfa' / 'sourcekit'
    monkeypatch.setattr(diagnosticos, 'carpeta_de_sourcekit',
                        lambda raiz: base / hashlib.sha256(str(raiz).encode()).hexdigest()[:16])


_CACHE_DEL_USUARIO = os.path.join(os.path.expanduser('~'), 'Library', 'Caches', 'alfalfa')
_HABIA_CACHE = os.path.exists(_CACHE_DEL_USUARIO)


def pytest_sessionfinish(session, exitstatus):
    """Si la suite creó ~/Library/Caches/alfalfa, alguna prueba lanzó sourcekit-lsp sin redirigir su caché."""
    if not _HABIA_CACHE and os.path.exists(_CACHE_DEL_USUARIO):
        session.exitstatus = 1
        print('\nERROR: la suite creó ' + _CACHE_DEL_USUARIO + '; una prueba no redirigió la caché de '
              'sourcekit-lsp (bórrala y busca cuál).')
