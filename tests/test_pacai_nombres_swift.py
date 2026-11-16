"""La app de Mac deshace los nombres de la extensión `alfalfa` con una copia de `alfalfa/pacai/nombres.py`
(`alfalfa-mac/Sources/AlfalfaKit/Pacai/PacaiNames.swift`). Si cambia la tabla del motor sin rehacer la copia, la app
dejaría de entender un campo o un valor nuevo: esta prueba lo dice."""
import importlib.util
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def _guion():
    spec = importlib.util.spec_from_file_location('nombres_pacai_swift', RAIZ / 'scripts' / 'nombres_pacai_swift.py')
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_la_tabla_de_la_app_es_la_del_motor():
    guion = _guion()
    assert guion.DESTINO.read_text(encoding='utf-8') == guion.generar(), \
        'PacaiNames.swift no está al día: python scripts/nombres_pacai_swift.py'


def test_la_tabla_de_la_app_lleva_cada_nombre_y_cada_valor():
    from alfalfa.pacai.nombres import CAMPOS, VALORES
    texto = _guion().generar()
    for espanol, ingles in CAMPOS.items():
        assert '"' + espanol + '": "' + ingles + '"' in texto
    for tabla in VALORES.values():
        for espanol, ingles in tabla.items():
            assert '"' + espanol + '": "' + ingles + '"' in texto
