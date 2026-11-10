import Foundation

/// Observación limitada de la página, sin leer cabeceras ni cuerpos de peticiones o respuestas.
enum BrowserDiagnosticsScripts {
    static let observe = #"""
    (() => {
      const bridge = window.webkit?.messageHandlers?.alfalfaDiagnostics;
      if (!bridge) return;
      const send = value => { try { bridge.postMessage(value); } catch (_) {} };
      const text = value => {
        try {
          if (value instanceof Error) return String(value.message).slice(0, 2000);
          return (typeof value === 'string' ? value : JSON.stringify(value) ?? String(value)).slice(0, 2000);
        } catch (_) { return '[valor no serializable]'; }
      };
      const log = (level, values) => send({ tipo: 'consola', nivel: level,
        texto: values.slice(0, 10).map(text).join(' ').slice(0, 2000) });
      for (const level of ['log', 'info', 'warn', 'error', 'debug']) {
        const original = console[level];
        console[level] = function (...values) { log(level, values); return original.apply(this, values); };
      }
      const urlOf = value => {
        try { const u = new URL(String(value), location.href);
          return ['http:', 'https:'].includes(u.protocol) ? u.href.slice(0, 4000) : '[URL no HTTP oculta]';
        } catch (_) { return '[URL no válida]'; }
      };
      const network = (method, url, status, start, failed) => send({ tipo: 'red',
        metodo: String(method).toUpperCase().slice(0, 16), url: urlOf(url), estado: status,
        duracion_ms: Math.max(0, performance.now() - start), fallo: failed || status >= 400 });
      addEventListener('error', event => {
        if (event.target === window) log('exception', [event.message || 'Error sin capturar']);
        else { const el = event.target;
          network('GET', el?.currentSrc || el?.src || el?.href || location.href, 0, performance.now(), true); }
      }, true);
      addEventListener('unhandledrejection', event => log('unhandledrejection', [event.reason]));
      const originalFetch = window.fetch;
      window.fetch = function (input, options) {
        const start = performance.now();
        const url = input instanceof Request ? input.url : input;
        const method = options?.method || (input instanceof Request ? input.method : 'GET');
        return originalFetch.apply(this, arguments).then(response => {
          network(method, url, response.status, start, false); return response;
        }, error => { network(method, url, 0, start, true); throw error; });
      };
      const open = XMLHttpRequest.prototype.open, sendXHR = XMLHttpRequest.prototype.send;
      const requests = new WeakMap();
      XMLHttpRequest.prototype.open = function (method, url) {
        requests.set(this, { method, url }); return open.apply(this, arguments);
      };
      XMLHttpRequest.prototype.send = function () {
        const request = requests.get(this), start = performance.now();
        this.addEventListener('loadend', () => {
          if (request) network(request.method, request.url, this.status, start, this.status === 0);
        }, { once: true });
        return sendXHR.apply(this, arguments);
      };
      try {
        new PerformanceObserver(list => {
          for (const item of list.getEntries()) {
            if (['fetch', 'xmlhttprequest'].includes(item.initiatorType)) continue;
            const status = item.responseStatus || 0;
            network('GET', item.name, status, performance.now() - item.duration, status >= 400);
          }
        }).observe({ type: 'resource', buffered: true });
      } catch (_) {}
    })();
    """#
}
