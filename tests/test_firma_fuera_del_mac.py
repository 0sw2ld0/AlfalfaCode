"""El código de cortesía fuera del Mac: sin Secure Enclave, la clave P-256 va en un archivo.

El servicio no distingue: la pública sale en X9.63 y la firma en DER, como las del ayudante de Swift.
"""
import asyncio
import base64
import io
import json
import time
import types

import httpx
import pytest

from alfalfa import proveedores
from alfalfa.proveedores import alfalfa, configuracion, llavero, orden

ec = pytest.importorskip('cryptography.hazmat.primitives.asymmetric.ec')
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402

CODIGO = 'CUY-ABCD-EFGH-JKMN'
URL = 'https://alfalfa.example.com:8444'


def _publica(texto):
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), base64.b64decode(texto))


def _verifica(publica, firma, datos):
    _publica(publica).verify(base64.b64decode(firma), datos, ec.ECDSA(hashes.SHA256()))
    return True


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(alfalfa.sys, 'platform', 'linux')
    monkeypatch.delenv('ALFALFA_FIRMA_CLAVE', raising=False)


def test_fuera_del_mac_la_clave_va_en_un_archivo_y_firma_como_el_secure_enclave(tmp_path, linux):
    firmante = alfalfa.firmante_de_este_equipo(tmp_path)
    assert not firmante.tiene_clave()
    publica = firmante.publica()
    clave = tmp_path / 'firma' / 'clave.pem'
    assert firmante.tiene_clave() and clave.is_file()
    assert clave.stat().st_mode & 0o777 == 0o600 and clave.parent.stat().st_mode & 0o777 == 0o700
    assert len(base64.b64decode(publica)) == 65 and base64.b64decode(publica)[0] == 4  # X9.63 sin comprimir
    assert firmante.publica() == publica  # la misma clave, no una nueva
    datos = alfalfa.canonico('POST', '/api/chat', 1, 'nonce-de-prueba', b'{}')
    assert _verifica(publica, firmante.firmar(datos), datos)
    assert _verifica(publica, asyncio.run(firmante.firmar_async(datos)), datos)


def test_en_el_mac_sigue_el_secure_enclave(tmp_path, monkeypatch):
    monkeypatch.setattr(alfalfa.sys, 'platform', 'darwin')
    assert isinstance(alfalfa.firmante_de_este_equipo(tmp_path), alfalfa.Firmante)


def test_con_alfalfa_firma_clave_usa_esa_y_no_crea_otra(tmp_path, linux, monkeypatch):
    externa = tmp_path / 'credenciales' / 'alfalfa-firma'
    monkeypatch.setenv('ALFALFA_FIRMA_CLAVE', str(externa))
    firmante = alfalfa.firmante_de_este_equipo(tmp_path / 'hogar')
    assert not firmante.tiene_clave()
    with pytest.raises(alfalfa.ErrorDeAlfalfa, match='ALFALFA_FIRMA_CLAVE'):
        firmante.publica()
    assert not externa.exists() and not (tmp_path / 'hogar' / 'firma').exists()
    # La que systemd deja descifrada con LoadCredentialEncrypted=: sólo se lee.
    privada = ec.generate_private_key(ec.SECP256R1())
    externa.parent.mkdir()
    externa.write_bytes(privada.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
    crudo = privada.public_key().public_bytes(serialization.Encoding.X962,
                                              serialization.PublicFormat.UncompressedPoint)
    assert firmante.tiene_clave() and firmante.publica() == base64.b64encode(crudo).decode()
    datos = alfalfa.canonico('GET', '/cuota', 2, 'otro-nonce', b'')
    assert _verifica(firmante.publica(), firmante.firmar(datos), datos)


def test_una_clave_que_no_se_entiende_se_dice_y_no_se_pisa(tmp_path, linux):
    clave = tmp_path / 'firma' / 'clave.pem'
    clave.parent.mkdir(mode=0o700)
    clave.write_text('no es una clave')
    firmante = alfalfa.firmante_de_este_equipo(tmp_path)
    with pytest.raises(alfalfa.ErrorDeAlfalfa, match='no se entiende'):
        firmante.publica()
    assert clave.read_text() == 'no es una clave'


class Servicio:
    """El servicio en memoria: guarda la pública al activar y exige la firma en lo demás."""

    def __init__(self):
        self.publica = None
        self.vistas = []

    def __call__(self, request):
        cuerpo = request.content
        self.vistas.append(request.url.path)
        h = request.headers
        assert h['x-alfalfa-codigo'] == CODIGO and abs(int(h['x-alfalfa-marca']) - time.time()) < 60
        if request.url.path == '/activar':
            self.publica = json.loads(cuerpo)['clave_publica']
        datos = alfalfa.canonico(request.method, request.url.path, h['x-alfalfa-marca'], h['x-alfalfa-nonce'],
                                 cuerpo)
        try:
            _verifica(self.publica, h['x-alfalfa-firma'], datos)
        except Exception:
            return httpx.Response(403, json={'error': 'firma'})
        if request.url.path == '/activar':
            return httpx.Response(200, json={'ok': True, 'nombre': 'Servidor', 'caduca': None, 'cuotas': [],
                                             'modelos': [{'name': 'gpt-6-luna'}]})
        return httpx.Response(200, json={'cuotas': [{'proveedor': 'openai', 'usado_pct': 1.0,
                                                     'renueva': '2026-12-01'}]})


@pytest.fixture
def sin_llavero(linux, monkeypatch):
    """Como en Linux: ni Llavero ni el diccionario de las pruebas."""
    monkeypatch.setattr(llavero, '_almacen', None)
    monkeypatch.setattr(llavero, '_security', lambda: None)
    falso = Servicio()
    monkeypatch.setattr(alfalfa, 'cliente_de_lista', lambda: httpx.Client(transport=httpx.MockTransport(falso)))
    configuracion.cambiar('alfalfa', 'url', None)
    return falso


def test_sin_llavero_se_activa_y_dice_la_variable_del_codigo(sin_llavero, monkeypatch):
    salida = io.StringIO()
    monkeypatch.setattr('sys.stdout', salida)
    args = types.SimpleNamespace(activar_codigo=True, json=False)
    assert orden.orden(args, io.StringIO(CODIGO + '@alfalfa.example.com:8444\n')) == 0
    texto = salida.getvalue()
    assert 'Código activado en este equipo' in texto and 'Mac' not in texto
    assert 'ALFALFA_CODIGO' in texto
    assert sin_llavero.vistas == ['/activar'] and configuracion.de('alfalfa')['url'] == URL
    # Con el código en su variable, lo que sigue va firmado con la misma clave.
    monkeypatch.setenv('ALFALFA_CODIGO', CODIGO)
    p = proveedores.obtener('alfalfa')
    assert p.configurado()
    assert p.cuota()[0]['usado_pct'] == 1.0 and sin_llavero.vistas[-1] == '/cuota'


def test_sin_llavero_el_json_lleva_la_variable(sin_llavero, monkeypatch):
    salida = io.StringIO()
    monkeypatch.setattr('sys.stdout', salida)
    args = types.SimpleNamespace(activar_codigo=True, json=True)
    assert orden.orden(args, io.StringIO(CODIGO + '@alfalfa.example.com:8444\n')) == 0
    valor = json.loads(salida.getvalue())
    assert valor['tipo'] == 'activado' and valor['variable'] == 'ALFALFA_CODIGO'


def test_los_avisos_del_servicio_no_hablan_del_mac_fuera_de_el(linux):
    p = proveedores.obtener('alfalfa')
    for estado in (401, 403):
        texto = p.accion({'status_code': estado}, '')
        assert 'este equipo' in texto and 'Mac' not in texto


def test_sin_alfalfa_codigo_se_dice_la_variable(sin_llavero, monkeypatch):
    proveedores.obtener('alfalfa').activar(CODIGO + '@alfalfa.example.com:8444')
    monkeypatch.delenv('ALFALFA_CODIGO', raising=False)
    p = proveedores.obtener('alfalfa')
    with pytest.raises(alfalfa.ErrorDeAlfalfa, match='ALFALFA_CODIGO'):
        p.cuota()
    with pytest.raises(alfalfa.ErrorDeAlfalfa, match='ALFALFA_CODIGO'):
        p.cliente(10)
    assert sin_llavero.vistas == ['/activar']
