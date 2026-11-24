// Demo del perfil `ui` de PACAI: un servidor local que abre un agente PACAI por stdio y lo enseña en el navegador.
//
// El servidor es el cliente PACAI: negocia `work` + `ui` con el catálogo base, numera los mensajes y los reenvía.
// La página recibe lo del agente por Server-Sent Events (GET /eventos) y manda lo suyo con POST /enviar.
// Sin dependencias: sólo Node 20 o posterior.
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { dirname, join } from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

const VERSION = '0.3';
const AQUI = dirname(fileURLToPath(import.meta.url));
const RAIZ = join(AQUI, '..', '..', '..');
const AYUDA = `Uso: node docs/demos/pacai-ui/servidor.mjs [opciones]

Abre un agente PACAI y sirve la demo en http://127.0.0.1:PUERTO.
  --simulado        Usa el agente simulado (sin modelo ni gasto). Necesita el .venv de AlfalfaCode.
  --alfalfa RUTA    Ejecutable de AlfalfaCode. Por defecto: alfalfa.
  --modelo MODELO   Modelo para AlfalfaCode. Por defecto, el configurado.
  --carpeta RUTA    Carpeta de trabajo del agente. Por defecto, la actual.
  --puerto N        Puerto local. Por defecto: 4173.
  --help            Muestra esta ayuda.`;

const ESTATICOS = {
    '/': ['index.html', 'text/html; charset=utf-8'],
    '/app.js': ['app.js', 'text/javascript; charset=utf-8'],
    '/ui.js': ['ui.js', 'text/javascript; charset=utf-8'],
    '/estilo.css': ['estilo.css', 'text/css; charset=utf-8'],
};
// Lo que la página puede mandar al agente. El sobre (id, sesión, seq, hora) lo pone el servidor.
const PERMITIDOS = new Set(['input', 'approval_result', 'interrupt']);

const { values: opciones } = parseArgs({
    options: {
        simulado: { type: 'boolean', default: false },
        alfalfa: { type: 'string', default: 'alfalfa' },
        modelo: { type: 'string' },
        carpeta: { type: 'string', default: process.cwd() },
        puerto: { type: 'string', default: '4173' },
        help: { type: 'boolean', default: false },
    },
});
if (opciones.help) {
    console.log(AYUDA);
    process.exit(0);
}

// -- el agente ---------------------------------------------------------------------------------------
const sesion = 's_' + randomBytes(16).toString('hex');
let seq = 0;
const historial = [];          // todo lo que dijo el agente, para la página que se conecta tarde o recarga
const oyentes = new Set();

const [ejecutable, argumentos] = opciones.simulado
    ? [join(RAIZ, '.venv', 'bin', 'python'), [join(AQUI, 'agente_simulado.py')]]
    : [opciones.alfalfa, ['--pacai', ...(opciones.modelo ? ['--modelo', opciones.modelo] : [])]];
const agente = spawn(ejecutable, argumentos, { cwd: opciones.carpeta, stdio: ['pipe', 'pipe', 'inherit'] });
agente.on('error', error => {
    console.error('No se pudo abrir el agente (' + ejecutable + '): ' + error.message);
    process.exit(1);
});
agente.on('close', codigo => {
    difundir({ type: 'demo.agent_exit', payload: { code: codigo } });
    console.error('El agente terminó con código ' + codigo + '.');
});
createInterface({ input: agente.stdout, crlfDelay: Infinity }).on('line', linea => {
    if (!linea.trim()) return;
    try {
        const mensaje = JSON.parse(linea);
        historial.push(mensaje);
        difundir(mensaje);
    } catch {
        console.error('Línea que no es JSON: ' + linea.slice(0, 200));
    }
});

function enviar(tipo, payload, respuestaA) {
    const mensaje = {
        pacai: VERSION, id: 'c_' + randomBytes(8).toString('hex'), type: tipo, session: sesion, sender: 'client',
        seq: ++seq, time: new Date().toISOString(), payload, ...(respuestaA && { reply_to: respuestaA }),
    };
    agente.stdin.write(JSON.stringify(mensaje) + '\n');
    historial.push(mensaje);
    difundir(mensaje);
    return mensaje.id;
}

function difundir(mensaje) {
    const datos = 'data: ' + JSON.stringify(mensaje) + '\n\n';
    for (const res of oyentes) res.write(datos);
}

enviar('hello', {
    versions: [VERSION], profiles: ['work', 'ui'], extensions: ['alfalfa'],
    render: { ui: { version: '1', catalog: 'base', actions: ['send', 'set', 'open_url'],
                    limits: { max_nodes: 300, max_depth: 12 } } },
    implementation: { name: 'pacai-ui-demo', version: '0.1.0' },
});

// -- el servidor web ---------------------------------------------------------------------------------
function leerCuerpo(req) {
    return new Promise((resolver, rechazar) => {
        let cuerpo = '';
        req.setEncoding('utf8');
        req.on('data', trozo => {
            cuerpo += trozo;
            if (cuerpo.length > 1024 * 1024) rechazar(new Error('Demasiado grande.'));
        });
        req.on('end', () => resolver(cuerpo));
        req.on('error', rechazar);
    });
}

const servidor = createServer(async (req, res) => {
    const ruta = new URL(req.url, 'http://localhost').pathname;
    // Sólo desde la propia página: nada de peticiones de otros orígenes al agente.
    const origen = req.headers.origin;
    if (origen && origen !== `http://127.0.0.1:${opciones.puerto}` && origen !== `http://localhost:${opciones.puerto}`) {
        res.writeHead(403).end();
        return;
    }
    if (req.method === 'GET' && ESTATICOS[ruta]) {
        const [archivo, tipo] = ESTATICOS[ruta];
        res.writeHead(200, {
            'content-type': tipo, 'cache-control': 'no-store',
            'content-security-policy': "default-src 'self'; img-src https: data:; style-src 'self'",
        });
        res.end(await readFile(join(AQUI, archivo)));
        return;
    }
    if (req.method === 'GET' && ruta === '/eventos') {
        res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store' });
        res.write('data: ' + JSON.stringify({ type: 'demo.reset' }) + '\n\n');
        for (const mensaje of historial) res.write('data: ' + JSON.stringify(mensaje) + '\n\n');
        oyentes.add(res);
        req.on('close', () => oyentes.delete(res));
        return;
    }
    if (req.method === 'POST' && ruta === '/enviar') {
        try {
            const { type, payload, reply_to: respuestaA } = JSON.parse(await leerCuerpo(req));
            if (!PERMITIDOS.has(type) || typeof payload !== 'object' || payload === null) throw new Error('Tipo no admitido.');
            res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ id: enviar(type, payload, respuestaA) }));
        } catch (error) {
            res.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ error: error.message }));
        }
        return;
    }
    res.writeHead(404).end();
});

servidor.on('error', error => {
    // Sin servidor no hay demo: se cierra también el agente, que si no quedaría huérfano escribiendo a nadie.
    console.error(error.code === 'EADDRINUSE'
        ? `El puerto ${opciones.puerto} está ocupado (¿otra demo abierta?). Ciérrala o usa --puerto N.`
        : 'No se pudo abrir el servidor: ' + error.message);
    agente.kill('SIGTERM');
    process.exit(1);
});

servidor.listen(Number(opciones.puerto), '127.0.0.1', () => {
    console.log(`Demo del perfil ui en http://127.0.0.1:${opciones.puerto}`
        + (opciones.simulado ? ' (agente simulado)' : ''));
});

function salir() {
    try {
        enviar('close', {});
        agente.stdin.end();
    } catch { /* ya cerrado */ }
    setTimeout(() => process.exit(0), 300);
}
process.on('SIGINT', salir);
process.on('SIGTERM', salir);
