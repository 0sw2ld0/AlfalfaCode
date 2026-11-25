// Corre los vectores compartidos de las piezas declarativas contra ui.js (sin navegador).
//   node docs/demos/pacai-ui/conformidad.mjs [tests/conformidad/pacai-ui-piezas.json]
// Escribe una línea JSON {total, fallos: [...]} y sale con 1 si algo falla. Los de donde=["motor"] se saltan.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { argumentos, calc, decimal, aTexto, estadoInicial, formatear, validarCampo, visibles } from './ui.js';

const ruta = process.argv[2]
    ?? fileURLToPath(new URL('../../../tests/conformidad/pacai-ui-piezas.json', import.meta.url));
const { vectores } = JSON.parse(readFileSync(ruta, 'utf8'));
const igual = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const fallos = [];
let total = 0;

for (const v of vectores) {
    if (v.donde && !v.donde.includes('web')) continue;
    total += 1;
    let obtenido;
    if (v.tipo === 'decimal') obtenido = aTexto(decimal(v.entrada));
    else if (v.tipo === 'formatear') obtenido = formatear(v.valor, v.formato);
    else if (v.tipo === 'calc') obtenido = calc(v.preset, v.compra, v.venta, v.cantidad);
    else if (v.tipo === 'validar') obtenido = validarCampo(v.componente, v.props, v.valor);
    else if (v.tipo === 'argumentos') obtenido = argumentos(v.query, k => v.estado[k]);
    else if (v.tipo === 'visibles') {
        let estado = estadoInicial(v.nodos, {});
        obtenido = [];
        for (const paso of v.pasos) {
            estado = { ...estado, ...paso.estado };
            obtenido.push(visibles(v.nodos, estado));
        }
        if (!igual(obtenido, v.pasos.map(p => p.esperado))) fallos.push({ id: v.id, obtenido });
        continue;
    } else {
        fallos.push({ id: v.id, obtenido: 'tipo desconocido' });
        continue;
    }
    if (!igual(obtenido ?? null, v.esperado)) fallos.push({ id: v.id, obtenido: obtenido ?? null });
}
console.log(JSON.stringify({ total, fallos }));
process.exit(fallos.length ? 1 : 0);
