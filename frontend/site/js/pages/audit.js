import { api } from '../api.js';
import { drawer, errorBox, errorMessage, esc, fmtTime, kv, table } from '../ui.js';

function detailSummary(detail) {
  const data = detail || {};
  if (data.pending_fragments !== undefined) {
    return `片段 ${esc(data.created ?? 0)} 新 / ${esc(data.updated ?? 0)} 复用 · 待处理 ${esc(data.pending_fragments)} 段 / ${esc(data.pending_events ?? 0)} 事件`;
  }
  if (data.diff) {
    const before = (data.diff.before || {}).rec_count ?? 0;
    const after = (data.diff.after || {}).rec_count ?? 0;
    return `推荐 ${esc(before)} → ${esc(after)}${data.restored ? ` · 已恢复旧版 ${esc(data.restored.restored)} 条` : ''}`;
  }
  return `<span class="mono">${esc(JSON.stringify(data))}</span>`;
}

function openAudit(view, ctx, item) {
  const detail = item.detail || {};
  const pending = detail.pending || {};
  const diff = detail.diff;
  const diffRows = diff
    ? ['fragments_total', 'attributable_fragments', 'pending_fragments', 'identities_total', 'rec_count']
        .filter((key) => key in (diff.before || {}))
        .map((key) => `${key}: ${esc(diff.before[key])} → ${esc((diff.after || {})[key] ?? 0)}`)
        .join('\n')
    : '';
  drawer({
    title: `审计 #${item.id} · ${item.action_label || item.action}`,
    subtitle: `${fmtTime(item.created_at)} · ${item.actor_email} · ${item.layer}`,
    body: `${kv([
      ['动作', esc(item.action_label || item.action)],
      ['对象', `${esc(item.entity)} ${esc(item.entity_id || '')}`],
      ['操作人', `<span class="mono">${esc(item.actor_email)}</span>`],
      ['角色', esc(item.actor_role)],
    ])}
    ${
      pending.pending_fragments !== undefined
        ? `<h3 style="font-size:0.95rem;margin:6px 0 2px">待处理片段（排除原因）</h3>
           <div class="hold-list">${(pending.by_reason || [])
             .map((reason) => `<div class="hold-item"><div class="hold-head"><span class="badge pending">${esc(reason.count)} 段</span>${esc(reason.label)}</div><div class="mono muted">${esc(reason.reason || '')}</div></div>`)
             .join('') || '<span class="muted mono">无</span>'}</div>`
        : ''
    }
    ${diffRows ? `<h3 style="font-size:0.95rem;margin:6px 0 2px">重建前后差异</h3><pre class="code">${esc(diffRows)}</pre>` : ''}
    <h3 style="font-size:0.95rem;margin:6px 0 2px">原始详情</h3>
    <pre class="code">${esc(JSON.stringify(detail, null, 2))}</pre>`,
  });
}

export const auditPage = {
  async render(view, ctx) {
    let data;
    try {
      data = await api.get('/api/audit', { limit: 100 });
    } catch (err) {
      view.innerHTML = errorBox(errorMessage(err));
      view.querySelector('#retry-btn')?.addEventListener('click', () => auditPage.render(view, ctx));
      return;
    }
    view.innerHTML = `
      <section class="panel">
        <div class="panel-head">
          <h2>审计日志</h2>
          <span class="meta">${data.total} 条 · 最近 100 条</span>
        </div>
        ${table({
          rows: data.items,
          empty: { title: '还没有审计记录', hint: '登录、登记事件、挖掘、复核都会留痕。' },
          columns: [
            { key: 'created_at', label: '时间', render: (row) => fmtTime(row.created_at) },
            { key: 'actor_email', label: '操作人', className: 'mono' },
            { key: 'actor_role', label: '角色' },
            { key: 'action_label', label: '动作' },
            { key: 'entity', label: '对象' },
            { key: 'layer', label: '层', render: (row) => `<span class="badge">${esc(row.layer)}</span>` },
            { key: 'detail', label: '详情', render: (row) => `<span class="muted">${detailSummary(row.detail)}</span>` },
          ],
        })}
      </section>`;
    view.querySelectorAll('table.data tbody tr').forEach((row) => {
      row.addEventListener('click', () => {
        const item = data.items.find((entry) => String(entry.id) === row.dataset.id);
        if (item) openAudit(view, ctx, item);
      });
    });
  },
};

export default auditPage;
