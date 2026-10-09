/* Canonical interest is separate from local recommendation enrollment. */
async function refreshCatalog() {
  const status = document.getElementById('catalog-status');
  try {
    const response = await fetch('/api/catalog');
    if (!response.ok) throw new Error('Не удалось прочитать каталог');
    const data = await response.json(), time = data.snapshot?.synced_at;
    status.textContent = `${time ? 'Обновлён ' + new Date(time).toLocaleString('ru-RU') : 'Ещё не синхронизирован'}. Ожидают отправки: ${data.outbox?.pending || 0}; конфликты: ${data.outbox?.conflict || 0}.`;
    const container = document.getElementById('catalog-links'); container.replaceChildren();
    const seen = new Set();
    for (const link of data.links || []) {
      if (!['owner', 'source'].includes(link.radarType) || seen.has(link.radarId)) continue;
      seen.add(link.radarId);
      const label = document.createElement('label'); label.style.cssText = 'display:flex;justify-content:space-between;gap:12px;margin-bottom:8px';
      label.append(document.createTextNode(link.canonical.name || link.radarId));
      const select = document.createElement('select'); select.setAttribute('aria-label', 'Интерес: ' + (link.canonical.name || link.radarId));
      for (const [value, name] of [['', 'Не оценено'], ['low', 'Низкий'], ['medium', 'Средний'], ['high', 'Высокий']]) { const option = document.createElement('option'); option.value = value; option.textContent = name; select.append(option); }
      const pending = link.pending?.find(p => p.field === 'interest');
      select.value = pending ? (typeof pending.value === 'string' ? pending.value : '') : (link.canonical.status?.interest || '');
      select.disabled = pending?.status === 'conflict';
      select.addEventListener('change', async () => {
        select.disabled = true;
        try { const result = await fetch(`/api/catalog/${encodeURIComponent(link.localType)}/${encodeURIComponent(link.localId)}/interest`, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({interest:select.value || null})}); if (!result.ok) throw new Error('Не удалось сохранить интерес'); await refreshCatalog(); }
        catch (e) { status.textContent = e.message; select.disabled = false; }
      });
      label.append(select); container.append(label);
    }
  } catch (e) { status.textContent = e.message; }
}
refreshCatalog();
