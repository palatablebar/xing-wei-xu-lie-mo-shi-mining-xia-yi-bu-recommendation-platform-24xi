/** 无框架 UI 组件：读数条 / 信号瀑布 / 序列轨道 / 模式阶梯 / 表格 / 抽屉 / 空态 / toast。 */

export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

export function fmtTime(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return esc(iso);
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date);
}

export function fmtPct(value, digits = 1) {
  const num = Number(value || 0);
  return `${num.toFixed(digits)}%`;
}

export function badge(tone, text) {
  return `<span class="badge ${esc(tone)}">${esc(text)}</span>`;
}

/** 顶部仪器读数条（替代统计卡片）。 */
export function readouts(items) {
  return `<div class="readouts">${items
    .map(
      (item) => `<div class="readout ${esc(item.tone || '')}">
        <span class="k">${esc(item.label)}</span>
        <span class="v">${item.value}${item.unit ? `<small>${esc(item.unit)}</small>` : ''}</span>
        <span class="hint">${esc(item.hint || '')}</span>
      </div>`,
    )
    .join('')}</div>`;
}

/** 信号瀑布：每个时间桶一条横向信号带，按模块堆叠。 */
export function waterfall(rows, modules) {
  if (!rows || rows.length === 0) return emptyState({ title: '还没有信号', hint: '等埋点写入会话后再看。' });
  const peak = Math.max(1, ...rows.map((row) => row.events));
  const body = rows
    .map((row) => {
      const segs = row.modules
        .map((mod) => {
          if (!mod.count) return '';
          const width = Math.max(0.6, (mod.count / peak) * 100);
          return `<span class="wf-seg ${esc(mod.tone)}" style="width:${width}%" title="${esc(mod.label)} · ${esc(mod.count)} 事件"></span>`;
        })
        .join('');
      return `<div class="wf-row" data-bucket="${esc(row.index)}">
        <span class="stamp">${esc(row.label)}</span>
        <span class="wf-band" title="${esc(row.sessions)} 条会话 / ${esc(row.events)} 个事件">${segs}</span>
        <span class="num">${esc(row.sessions)}</span>
      </div>`;
    })
    .join('');
  const legend = `<div class="legend">${modules
    .map((mod) => `<span><i class="wf-seg ${esc(mod.tone)}"></i>${esc(mod.label)}</span>`)
    .join('')}<span>右侧数字 = 该时间桶会话数</span></div>`;
  return `<div class="waterfall"><div class="wf-axis"><span>时间桶（UTC+8）</span><span>会话</span></div>${body}${legend}</div>`;
}

/** 事件序列轨道：模块着色的等宽 chip。 */
export function track(events, max = 40) {
  if (!events || events.length === 0) return '<span class="muted mono">（空序列）</span>';
  const shown = events.slice(0, max);
  const chips = shown
    .map(
      (event) => `<span class="chip ${esc(event.module || 'board')}" title="${esc(event.label || event.code)}">
        ${esc(event.label || event.code)}<i>${esc(event.module_label || event.module || '')}</i>
      </span>`,
    )
    .join('<span class="arrow">›</span>');
  const rest = events.length > max ? `<span class="arrow">… 其余 ${events.length - max} 个</span>` : '';
  return `<div class="track">${chips}${rest}</div>`;
}

/** 模式支持度阶梯（横向条 + 支持度 + 置信度）。 */
export function ladder(items) {
  if (!items || items.length === 0) return emptyState({ title: '模式库还是空的', hint: '去「模式挖掘」发起一次 PrefixSpan。' });
  const peak = Math.max(1, ...items.map((item) => item.support_count));
  return `<div class="ladder">${items
    .map(
      (item) => `<div class="rung" title="${esc(item.seq_key)}">
        <span class="rank">${esc(item.rank)}</span>
        <span class="meter"><i style="width:${Math.max(3, (item.support_count / peak) * 100)}%"></i></span>
        <span class="pct">${fmtPct(item.support_pct)}</span>
        <span class="conf">${esc(item.confidence.toFixed(2))}×</span>
      </div>`,
    )
    .join('')}</div>`;
}

export function statStrip(items) {
  return readouts(items.map((item) => ({ label: item.label, value: item.value, hint: item.hint })));
}

export function emptyState({ title, hint, actionText, actionId }) {
  return `<div class="empty" data-testid="empty-state">
    <div class="mark">${esc(title)}</div>
    <p>${esc(hint || '')}</p>
    ${actionText ? `<button class="btn ghost" id="${esc(actionId || 'empty-action')}">${esc(actionText)}</button>` : ''}
  </div>`;
}

export function errorBox(message, retryId = 'retry-btn') {
  return `<div class="error-box"><span>${esc(message)}</span><button class="btn sm ghost" id="${esc(retryId)}">重试</button></div>`;
}

export function table({ columns, rows, rowId, empty }) {
  if (!rows || rows.length === 0) return emptyState(empty || { title: '没有匹配记录', hint: '调整筛选条件后再试。' });
  const head = columns.map((col) => `<th>${esc(col.label)}</th>`).join('');
  const body = rows
    .map(
      (row) => `<tr data-id="${esc(row.id)}" ${rowId ? `id="${esc(rowId(row))}"` : ''}>
        ${columns.map((col) => `<td class="${esc(col.className || '')}">${col.render ? col.render(row) : esc(row[col.key])}</td>`).join('')}
      </tr>`,
    )
    .join('');
  return `<div class="table-wrap"><table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

export function drawer({ title, subtitle, body, footer }) {
  const backdrop = document.createElement('div');
  backdrop.className = 'drawer-backdrop';
  backdrop.innerHTML = `<aside class="drawer" role="dialog" aria-modal="true">
    <div class="drawer-head">
      <div>
        <h2>${esc(title)}</h2>
        <div class="mono muted">${esc(subtitle || '')}</div>
      </div>
      <button class="btn sm ghost" data-close>关闭</button>
    </div>
    <div class="drawer-body">${body}</div>
    ${footer ? `<div class="drawer-foot">${footer}</div>` : ''}
  </aside>`;
  const close = () => {
    backdrop.remove();
    document.removeEventListener('keydown', onKey);
  };
  const onKey = (event) => {
    if (event.key === 'Escape') close();
  };
  backdrop.addEventListener('click', (event) => {
    if (event.target === backdrop || event.target.closest('[data-close]')) close();
  });
  document.addEventListener('keydown', onKey);
  document.body.appendChild(backdrop);
  return { root: backdrop, close };
}

export function kv(items) {
  return `<dl class="kv">${items
    .map(([key, value]) => `<dt>${esc(key)}</dt><dd>${value === undefined || value === null ? '—' : value}</dd>`)
    .join('')}</dl>`;
}

export function barList(items, tone = '') {
  const max = Math.max(1, ...items.map((item) => item.count));
  return `<div class="bars">${items
    .map(
      (item) => `<div class="bar-row">
        <span>${esc(item.label)}</span>
        <span class="bar-track"><span class="bar-fill ${esc(tone)}" style="width:${Math.round((item.count / max) * 100)}%"></span></span>
        <span class="mono">${esc(item.count)}</span>
      </div>`,
    )
    .join('')}</div>`;
}

export function trendChart(points) {
  const max = Math.max(1, ...points.map((point) => point.sessions ?? point.runs ?? 0));
  return `<div class="waterfall">${points
    .map(
      (point) => `<div class="wf-row">
        <span class="stamp">${esc(String(point.date || point.label || '').slice(-5))}</span>
        <span class="wf-band"><span class="wf-seg board" style="width:${Math.max(2, Math.round(((point.sessions ?? point.runs ?? 0) / max) * 100))}%"></span></span>
        <span class="num">${esc(point.sessions ?? point.runs ?? 0)}</span>
      </div>`,
    )
    .join('')}</div>`;
}

export function field({ label, name, type = 'text', value = '', placeholder = '', options = null, attrs = '' }) {
  const control = options
    ? `<select name="${esc(name)}" id="${esc(name)}" ${attrs}>${options
        .map((opt) => `<option value="${esc(opt.value)}" ${String(opt.value) === String(value) ? 'selected' : ''}>${esc(opt.label)}</option>`)
        .join('')}</select>`
    : `<input name="${esc(name)}" id="${esc(name)}" type="${esc(type)}" value="${esc(value)}" placeholder="${esc(placeholder)}" ${attrs} />`;
  return `<div class="field"><label for="${esc(name)}">${esc(label)}</label>${control}</div>`;
}

/** 身份片段轨道：可归属片段与待处理片段分段着色，hover 显示归属与原因。 */
export function fragmentTrack(fragments) {
  if (!fragments || fragments.length === 0) return '<span class="muted mono">（未切分）</span>';
  const chips = fragments
    .map((fragment) => {
      const tone = fragment.status === 'pending' ? 'pending' : 'ok';
      const who = fragment.actor
        ? fragment.actor
        : fragment.candidates && fragment.candidates.length
          ? `候选 ${fragment.candidates.join('/')}`
          : '匿名';
      const title = `${fragment.session_id}#${fragment.fragment_no} · ${fragment.resolution_label}${fragment.reason ? ` · ${fragment.reason}` : ''}`;
      const events = (fragment.events || []).join(' › ');
      return `<span class="frag ${tone}" title="${esc(title)}"><b>${esc(who)}</b><i>${esc(events)}</i></span>`;
    })
    .join('<span class="arrow">|</span>');
  return `<div class="frag-track">${chips}</div>`;
}

/** 重建前后差异：before → after，正负变化用色区分。 */
export function diffTable(diff, labels = {}) {
  if (!diff) return emptyState({ title: '还没有重建差异', hint: '发起一次挖掘或身份重建后生成。' });
  const rows = [
    ['fragments_total', '片段总数'],
    ['attributable_fragments', '可归属片段'],
    ['pending_fragments', '待处理片段'],
    ['pending_events', '待处理事件'],
    ['identities_total', '可归属身份'],
    ['rec_count', '推荐条数'],
  ]
    .filter(([key]) => diff.before && key in diff.before)
    .map(([key, label]) => {
      const before = Number(diff.before?.[key] ?? 0);
      const after = Number(diff.after?.[key] ?? 0);
      const delta = Number(diff.delta?.[key] ?? after - before);
      const tone = delta > 0 ? 'ok' : delta < 0 ? 'danger' : 'muted';
      return `<div class="diff-row">
        <span>${esc(label)}</span>
        <span class="mono">${esc(before)} → ${esc(after)}</span>
        <span class="badge ${tone}">${delta > 0 ? '+' : ''}${esc(delta)}</span>
      </div>`;
    });
  return `<div class="diff-table">${rows.join('')}</div>`;
}

/** 待处理片段列表：片段号 / 会话 / 原因 / 候选账号，绝不出现推荐动作。 */
export function pendingList(items, { showSession = true } = {}) {
  if (!items || items.length === 0) {
    return emptyState({ title: '待处理区为空', hint: '没有无法归属的匿名片段。' });
  }
  return `<div class="hold-list">${items
    .map(
      (item) => `<div class="hold-item">
        <div class="hold-head">
          <span class="badge pending">待处理</span>
          ${showSession ? `<span class="mono">${esc(item.session_id)} #${esc(item.fragment_no)}</span>` : ''}
          <span class="mono muted">下标 ${esc(item.start_index)}–${esc(item.end_index)}</span>
        </div>
        <div class="hold-reason">${esc(item.reason || '无法唯一归属')}</div>
        <div class="mono muted">候选：${esc((item.candidates || []).join(' / ') || '无')} · 事件 ${esc(item.event_count)} 个</div>
        ${item.events ? `<div class="mono muted hold-events">${esc((item.events || []).join(' › '))}</div>` : ''}
      </div>`,
    )
    .join('')}</div>`;
}

export function toast(message, tone = '') {
  const stack = document.getElementById('toast-stack');
  const node = document.createElement('div');
  node.className = `toast ${tone}`;
  node.textContent = message;
  stack.appendChild(node);
  setTimeout(() => node.remove(), 3600);
}

export function errorMessage(err) {
  if (!err) return '未知错误';
  if (err.code === 'FORBIDDEN') return '当前角色没有这个操作的权限';
  return err.message || '请求失败';
}
