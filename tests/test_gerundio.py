"""La fila viva de la app dice lo que hace el agente en gerundio («Leyendo runtime.py»):
viaja en `herramienta.en_curso` del evento que abre la llamada, junto a `descripcion`."""

import pytest

from alfalfa.proveedores.nativo.descripciones import datos_de_inicio
from alfalfa.proveedores.nativo.gerundio import en_curso, gerundio_de


@pytest.mark.parametrize('infinitivo, gerundio', [
    ('usar', 'usando'), ('ejecutar', 'ejecutando'), ('crear', 'creando'), ('hacer', 'haciendo'),
    ('escribir', 'escribiendo'), ('abrir', 'abriendo'), ('ver', 'viendo'), ('ser', 'siendo'),
    ('leer', 'leyendo'), ('releer', 'releyendo'), ('traer', 'trayendo'), ('extraer', 'extrayendo'),
    ('ir', 'yendo'), ('oír', 'oyendo'), ('construir', 'construyendo'), ('incluir', 'incluyendo'),
    ('distinguir', 'distinguiendo'), ('seguir', 'siguiendo'), ('conseguir', 'consiguiendo'),
    ('pedir', 'pidiendo'), ('repetir', 'repitiendo'), ('elegir', 'eligiendo'), ('corregir', 'corrigiendo'),
    ('servir', 'sirviendo'), ('convertir', 'convirtiendo'), ('sugerir', 'sugiriendo'), ('sentir', 'sintiendo'),
    ('vestir', 'vistiendo'), ('decir', 'diciendo'), ('predecir', 'prediciendo'), ('venir', 'viniendo'),
    ('prevenir', 'previniendo'), ('poder', 'pudiendo'), ('dormir', 'durmiendo'), ('morir', 'muriendo'),
    ('reír', 'riendo'), ('sonreír', 'sonriendo'), ('gruñir', 'gruñendo'), ('teñir', 'tiñendo'),
    ('permitir', 'permitiendo'), ('existir', 'existiendo'), ('recibir', 'recibiendo'), ('dirigir', 'dirigiendo'),
])
def test_gerundio_de_cada_verbo(infinitivo, gerundio):
    assert gerundio_de(infinitivo) == gerundio


def test_solo_cambia_el_primer_verbo_y_conserva_lo_demas():
    assert en_curso('Leer runtime.py') == 'Leyendo runtime.py'
    assert en_curso('Ver los archivos del proyecto') == 'Viendo los archivos del proyecto'
    assert en_curso('Buscar «leer el archivo» en src') == 'Buscando «leer el archivo» en src'
    assert en_curso('Ir a la página siguiente') == 'Yendo a la página siguiente'
    assert en_curso('Encargar a un subagente: revisar') == 'Encargando a un subagente: revisar'
    assert en_curso('  Pasar   las pruebas ') == 'Pasando las pruebas'
    assert en_curso('compilar') == 'compilando'


def test_pronombres_pegados_llevan_tilde():
    assert en_curso('Revisarlo todo') == 'Revisándolo todo'
    assert en_curso('Decirle que siga') == 'Diciéndole que siga'
    assert en_curso('Leerlas otra vez') == 'Leyéndolas otra vez'
    assert en_curso('Prepararse') == 'Preparándose'


def test_lo_que_no_empieza_por_infinitivo_queda_igual():
    assert en_curso('Ejecutando ya') == 'Ejecutando ya'
    assert en_curso('Primer intento') == 'Primer intento'
    assert en_curso('git status') == 'git status'
    assert en_curso('¿Qué cambió?') == '¿Qué cambió?'
    assert en_curso('') == ''
    assert en_curso(None) == ''
    # La única frase sin verbo que genera el motor.
    assert en_curso('Captura del navegador') == 'Capturando el navegador'


def test_la_descripcion_del_modelo_en_ingles_no_se_toca():
    assert en_curso('Run the tests', propia=True) == 'Run the tests'
    assert en_curso('Clear cache', propia=True) == 'Clear cache'
    assert en_curso('Filter logs', propia=True) == 'Filter logs'
    # En español sí, aunque lleve «a» o una cita en inglés.
    assert en_curso('Volver a compilar', propia=True) == 'Volviendo a compilar'
    assert en_curso('Buscar «the end» en los logs', propia=True) == 'Buscando «the end» en los logs'
    assert en_curso('Compilar', propia=True) == 'Compilando'


def test_el_inicio_de_la_llamada_lleva_las_dos_frases():
    datos = datos_de_inicio('read_file', {'path': 'alfalfa/terminal.py'})
    assert datos['descripcion'] == 'Leer terminal.py'
    assert datos['en_curso'] == 'Leyendo terminal.py'
    orden = datos_de_inicio('run_command', {'argv': ['pytest', '-q'], 'description': 'Pasar las pruebas'})
    assert orden['descripcion'] == 'Pasar las pruebas'
    assert orden['en_curso'] == 'Pasando las pruebas'
    assert datos_de_inicio('run_command', {'argv': ['make'], 'description': 'Build the app'})['en_curso'] \
        == 'Build the app'
    assert datos_de_inicio('run_command', {'argv': ['make']})['en_curso'] == 'Ejecutando make'
    assert datos_de_inicio('browser_screenshot', {})['en_curso'] == 'Capturando el navegador'
    assert datos_de_inicio('update_plan', {})['en_curso'] == 'Actualizando el plan'
