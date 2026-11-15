"""Bifurcar no deja carpetas a medias.

Antes, con la última línea de la transcripción cortada (un corte de luz, el disco lleno), cada intento
de bifurcar fallaba después de crear la carpeta nueva y la dejaba sin task.json: la barra lateral la
enseñaba como una sesión «corrupta» más por intento.
"""
import json
from unittest.mock import patch

import pytest

from alfalfa import sesiones as registro
from alfalfa.sesiones import bifurcar
from tests.test_alfalfa_sesiones import preparar, proyecto  # noqa: F401  (fixture)


def carpetas(base):
    return sorted(c.name for c in base.iterdir() if c.is_dir())


def test_una_linea_cortada_no_impide_bifurcar(proyecto):  # noqa: F811
    ident, (t1, t2), base = preparar(proyecto)
    transcripcion = base / ident / (ident + '.jsonl')
    with open(transcripcion, 'a') as salida:
        salida.write('{"role": "agent", "text": "línea cortada a med')  # sin terminar
    nuevo = bifurcar(base, ident)
    copia = (base / nuevo / (nuevo + '.jsonl')).read_text().splitlines()
    assert copia and all(json.loads(linea) for linea in copia)
    assert json.loads((base / nuevo / 'task.json').read_text())['bifurcada_de']['sesion'] == ident


def test_si_falla_a_medias_no_queda_la_carpeta(proyecto):  # noqa: F811
    ident, _, base = preparar(proyecto)
    antes = carpetas(base)

    def disco_lleno(path, valor):
        raise OSError(28, 'No queda espacio en el disco')
    with patch.object(registro, '_escribir', disco_lleno):
        for _ in range(2):
            with pytest.raises(OSError):
                bifurcar(base, ident)
    assert carpetas(base) == antes


def test_continuar_tampoco_deja_restos(proyecto):  # noqa: F811
    ident, _, base = preparar(proyecto)
    estado = base / ident / 'native' / ident / 'session.json'
    datos = json.loads(estado.read_text())
    datos['checkpoint'] = {'estilo': 'codex', 'text': 'Resumen.', 'through': 1}
    estado.write_text(json.dumps(datos))
    antes = carpetas(base)

    def disco_lleno(path, valor):
        raise OSError(28, 'No queda espacio en el disco')
    with patch.object(registro, '_escribir', disco_lleno):
        with pytest.raises(OSError):
            registro.continuar(base, ident)
    assert carpetas(base) == antes
