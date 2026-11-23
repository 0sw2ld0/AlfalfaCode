'use strict';
// Dónde está `alfalfa`. Un editor abierto desde el Dock no hereda el PATH del shell, y quien sólo instaló la app de
// Mac no tiene la orden en ningún PATH: la app lleva su motor dentro. Por eso, si el ajuste no lo dice, se prueba en
// este orden: el PATH del editor, el del shell del usuario, las carpetas habituales y el motor de la app.
const { execFile, spawn } = require('node:child_process');
const { accessSync, constants } = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const HABITUALES = ['/opt/homebrew/bin', '/usr/local/bin', path.join(os.homedir(), '.local/bin')];
const MOTOR_EN_LA_APP = 'Contents/Resources/motor/alfalfa';
const APPS = ['/Applications/AlfalfaCode.app', path.join(os.homedir(), 'Applications/AlfalfaCode.app')];
const ID_DE_LA_APP = 'com.cuycoders.alfalfacode';

function ejecutable(ruta) {
    try {
        accessSync(ruta, constants.X_OK);
        return true;
    } catch {
        return false;
    }
}

function salidaDe(orden, argumentos, plazo = 5000) {
    return new Promise(resolver => {
        execFile(orden, argumentos, { timeout: plazo }, (error, salida) => resolver(error ? '' : String(salida || '')));
    });
}

/**
 * Una orden en el shell del usuario, en su propia sesión y sin terminal (`detached`, stdin cerrado), como hace
 * VS Code al leer el entorno del shell. Un zsh interactivo activa el control de trabajos e intenta quedarse con la
 * terminal: si el editor se abrió desde una, eso puede detener el proceso de las extensiones (el panel en negro).
 * Con plazo: al vencer, se mata todo su grupo de procesos.
 */
function enElShell(shell, argumentos, plazo = 5000) {
    return new Promise(resolver => {
        let salida = '';
        let hijo;
        try {
            hijo = spawn(shell, argumentos, { detached: true, stdio: ['ignore', 'pipe', 'ignore'] });
        } catch {
            resolver('');
            return;
        }
        const reloj = setTimeout(() => {
            try { process.kill(-hijo.pid, 'SIGKILL'); } catch { /* ya terminó */ }
            resolver('');
        }, plazo);
        hijo.stdout.setEncoding('utf8');
        hijo.stdout.on('data', trozo => { salida += trozo; });
        hijo.on('error', () => { clearTimeout(reloj); resolver(''); });
        hijo.on('close', codigo => { clearTimeout(reloj); resolver(codigo === 0 ? salida : ''); });
    });
}

/** `command -v alfalfa` en el shell del usuario, también interactivo: el PATH de ~/.zshrc sólo se lee así. */
async function delShell() {
    const shell = process.env.SHELL || '/bin/zsh';
    for (const opciones of ['-lc', '-ilc']) {
        const salida = await enElShell(shell, [opciones, 'command -v alfalfa']);
        // Un shell interactivo puede escribir más cosas (avisos, un alias): sólo vale una ruta absoluta.
        const ruta = salida.split('\n').map(l => l.trim()).filter(l => l.startsWith('/')).pop();
        if (ruta && ejecutable(ruta)) return ruta;
    }
    return null;
}

/** El motor de la app de Mac, en /Applications, en ~/Applications o donde la encuentre Spotlight. */
async function deLaApp() {
    for (const app of APPS) {
        const ruta = path.join(app, MOTOR_EN_LA_APP);
        if (ejecutable(ruta)) return ruta;
    }
    const encontradas = await salidaDe('/usr/bin/mdfind', [`kMDItemCFBundleIdentifier == '${ID_DE_LA_APP}'`]);
    for (const app of encontradas.split('\n').map(l => l.trim()).filter(Boolean)) {
        const ruta = path.join(app, MOTOR_EN_LA_APP);
        if (ejecutable(ruta)) return ruta;
    }
    return null;
}

/** La ruta de `alfalfa`, o null si no aparece. `ajuste` es el de `alfalfa.ejecutable`. */
async function buscarAlfalfa(ajuste) {
    if (ajuste) {
        const ruta = ajuste.startsWith('~') ? path.join(os.homedir(), ajuste.slice(1)) : ajuste;
        if (!ruta.includes('/')) return ruta;          // un nombre: que lo busque el PATH
        return ejecutable(ruta) ? ruta : null;
    }
    for (const carpeta of (process.env.PATH || '').split(path.delimiter)) {
        if (carpeta && ejecutable(path.join(carpeta, 'alfalfa'))) return path.join(carpeta, 'alfalfa');
    }
    const delInicio = await delShell();
    if (delInicio) return delInicio;
    for (const carpeta of HABITUALES) {
        if (ejecutable(path.join(carpeta, 'alfalfa'))) return path.join(carpeta, 'alfalfa');
    }
    return deLaApp();
}

/** Dónde se buscó, para explicarlo cuando no aparece. */
const LUGARES = ['el PATH del editor', 'el de tu shell', ...HABITUALES.map(c => c.replace(os.homedir(), '~')),
    'la app AlfalfaCode'];

/** El entorno del proceso con las carpetas habituales en el PATH (git, ollama, node… para las herramientas). */
function entorno() {
    const actuales = (process.env.PATH || '').split(path.delimiter).filter(Boolean);
    const extra = HABITUALES.filter(c => !actuales.includes(c));
    return { ...process.env, PATH: [...actuales, ...extra].join(path.delimiter) };
}

module.exports = { buscarAlfalfa, entorno, LUGARES, deLaApp, delShell, enElShell };
