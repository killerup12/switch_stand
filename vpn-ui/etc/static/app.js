'use strict';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let state = {
  draft: { groups: [] },
  live: { groups: [] },
  dirty: false,
  mihomo_status: { mihomo_ok: false, delay_ms: null },
};

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

function inferType(value) {
  if (value.includes('/')) return 'cidr';
  if (/^[0-9]{1,3}(\.[0-9]{1,3}){3}$/.test(value)) return 'ip';
  return 'domain';
}

function validateEntry(value) {
  const type = inferType(value);
  if (type === 'cidr') {
    return /^[0-9]{1,3}(\.[0-9]{1,3}){3}\/[0-9]{1,2}$/.test(value)
      ? null
      : 'Неверный CIDR (пример: 91.108.4.0/22)';
  }
  if (type === 'ip') {
    return /^((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$/.test(value)
      ? null
      : 'Неверный IP-адрес (пример: 104.18.35.28)';
  }
  return /^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?\.[a-zA-Z]{2,}$/.test(value)
    ? null
    : 'Неверный домен (пример: claude.ai)';
}

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------

let _toastTimer = null;

function showToast(msg, success = false) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = success ? 'success' : '';
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.add('hidden'), 5000);
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

async function apiFetch(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const resp = await fetch(path, opts);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

// ---------------------------------------------------------------------------
// Status bar
// ---------------------------------------------------------------------------

async function refreshStatus() {
  try {
    const s = await apiFetch('GET', '/api/status');
    const bar = document.getElementById('status-bar');
    if (s.mihomo_ok) {
      const delay = s.delay_ms != null ? `${s.delay_ms} ms` : 'n/a';
      bar.textContent = `⚡ Mihomo: ✓  | VPN delay: ${delay}`;
      bar.style.color = '#4ec9b0';
    } else {
      bar.textContent = '⚡ Mihomo: ✗ (offline)';
      bar.style.color = '#f44747';
    }
  } catch (_) {}
}

// ---------------------------------------------------------------------------
// Load state
// ---------------------------------------------------------------------------

async function loadState() {
  try {
    const s = await apiFetch('GET', '/api/state');
    state = s;
    renderAll();
  } catch (e) {
    showToast('Не удалось загрузить состояние: ' + e.message);
  }
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

function renderAll() {
  renderDirtyBanner();
  renderGroups();
}

function renderDirtyBanner() {
  document.getElementById('dirty-banner').classList.toggle('hidden', !state.dirty);
}

function renderGroups() {
  const container = document.getElementById('groups-list');
  container.innerHTML = '';
  for (const group of [...state.draft.groups].sort((a, b) => a.name.localeCompare(b.name))) {
    container.appendChild(buildGroupEl(group));
  }
}

function buildGroupEl(group) {
  const div = document.createElement('div');
  div.className = 'group';
  div.dataset.groupName = group.name;

  // Header
  const header = document.createElement('div');
  header.className = 'group-header';

  const nameSpan = document.createElement('span');
  nameSpan.className = 'group-name';
  nameSpan.textContent = '▶ ' + group.name;
  header.appendChild(nameSpan);

  const actions = document.createElement('div');
  actions.className = 'group-header-actions';

  const addBtn = makeButton('+ Add', '', () => toggleAddForm(div, group.name));
  const delBtn = makeButton('× group', 'danger', () => deleteGroup(group.name, group.entries.length));
  delBtn.title = 'Удалить группу';

  actions.append(addBtn, delBtn);
  header.appendChild(actions);
  div.appendChild(header);

  // Collapse toggle
  header.addEventListener('click', (e) => {
    if (e.target.tagName === 'BUTTON') return;
    const collapsed = body.classList.toggle('collapsed');
    nameSpan.textContent = (collapsed ? '▶ ' : '▼ ') + group.name;
  });

  // Body (collapsed by default)
  const body = document.createElement('div');
  body.className = 'group-body collapsed';
  for (const entry of group.entries) {
    body.appendChild(buildEntryEl(entry, group.name));
  }

  const slot = document.createElement('div');
  slot.className = 'add-form-slot';
  body.appendChild(slot);

  div.appendChild(body);
  return div;
}

function buildEntryEl(entry, groupName) {
  const row = document.createElement('div');
  row.className = 'entry';

  const val = document.createElement('span');
  val.className = 'entry-value';
  val.textContent = entry.value;

  const typ = document.createElement('span');
  typ.className = 'entry-type';
  typ.textContent = entry.type;

  const del = makeButton('×', 'danger', () => deleteEntry(groupName, entry.value));
  del.title = 'Удалить запись';

  row.append(val, typ, del);
  return row;
}

function makeButton(text, cls, onClick) {
  const btn = document.createElement('button');
  btn.textContent = text;
  if (cls) btn.className = cls;
  btn.onclick = onClick;
  return btn;
}

// ---------------------------------------------------------------------------
// Inline add form
// ---------------------------------------------------------------------------

function toggleAddForm(groupDiv, groupName) {
  const slot = groupDiv.querySelector('.add-form-slot');

  if (slot.querySelector('.inline-form')) {
    slot.innerHTML = '';
    return;
  }

  const form = document.createElement('div');
  form.className = 'inline-form';

  const input = document.createElement('input');
  input.type = 'text';
  input.placeholder = 'domain.com или 1.2.3.0/24';

  const saveBtn = makeButton('Save', 'primary', doSave);
  const cancelBtn = makeButton('Cancel', '', () => { slot.innerHTML = ''; });

  const hint = document.createElement('div');
  hint.className = 'validation-hint';

  form.append(input, saveBtn, cancelBtn);
  slot.append(form, hint);
  input.focus();

  async function doSave() {
    const value = input.value.trim().toLowerCase();
    const err = validateEntry(value);
    if (err) {
      hint.textContent = err;
      return;
    }
    hint.textContent = '';
    saveBtn.disabled = true;
    try {
      await apiFetch('POST', `/api/groups/${encodeURIComponent(groupName)}/entries`, { value });
      await loadState();
    } catch (e) {
      showToast(e.message);
      saveBtn.disabled = false;
    }
  }

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') doSave();
    if (e.key === 'Escape') slot.innerHTML = '';
  });
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

async function deleteEntry(groupName, value) {
  try {
    await apiFetch('DELETE', `/api/groups/${encodeURIComponent(groupName)}/entries/${encodeURIComponent(value)}`);
    await loadState();
  } catch (e) {
    showToast(e.message);
  }
}

async function deleteGroup(name, entryCount) {
  if (entryCount > 0) {
    if (!confirm(`Удалить группу «${name}» вместе с ${entryCount} записями?`)) return;
  }
  try {
    await apiFetch('DELETE', `/api/groups/${encodeURIComponent(name)}`);
    await loadState();
  } catch (e) {
    showToast(e.message);
  }
}

async function createGroup() {
  const name = prompt('Имя новой группы:');
  if (!name || !name.trim()) return;
  try {
    await apiFetch('POST', '/api/groups', { name: name.trim() });
    await loadState();
  } catch (e) {
    showToast(e.message);
  }
}

async function applyChanges() {
  const btn = document.getElementById('btn-apply');
  btn.disabled = true;
  btn.textContent = 'Applying...';
  try {
    await apiFetch('POST', '/api/apply');
    await loadState();
    showToast('Применено успешно', true);
  } catch (e) {
    showToast('Apply не удался: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Apply';
  }
}

async function discardChanges() {
  try {
    await apiFetch('POST', '/api/discard');
    await loadState();
  } catch (e) {
    showToast(e.message);
  }
}

async function testVpn() {
  const btn = document.getElementById('btn-test-vpn');
  const result = document.getElementById('vpn-test-result');
  btn.disabled = true;
  result.textContent = 'тестируем...';
  result.style.color = '#888';
  try {
    const data = await apiFetch('GET', '/api/test/vpn');
    result.textContent = 'IP: ' + data.ip;
    result.style.color = '#4ec9b0';
  } catch (e) {
    result.textContent = 'ошибка: ' + e.message;
    result.style.color = '#f44747';
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

async function loadSettings() {
  try {
    const s = await apiFetch('GET', '/api/settings');
    document.getElementById('setting-router-host').value = s.router_host;
    document.getElementById('setting-router-user').value = s.router_user;
    document.getElementById('setting-mihomo-dns').value  = s.mihomo_dns;
  } catch (e) {
    showToast('Не удалось загрузить настройки: ' + e.message);
  }
}

function toggleSettings() {
  const panel = document.getElementById('settings-panel');
  const hidden = panel.classList.toggle('hidden');
  if (!hidden) loadSettings();
}

async function saveSettings() {
  const host = document.getElementById('setting-router-host').value.trim();
  const user = document.getElementById('setting-router-user').value.trim();
  const dns  = document.getElementById('setting-mihomo-dns').value.trim();
  try {
    await apiFetch('POST', '/api/settings', { router_host: host, router_user: user, mihomo_dns: dns });
    document.getElementById('settings-panel').classList.add('hidden');
    showToast('Настройки сохранены', true);
  } catch (e) {
    showToast('Ошибка: ' + e.message);
  }
}

// ---------------------------------------------------------------------------
// Proxy management
// ---------------------------------------------------------------------------

async function loadProxies() {
  try {
    const data = await apiFetch('GET', '/api/proxies');
    renderProxies(data.proxies || []);
  } catch (e) {
    showToast('Не удалось загрузить прокси: ' + e.message);
  }
}

function renderProxies(proxies) {
  const list = document.getElementById('proxies-list');
  list.innerHTML = '';
  for (const p of proxies) {
    list.appendChild(buildProxyCard(p));
  }
}

function buildProxyCard(proxy) {
  const card = document.createElement('div');
  card.className = 'proxy-card';

  const name = document.createElement('span');
  name.className = 'proxy-name';
  name.textContent = proxy.name;

  const type = document.createElement('span');
  type.className = 'proxy-type';
  type.textContent = proxy.type;

  const addr = document.createElement('span');
  addr.className = 'proxy-addr';
  addr.textContent = `${proxy.server}:${proxy.port}`;

  const del = makeButton('×', 'danger', () => deleteProxy(proxy.name));
  del.title = 'Удалить прокси';

  card.append(name, type, addr, del);
  return card;
}

async function deleteProxy(name) {
  if (!confirm(`Удалить прокси «${name}»?`)) return;
  try {
    await apiFetch('DELETE', '/api/proxies', { name });
    showToast(`Прокси «${name}» удалён`, true);
    loadProxies();
  } catch (e) {
    showToast('Ошибка: ' + e.message);
  }
}

function parseProxyLink(link) {
  let url;
  try {
    url = new URL(link.trim());
  } catch {
    throw new Error('Невалидная ссылка');
  }
  const scheme = url.protocol.replace(':', '');
  const name = url.hash ? decodeURIComponent(url.hash.slice(1)) : url.hostname;
  const params = Object.fromEntries(url.searchParams);

  if (scheme === 'hy2' || scheme === 'hysteria2') {
    const data = {
      type: 'hysteria2',
      name,
      server: url.hostname,
      port: url.port || '443',
      password: decodeURIComponent(url.username),
      skip_cert_verify: params.insecure === '1' || params['skip-cert-verify'] === '1',
    };
    if (params['obfs-password']) data.obfs_password = params['obfs-password'];
    return data;
  }

  if (scheme === 'vless') {
    const security = params.security || '';
    if (security !== 'reality') throw new Error('Поддерживается только VLESS+Reality (security=reality)');
    return {
      type: 'vless',
      name,
      server: url.hostname,
      port: url.port || '443',
      uuid: url.username,
      public_key: params.pbk || '',
      short_id: params.sid || '',
      servername: params.sni || '',
      fingerprint: params.fp || 'chrome',
    };
  }

  throw new Error(`Неизвестный формат: ${scheme}://`);
}

function showLinkImportForm() {
  const slot = document.getElementById('proxy-form-slot');
  if (slot.querySelector('.link-import-form')) {
    slot.innerHTML = '';
    return;
  }
  slot.innerHTML = '';

  const form = document.createElement('div');
  form.className = 'proxy-form link-import-form';

  const row = document.createElement('div');
  row.className = 'proxy-form-row';
  const label = document.createElement('label');
  label.textContent = 'Ссылка';
  const input = document.createElement('input');
  input.type = 'text';
  input.placeholder = 'hy2://... или vless://...';
  input.style.fontFamily = 'monospace';
  row.append(label, input);
  form.appendChild(row);

  const hint = document.createElement('div');
  hint.style.fontSize = '11px';
  hint.style.color = '#666';
  hint.style.paddingLeft = '98px';
  hint.textContent = 'Поддерживается: hy2://, hysteria2://, vless:// (Reality)';
  form.appendChild(hint);

  const actions = document.createElement('div');
  actions.className = 'proxy-form-actions';

  const addBtn = makeButton('Добавить', 'primary', async () => {
    let data;
    try {
      data = parseProxyLink(input.value);
    } catch (e) {
      showToast('Ошибка парсинга: ' + e.message);
      return;
    }
    try {
      await apiFetch('POST', '/api/proxies', data);
      slot.innerHTML = '';
      showToast(`Прокси «${data.name}» добавлен`, true);
      loadProxies();
    } catch (e) {
      showToast('Ошибка: ' + e.message);
    }
  });

  const cancelBtn = makeButton('Отмена', '', () => { slot.innerHTML = ''; });
  actions.append(addBtn, cancelBtn);
  form.appendChild(actions);

  input.addEventListener('keydown', e => { if (e.key === 'Enter') addBtn.click(); });

  slot.appendChild(form);
  input.focus();
}

function toggleProxyForm() {
  const slot = document.getElementById('proxy-form-slot');
  if (slot.querySelector('.proxy-form')) {
    slot.innerHTML = '';
    return;
  }
  slot.appendChild(buildProxyForm());
}

function buildProxyForm() {
  const form = document.createElement('div');
  form.className = 'proxy-form';

  const typeRow = makeFormRow('Тип');
  const typeSelect = document.createElement('select');
  typeSelect.innerHTML = `
    <option value="hysteria2">Hysteria2</option>
    <option value="vless">VLESS+Reality</option>
  `;
  typeRow.append(typeSelect);
  form.appendChild(typeRow);

  const nameRow = makeFormRow('Имя');
  const nameInput = makeInput('мой-vpn');
  nameRow.append(nameInput);
  form.appendChild(nameRow);

  const serverRow = makeFormRow('Сервер');
  const serverInput = makeInput('1.2.3.4');
  const portInput = makeInput('443');
  portInput.style.maxWidth = '80px';
  serverRow.append(serverInput, portInput);
  form.appendChild(serverRow);

  const fieldsContainer = document.createElement('div');
  form.appendChild(fieldsContainer);

  function renderFields(type) {
    fieldsContainer.innerHTML = '';
    if (type === 'hysteria2') {
      fieldsContainer.appendChild(makeFormRow('Пароль', makeInput('', 'password')));
      fieldsContainer.appendChild(makeFormRow('Obfs-пароль', makeInput('(опционально)')));

      const skipRow = makeFormRow('');
      const skipLabel = document.createElement('label');
      skipLabel.style.display = 'flex';
      skipLabel.style.alignItems = 'center';
      skipLabel.style.gap = '6px';
      skipLabel.style.color = '#aaa';
      skipLabel.style.fontSize = '13px';
      const skipCheck = document.createElement('input');
      skipCheck.type = 'checkbox';
      skipCheck.checked = true;
      skipCheck.dataset.field = 'skip_cert_verify';
      skipLabel.append(skipCheck, 'skip-cert-verify');
      skipRow.append(skipLabel);
      fieldsContainer.appendChild(skipRow);
    } else {
      fieldsContainer.appendChild(makeFormRow('UUID', makeInput('')));
      fieldsContainer.appendChild(makeFormRow('Public key', makeInput('')));
      fieldsContainer.appendChild(makeFormRow('Short ID', makeInput('(опционально)')));
      fieldsContainer.appendChild(makeFormRow('SNI', makeInput('www.microsoft.com')));
      const fpRow = makeFormRow('Fingerprint');
      const fpSelect = document.createElement('select');
      fpSelect.dataset.field = 'fingerprint';
      fpSelect.innerHTML = `<option>chrome</option><option>firefox</option><option>safari</option><option>edge</option>`;
      fpRow.append(fpSelect);
      fieldsContainer.appendChild(fpRow);
    }
  }

  typeSelect.addEventListener('change', () => renderFields(typeSelect.value));
  renderFields(typeSelect.value);

  const actions = document.createElement('div');
  actions.className = 'proxy-form-actions';

  const saveBtn = makeButton('Добавить', 'primary', async () => {
    const payload = collectFormData(form, typeSelect.value, nameInput, serverInput, portInput);
    try {
      await apiFetch('POST', '/api/proxies', payload);
      document.getElementById('proxy-form-slot').innerHTML = '';
      showToast('Прокси добавлен', true);
      loadProxies();
    } catch (e) {
      showToast('Ошибка: ' + e.message);
    }
  });
  const cancelBtn = makeButton('Отмена', '', () => {
    document.getElementById('proxy-form-slot').innerHTML = '';
  });
  actions.append(saveBtn, cancelBtn);
  form.appendChild(actions);
  return form;
}

function makeFormRow(labelText, ...children) {
  const row = document.createElement('div');
  row.className = 'proxy-form-row';
  const label = document.createElement('label');
  label.textContent = labelText;
  row.append(label, ...children);
  return row;
}

function makeInput(placeholder, type = 'text') {
  const inp = document.createElement('input');
  inp.type = type;
  inp.placeholder = placeholder;
  return inp;
}

function collectFormData(form, type, nameInput, serverInput, portInput) {
  const data = { type, name: nameInput.value.trim(), server: serverInput.value.trim(), port: portInput.value.trim() };
  const inputs = form.querySelectorAll('input[type="text"], input[type="password"], select');
  const labels = form.querySelectorAll('.proxy-form-row label');
  const fieldMap = {
    'Пароль': 'password', 'Obfs-пароль': 'obfs_password',
    'UUID': 'uuid', 'Public key': 'public_key', 'Short ID': 'short_id',
    'SNI': 'servername',
  };
  labels.forEach(label => {
    const key = fieldMap[label.textContent];
    if (!key) return;
    const inp = label.parentElement.querySelector('input, select');
    if (inp) data[key] = inp.value.trim();
  });
  const fpSelect = form.querySelector('select[data-field="fingerprint"]');
  if (fpSelect) data.fingerprint = fpSelect.value;
  const skipCheck = form.querySelector('input[data-field="skip_cert_verify"]');
  if (skipCheck) data.skip_cert_verify = skipCheck.checked;
  return data;
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

document.getElementById('btn-apply').addEventListener('click', applyChanges);
document.getElementById('btn-discard').addEventListener('click', discardChanges);
document.getElementById('btn-new-group').addEventListener('click', createGroup);
document.getElementById('btn-test-vpn').addEventListener('click', testVpn);
document.getElementById('btn-add-proxy').addEventListener('click', toggleProxyForm);
document.getElementById('btn-import-link').addEventListener('click', showLinkImportForm);
document.getElementById('btn-settings').addEventListener('click', toggleSettings);
document.getElementById('btn-settings-save').addEventListener('click', saveSettings);
document.getElementById('btn-settings-close').addEventListener('click', () => {
  document.getElementById('settings-panel').classList.add('hidden');
});

loadState();
loadProxies();
refreshStatus();
setInterval(refreshStatus, 30000);
