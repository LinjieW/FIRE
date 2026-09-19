/* Single-choice menus: keep the real select, its labels and change handlers.
   Browsing is tentative; only Enter/Space or an option click commits. */
(() => {
  'use strict';
  let current = null, serial = 0;
  const eligible = node => node instanceof HTMLSelectElement && !node.disabled && !node.multiple && node.size <= 1;
  const enabled = option => !!option && !option.disabled && !(option.parentElement.tagName === 'OPTGROUP' && option.parentElement.disabled) && !option.hidden;
  function close() {
    if (!current) return;
    const {select, menu} = current;
    select.setAttribute('aria-expanded', 'false');
    select.removeAttribute('aria-controls');
    select.removeAttribute('aria-activedescendant');
    menu.remove(); current = null;
  }
  function highlight(index) {
    const c = current;
    if (!c || !c.options[index] || !enabled(c.options[index])) return;
    c.index = index;
    c.rows.forEach((row, i) => row.classList.toggle('active', i === index));
    c.select.setAttribute('aria-activedescendant', c.rows[index].id);
    c.rows[index].scrollIntoView({block:'nearest'});
  }
  function move(direction) {
    const c = current;
    for (let i = c.index + direction; i >= 0 && i < c.options.length; i += direction) {
      if (enabled(c.options[i])) { highlight(i); break; }
    }
  }
  function commit(index) {
    const c = current;
    if (!c || !enabled(c.options[index]) || !eligible(c.select)) return;
    const changed = c.select.selectedIndex !== index;
    c.select.selectedIndex = index;
    close();
    c.select.focus({preventScroll:true});
    if (changed) {
      c.select.dispatchEvent(new Event('input', {bubbles:true}));
      c.select.dispatchEvent(new Event('change', {bubbles:true}));
    }
  }
  function open(select) {
    close();
    const options = Array.from(select.options);
    if (!options.length) return;
    const menu = document.createElement('div');
    menu.className = 'fire-select-menu'; menu.id = `fire-select-${++serial}`;
    menu.setAttribute('role', 'listbox');
    menu.setAttribute('aria-label', select.getAttribute('aria-label') || Array.from(select.labels || [], l => l.textContent).join(' ') || select.id);
    menu.setAttribute('popover', 'manual');
    let group = null;
    const rows = options.map((option, index) => {
      if (option.parentElement.tagName === 'OPTGROUP' && option.parentElement !== group) {
        group = option.parentElement;
        const heading = document.createElement('div'); heading.className = 'fire-select-group';
        heading.textContent = group.label; menu.appendChild(heading);
      }
      const row = document.createElement('div'); row.className = 'fire-select-option';
      row.id = `${menu.id}-${index}`; row.textContent = option.label;
      row.setAttribute('role', 'option'); row.setAttribute('aria-selected', String(index === select.selectedIndex));
      row.setAttribute('aria-disabled', String(!enabled(option))); row.hidden = option.hidden;
      row.addEventListener('pointerdown', e => e.preventDefault());
      row.addEventListener('click', () => commit(index));
      row.addEventListener('pointermove', () => highlight(index));
      menu.appendChild(row); return row;
    });
    document.body.appendChild(menu);
    if (menu.showPopover) menu.showPopover();
    const rect = select.getBoundingClientRect();
    const below = innerHeight - rect.bottom - 12, above = rect.top - 12;
    const upward = below < 200 && above > below;
    menu.style.minWidth = Math.min(rect.width, innerWidth - 16) + 'px';
    menu.style.maxHeight = Math.max(40, Math.min(320, upward ? above : below)) + 'px';
    const width = menu.getBoundingClientRect().width;
    menu.style.left = Math.max(8, Math.min(rect.left, innerWidth - width - 8)) + 'px';
    menu.style.top = (upward ? Math.max(8, rect.top - menu.offsetHeight - 5) : rect.bottom + 5) + 'px';
    current = {select, menu, options, rows, index:select.selectedIndex, prefix:'', typedAt:0};
    select.setAttribute('aria-expanded', 'true'); select.setAttribute('aria-controls', menu.id);
    select.focus({preventScroll:true});
    highlight(enabled(options[select.selectedIndex]) ? select.selectedIndex : options.findIndex(enabled));
  }
  document.addEventListener('pointerdown', e => {
    if (eligible(e.target)) {
      e.preventDefault();
      if (current && current.select === e.target) close(); else open(e.target);
    } else if (current && !current.menu.contains(e.target)) close();
  });
  document.addEventListener('keydown', e => {
    const select = e.target;
    if (!eligible(select)) return;
    if (e.key === 'Tab') { close(); return; }
    if (e.key === 'Escape') {
      if (current) { e.preventDefault(); e.stopPropagation(); close(); }
      return;
    }
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    if (['ArrowDown','ArrowUp','Home','End','Enter',' '].includes(e.key)) {
      e.preventDefault();
      if (!current || current.select !== select) { open(select); return; }
      if (e.key === 'Enter' || e.key === ' ') { commit(current.index); return; }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') move(e.key === 'ArrowDown' ? 1 : -1);
      else {
        const indices = current.options.map((o,i) => enabled(o) ? i : -1).filter(i => i >= 0);
        highlight(e.key === 'Home' ? indices[0] : indices[indices.length - 1]);
      }
    } else if (e.key.length === 1) {
      e.preventDefault();
      if (!current || current.select !== select) open(select);
      if (!current) return;
      const now = Date.now();
      current.prefix = now - current.typedAt > 700 ? e.key : current.prefix + e.key;
      current.typedAt = now;
      const prefix = current.prefix.toLocaleLowerCase();
      const index = current.options.findIndex(o => enabled(o) && o.label.toLocaleLowerCase().startsWith(prefix));
      if (index >= 0) highlight(index);
    }
  }, true);
  document.addEventListener('focusin', e => { if (current && e.target !== current.select) close(); });
  window.addEventListener('resize', close);
  document.addEventListener('scroll', e => { if (current && !current.menu.contains(e.target)) close(); }, true);
  new MutationObserver(() => {
    if (current && (!current.select.isConnected || !eligible(current.select) || !current.select.getClientRects().length)) close();
  }).observe(document.body, {childList:true, subtree:true, attributes:true, attributeFilter:['disabled','class','hidden']});
})();
