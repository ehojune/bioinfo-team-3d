export const RESIZE_STEP = 24;

export function clampPanelSize(value, min, max) {
  return Math.round(Math.min(max, Math.max(min, Number(value) || min)));
}

export function readPanelSizes(raw) {
  try {
    const value = JSON.parse(raw || '{}'), out = {};
    for (const key of ['main', 'staff']) {
      if (Number.isFinite(value?.[key]) && value[key] > 0) out[key] = Math.round(value[key]);
    }
    return out;
  } catch { return {}; }
}

export function keyDelta(axis, key) {
  if (axis === 'vertical') return key === 'ArrowLeft' ? -RESIZE_STEP : key === 'ArrowRight' ? RESIZE_STEP : 0;
  return key === 'ArrowUp' ? RESIZE_STEP : key === 'ArrowDown' ? -RESIZE_STEP : 0;
}

export function initResizablePanels(options) {
  const {layout, office, staff, mainSplitter, staffSplitter, storage, storageKey, win = window} = options;
  const desktop = win.matchMedia('(min-width: 1000px)');
  const values = readPanelSizes(storage.get(storageKey));

  const bounds = kind => {
    if (kind === 'main') {
      const width = layout.getBoundingClientRect().width - mainSplitter.getBoundingClientRect().width;
      return [320, Math.max(320, width - 330)];
    }
    return [92, Math.max(92, Math.min(300, Math.round(win.innerHeight * .42)))];
  };
  const current = kind => kind === 'main' ? office.getBoundingClientRect().width : staff.getBoundingClientRect().height;
  const splitter = kind => kind === 'main' ? mainSplitter : staffSplitter;
  const describe = (kind, value) => {
    const [min, max] = bounds(kind), size = clampPanelSize(value, min, max), handle = splitter(kind);
    handle.setAttribute('aria-valuemin', String(min));
    handle.setAttribute('aria-valuemax', String(max));
    handle.setAttribute('aria-valuenow', String(size));
    handle.setAttribute('aria-valuetext', `${size}px`);
    return size;
  };
  const apply = (kind, value, remember = true) => {
    const size = describe(kind, value);
    if (kind === 'main') layout.style.setProperty('--office-panel-size', `${size}px`);
    else staff.style.setProperty('--staff-panel-size', `${size}px`);
    if (remember) {
      values[kind] = size;
      storage.set(storageKey, JSON.stringify(values));
    }
    return size;
  };
  const reset = kind => {
    if (kind === 'main') layout.style.removeProperty('--office-panel-size');
    else staff.style.removeProperty('--staff-panel-size');
    delete values[kind];
    storage.set(storageKey, JSON.stringify(values));
    win.requestAnimationFrame(() => describe(kind, current(kind)));
  };
  const bind = (kind, axis) => {
    const handle = splitter(kind);
    let start = null;
    handle.addEventListener('pointerdown', event => {
      if (!desktop.matches || event.button !== 0) return;
      event.preventDefault();
      start = {coordinate: axis === 'vertical' ? event.clientX : event.clientY, size: current(kind)};
      handle.setPointerCapture?.(event.pointerId);
      handle.classList.add('is-resizing');
    });
    handle.addEventListener('pointermove', event => {
      if (!start) return;
      const coordinate = axis === 'vertical' ? event.clientX : event.clientY;
      const delta = axis === 'vertical' ? coordinate - start.coordinate : start.coordinate - coordinate;
      apply(kind, start.size + delta, false);
    });
    const finish = event => {
      if (!start) return;
      start = null;
      if (handle.hasPointerCapture?.(event.pointerId)) handle.releasePointerCapture(event.pointerId);
      handle.classList.remove('is-resizing');
      apply(kind, current(kind));
    };
    handle.addEventListener('pointerup', finish);
    handle.addEventListener('pointercancel', finish);
    handle.addEventListener('keydown', event => {
      if (!desktop.matches) return;
      const delta = keyDelta(axis, event.key);
      if (!delta) return;
      event.preventDefault();
      apply(kind, current(kind) + delta);
    });
    handle.addEventListener('dblclick', () => reset(kind));
  };
  bind('main', 'vertical');
  bind('staff', 'horizontal');

  const sync = () => {
    if (!desktop.matches) {
      layout.style.removeProperty('--office-panel-size');
      staff.style.removeProperty('--staff-panel-size');
      return;
    }
    for (const kind of ['main', 'staff']) {
      if (values[kind] === undefined) win.requestAnimationFrame(() => describe(kind, current(kind)));
      else apply(kind, values[kind], false);
    }
  };
  desktop.addEventListener?.('change', sync);
  win.addEventListener('resize', sync);
  sync();
  return {reset, values};
}
