const state = { data: null, editingRule: null, toastTimer: null };
const $ = (selector) => document.querySelector(selector);
const money = (cents) => new Intl.NumberFormat('en-IE', { style: 'currency', currency: 'EUR', maximumFractionDigits: 2 }).format(cents / 100);
const monthKey = (date) => date.slice(0, 7);
const monthLabel = (key) => new Intl.DateTimeFormat('en', { month: 'short' }).format(new Date(`${key}-01T12:00:00`));
const categoryColor = (name) => state.data.categories.find((category) => category.name === name)?.color || '#777b77';

async function request(url, options = {}) {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...options });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Something went wrong');
  return result;
}

function showToast(message, isError = false) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.classList.toggle('error', isError);
  toast.classList.add('show');
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.remove('show'), 3200);
}

function merchantText(transaction) {
  return transaction.counterparty.replace(/^VISA\s+/i, '').trim();
}

function visibleTransactions() {
  return state.data.transactions.filter((transaction) => !transaction.duplicate_of);
}

function categoryNetTotals(transactions) {
  const totals = new Map();
  for (const transaction of transactions) {
    totals.set(
      transaction.category,
      (totals.get(transaction.category) || 0) - transaction.amount_cents,
    );
  }
  return new Map([...totals].filter(([, amount]) => amount > 0));
}

function getSelectedTransactions() {
  const limit = $('#period-select').value;
  const transactions = visibleTransactions();
  const sortedMonths = [...new Set(transactions.map((transaction) => monthKey(transaction.booked_on)))].sort();
  const visibleMonths = limit === 'all' ? sortedMonths : sortedMonths.slice(-Number(limit));
  return transactions.filter((transaction) => visibleMonths.includes(monthKey(transaction.booked_on)));
}

function render() {
  if (!state.data) return;
  const transactions = visibleTransactions();
  const hiddenCount = state.data.transactions.length - transactions.length;
  $('#transaction-total').textContent = `${transactions.length.toLocaleString()} transactions${hiddenCount ? ` · ${hiddenCount} duplicates hidden` : ''}`;
  $('#rule-count').textContent = state.data.rules.length;
  $('#duplicate-count').textContent = hiddenCount;
  renderCategories();
  renderCategoryEditor();
  renderDashboard();
  renderTransactions();
  renderRules();
  renderAdvanced();
}

function renderCategories() {
  const names = ['Unsorted', ...state.data.categories.map((category) => category.name)];
  const categoryFilter = $('#category-filter');
  const selectedCategory = categoryFilter.value;
  categoryFilter.innerHTML = '<option value="">All categories</option>' + names.map((name) => `<option>${escapeHtml(name)}</option>`).join('');
  categoryFilter.value = names.includes(selectedCategory) ? selectedCategory : '';
  const monthFilter = $('#month-filter');
  const selectedMonth = monthFilter.value;
  const months = [...new Set(visibleTransactions().map((transaction) => monthKey(transaction.booked_on)))].sort().reverse();
  monthFilter.innerHTML = '<option value="">All months</option>' + months.map((month) => `<option value="${month}">${new Date(`${month}-01T12:00:00`).toLocaleDateString('en', { month: 'long', year: 'numeric' })}</option>`).join('');
  monthFilter.value = months.includes(selectedMonth) ? selectedMonth : '';
  $('#rule-category').innerHTML = state.data.categories.map((category) => `<option value="${escapeHtml(category.name)}">${escapeHtml(category.name)}</option>`).join('');
  $('#category-drops').innerHTML = state.data.categories.map((category) => `<button class="drop-category" data-category="${escapeHtml(category.name)}" style="--drop-color:${category.color}">${escapeHtml(category.name)}</button>`).join('');
  document.querySelectorAll('.drop-category').forEach((target) => {
    target.addEventListener('dragover', (event) => { event.preventDefault(); target.classList.add('drag-over'); });
    target.addEventListener('dragleave', () => target.classList.remove('drag-over'));
    target.addEventListener('drop', (event) => {
      event.preventDefault();
      target.classList.remove('drag-over');
      const transaction = state.data.transactions.find((item) => item.fingerprint === event.dataTransfer.getData('text/plain'));
      if (transaction) createRuleFromDrop(transaction, target.dataset.category);
    });
  });
}

function renderCategoryEditor() {
  $('#category-editor-list').innerHTML = state.data.categories.map((category) => `
    <article class="category-editor-row" data-category-id="${category.id}">
      <span class="category-editor-swatch" style="background:${category.color}"></span>
      <label class="category-edit-label">NAME<input class="category-name-input" type="text" value="${escapeHtml(category.name)}" maxlength="40" required></label>
      <label class="category-edit-label">COLOR<input class="category-color-input" type="color" value="${category.color}"></label>
      <button class="small-action" data-save-category="${category.id}">Save name</button>
    </article>`).join('');
  document.querySelectorAll('.category-editor-row').forEach((row) => {
    const colorInput = row.querySelector('.category-color-input');
    colorInput.addEventListener('input', () => {
      row.querySelector('.category-editor-swatch').style.background = colorInput.value;
    });
    colorInput.addEventListener('change', () => saveCategoryColor(row, colorInput.value));
    row.querySelector('[data-save-category]').addEventListener('click', (event) => saveCategory(event.currentTarget));
  });
}

function renderDashboard() {
  const selected = getSelectedTransactions();
  const monthly = new Map();
  for (const transaction of selected) {
    const key = monthKey(transaction.booked_on);
    const current = monthly.get(key) || { spend: 0, income: 0, categoryNets: new Map() };
    const categoryNet = (current.categoryNets.get(transaction.category) || 0) - transaction.amount_cents;
    current.categoryNets.set(transaction.category, categoryNet);
    if (transaction.amount_cents > 0) current.income += transaction.amount_cents;
    monthly.set(key, current);
  }
  for (const month of monthly.values()) {
    month.spend = [...month.categoryNets.values()].reduce((sum, amount) => sum + Math.max(0, amount), 0);
  }
  const allKeys = [...monthly.keys()].sort();
  const currentKey = allKeys.at(-1);
  const previousKey = allKeys.at(-2);
  const current = monthly.get(currentKey) || { spend: 0, income: 0 };
  const previous = monthly.get(previousKey) || { spend: 0, income: 0 };
  const average = monthly.size ? Math.round([...monthly.values()].reduce((sum, month) => sum + month.spend, 0) / monthly.size) : 0;
  const difference = current.spend - previous.spend;
  $('#kpi-month').textContent = money(current.spend);
  $('#kpi-average').textContent = money(average);
  $('#kpi-income').textContent = money(current.income);
  $('#kpi-count').textContent = selected.length.toLocaleString();
  $('#month-change').textContent = previousKey ? `${difference > 0 ? '+' : ''}${money(difference)}` : '—';
  $('#month-change').classList.toggle('negative', difference > 0);
  $('#overview-period').textContent = currentKey ? `ACTIVITY THROUGH ${new Date(`${currentKey}-01T12:00:00`).toLocaleDateString('en', { month: 'long', year: 'numeric' }).toUpperCase()}` : 'YOUR MONEY, IN FOCUS';
  drawTrend(monthly, allKeys);
  drawCategorySummary(selected);
  drawMonthlyCategoryCharts(selected);
  $('#recent-rows').innerHTML = transactionRows(visibleTransactions().slice(0, 6), false);
}

function drawTrend(monthly, keys) {
  const visible = keys.slice(-12);
  const peak = Math.max(1, ...visible.flatMap((key) => [monthly.get(key).spend, monthly.get(key).income]));
  const chart = $('#trend-chart');
  if (!visible.length) {
    chart.innerHTML = '<p class="empty-note">Import a CSV to see your monthly activity.</p>';
    return;
  }
  chart.innerHTML = '<div class="chart-gridlines"><span></span><span></span><span></span></div>' + visible.map((key) => {
    const value = monthly.get(key);
    const spendHeight = Math.max(1, value.spend / peak * 100);
    const incomeHeight = Math.max(1, value.income / peak * 100);
    return `<div class="chart-column" title="${monthLabel(key)} ${key.slice(0, 4)} · net spend ${money(value.spend)} · income ${money(value.income)}"><div class="chart-bars"><i class="chart-bar spend" style="height:${spendHeight}%"></i><i class="chart-bar income" style="height:${incomeHeight}%"></i></div><span class="chart-label">${monthLabel(key)}</span></div>`;
  }).join('');
}

function drawCategorySummary(transactions) {
  const totals = categoryNetTotals(transactions);
  const ranked = [...totals].sort((a, b) => b[1] - a[1]).slice(0, 6);
  const max = ranked[0]?.[1] || 1;
  $('#category-list').innerHTML = ranked.length ? ranked.map(([name, total]) => `<div class="category-line"><span class="category-name">${escapeHtml(name)}</span><div class="category-track"><div class="category-fill" style="width:${total / max * 100}%;background:${categoryColor(name)}"></div></div><span class="category-total">${money(total)}</span></div>`).join('') : '<p class="empty-note">No spending in this period.</p>';
}

function drawMonthlyCategoryCharts(transactions) {
  const months = new Map();
  for (const transaction of transactions) {
    const key = monthKey(transaction.booked_on);
    if (!months.has(key)) months.set(key, new Map());
    const categories = months.get(key);
    categories.set(transaction.category, (categories.get(transaction.category) || 0) - transaction.amount_cents);
  }
  const keys = [...months.keys()].sort().reverse();
  const container = $('#monthly-category-charts');
  if (!keys.length) {
    container.innerHTML = '<p class="empty-note">Import a CSV to see monthly category breakdowns.</p>';
    return;
  }
  container.innerHTML = keys.map((key) => {
    const categories = [...months.get(key)].filter(([, amount]) => amount > 0).sort((a, b) => b[1] - a[1]);
    const total = categories.reduce((sum, [, amount]) => sum + amount, 0);
    let position = 0;
    const slices = categories.map(([name, amount]) => {
      const start = position;
      position += total ? amount / total * 100 : 0;
      return `${categoryColor(name)} ${start}% ${position}%`;
    });
    const month = new Date(`${key}-01T12:00:00`).toLocaleDateString('en', { month: 'long', year: 'numeric' });
    const label = categories.map(([name, amount]) => `${name} ${money(amount)}`).join(', ');
      const legend = categories.map(([name, amount]) => `<button type="button" class="monthly-category-item" data-month="${key}" data-category="${escapeHtml(name)}" aria-label="Show ${escapeHtml(name)} transactions for ${escapeHtml(month)}."><i style="background:${categoryColor(name)}"></i><span>${escapeHtml(name)}</span><strong>${money(amount)}</strong><small>${total ? Math.round(amount / total * 100) : 0}%</small></button>`).join('');
    return `<article class="monthly-category"><div class="monthly-category-heading"><h3>${month}</h3><strong>${money(total)}</strong></div><div class="monthly-category-content"><div class="monthly-pie${total ? '' : ' empty'}" role="img" aria-label="${escapeHtml(month)} spending categories: ${escapeHtml(label || 'No spending')}." style="background:${total ? `conic-gradient(${slices.join(',')})` : '#e8e7df'}"></div><div class="monthly-category-legend">${legend || '<span class="monthly-empty">No spending this month</span>'}</div></div></article>`;
  }).join('');
  container.querySelectorAll('.monthly-category-item').forEach((button) => {
    button.addEventListener('click', () => openTransactionsFor(button.dataset.month, button.dataset.category));
  });
}

function transactionRows(transactions, draggable = true) {
  if (!transactions.length) return '<tr><td colspan="4"><p class="empty-note">No transactions found.</p></td></tr>';
  return transactions.map((transaction) => {
    const initial = escapeHtml((transaction.counterparty.trim()[0] || '?').toUpperCase());
    const signedAmount = transaction.amount_cents > 0 ? `+${money(transaction.amount_cents)}` : `−${money(-transaction.amount_cents)}`;
    const date = new Date(`${transaction.booked_on}T12:00:00`).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
    const subtitle = escapeHtml([transaction.tx_type, transaction.details].filter(Boolean).join(' · ') || 'Transaction');
    const searchUrl = `https://www.google.com/search?q=${encodeURIComponent(transaction.counterparty)}`;
    return `<tr ${draggable ? `class="draggable-row" draggable="true" data-fingerprint="${transaction.fingerprint}"` : ''}><td><div class="merchant-cell"><span class="merchant-icon">${initial}</span><span class="merchant-meta"><strong><a class="merchant-search-link" href="${searchUrl}" target="_blank" rel="noopener noreferrer" title="Search Google for ${escapeHtml(transaction.counterparty)}">${escapeHtml(transaction.counterparty)}</a></strong><small title="${subtitle}">${subtitle}</small></span></div></td><td>${date}</td><td><span class="category-tag"><i style="background:${transaction.category_color}"></i>${escapeHtml(transaction.category)}</span></td><td class="amount-cell ${transaction.amount_cents >= 0 ? 'amount-income' : 'amount-expense'}">${signedAmount}</td></tr>`;
  }).join('');
}

function renderTransactions() {
  const query = $('#search-input').value.trim().toLocaleLowerCase();
  const category = $('#category-filter').value;
  const month = $('#month-filter').value;
  const sort = $('#sort-select').value;
  const transactions = visibleTransactions();
  const rows = transactions.filter((transaction) => {
    const text = `${transaction.counterparty} ${transaction.details} ${transaction.tx_type}`.toLocaleLowerCase();
    return (!query || text.includes(query)) && (!category || transaction.category === category) && (!month || monthKey(transaction.booked_on) === month);
  });
  if (sort === 'oldest') rows.reverse();
  if (sort === 'highest') rows.sort((a, b) => b.amount_cents - a.amount_cents);
  if (sort === 'lowest') rows.sort((a, b) => a.amount_cents - b.amount_cents);
  $('#transaction-rows').innerHTML = transactionRows(rows);
  $('#transaction-footer').textContent = `Showing ${rows.length.toLocaleString()} of ${transactions.length.toLocaleString()} displayed transactions`;
  document.querySelectorAll('.draggable-row').forEach((row) => {
    row.addEventListener('dragstart', (event) => {
      row.classList.add('dragging');
      event.dataTransfer.setData('text/plain', row.dataset.fingerprint);
      event.dataTransfer.effectAllowed = 'copy';
    });
    row.addEventListener('dragend', () => row.classList.remove('dragging'));
  });
}

function renderRules() {
  $('#rules-summary-count').textContent = `${state.data.rules.length} ${state.data.rules.length === 1 ? 'filter' : 'filters'}`;
  $('#rules-list').innerHTML = state.data.rules.length ? state.data.rules.map((rule) => {
    const field = { counterparty: 'merchant', details: 'payment details', type: 'transaction type' }[rule.field];
    const operator = { contains: 'contains', equals: 'is exactly', starts_with: 'starts with' }[rule.operator];
    return `<article class="rule-item"><span class="rule-color" style="background:${rule.color}"></span><div class="rule-detail"><strong>${escapeHtml(rule.category)}</strong><p><code>${field} ${operator} “${escapeHtml(rule.value)}”</code></p></div><div class="rule-actions"><button class="small-action" data-edit-rule="${rule.id}">Edit</button><button class="small-action delete" data-delete-rule="${rule.id}">Delete</button></div></article>`;
  }).join('') : '<p class="empty-note">No filters yet. Drag a transaction onto a category to create your first one.</p>';
  document.querySelectorAll('[data-edit-rule]').forEach((button) => button.addEventListener('click', () => openRuleEditor(state.data.rules.find((rule) => rule.id === Number(button.dataset.editRule)))));
  document.querySelectorAll('[data-delete-rule]').forEach((button) => button.addEventListener('click', () => deleteRule(Number(button.dataset.deleteRule))));
}

function renderAdvanced() {
  const groups = state.data.duplicates || [];
  const hiddenCount = groups.reduce((count, group) => count + group.duplicates.length, 0);
  $('#duplicate-summary-count').textContent = `${hiddenCount} hidden ${hiddenCount === 1 ? 'duplicate' : 'duplicates'}`;
  $('#duplicate-list').innerHTML = groups.length ? groups.map((group) => {
    const kept = group.representative;
    const duplicates = group.duplicates.map((transaction) => {
      const difference = transaction.duplicate_difference_cents;
      const amountDifference = difference ? `Amount differs by ${money(difference)}.` : 'Amount matches exactly.';
      return `<div class="duplicate-record duplicate-hidden"><span class="duplicate-label">HIDDEN FROM EXPENSES</span><strong>${escapeHtml(transaction.counterparty)}</strong><span>${formatTransactionDate(transaction.booked_on)} · ${escapeHtml(transaction.tx_type)}</span><span>${escapeHtml(transaction.details || 'No payment details')}</span><b>${formatTransactionAmount(transaction.amount_cents)}</b><small>${amountDifference}</small><code>${transaction.fingerprint.slice(0, 16)}</code></div>`;
    }).join('');
    return `<article class="duplicate-group"><div class="duplicate-group-heading"><div><p class="eyebrow">PROBABLE DUPLICATE</p><h2>${escapeHtml(kept.counterparty)}</h2></div><span>${group.duplicates.length} hidden</span></div><div class="duplicate-pair"><div class="duplicate-record duplicate-kept"><span class="duplicate-label">KEPT IN EXPENSES</span><strong>${escapeHtml(kept.counterparty)}</strong><span>${formatTransactionDate(kept.booked_on)} · ${escapeHtml(kept.tx_type)}</span><span>${escapeHtml(kept.details || 'No payment details')}</span><b>${formatTransactionAmount(kept.amount_cents)}</b><code>${kept.fingerprint.slice(0, 16)}</code></div>${duplicates}</div></article>`;
  }).join('') : '<p class="empty-note">No probable duplicate transactions found.</p>';
}

function formatTransactionDate(date) {
  return new Date(`${date}T12:00:00`).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
}

function formatTransactionAmount(cents) {
  return cents < 0 ? `−${money(-cents)}` : `+${money(cents)}`;
}

async function createRuleFromDrop(transaction, category) {
  const merchant = merchantText(transaction);
  const initialValue = merchant.replace(/\s+[A-Z0-9*_-]{7,}.*$/i, '').trim() || merchant;
  try {
    const created = await request('/api/rules', { method: 'POST', body: JSON.stringify({ category, field: 'counterparty', operator: 'contains', value: initialValue }) });
    await loadData();
    openRuleEditor(state.data.rules.find((rule) => rule.id === created.id));
    showToast('Filter created. Adjust the match to catch similar transactions.');
  } catch (error) {
    showToast(error.message, true);
  }
}

function openRuleEditor(rule) {
  state.editingRule = rule;
  $('#dialog-eyebrow').textContent = rule ? 'EDIT FILTER' : 'NEW FILTER';
  $('#dialog-title').textContent = rule ? 'Refine your filter' : 'Create a filter';
  $('#rule-save').textContent = rule ? 'Save changes' : 'Save filter';
  $('#rule-category').value = rule?.category || state.data.categories[0]?.name;
  $('#rule-field').value = rule?.field || 'counterparty';
  $('#rule-operator').value = rule?.operator || 'contains';
  $('#rule-value').value = rule?.value || '';
  $('#dialog-error').textContent = '';
  $('#rule-dialog').showModal();
}

async function saveRule(event) {
  event.preventDefault();
  const rule = { category: $('#rule-category').value, field: $('#rule-field').value, operator: $('#rule-operator').value, value: $('#rule-value').value };
  try {
    if (state.editingRule) await request(`/api/rules/${state.editingRule.id}`, { method: 'PUT', body: JSON.stringify(rule) });
    else await request('/api/rules', { method: 'POST', body: JSON.stringify(rule) });
    $('#rule-dialog').close();
    await loadData();
    showToast('Filter saved. Transactions and totals updated.');
  } catch (error) {
    $('#dialog-error').textContent = error.message;
  }
}

async function deleteRule(id) {
  try {
    await request(`/api/rules/${id}`, { method: 'DELETE' });
    await loadData();
    showToast('Filter deleted. Transaction categories updated.');
  } catch (error) {
    showToast(error.message, true);
  }
}

async function importFile(file) {
  const buffer = await file.arrayBuffer();
  let content;
  try {
    content = new TextDecoder('utf-8', { fatal: true }).decode(buffer);
  } catch {
    content = new TextDecoder('windows-1252').decode(buffer);
  }
  try {
    const result = await request('/api/import', { method: 'POST', body: JSON.stringify({ name: file.name, content }) });
    await loadData();
    showToast(`Imported ${result.imported}; skipped ${result.duplicates} duplicates${result.rejected ? `; rejected ${result.rejected} invalid rows` : ''}.`);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    $('#file-input').value = '';
  }
}

async function exportFilters() {
  try {
    const response = await fetch('/api/rules/export');
    if (!response.ok) throw new Error('Could not export filters');
    const blob = await response.blob();
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'ledger-filters.json';
    link.click();
    URL.revokeObjectURL(link.href);
    showToast(`Exported ${state.data.rules.length} ${state.data.rules.length === 1 ? 'filter' : 'filters'}.`);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function importFilters(file) {
  try {
    const filters = JSON.parse(await file.text());
    const result = await request('/api/rules/import', { method: 'POST', body: JSON.stringify(filters) });
    await loadData();
    showToast(`Imported ${result.imported}; skipped ${result.duplicates} existing filters.`);
  } catch (error) {
    showToast(error instanceof SyntaxError ? 'That file is not valid JSON.' : error.message, true);
  } finally {
    $('#filter-file-input').value = '';
  }
}

async function exportCategories() {
  try {
    const response = await fetch('/api/categories/export');
    if (!response.ok) throw new Error('Could not export categories');
    const blob = await response.blob();
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'ledger-categories.json';
    link.click();
    URL.revokeObjectURL(link.href);
    showToast(`Exported ${state.data.categories.length} categories.`);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function importCategories(file) {
  try {
    const categories = JSON.parse(await file.text());
    const result = await request('/api/categories/import', { method: 'POST', body: JSON.stringify(categories) });
    await loadData();
    showToast(`Categories: ${result.added} added, ${result.updated} recolored, ${result.unchanged} unchanged.`);
  } catch (error) {
    showToast(error instanceof SyntaxError ? 'That file is not valid JSON.' : error.message, true);
  } finally {
    $('#category-file-input').value = '';
  }
}

async function loadData() {
  state.data = await request('/api/data');
  render();
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
}

function navigateToView(view) {
  document.querySelectorAll('.view').forEach((section) => section.classList.toggle('active', section.id === `${view}-view`));
  document.querySelectorAll('.nav-item').forEach((item) => item.classList.toggle('active', item.dataset.view === view));
  $('#page-title').textContent = { overview: 'Overview', transactions: 'Transactions', categories: 'Categories', rules: 'Filters', advanced: 'Advanced' }[view];
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function openTransactionsFor(month, category) {
  $('#month-filter').value = month;
  $('#category-filter').value = category;
  $('#search-input').value = '';
  renderTransactions();
  navigateToView('transactions');
}

document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => navigateToView(button.dataset.view)));
$('#period-select').addEventListener('change', renderDashboard);
$('#search-input').addEventListener('input', renderTransactions);
$('#category-filter').addEventListener('change', renderTransactions);
$('#month-filter').addEventListener('change', renderTransactions);
$('#sort-select').addEventListener('change', renderTransactions);
$('#import-button').addEventListener('click', () => $('#file-input').click());
$('#file-input').addEventListener('change', (event) => { if (event.target.files[0]) importFile(event.target.files[0]); });
$('#export-filters-button').addEventListener('click', exportFilters);
$('#import-filters-button').addEventListener('click', () => $('#filter-file-input').click());
$('#filter-file-input').addEventListener('change', (event) => { if (event.target.files[0]) importFilters(event.target.files[0]); });
$('#add-category-button').addEventListener('click', () => {
  $('#new-category-name').value = '';
  $('#new-category-color').value = '#7184a5';
  $('#category-dialog-error').textContent = '';
  $('#category-dialog').showModal();
});
$('#export-categories-button').addEventListener('click', exportCategories);
$('#import-categories-button').addEventListener('click', () => $('#category-file-input').click());
$('#category-file-input').addEventListener('change', (event) => { if (event.target.files[0]) importCategories(event.target.files[0]); });
$('#category-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await request('/api/categories', { method: 'POST', body: JSON.stringify({ name: $('#new-category-name').value, color: $('#new-category-color').value }) });
    $('#category-dialog').close();
    await loadData();
    showToast('Category added.');
  } catch (error) {
    $('#category-dialog-error').textContent = error.message;
  }
});
async function saveCategory(button) {
  const row = button.closest('.category-editor-row');
  const payload = { name: row.querySelector('.category-name-input').value, color: row.querySelector('.category-color-input').value };
  try {
    await request(`/api/categories/${button.dataset.saveCategory}`, { method: 'PUT', body: JSON.stringify(payload) });
    await loadData();
    showToast('Category name saved.');
  } catch (error) {
    showToast(error.message, true);
  }
}

async function saveCategoryColor(row, color) {
  const categoryId = Number(row.dataset.categoryId);
  const category = state.data.categories.find((item) => item.id === categoryId);
  if (!category || category.color.toLowerCase() === color.toLowerCase()) return;
  try {
    await request(`/api/categories/${categoryId}`, {
      method: 'PUT',
      body: JSON.stringify({ name: category.name, color }),
    });
    category.color = color;
    state.data.rules.filter((rule) => rule.category_id === categoryId).forEach((rule) => { rule.color = color; });
    state.data.transactions.filter((transaction) => transaction.category === category.name).forEach((transaction) => { transaction.category_color = color; });
    renderCategories();
    renderDashboard();
    renderTransactions();
    renderRules();
    renderAdvanced();
    showToast('Category color saved.');
  } catch (error) {
    row.querySelector('.category-color-input').value = category.color;
    row.querySelector('.category-editor-swatch').style.background = category.color;
    showToast(error.message, true);
  }
}
$('#category-dialog-close').addEventListener('click', () => $('#category-dialog').close());
$('#category-cancel').addEventListener('click', () => $('#category-dialog').close());
$('#rule-form').addEventListener('submit', saveRule);
$('#dialog-close').addEventListener('click', () => $('#rule-dialog').close());
$('#rule-cancel').addEventListener('click', () => $('#rule-dialog').close());

loadData().catch((error) => {
  showToast(`Could not load your database: ${error.message}`, true);
  $('#sync-label').textContent = 'DATABASE OFFLINE';
});