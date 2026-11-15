"""Una línea de gasto cortada no se lleva por delante la siguiente.

Si una sesión muere a media línea (o el disco se llena), su trozo queda sin salto y la fila de la
siguiente petición se escribe pegada detrás. Antes las dos se descartaban al leer: el tope del mes
contaba de menos y se podía gastar más de lo fijado sin aviso.
"""
from alfalfa.presupuesto import GastoMensual, mes_actual


def test_la_fila_pegada_tras_una_cortada_cuenta(tmp_path):
    GastoMensual(tmp_path).apuntar('s1', 0.25)
    archivo = tmp_path / 'gasto' / (mes_actual() + '.jsonl')
    archivo.write_bytes(archivo.read_bytes() + b'{"t": 1.0, "s": "s2", "us')  # murió a media línea
    GastoMensual(tmp_path).apuntar('s3', 0.5)
    assert GastoMensual(tmp_path).total() == 0.75


def test_la_misma_lectura_que_ya_iba_por_ahi_tambien(tmp_path):
    gasto = GastoMensual(tmp_path)
    gasto.apuntar('s1', 0.25)
    assert gasto.total() == 0.25
    archivo = tmp_path / 'gasto' / (mes_actual() + '.jsonl')
    with open(archivo, 'ab') as salida:
        salida.write(b'{"t": 1.0, "s": "s2", "usd": 0.1')  # sin cerrar ni salto
    gasto.apuntar('s3', 0.5)
    assert gasto.total() == 0.75


def test_la_basura_sola_no_cuenta(tmp_path):
    (tmp_path / 'gasto').mkdir()
    (tmp_path / 'gasto' / (mes_actual() + '.jsonl')).write_bytes(b'basura {"t" nada\n{"t": 1, "usd": -3}\n')
    assert GastoMensual(tmp_path).total() == 0.0
