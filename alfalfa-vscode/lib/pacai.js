'use strict';
// Cliente PACAI 0.3 por stdio: lanza `alfalfa --pacai`, negocia y reparte los mensajes del agente.
// No depende de VS Code, así que se prueba con Node solo (test/pacai.test.js).
const { spawn } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const { EventEmitter } = require('node:events');
const { createInterface } = require('node:readline');

const VERSION = '0.3';
// Perfil `ui`: el panel dibuja las pantallas del agente con el catálogo base (media/ui.js).
const CATALOGO_UI = { version: '1', catalog: 'base', actions: ['send', 'set', 'open_url'],
    limits: { max_nodes: 300, max_depth: 12 } };
const ESQUEMA_ACCION = 'pacai.ui.action/1';
// Los modos no se listan aquí: los anuncia el motor (`alfalfa.session` → `modes`) y él valida el que llega.
const MODO_VALIDO = /^[a-z][a-z0-9_]{0,40}$/;

function nuevoId() {
    return 'c_' + randomBytes(8).toString('hex');
}

/**
 * Eventos: `mensaje` (cada mensaje del agente, ya comprobado), `estado` ({ocupado, run}),
 * `salida` ({codigo, senal, stderr}), `fallo` (Error del cliente o del proceso).
 */
class ClientePacai extends EventEmitter {
    /**
     * @param {{ejecutable: string, cwd: string, modo?: string, modelo?: string, esfuerzo?: string,
     *          reanudar?: string, env?: NodeJS.ProcessEnv, argumentos?: string[]}} opciones
     */
    constructor(opciones) {
        super();
        this.opciones = opciones;
        this.sesion = 's_' + randomBytes(16).toString('hex');
        this.seq = 0;
        this.seqAgente = 0;
        this.proceso = null;
        this.iniciada = false;
        this.raiz = null;              // run_id de la ejecución raíz activa
        this.stderr = '';
        this.cerrado = false;
    }

    get ocupado() {
        return this.raiz !== null;
    }

    iniciar() {
        if (this.proceso) return;
        const o = this.opciones;
        const argumentos = ['--pacai'];
        if (o.modo) argumentos.push('--modo', o.modo);
        if (o.modelo) argumentos.push('--modelo', o.modelo);
        if (o.esfuerzo) argumentos.push('--esfuerzo', o.esfuerzo);
        if (o.reanudar) argumentos.push('--reanudar', o.reanudar);
        argumentos.push(...(o.argumentos ?? []));
        const proceso = spawn(o.ejecutable, argumentos, {
            cwd: o.cwd, env: o.env ?? process.env, stdio: ['pipe', 'pipe', 'pipe'],
        });
        this.proceso = proceso;
        proceso.on('error', error => this._fallar(error));
        proceso.stdin.on('error', error => this._fallar(error));
        proceso.stderr.setEncoding('utf8');
        proceso.stderr.on('data', trozo => {
            // Sólo lo último: sirve para explicar una salida inesperada.
            this.stderr = (this.stderr + trozo).slice(-8000);
        });
        proceso.on('close', (codigo, senal) => {
            this.cerrado = true;
            this.raiz = null;
            this.emit('estado', { ocupado: false, run: null });
            this.emit('salida', { codigo, senal, stderr: this.stderr });
        });
        const lineas = createInterface({ input: proceso.stdout, crlfDelay: Infinity });
        lineas.on('line', linea => this._recibir(linea));
        this.saludo = this._enviar('hello', {
            versions: [VERSION], profiles: ['work', 'ui'], extensions: ['alfalfa'], render: { ui: CATALOGO_UI },
            implementation: { name: 'alfalfa-vscode', version: o.version ?? '0.0.0' },
        });
    }

    /** Un mensaje del usuario. Con una ejecución en curso, `encauzar` lo mete en ella (steer); si no, espera turno. */
    enviarTexto(texto, { adjuntos = [], encauzar = false } = {}) {
        this.iniciar();
        const blocks = [];
        if (texto) blocks.push({ type: 'text', text: texto });
        for (const ruta of adjuntos) {
            blocks.push({ type: 'file', name: ruta.split('/').pop(), media_type: tipoDe(ruta),
                url: 'file://' + encodeURI(ruta) });
        }
        const payload = { blocks };
        if (encauzar && this.raiz) {
            payload.mode = 'steer';
            payload.run_id = this.raiz;
            payload.alfalfa = { redirect: false };
        }
        return this._enviar('input', payload);
    }

    /**
     * Lo que el usuario pulsó o envió en una pantalla del agente: un `input` con su texto legible y el bloque
     * `pacai.ui.action/1`. Con una ejecución en curso entra en ella (steer), como un mensaje.
     */
    accionDePantalla(data, resumen) {
        this.iniciar();
        const payload = { blocks: [{ type: 'text', text: resumen },
                                   { type: 'structured', schema: ESQUEMA_ACCION, data }] };
        if (this.raiz) {
            payload.mode = 'steer';
            payload.run_id = this.raiz;
            payload.alfalfa = { redirect: false };
        }
        return this._enviar('input', payload);
    }

    aprobar(peticion, alcance = 'once') {
        return this._enviar('approval_result', { decision: 'approved', scope: alcance }, peticion);
    }

    denegar(peticion, motivo) {
        return this._enviar('approval_result', { decision: 'denied', ...(motivo && { reason: motivo }) }, peticion);
    }

    interrumpir() {
        if (this.raiz) return this._enviar('interrupt', { run_id: this.raiz });
        return null;
    }

    /** `alfalfa.set_settings`: cambia modelo, esfuerzo o modo de permisos, también a mitad de sesión. */
    ajustes({ modelo, esfuerzo, modo }) {
        if (!this.proceso || this.cerrado) return null;
        if (modo && !MODO_VALIDO.test(modo)) throw new Error('Modo no válido: ' + modo);
        const payload = {};
        if (modelo) payload.model = modelo;
        if (esfuerzo) payload.effort = esfuerzo;
        if (modo) payload.mode = modo;
        return this._enviar('alfalfa.set_settings', payload);
    }

    cerrar() {
        if (!this.proceso || this.cerrado) return;
        try {
            this._enviar('close', {});
            this.proceso.stdin.end();
        } catch { /* ya cerrado */ }
        const proceso = this.proceso;
        // Si no cierra solo en unos segundos, se le corta.
        setTimeout(() => { if (!this.cerrado) proceso.kill('SIGTERM'); }, 5000).unref?.();
    }

    _enviar(tipo, payload, respuestaA) {
        if (!this.proceso || this.cerrado) throw new Error('AlfalfaCode no está en marcha.');
        const mensaje = {
            pacai: VERSION, id: nuevoId(), type: tipo, session: this.sesion, sender: 'client',
            seq: ++this.seq, time: new Date().toISOString(), payload,
            ...(respuestaA && { reply_to: respuestaA }),
        };
        this.proceso.stdin.write(JSON.stringify(mensaje) + '\n');
        this.emit('enviado', mensaje);
        return mensaje.id;
    }

    _recibir(linea) {
        if (!linea.trim()) return;
        let mensaje;
        try {
            mensaje = JSON.parse(linea);
        } catch {
            this._fallar(new Error('Línea que no es JSON en la salida de AlfalfaCode: ' + linea.slice(0, 200)));
            return;
        }
        if (mensaje.pacai !== VERSION || mensaje.sender !== 'agent') {
            this._fallar(new Error('Sobre PACAI inesperado: ' + linea.slice(0, 200)));
            return;
        }
        // El agente adopta nuestra sesión en el `hello`; antes de él puede usar la suya.
        if (mensaje.seq !== ++this.seqAgente) {
            this._fallar(new Error(`Secuencia rota: llegó ${mensaje.seq}, se esperaba ${this.seqAgente}.`));
            return;
        }
        const p = mensaje.payload ?? {};
        switch (mensaje.type) {
            case 'session_started':
                this.iniciada = true;
                break;
            case 'run_started':
                if (!p.parent_run_id) {
                    this.raiz = mensaje.id;
                    this.emit('estado', { ocupado: true, run: mensaje.id });
                }
                break;
            case 'run_finished':
                if (p.run_id === this.raiz) {
                    this.raiz = null;
                    this.emit('estado', { ocupado: false, run: null });
                }
                break;
        }
        this.emit('mensaje', mensaje);
    }

    _fallar(error) {
        this.emit('fallo', error);
    }
}

function tipoDe(ruta) {
    const ext = ruta.toLowerCase().split('.').pop();
    return {
        png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp',
        pdf: 'application/pdf', json: 'application/json', md: 'text/markdown',
    }[ext] ?? 'text/plain';
}

module.exports = { ClientePacai, VERSION, CATALOGO_UI };
