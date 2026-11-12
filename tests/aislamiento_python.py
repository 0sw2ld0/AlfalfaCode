"""Aislamiento del Llavero de IA heredado por los procesos Python de las pruebas.

No cambia la aplicación: se instala desde conftest y como sitecustomize temporal.
El import hook sólo sustituye el almacén cuando un hijo importa llavero.
"""
import importlib.abc
import importlib.machinery
import os
import subprocess
import sys
import tempfile
from pathlib import Path


class _LlaveroSimulado(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname != 'alfalfa.proveedores.llavero':
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return None
        original = spec.loader

        class Loader:
            def create_module(self, spec):
                crear = getattr(original, 'create_module', None)
                return crear(spec) if crear else None

            def exec_module(self, module):
                original.exec_module(module)
                module._almacen = {}
                module._leidos = {}

                def prohibido():
                    raise AssertionError('Una prueba intentó acceder al Llavero real de IA.')

                module._security = prohibido

        spec.loader = Loader()
        return spec


def instalar(directorio, *, hijo=True):
    if getattr(subprocess, '_alfalfa_aislamiento', False):
        return
    if hijo:
        sys.meta_path.insert(0, _LlaveroSimulado())
    original = subprocess.Popen
    hook = str(Path(directorio) / 'sitecustomize.py')
    bootstrap = 'exec(compile(open(' + repr(hook) + ', encoding="utf-8").read(), ' + repr(hook) \
        + ', "exec"), {"__file__": ' + repr(hook) + ', "__name__": "sitecustomize"})\n'

    def argumentos(*args, **kwargs):
        orden = args[0] if args else kwargs.get('args')
        lista = list(orden) if isinstance(orden, (list, tuple)) else [orden]
        inicio = lista.index('--') + 1 if lista and Path(os.fsdecode(lista[0])).name == 'sandbox-exec' \
            and '--' in lista else 0
        nombre = Path(os.fsdecode(lista[inicio])).name if lista and lista[inicio] is not None else ''
        if nombre == 'security':
            raise AssertionError('Las pruebas no pueden ejecutar security contra el Llavero real.')
        # Bootstrap en argv, también con -I/-E/-S. No se amplía el entorno mínimo de MCP.
        python = lista[inicio:]
        if nombre.startswith('python'):
            if '-c' in python:
                indice = inicio + python.index('-c')
                lista[indice + 1] = bootstrap + lista[indice + 1]
            elif '-m' in python:
                indice = inicio + python.index('-m')
                modulo = lista[indice + 1]
                codigo = bootstrap + 'import runpy, sys\nsys.argv[0] = ' + repr(modulo) \
                    + '\nrunpy.run_module(' + repr(modulo) + ', run_name="__main__", alter_sys=True)'
                lista[indice:] = ['-c', codigo, *lista[indice + 2:]]
            else:
                saltar = False
                indice = None
                for posicion, valor in enumerate(python[1:], inicio + 1):
                    if saltar:
                        saltar = False
                    elif valor in ('-W', '-X'):
                        saltar = True
                    elif not str(valor).startswith('-'):
                        indice = posicion
                        break
                if indice is None:
                    if any(v in ('-V', '--version', '-h', '--help') for v in python):
                        return args, kwargs
                    raise AssertionError('Un Python de pruebas sin script debe usar -c o -m para conservar el guard.')
                script = os.fsdecode(lista[indice])
                ruta = str((Path(kwargs.get('cwd') or os.getcwd()) / script).resolve())
                codigo = bootstrap + 'import runpy,sys\nsys.argv[0] = ' + repr(script) \
                    + '\nsys.path[0] = ' + repr(str(Path(ruta).parent)) \
                    + '\nrunpy.run_path(' + repr(ruta) + ', run_name="__main__")'
                lista[indice:] = ['-c', codigo, *lista[indice + 1:]]
            if args:
                args = (lista, *args[1:])
            else:
                kwargs['args'] = lista
        elif nombre == 'alfalfa':
            # El lanzador de scripts/alfalfa conserva PYTHONPATH al llamar al Python compartido.
            entorno = dict(os.environ if kwargs.get('env') is None else kwargs['env'])
            anterior = entorno.get('PYTHONPATH', '')
            entorno['PYTHONPATH'] = directorio + (os.pathsep + anterior if anterior else '')
            kwargs['env'] = entorno
        return args, kwargs

    class Popen(original):
        def __init__(self, *args, **kwargs):
            args, kwargs = argumentos(*args, **kwargs)
            super().__init__(*args, **kwargs)

    subprocess.Popen = Popen
    subprocess._alfalfa_aislamiento = True


def preparar():
    directorio = tempfile.mkdtemp(prefix='alfalfa-pytest-python-')
    Path(directorio, 'sitecustomize.py').write_text(Path(__file__).read_text(), encoding='utf-8')
    instalar(directorio, hijo=False)


if Path(globals().get('__file__', '')).name == 'sitecustomize.py':
    instalar(str(Path(__file__).parent))
