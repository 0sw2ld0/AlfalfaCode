"""La redacción de secretos tapa más formas de clave antes de que lleguen al modelo, al índice o a la memoria.

Antes pasaban tal cual las claves de Stripe, Hugging Face, npm, PyPI, Slack (xoxe y webhooks), Discord,
SendGrid, Google OAuth, DigitalOcean, Telegram, las de AWS temporales y las direcciones con ?key=.
"""
import pytest

from alfalfa.aprendizaje import escaneo
from alfalfa.busqueda_sesiones.redaccion import REDACTADO, tapar

# Cada muestra se arma al ejecutar (prefijo + resto): escrita de una pieza, el escáner de secretos
# de GitHub la toma por una clave de verdad y rechaza el push.
MUESTRAS = [
    ('AWS id temporal (STS)', 'ASIA' + 'PRUEBA0000PRUEBA'),
    ('Stripe', 'sk_' + 'live_' + 'PRUEBA0000PRUEBA0000PRUEBA00'),
    ('Stripe restringida', 'rk_' + 'live_' + 'PRUEBA0000PRUEBA0000PRUEBA00'),
    ('Hugging Face', 'hf' + '_PRUEBAaaaaBBBBccccDDDDeeeeFFFFgggg'),
    ('npm', 'npm' + '_PRUEBAaaaaBBBBccccDDDDeeeeFFFFgggg0000'),
    ('PyPI', 'pypi' + '-AgEIcHlwaS5vcmcPRUEBA0000PRUEBA0000'),
    ('Slack xoxe', 'xo' + 'xe-1-PRUEBA0000PRUEBA0000PRUEBA'),
    ('Slack webhook', 'https://hooks.' + 'slack.com/services/T00000000/B00000000/PRUEBA0000PRUEBA0000PRUE'),
    ('Discord webhook', 'https://discord.' + 'com/api/webhooks/000000000000000000/PRUEBA0000-PRUEBA0000_PRUEBA0000'),
    ('SendGrid', 'SG' + '.PRUEBA0000PRUEBA0000PR.PRUEBA0000PRUEBA0000PRUEBA0000PRUEBA0000PRU'),
    ('Google OAuth', 'ya29' + '.a0PRUEBA0000PRUEBA0000PRUEBA0000PRUEBA0000'),
    ('DigitalOcean', 'dop' + '_v1_0000111122223333444455556666777788889999aaaabbbbccccddddeeeeffff'),
    ('Telegram bot', '123456789' + ':AAPRUEBA0000PRUEBA0000PRUEBA0000PRU'),
    ('URL con ?key=', 'https://api.prueba.invalid/v1/x?key=PRUEBA0000PRUEBA0000'),
    ('URL con &access_token=', 'https://api.prueba.invalid/v1/x?a=1&access_token=PRUEBA0000PRUEBA0000'),
]
# La parte que no debe quedar: en los webhooks y las URL, lo que va después de la parte fija.
SECRETO = {'Slack webhook': 'PRUEBA0000PRUEBA0000PRUE', 'Discord webhook': 'PRUEBA0000-PRUEBA0000_PRUEBA0000',
           'URL con ?key=': 'PRUEBA0000PRUEBA0000', 'URL con &access_token=': 'PRUEBA0000PRUEBA0000'}


@pytest.mark.parametrize('nombre, valor', MUESTRAS, ids=[m[0] for m in MUESTRAS])
def test_se_tapa(nombre, valor):
    tapado = tapar('salida de una orden: ' + valor + ' fin')
    assert SECRETO.get(nombre, valor) not in tapado and REDACTADO in tapado


@pytest.mark.parametrize('nombre, valor', MUESTRAS, ids=[m[0] for m in MUESTRAS])
def test_la_memoria_no_la_guarda(nombre, valor):
    assert escaneo.problemas('Para desplegar usa ' + valor)


@pytest.mark.parametrize('texto', [
    'commit 3f2a1b4c5d6e7f8091a2b3c4d5e6f708192a3b4c',
    'https://ejemplo.org/lista?page=2&orden=fecha',
    'hf_hub_download descarga el modelo',
    'El puerto es 127.0.0.1:8080 y la versión 1.2.3',
    'Usa el paquete npm_config para la caché',
])
def test_lo_corriente_no_se_tapa(texto):
    assert tapar(texto) == texto
