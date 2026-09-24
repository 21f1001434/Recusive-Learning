/* Minimal Dell DDS / Angular behaviour for HIP form replicas.
 *
 * Mirrors the live markup recorded from the HIP portal:
 *  - <dds-dropdown formcontrolname=...> hosting input.dds__dropdown__input-field
 *    role=combobox whose aria-controls names the owned popup listbox;
 *  - options are button[role=option] that commit on click; multi-selects keep
 *    the popup open and render .dds__tag chips;
 *  - switches are input[type=checkbox][role=switch];
 *  - radio groups share a name and sit under a group label;
 *  - file inputs are hidden behind a "Browse Files" button.
 * Values change only through real user events (click / type), never by script.
 */
(function () {
  let seq = 100;
  const nextId = (p) => `${p}-${++seq}${Math.floor(Math.random() * 1000)}`;
  const el = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  const esc = (v) => String(v).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');

  function closeAll(except) {
    document.querySelectorAll('dds-dropdown').forEach((dd) => { if (dd !== except && dd.__close) dd.__close(); });
  }
  document.addEventListener('mousedown', (e) => {
    const dd = e.target.closest && e.target.closest('dds-dropdown');
    closeAll(dd || null);
  });

  function dropdown({ label, options, multiple = false, onChange, showLabel = true, name = '', value = '', disabled = false }) {
    const dd = document.createElement('dds-dropdown');
    dd.setAttribute('selection', multiple ? 'multiple' : 'single');
    if (name) dd.setAttribute('formcontrolname', name);
    const inputId = nextId('dds-form-field');
    const labelId = nextId('dds-label');
    const listId = nextId('dropdown-popup-list');
    const opts = typeof options === 'function' ? options : () => options;
    dd.innerHTML = `
      <div class="dds__dropdown${multiple ? ' dds__dropdown--is-multiple' : ''}" data-dds="dropdown">
        <div class="dds__dropdown__input-container">
          ${showLabel ? `<label id="${labelId}" for="${inputId}" class="dds__label dds__label--required">${esc(label)}</label>` : ''}
          <div class="dds__dropdown__input-wrapper">
            <input type="text" required role="combobox" autocomplete="off" aria-invalid="false" aria-expanded="false"
              placeholder="${esc(label)}" id="${inputId}" class="dds__dropdown__input-field"
              ${showLabel ? `aria-labelledby="${labelId}"` : ''} aria-controls="${listId}"${disabled ? ' disabled' : ''}>
          </div>
          <div class="dds__tags"></div>
        </div>
        <div class="dds__dropdown__popup dds__dropdown__popup--hidden" role="presentation" tabindex="-1">
          <ul class="dds__dropdown__list" role="listbox" tabindex="-1" id="${listId}"${multiple ? ' aria-multiselectable="true"' : ''}></ul>
        </div>
      </div>`;
    const input = dd.querySelector('input');
    const popup = dd.querySelector('.dds__dropdown__popup');
    const list = dd.querySelector('[role=listbox]');
    const tags = dd.querySelector('.dds__tags');
    let committed = value || '';
    let selected = [];
    input.value = committed;
    const render = () => {
      const current = opts();
      list.innerHTML = current.map((o, i) => `<li class="dds__dropdown__item" role="none"><button type="button" class="dds__dropdown__item-option" role="option" tabindex="-1" aria-selected="${(multiple ? selected.includes(o) : committed === o) ? 'true' : 'false'}" aria-posinset="${i + 1}" aria-setsize="${current.length}" data-value="${esc(o)}">${esc(o)}</button></li>`).join('');
      list.querySelectorAll('[role=option]').forEach((btn) => {
        btn.addEventListener('mousedown', (e) => e.preventDefault());
        btn.addEventListener('click', () => {
          const text = btn.textContent.trim();
          if (multiple) {
            if (text === 'Select all') return;
            selected = selected.includes(text) ? selected.filter((x) => x !== text) : [...selected, text];
            btn.setAttribute('aria-selected', selected.includes(text) ? 'true' : 'false');
            tags.innerHTML = selected.map((x) => `<span class="dds__tag">${esc(x)}</span>`).join('');
            input.focus();
          } else {
            committed = text;
            dd.__close();
            input.value = text;
          }
          input.dispatchEvent(new Event('change', { bubbles: true }));
          onChange && onChange(multiple ? selected.slice() : committed, dd);
        });
      });
    };
    const open = () => { if (input.disabled) return; closeAll(dd); render(); popup.classList.remove('dds__dropdown__popup--hidden'); input.setAttribute('aria-expanded', 'true'); };
    dd.__close = () => {
      popup.classList.add('dds__dropdown__popup--hidden');
      input.setAttribute('aria-expanded', 'false');
      list.querySelectorAll('.dds__dropdown__item').forEach((li) => { li.style.display = ''; });
      input.value = multiple ? '' : committed;
    };
    input.addEventListener('click', open);
    input.addEventListener('input', () => {
      if (popup.classList.contains('dds__dropdown__popup--hidden')) open();
      const q = input.value.trim().toLowerCase();
      list.querySelectorAll('.dds__dropdown__item').forEach((li) => { li.style.display = !q || li.textContent.toLowerCase().includes(q) ? '' : 'none'; });
    });
    input.addEventListener('blur', () => setTimeout(() => { if (!dd.contains(document.activeElement)) dd.__close(); }, 160));
    render();
    return group(dd, 'dds__dropdown-host');
  }

  function group(node, extra = '') {
    const g = document.createElement('div');
    g.className = `dds__form-group ${extra}`.trim();
    g.appendChild(node);
    return g;
  }

  function text({ label, name, placeholder = '', value = '', disabled = false, textarea = false, duplicateMessage = '', showLabel = true }) {
    const id = nextId('dds-form-field');
    const tag = textarea ? 'textarea' : 'input';
    const g = el(`<div class="dds__form-group">
        ${showLabel ? `<label class="dds__label" for="${id}">${esc(label)}</label>` : ''}
        <${tag} ${textarea ? '' : 'type="text"'} id="${id}" name="${esc(name)}" formcontrolname="${esc(name)}" placeholder="${esc(placeholder || label)}"${disabled ? ' disabled' : ''}>${textarea ? `</${tag}>` : ''}
        ${duplicateMessage ? `<small class="dds__invalid-feedback" id="${id}-err" style="display:none"></small>` : ''}
      </div>`);
    const input = g.querySelector(tag);
    input.value = value;
    if (duplicateMessage) {
      let t = null;
      input.setAttribute('aria-describedby', `${id}-err`);
      input.addEventListener('input', () => {
        clearTimeout(t);
        t = setTimeout(() => {
          const on = input.value.trim().length > 0;
          const err = g.querySelector('.dds__invalid-feedback');
          input.setAttribute('aria-invalid', on ? 'true' : 'false');
          err.textContent = on ? duplicateMessage : '';
          err.style.display = on ? 'block' : 'none';
        }, 200);
      });
    }
    return g;
  }

  function switchControl({ label, name, checked = false, onText = 'Enabled', offText = 'Disabled', onChange }) {
    const id = nextId('dds-switch');
    const g = el(`<div class="dds__form-group dds__switch">
        <label class="dds__label" for="${id}">${esc(label)}</label>
        <input type="checkbox" role="switch" id="${id}" name="${esc(name)}" formcontrolname="${esc(name)}"${checked ? ' checked' : ''} aria-checked="${checked}">
        <span class="dds__switch__state">${esc(checked ? onText : offText)}</span>
      </div>`);
    const input = g.querySelector('input');
    input.addEventListener('change', () => {
      input.setAttribute('aria-checked', String(input.checked));
      g.querySelector('.dds__switch__state').textContent = input.checked ? onText : offText;
      onChange && onChange(input.checked);
    });
    return g;
  }

  function checkbox({ label, name, checked = false, onChange }) {
    const id = nextId('dds-checkbox');
    const g = el(`<div class="dds__form-group dds__checkbox">
        <input type="checkbox" class="dds__checkbox__input" id="${id}" name="${esc(name)}" formcontrolname="${esc(name)}"${checked ? ' checked' : ''}>
        <label class="dds__checkbox__label" for="${id}">${esc(label)}</label>
      </div>`);
    g.querySelector('input').addEventListener('change', (e) => onChange && onChange(e.target.checked));
    return g;
  }

  function radios({ label, name, options, value = '', onChange }) {
    const labelId = nextId('dds-label');
    const g = el(`<div class="dds__form-group">
        <label class="dds__label" id="${labelId}">${esc(label)}</label>
        <div class="dds__radio-button-group" role="radiogroup" aria-labelledby="${labelId}"></div>
      </div>`);
    const holder = g.querySelector('[role=radiogroup]');
    options.forEach(([text, val]) => {
      const id = nextId('dds-radio');
      const r = el(`<div class="dds__radio-button">
          <input type="radio" class="dds__radio-button__input" id="${id}" name="${esc(name)}" value="${esc(val)}"${value === text ? ' checked' : ''}>
          <label class="dds__radio-button__label" for="${id}">${esc(text)}</label>
        </div>`);
      r.querySelector('input').addEventListener('change', () => onChange && onChange(text));
      holder.appendChild(r);
    });
    return g;
  }

  function file({ label, name, accept = '' }) {
    const id = nextId('dds-file');
    const g = el(`<div class="dds__form-group dds__file-input">
        <label class="dds__label" for="${id}">${esc(label)}</label>
        <button type="button" class="dds__button dds__button--secondary" onclick="document.getElementById('${id}').click()">Browse Files</button>
        <input type="file" id="${id}" name="${esc(name)}" formcontrolname="${esc(name)}" accept="${esc(accept)}" style="position:absolute;width:1px;height:1px;opacity:0.01">
        <span class="dds__file-input__name"></span>
      </div>`);
    const input = g.querySelector('input');
    input.addEventListener('change', () => { g.querySelector('.dds__file-input__name').textContent = input.files[0] ? input.files[0].name : ''; });
    return g;
  }

  function fieldset(legend, ...children) {
    const fs = document.createElement('fieldset');
    fs.innerHTML = `<legend>${esc(legend)}</legend>`;
    children.flat().forEach((c) => c && fs.appendChild(c));
    return fs;
  }

  function row(...children) {
    const r = document.createElement('div');
    r.className = 'row';
    children.flat().forEach((c) => c && r.appendChild(c));
    return r;
  }

  const STYLE = `
    html { scroll-behavior: smooth; }
    body { margin: 0; font-family: Arial, sans-serif; font-size: 14px; }
    header.app-header { position: fixed; top: 0; left: 0; right: 0; height: 64px; background: #0e2e5c; color: #fff; z-index: 1000; display: flex; align-items: center; padding: 0 24px; }
    main { padding: 84px 40px 40px 40px; }
    fieldset { border: 1px solid #ccc; margin: 0 0 18px 0; padding: 10px 16px; }
    legend { font-weight: bold; }
    .row { display: flex; gap: 18px; align-items: flex-start; margin: 6px 0; min-height: 64px; flex-wrap: wrap; }
    .dds__form-group { display: flex; flex-direction: column; position: relative; }
    .dds__label { font-size: 12px; margin-bottom: 2px; }
    input[type=text], textarea { width: 280px; height: 34px; box-sizing: border-box; }
    textarea { height: 54px; }
    dds-dropdown { display: block; position: relative; width: 280px; }
    .dds__dropdown__popup { position: absolute; left: 0; right: 0; top: 100%; background: #fff; border: 1px solid #777; z-index: 50; max-height: 300px; overflow: auto; animation: dds-pop 140ms ease-out; }
    @keyframes dds-pop { from { opacity: .2; transform: translateY(-6px); } to { opacity: 1; transform: none; } }
    .dds__dropdown__popup--hidden { display: none; }
    .dds__dropdown__list { list-style: none; margin: 0; padding: 0; }
    .dds__dropdown__item-option { display: block; width: 100%; text-align: left; padding: 7px 10px; background: #fff; border: 0; cursor: pointer; }
    .dds__dropdown__item-option[aria-selected=true] { background: #dbe9f9; }
    .dds__tag { display: inline-block; background: #e1e1e1; border-radius: 10px; padding: 1px 8px; margin: 2px; font-size: 11px; }
    .dds__radio-button-group { display: flex; gap: 12px; }
    .actions { position: sticky; bottom: 0; background: #f4f4f4; border-top: 1px solid #ccc; padding: 12px 16px; z-index: 900; }
    [role=tablist] { display: flex; gap: 24px; border-bottom: 1px solid #ccc; margin-bottom: 12px; }
    [role=tab] { background: none; border: 0; padding: 8px 4px; cursor: pointer; }
    [role=tab][aria-selected=true] { border-bottom: 3px solid #0672cb; }
    [role=tabpanel][hidden] { display: none; }
  `;

  function page(title, form, { header = 'Hybrid Integration Platform' } = {}) {
    const style = document.createElement('style');
    style.textContent = STYLE;
    document.head.appendChild(style);
    document.body.innerHTML = `<header class="app-header">${esc(header)}</header><main><h2>${esc(title)}</h2></main>`;
    document.querySelector('main').appendChild(form);
    return form;
  }

  function form(...children) {
    const f = document.createElement('form');
    f.setAttribute('autocomplete', 'off');
    children.flat().forEach((c) => c && f.appendChild(c));
    const actions = el('<div class="actions"><button type="button" id="cancel">Cancel</button> <button type="button" id="submit">Submit</button></div>');
    f.appendChild(actions);
    return f;
  }

  // Conditional rendering helper: renders children into host when predicate holds.
  function when(host, build) {
    host.innerHTML = '';
    const out = build();
    (Array.isArray(out) ? out : [out]).forEach((c) => c && host.appendChild(c));
  }

  window.HIP = { dropdown, text, switchControl, checkbox, radios, file, fieldset, row, page, form, when, el, nextId, closeAll };
})();
