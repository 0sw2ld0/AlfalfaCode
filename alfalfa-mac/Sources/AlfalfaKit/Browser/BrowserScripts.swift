import Foundation

/// El JavaScript con el que el navegador propio lee y maneja la página. Cada guion es el cuerpo de
/// una función de `callAsyncJavaScript` con un argumento `args` y corre en un mundo aislado
/// (`WKContentWorld` «alfalfa»): la página no ve sus variables ni las referencias [eN].
enum BrowserScripts {

    /// Lo que comparten todos: referencias, visibilidad, rol, nombre accesible y búsqueda.
    static let prelude = #"""
    const A = globalThis.__alfalfa ??= { refs: new Map(), next: 1, doc: null };
    if (A.doc !== document) { A.refs = new Map(); A.next = 1; A.doc = document; }
    const clean = (t, n = 120) => {
      const s = String(t ?? '').replace(/\s+/g, ' ').trim();
      return s.length > n ? s.slice(0, n - 1) + '…' : s;
    };
    const INTERACTIVE = new Set(['link', 'button', 'textbox', 'searchbox', 'combobox', 'listbox', 'checkbox', 'radio',
      'slider', 'spinbutton', 'switch', 'tab', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'option', 'treeitem']);
    function visible(el) {
      if (!(el instanceof Element)) return false;
      if (el.closest('[aria-hidden="true"],[hidden],[inert]')) return false;
      if (typeof el.checkVisibility === 'function') {
        if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
      } else {
        const s = getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
      }
      const r = el.getBoundingClientRect();
      return (r.width > 0 && r.height > 0) || getComputedStyle(el).display === 'contents';
    }
    function role(el) {
      const explicit = (el.getAttribute('role') || '').trim().split(/\s+/)[0];
      if (explicit && explicit !== 'presentation' && explicit !== 'none') return explicit;
      const tag = el.tagName.toLowerCase();
      if (tag === 'a') return el.hasAttribute('href') ? 'link' : null;
      if (tag === 'button' || tag === 'summary') return 'button';
      if (tag === 'select') return el.multiple || el.size > 1 ? 'listbox' : 'combobox';
      if (tag === 'textarea') return 'textbox';
      if (tag === 'img') return clean(el.getAttribute('alt')) ? 'img' : null;
      if (/^h[1-6]$/.test(tag)) return 'heading';
      if (tag === 'form') return 'form';
      if (tag === 'nav') return 'navigation';
      if (tag === 'main') return 'main';
      if (tag === 'dialog') return 'dialog';
      if (tag === 'input') {
        const t = (el.getAttribute('type') || 'text').toLowerCase();
        if (t === 'hidden') return null;
        if (['button', 'submit', 'reset', 'image', 'file'].includes(t)) return 'button';
        if (t === 'checkbox' || t === 'radio') return t;
        if (t === 'range') return 'slider';
        if (t === 'number') return 'spinbutton';
        if (t === 'search') return 'searchbox';
        return 'textbox';
      }
      if (el.isContentEditable && el.hasAttribute('contenteditable')) return 'textbox';
      if (el.hasAttribute('onclick') || (el.tabIndex >= 0 && el.hasAttribute('tabindex'))) return 'button';
      return null;
    }
    function name(el) {
      const by = el.getAttribute('aria-labelledby');
      if (by) {
        const t = clean(by.split(/\s+/).map(id => document.getElementById(id)?.innerText || '').join(' '));
        if (t) return t;
      }
      const aria = clean(el.getAttribute('aria-label'));
      if (aria) return aria;
      if (el.labels && el.labels.length) {
        const t = clean([...el.labels].map(l => l.innerText).join(' '));
        if (t) return t;
      }
      const tag = el.tagName.toLowerCase();
      if (tag === 'img') return clean(el.getAttribute('alt'));
      if (tag === 'input') {
        const t = (el.type || '').toLowerCase();
        if (['button', 'submit', 'reset'].includes(t)) return clean(el.value) || (t === 'submit' ? 'Enviar' : '');
        if (t === 'image') return clean(el.alt);
      }
      if (tag !== 'input' && tag !== 'textarea' && tag !== 'select') {
        const t = clean(el.innerText || el.textContent);
        if (t) return t;
      }
      return clean(el.getAttribute('placeholder') || el.getAttribute('title')
        || el.querySelector?.('img[alt]')?.alt || '');
    }
    function control(el) { return el.tagName === 'LABEL' && el.control ? el.control : el; }
    function submits(el) {
      const c = control(el);
      const t = (c.getAttribute('type') || '').toLowerCase();
      const isSubmit = (c.tagName === 'BUTTON' && (t === '' || t === 'submit'))
        || (c.tagName === 'INPUT' && (t === 'submit' || t === 'image'));
      if (!isSubmit) {
        const b = el.parentElement?.closest('button');
        return b ? submits(b) : null;
      }
      return c.form || null;
    }
    function describeForm(form) {
      const label = clean(form.getAttribute('aria-label') || form.querySelector('h1,h2,h3,legend')?.innerText
        || form.id || form.getAttribute('name'), 60);
      const method = (form.getAttribute('method') || 'get').toUpperCase();
      const action = form.action || location.href;
      const fields = [...form.elements].filter(e => e.name && e.type !== 'hidden' && e.type !== 'submit')
        .map(e => e.name).slice(0, 8);
      return 'Formulario' + (label ? ' «' + label + '»' : '') + ' (' + method + ' ' + action + ')'
        + (fields.length ? '; campos: ' + fields.join(', ') : '');
    }
    function refOf(el) {
      for (const [ref, other] of A.refs) if (other === el) return ref;
      const ref = 'e' + A.next++;
      A.refs.set(ref, el);
      return ref;
    }
    function find(args) {
      if (args.ref) {
        const el = A.refs.get(args.ref);
        if (!el || !el.isConnected) {
          throw new Error('La referencia ' + args.ref
            + ' ya no existe en esta página: vuelve a leerla con browser_read.');
        }
        return el;
      }
      const wanted = clean(args.objetivo, 300).toLowerCase();
      const selector = 'a[href],button,input,select,textarea,summary,label,'
        + '[role],[onclick],[contenteditable],[tabindex]';
      const all = [...document.querySelectorAll(selector)]
        .filter(el => visible(el) && (INTERACTIVE.has(role(el)) || el.tagName === 'LABEL'));
      const named = all.map(el => [el, name(el).toLowerCase(), clean(el.getAttribute('placeholder')).toLowerCase()]);
      const exact = named.filter(([, n, p]) => n === wanted || p === wanted);
      const partial = exact.length ? exact : named.filter(([, n, p]) => n.includes(wanted) || p.includes(wanted));
      if (!partial.length) {
        throw new Error('No hay ningún elemento visible con el texto «' + args.objetivo
          + '». Lee la página con browser_read y usa ref.');
      }
      return partial[0][0];
    }
    function flash(el) {
      try {
        const r = el.getBoundingClientRect();
        const box = document.createElement('div');
        box.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483647;border:2px solid #4CAF50;'
          + 'border-radius:4px;box-shadow:0 0 0 4px rgba(76,175,80,.25);transition:opacity .6s;'
          + 'left:' + (r.left - 3) + 'px;top:' + (r.top - 3)
          + 'px;width:' + (r.width + 2) + 'px;height:' + (r.height + 2) + 'px';
        document.documentElement.appendChild(box);
        setTimeout(() => { box.style.opacity = '0'; }, 700);
        setTimeout(() => box.remove(), 1400);
      } catch (e) {}
    }
    function describe(el) { return (role(el) || el.tagName.toLowerCase()) + (name(el) ? ' «' + name(el) + '»' : ''); }
    """#

}

extension BrowserScripts {
    /// browser_read: el árbol con referencias o el texto visible.
    static let read = prelude + #"""
    const max = Math.max(500, Math.min(args.max || 15000, 15000));
    if (args.modo === 'texto') {
      const text = (document.body?.innerText || '').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
      return { texto: text.slice(0, max), recortado: text.length > max };
    }
    // Las anotaciones del usuario y las lecturas comparten referencias hasta la siguiente navegación.
    const CONTEXT = new Set(['form', 'navigation', 'main', 'dialog']);
    const lines = [];
    let size = 0, cut = false, last = '';
    function depth(el) {
      let d = 0;
      for (let p = el.parentElement; p; p = p.parentElement) if (CONTEXT.has(role(p))) d++;
      return d;
    }
    function push(el, text) {
      if (cut || text === last) return;
      const line = '  '.repeat(depth(el)) + '- ' + text;
      if (size + line.length + 1 > max) { cut = true; return; }
      lines.push(line); size += line.length + 1; last = text;
    }
    function extras(el, r) {
      const out = [];
      const tag = el.tagName;
      if (r === 'heading') out.push('nivel ' + (tag.match(/^H([1-6])$/)?.[1] || el.getAttribute('aria-level') || '?'));
      if (r === 'link') {
        const href = el.getAttribute('href') || '';
        if (href && !href.startsWith('javascript:')) out.push('→ ' + clean(href, 80));
      }
      if (r === 'textbox' || r === 'searchbox' || r === 'spinbutton') {
        if (tag === 'INPUT' && el.type === 'password') out.push('contraseña' + (el.value ? ', escrita' : ''));
        else if ('value' in el && el.value) out.push('valor: ' + JSON.stringify(clean(el.value, 80)));
        else if (el.isContentEditable && el.innerText.trim()) {
          out.push('valor: ' + JSON.stringify(clean(el.innerText, 80)));
        }
        const hint = clean(el.getAttribute('placeholder'), 60);
        if (hint && name(el) !== hint) out.push('pista: ' + JSON.stringify(hint));
      }
      if (r === 'checkbox' || r === 'radio' || r === 'switch') {
        out.push(el.checked || el.getAttribute('aria-checked') === 'true' ? 'marcado' : 'sin marcar');
      }
      if (r === 'combobox' && tag === 'SELECT') {
        const options = [...el.options].slice(0, 12).map(o => JSON.stringify(clean(o.text, 30))).join(' ');
        out.push('elegido: ' + JSON.stringify(clean(el.selectedOptions[0]?.text || '', 60)) + ', opciones: ' + options);
      }
      if (el.disabled || el.getAttribute('aria-disabled') === 'true') out.push('desactivado');
      if (el.required) out.push('obligatorio');
      if (submits(el)) out.push('envía el formulario');
      if (r === 'form') {
        out.push((el.getAttribute('method') || 'get').toUpperCase() + ' ' + clean(el.getAttribute('action') || '', 60));
      }
      return out.length ? ' (' + out.join(', ') + ')' : '';
    }
    function walk(el) {
      if (cut || !visible(el)) return;
      const tag = el.tagName;
      if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'SVG', 'svg'].includes(tag)) return;
      if (tag === 'IFRAME') {
        push(el, 'iframe' + (el.title ? ' «' + clean(el.title, 60) + '»' : '') + ' (su contenido no se lee)');
        return;
      }
      const r = role(el);
      if (r && INTERACTIVE.has(r)) {
        push(el, r + ' «' + name(el) + '» [' + refOf(el) + ']' + extras(el, r));
        return;
      }
      if (r === 'heading') { push(el, 'heading «' + clean(el.innerText, 160) + '»' + extras(el, r)); return; }
      if (r === 'img') { push(el, 'img «' + name(el) + '»'); return; }
      if (CONTEXT.has(r)) {
        const label = clean(el.getAttribute('aria-label'), 60);
        push(el, r + (label ? ' «' + label + '»' : '') + extras(el, r));
      }
      let own = '';
      for (const child of el.childNodes) {
        if (child.nodeType === Node.TEXT_NODE) own += child.textContent;
        else if (child.nodeType === Node.ELEMENT_NODE) {
          const t = clean(own, 400);
          if (t) push(el, 'text «' + t + '»');
          own = '';
          walk(child);
        }
      }
      const t = clean(own, 400);
      if (t) push(el, 'text «' + t + '»');
    }
    if (document.body) walk(document.body);
    return { arbol: lines.join('\n'), referencias: A.next - 1, recortado: cut };
    """#

}

extension BrowserScripts {
    /// browser_click: pulsa, salvo que envíe un formulario sin permiso.
    static let click = prelude + #"""
    const el = find(args);
    const allowed = args.permitido || [];
    const form = submits(el);
    if (form && !allowed.includes('formulario')) {
      return { permiso: { tipo: 'formulario', detalle: describeForm(form) } };
    }
    const link = el.closest('a[download]');
    if (link) return { descarga: link.href };
    el.scrollIntoView({ block: 'center', inline: 'center' });
    flash(el);
    const r = el.getBoundingClientRect();
    const opts = { bubbles: true, cancelable: true, composed: true, view: window, button: 0,
                   clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 };
    el.dispatchEvent(new PointerEvent('pointerdown', opts));
    el.dispatchEvent(new MouseEvent('mousedown', opts));
    if (typeof el.focus === 'function') el.focus({ preventScroll: true });
    el.dispatchEvent(new PointerEvent('pointerup', opts));
    el.dispatchEvent(new MouseEvent('mouseup', opts));
    el.click();
    return { elemento: describe(el) };
    """#

}

extension BrowserScripts {
    /// browser_type: escribe en un campo; contraseña y Enviar piden permiso antes.
    static let type = prelude + #"""
    const el = control(find(args));
    const allowed = args.permitido || [];
    const tag = el.tagName;
    const editable = el.isContentEditable || tag === 'TEXTAREA' || tag === 'SELECT'
      || (tag === 'INPUT' && !['button', 'submit', 'reset', 'image', 'checkbox', 'radio', 'file', 'hidden',
                                 'range', 'color'].includes(el.type));
    if (!editable) throw new Error('En ' + describe(el) + ' no se puede escribir.');
    if (el.disabled || el.readOnly) throw new Error(describe(el) + ' está desactivado o es de solo lectura.');
    if (tag === 'INPUT' && el.type === 'password' && !allowed.includes('contrasena'))
      return { permiso: { tipo: 'contrasena', detalle: 'Campo ' + describe(el) } };
    const form = el.form || el.closest('form');
    if (args.enviar && form && !allowed.includes('formulario')) {
      return { permiso: { tipo: 'formulario', detalle: describeForm(form) } };
    }
    el.scrollIntoView({ block: 'center' });
    flash(el);
    el.focus();
    const text = String(args.texto ?? '');
    if (tag === 'SELECT') {
      const wanted = text.trim().toLowerCase();
      const option = [...el.options]
        .find(o => o.text.trim().toLowerCase() === wanted || o.value.toLowerCase() === wanted)
        || [...el.options].find(o => o.text.toLowerCase().includes(wanted));
      if (!option) throw new Error('No hay ninguna opción «' + text + '» en ' + describe(el) + '.');
      el.value = option.value;
    } else if (el.isContentEditable) {
      if (args.borrar !== false) document.execCommand('selectAll');
      document.execCommand('insertText', false, text);
    } else {
      const proto = tag === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
      setter.call(el, args.borrar !== false ? text : el.value + text);
      el.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true, inputType: 'insertText', data: text }));
    }
    el.dispatchEvent(new Event('change', { bubbles: true }));
    if (args.enviar) {
      for (const kind of ['keydown', 'keypress', 'keyup'])
        el.dispatchEvent(new KeyboardEvent(kind, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13,
                                                   bubbles: true, cancelable: true }));
      if (form) form.requestSubmit();
    }
    const hidden = tag === 'INPUT' && el.type === 'password';
    return { elemento: describe(el), valor: hidden ? '(oculto)' : clean(el.value ?? el.innerText, 200) };
    """#

}

extension BrowserScripts {
    /// browser_scroll: la página, el contenedor que se desplaza o hasta un elemento.
    static let scroll = prelude + #"""
    if (args.ref) {
      const el = find(args);
      el.scrollIntoView({ block: 'center', inline: 'center' });
      flash(el);
      return { elemento: describe(el), x: Math.round(scrollX), y: Math.round(scrollY) };
    }
    const vertical = args.direccion === 'abajo' || args.direccion === 'arriba';
    const sign = args.direccion === 'arriba' || args.direccion === 'izquierda' ? -1 : 1;
    const root = document.scrollingElement || document.documentElement;
    let target = root;
    if (vertical ? root.scrollHeight <= innerHeight + 1 : root.scrollWidth <= innerWidth + 1) {
      let best = 0;
      for (const el of document.querySelectorAll('*')) {
        const extra = vertical ? el.scrollHeight - el.clientHeight : el.scrollWidth - el.clientWidth;
        const style = getComputedStyle(el)[vertical ? 'overflowY' : 'overflowX'];
        if (extra > best && (style === 'auto' || style === 'scroll')) { best = extra; target = el; }
      }
    }
    const screen = vertical ? (target === root ? innerHeight : target.clientHeight) : innerWidth;
    const step = args.cantidad || Math.round(screen * 0.85);
    target.scrollBy({ left: vertical ? 0 : sign * step, top: vertical ? sign * step : 0, behavior: 'instant' });
    const y = target === root ? scrollY : target.scrollTop;
    const height = target === root ? root.scrollHeight : target.scrollHeight;
    const view = target === root ? innerHeight : target.clientHeight;
    const x = target === root ? scrollX : target.scrollLeft;
    return { x: Math.round(x), y: Math.round(y), alto: height, visible: view,
             al_final: y + view >= height - 2, al_principio: y <= 0 };
    """#

}

extension BrowserScripts {
    /// browser_eval: el código del agente dentro de una función asíncrona, con su valor en JSON.
    /// En el mundo aislado se cuentan los cambios del DOM mientras corre (los de la propia página también).
    static func evaluate(_ code: String) -> String {
        """
        const __observer = new MutationObserver(() => {});
        __observer.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
        let __value, __changes = 0;
        try {
          __value = await (async () => {
        \(code)
          })();
        } finally {
          __changes = __observer.takeRecords().length;
          __observer.disconnect();
        }
        let __text;
        try { __text = JSON.stringify(__value); } catch (e) { __text = JSON.stringify(String(__value)); }
        return { valor: __text === undefined ? 'undefined' : __text, cambios_dom: __changes };
        """
    }
}

extension BrowserScripts {
    /// El mundo de sólo lectura de browser_eval sin permiso (`WKContentWorld` «alfalfa-lectura»).
    ///
    /// Se instala en cada marco al empezar el documento (WKUserScript) y otra vez antes de cada script
    /// (no hace nada si ya está). Como cada mundo tiene sus propios prototipos, nada de esto toca la
    /// página ni los guiones del agente («alfalfa»). En este mundo:
    /// - ningún setter del DOM funciona (innerHTML, value, type, src, action, cookie, estilos…), ni los
    ///   métodos que cambian el árbol, envían formularios, pulsan, disparan eventos o guardan;
    /// - no hay red (fetch, XMLHttpRequest, WebSocket, EventSource, sendBeacon, window.open);
    /// - el valor de un campo de contraseña se lee vacío, también con FormData y getAttribute.
    /// La regex del motor (`navegador.parece_que_cambia`) sólo es una ayuda; esto es la barrera. Lo
    /// que queda fuera: `location` (no se puede redefinir; navegar sigue pasando por la política del
    /// navegador), el atributo `value` escrito en el HTML de una contraseña (outerHTML) y un marco que
    /// la página cree mientras el script espera (se instalan en los marcos que hay al empezar).
    static let readOnlyWorld = #"""
    (() => {
      const install = g => {
        if (Object.prototype.hasOwnProperty.call(g, '__alfalfaLectura')) return;
        Object.defineProperty(g, '__alfalfaLectura', { value: true });
        const blocked = what => function () {
          throw new Error('browser_eval sin modifies sólo lee: «' + what + '» cambiaría la página o usaría la '
            + 'red. Si hace falta, repite con modifies: true (pide permiso).');
        };
        const lock = (target, key, descriptor) => {
          try { Object.defineProperty(target, key, Object.assign({ configurable: false }, descriptor)); } catch (e) {}
        };
        const getter = (proto, key) => Object.getOwnPropertyDescriptor(proto, key)?.get;
        const Input = g.HTMLInputElement;
        const typeOf = getter(Input.prototype, 'type');
        const valueOf = getter(Input.prototype, 'value');
        const defaultOf = getter(Input.prototype, 'defaultValue');
        const nameOf = getter(Input.prototype, 'name');
        const elementsOf = getter(g.HTMLFormElement.prototype, 'elements');
        const getAttribute = g.Element.prototype.getAttribute;
        // typeOf es el getter nativo: vale con elementos de cualquier marco.
        const isPassword = el => {
          try { return String(typeOf.call(el)).toLowerCase() === 'password'; } catch (e) { return false; }
        };
        // 1. Ningún setter del DOM: ni valores, ni atributos reflejados, ni estilos, ni cookies.
        const names = Object.getOwnPropertyNames(g).filter(n => /^(?:Node|Element|Attr|CharacterData|Text|Document|DocumentFragment|ShadowRoot|CSSStyleDeclaration|DOMTokenList|NamedNodeMap|Storage|History|Selection|(?:HTML|SVG)\w*Element)$/.test(n));
        for (const n of names) {
          const proto = g[n]?.prototype;
          if (!proto) continue;
          for (const key of Object.getOwnPropertyNames(proto)) {
            const d = Object.getOwnPropertyDescriptor(proto, key);
            if (!d || !d.set) continue;
            let get = d.get;
            if (proto === Input.prototype && key === 'value') {
              get = function () { return isPassword(this) ? '' : valueOf.call(this); };
            } else if (proto === Input.prototype && key === 'defaultValue') {
              get = function () { return isPassword(this) ? '' : defaultOf.call(this); };
            }
            lock(proto, key, { get, set: blocked(key), enumerable: d.enumerable });
          }
        }
        lock(g.Element.prototype, 'getAttribute', { value: function (name) {
          if (isPassword(this) && String(name).toLowerCase() === 'value') return null;
          return getAttribute.call(this, name);
        } });
        // 2. Ningún método que cambie la página, la envíe, pulse o guarde.
        const methods = {
          Node: ['appendChild', 'insertBefore', 'removeChild', 'replaceChild', 'normalize'],
          Element: ['setAttribute', 'setAttributeNS', 'setAttributeNode', 'setAttributeNodeNS', 'removeAttribute',
            'removeAttributeNS', 'removeAttributeNode', 'toggleAttribute', 'append', 'prepend', 'after', 'before',
            'replaceWith', 'replaceChildren', 'remove', 'insertAdjacentHTML', 'insertAdjacentElement',
            'insertAdjacentText', 'attachShadow', 'setHTMLUnsafe', 'requestFullscreen', 'requestPointerLock'],
          CharacterData: ['appendData', 'deleteData', 'insertData', 'replaceData', 'after', 'before', 'remove',
            'replaceWith'],
          Text: ['splitText'],
          Document: ['write', 'writeln', 'open', 'close', 'execCommand', 'append', 'prepend', 'replaceChildren'],
          DocumentFragment: ['append', 'prepend', 'replaceChildren'],
          EventTarget: ['dispatchEvent'],
          HTMLElement: ['click', 'showPopover', 'hidePopover', 'togglePopover'],
          HTMLFormElement: ['submit', 'requestSubmit', 'reset'],
          HTMLInputElement: ['setRangeText', 'stepUp', 'stepDown', 'showPicker'],
          HTMLTextAreaElement: ['setRangeText'],
          HTMLSelectElement: ['add', 'remove', 'showPicker'],
          HTMLDialogElement: ['show', 'showModal', 'close', 'requestClose'],
          HTMLMediaElement: ['play', 'pause', 'load', 'fastSeek'],
          DOMTokenList: ['add', 'remove', 'toggle', 'replace'],
          Storage: ['setItem', 'removeItem', 'clear'],
          History: ['pushState', 'replaceState', 'back', 'forward', 'go'],
          Range: ['deleteContents', 'extractContents', 'insertNode', 'surroundContents'],
          CSSStyleDeclaration: ['setProperty', 'removeProperty'],
          Navigator: ['sendBeacon', 'share', 'vibrate'],
          Clipboard: ['write', 'writeText'],
          IDBFactory: ['open', 'deleteDatabase'],
        };
        for (const [n, keys] of Object.entries(methods)) {
          const proto = g[n]?.prototype;
          if (!proto) continue;
          for (const key of keys) if (key in proto) lock(proto, key, { value: blocked(key) });
        }
        // 3. Sin red ni ventanas.
        for (const key of ['fetch', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'Worker', 'SharedWorker', 'open',
                           'alert', 'confirm', 'prompt', 'print', 'postMessage']) {
          if (key in g) lock(g, key, { value: blocked(key) });
        }
        // 4. Los marcos: nada de contentWindow ni contentDocument (window[n] se cubre abajo).
        for (const n of ['HTMLIFrameElement', 'HTMLFrameElement', 'HTMLObjectElement', 'HTMLEmbedElement']) {
          const proto = g[n]?.prototype;
          if (!proto) continue;
          for (const key of ['contentWindow', 'contentDocument']) {
            if (key in proto) lock(proto, key, { get: () => null });
          }
          if ('getSVGDocument' in proto) lock(proto, 'getSVGDocument', { value: () => null });
        }
        // 5. FormData de un formulario sin sus contraseñas, y sin el constructor original a mano.
        const NativeFormData = g.FormData;
        function SafeFormData(form) {
          const data = new NativeFormData(...arguments);
          if (form && typeof form === 'object') {
            try { for (const el of elementsOf.call(form)) if (isPassword(el)) data.delete(nameOf.call(el)); }
            catch (e) {}
          }
          return data;
        }
        SafeFormData.prototype = NativeFormData.prototype;
        lock(NativeFormData.prototype, 'constructor', { value: SafeFormData });
        lock(g, 'FormData', { value: SafeFormData });
      };
      // Este marco y todos los suyos del mismo origen: un marco about:blank creado por la página no
      // recibe el script de usuario, y window[n] daría sus prototipos sin barreras.
      const visit = (g, depth) => {
        try { install(g); } catch (e) { return; }
        if (depth > 8) return;
        for (let i = 0; i < g.length; i++) {
          try { visit(g[i], depth + 1); } catch (e) {}
        }
      };
      visit(globalThis, 0);
    })();
    """#
}
