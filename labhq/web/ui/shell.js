export function activateTab(nav, panels, id) {
  for (const panel of panels) panel.hidden = panel.dataset.tab !== id;
  for (const button of Array.from(nav.children || [])) {
    const active = button.dataset.tabTarget === id;
    button.classList?.toggle('active', active);
    button.setAttribute?.('aria-selected', String(active));
  }
}

export function initTabShell(nav, root, initial = 'decisions') {
  const panels = Array.from(root.querySelectorAll('[data-tab]'));
  nav.addEventListener('click', event => {
    const button = event.target.closest('button[data-tab-target]');
    if (button) activateTab(nav, panels, button.dataset.tabTarget);
  });
  activateTab(nav, panels, initial);
  return id => activateTab(nav, panels, id);
}
