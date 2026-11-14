import { api } from '../api.js';
import { ENUMS, badgeTone, can, reviewTone } from '../config.js';
import {
  diffTable,
  drawer,
  errorBox,
  errorMessage,
  esc,
  field,
  fmtPct,
  fmtTime,
  kv,
  pendingList,
  table,
  toast,
  track,
} from '../ui.js';

const state = { runId: '', status: 'pending', priority: '', module: '', q: '' };

function runRecoveryNotice(context) {
  const run = context || {};
  if (run.failed_run) {
    const recovered = run.recovered_run || {};
    const restored = run.restored || {};
    return `<div class="notice warn" data-testid="recovery-notice">
      <span class="badge high">已回滚</span>
      <span>最近一次重建失败（${esc(run.failed_run.run_no)}：${esc(run.failed_run.error || '未知错误')}），
        已恢复旧版推荐名单 ${esc(restored.restored ?? 0)} 条
        （原运行 ${esc(recovered.run_no || run.recovered_from_run || '—')}，覆盖 ${esc(recovered.coverage_pct ?? 0)}%）。
        会话与待处理片段均未丢失。</span>
    </div>`;
  }
  return '';
}

function pendingPanel(context) {
  const pending = (context || {}).pending || {};
  const samples = (context || {}).pending_samples || [];
  const reasons = (pending.by_reason || [])
    .map((item) => `<div class="diff-row"><span>${esc(item.label)}</span><span class="mono">${esc(item.count)} 段</span><span class="badge pending">排除</span></div>`)
    .join('');
  return `<section class="panel" data-testid="pending-panel">
    <div class="panel-head">
      <h2>待处理片段</h2>
      <span class="meta">hold · 不参与推荐</span>
    </div>
    <div class="panel-body">
      <div class="notice warn"><span class="badge pending">${esc(pending.pending_fragments ?? 0)} 段</span>
        <span>${esc(pending.pending_events ?? 0)} 个事件 / ${esc(pending.pending_sessions ?? 0)} 条会话无法唯一归属，
        已排除在模式与推荐之外；记录保留，等待人工确认身份。</span></div>
      ${reasons ? `<div class="diff-table" style="margin-top:10px">${reasons}</div>` : ''}
      ${samples.length ? `<h3 style="font-size:0.95rem;margin:12px 0 2px">待处理明细（前 20）</h3>${pendingList(samples)}` : ''}
    </div>
  </section>`;
}

function diffPanel(context) {
  const diff = (context || {}).diff;
  const built = (context || {}).built;
  return `<section class="panel" data-testid="diff-panel">
    <div class="panel-head">
      <h2>重建前后差异</h2>
      <span class="meta">${esc((context || {}).run_no || '—')}</span>
    </div>
    <div class="panel-body">
      ${diffTable(diff)}
      ${built ? `<div class="legend" style="margin-top:10px"><span>本次重建：新建 ${esc(built.created)} · 复用 ${esc(built.updated)} 段</span></div>` : ''}
    </div>
  </section>`;
}

function openRecommendation(view, ctx, item) {
  const canReview = can(ctx.user.role, 'recommendation:review');
  const { root, close } = drawer({
    title: `推荐下一步：${item.next_label}`,
    subtitle: `${item.user_id} · ${item.next_module_label} 模块 · 分数 ${item.score.toFixed(3)}`,
    body: `${kv([
      ['归属身份', `<span class="mono">${esc(item.user_id)}</span>`],
      ['最近后缀', `<span class="mono">${esc(item.suffix.join(' › ') || '—')}</span>`],
      ['推荐事件', `<span class="mono hl">${esc(item.next_event)}</span>`],
      ['下一步名称', esc(item.next_label)],
      ['模块', esc(item.next_module_label)],
      ['置信度', esc(item.confidence.toFixed(4))],
      ['提升度', esc(item.lift.toFixed(4))],
      ['分数（置信度×提升度）', `<span class="mono hl">${esc(item.score.toFixed(4))}</span>`],
      ['优先级', `<span class="badge ${badgeTone(item.priority)}">${esc(ENUMS.priority[item.priority] || item.priority)}</span>`],
      ['复核状态', `<span class="badge ${reviewTone(item.status)}">${esc(ENUMS.reviewStatus[item.status] || item.status)}</span>`],
      ['复核人', esc(item.reviewer || '—')],
      ['复核备注', esc(item.review_note || '—')],
      ['生成时间', fmtTime(item.created_at)],
    ])}
    <h3 style="font-size:0.95rem;margin:6px 0 2px">身份最近事件</h3>
    ${track(item.suffix_detail, 10)}`,
    footer: canReview
      ? `<form id="review-form" style="display:grid;gap:10px">
          ${field({ label: '复核结论', name: 'status', value: item.status, options: Object.entries(ENUMS.reviewStatus).map(([value, label]) => ({ value, label })) })}
          ${field({ label: '备注', name: 'note', value: item.review_note || '', placeholder: '这一条为什么值得推给运营' })}
          <button class="btn accent" type="submit">提交复核</button>
        </form>`
      : '<span class="mono muted">只读角色可以查看推荐（用户 ID 已脱敏），不能复核</span>',
  });
  root.querySelector('#review-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      await api.patch(`/api/recommendations/${item.id}/review`, {
        status: form.status.value,
        note: form.note.value.trim() || null,
      });
      toast('复核结果已保存');
      close();
      await recommendationsPage.render(view, ctx);
    } catch (err) {
      toast(errorMessage(err), 'err');
    }
  });
}

export const recommendationsPage = {
  async render(view, ctx) {
    state.runId = ctx.query.run_id || state.runId;
    let data;
    let runs;
    let context;
    try {
      runs = await api.get('/api/mine-runs', { limit: 10 });
      context = await api.get('/api/recommendations/context', {});
      if (!state.runId) {
        if (context.serving_run_id) state.runId = String(context.serving_run_id);
        else if (runs.items.length) state.runId = String(runs.items[0].id);
      }
      data = await api.get('/api/recommendations', {
        run_id: state.runId,
        status: state.status,
        priority: state.priority,
        module: state.module,
        q: state.q,
        limit: 60,
      });
      context = await api.get('/api/recommendations/context', { run_id: state.runId });
    } catch (err) {
      view.innerHTML = errorBox(errorMessage(err));
      view.querySelector('#retry-btn')?.addEventListener('click', () => recommendationsPage.render(view, ctx));
      return;
    }
    const activeRun = runs.items.find((item) => String(item.id) === String(state.runId)) || runs.items[0];
    const servingNote = (context || {}).failed_run
      ? `<span class="meta">正在供数：${esc(((context || {}).recovered_run || {}).run_no || '旧版')}</span>`
      : '';
    const pendingInfo = (context || {}).pending || {};
    view.innerHTML = `
      ${runRecoveryNotice(context)}
      <section class="panel">
        <div class="panel-head">
          <h2>下一步推荐名单</h2>
          <div style="display:flex;gap:8px;align-items:center">
            <span class="meta">${data.total} 条 · 覆盖率 ${fmtPct(activeRun ? activeRun.coverage_pct : 0)} · 待处理 ${esc(pendingInfo.pending_fragments ?? 0)} 段</span>${servingNote}
            ${can(ctx.user.role, 'export:read') ? '<button class="btn sm ghost" id="export-fragments">导出待处理片段</button>' : ''}
            ${can(ctx.user.role, 'export:read') ? '<button class="btn sm ghost" id="export-recs">导出 CSV</button>' : ''}
          </div>
        </div>
        <div class="panel-body">
          <div class="filter-bar" id="rec-filters">
            ${field({ label: '搜索', name: 'q', value: state.q, placeholder: '身份 ID / 事件码' })}
            ${field({ label: '挖掘运行', name: 'run_id', value: state.runId, options: [{ value: '', label: '全部运行' }, ...runs.items.map((run) => ({ value: run.id, label: run.run_no }))] })}
            ${field({ label: '复核状态', name: 'status', value: state.status, options: [{ value: '', label: '全部' }, ...Object.entries(ENUMS.reviewStatus).map(([value, label]) => ({ value, label }))] })}
            ${field({ label: '优先级', name: 'priority', value: state.priority, options: [{ value: '', label: '全部' }, ...Object.entries(ENUMS.priority).map(([value, label]) => ({ value, label }))] })}
            ${field({ label: '模块', name: 'module', value: state.module, options: [{ value: '', label: '全部' }, ...Object.entries(ENUMS.module).map(([value, label]) => ({ value, label }))] })}
            <div class="spacer"></div>
          </div>
        </div>
        ${table({
          rows: data.items,
          empty: { title: '该筛选下没有推荐', hint: '换一个运行或把复核状态改成「全部」。' },
          columns: [
            { key: 'user_id', label: '归属身份', className: 'mono' },
            { key: 'suffix', label: '最近后缀', render: (row) => `<span class="mono muted">${esc(row.suffix.join(' › ') || '—')}</span>` },
            { key: 'next_label', label: '推荐下一步', render: (row) => `<b>${esc(row.next_label)}</b><div class="mono muted">${esc(row.next_event)}</div>` },
            { key: 'next_module_label', label: '模块' },
            { key: 'confidence', label: '置信度', className: 'num', render: (row) => row.confidence.toFixed(3) },
            { key: 'lift', label: '提升度', className: 'num', render: (row) => row.lift.toFixed(3) },
            { key: 'score', label: '分数', className: 'num', render: (row) => `<span class="hl">${row.score.toFixed(3)}</span>` },
            { key: 'priority', label: '优先级', render: (row) => `<span class="badge ${badgeTone(row.priority)}">${esc(ENUMS.priority[row.priority] || row.priority)}</span>` },
            { key: 'status', label: '复核', render: (row) => `<span class="badge ${reviewTone(row.status)}">${esc(ENUMS.reviewStatus[row.status] || row.status)}</span>` },
          ],
        })}
      </section>
      <div class="grid-2" style="margin-top:16px">
        ${pendingPanel(context)}
        ${diffPanel(context)}
      </div>`;
    const filters = view.querySelector('#rec-filters');
    filters?.addEventListener('change', () => {
      state.q = filters.querySelector('[name=q]').value.trim();
      state.runId = filters.querySelector('[name=run_id]').value;
      state.status = filters.querySelector('[name=status]').value;
      state.priority = filters.querySelector('[name=priority]').value;
      state.module = filters.querySelector('[name=module]').value;
      recommendationsPage.render(view, ctx);
    });
    filters?.querySelector('[name=q]')?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        state.q = event.target.value.trim();
        recommendationsPage.render(view, ctx);
      }
    });
    view.querySelectorAll('table.data tbody tr').forEach((row) => {
      row.addEventListener('click', () => {
        const item = data.items.find((entry) => String(entry.id) === row.dataset.id);
        if (item) openRecommendation(view, ctx, item);
      });
    });
    view.querySelector('#export-recs')?.addEventListener('click', async () => {
      try {
        await api.download(`/api/exports/recommendations.csv${state.runId ? `?run_id=${state.runId}` : ''}`, 'recommendations.csv');
        toast('已开始下载推荐名单');
      } catch (err) {
        toast(errorMessage(err), 'err');
      }
    });
    view.querySelector('#export-fragments')?.addEventListener('click', async () => {
      try {
        await api.download('/api/exports/fragments.csv?status=pending', 'identity-fragments.csv');
        toast('已开始下载待处理片段');
      } catch (err) {
        toast(errorMessage(err), 'err');
      }
    });
  },
};

export default recommendationsPage;
