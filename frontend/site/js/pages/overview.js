import { api } from '../api.js';
import { ENUMS } from '../config.js';
import { barList, diffTable, emptyState, errorBox, errorMessage, esc, fmtPct, fmtTime, ladder, pendingList, readouts, waterfall } from '../ui.js';

function identityNotice(data) {
  const identity = data.identity || {};
  const pending = data.pending_identity || {};
  if (!identity.fragments_total) return '';
  if (!pending.pending_fragments) {
    return `<div class="notice" data-testid="identity-notice"><span class="badge ok">身份已切分</span>
      可归属片段 ${esc(identity.attributable_fragments)} 条 / ${esc(identity.identities_total)} 个身份；无待处理片段。</div>`;
  }
  const reasons = (pending.by_reason || [])
    .map((item) => `${esc(item.label)} ${esc(item.count)}`)
    .join(' · ');
  return `<div class="notice warn" data-testid="identity-notice"><span class="badge pending">待处理 ${esc(pending.pending_fragments)} 段</span>
    <span>${esc(pending.pending_events)} 个事件 / ${esc(pending.pending_sessions)} 条会话无法归属，已留在待处理区，未参与模式与推荐。${reasons}</span></div>`;
}

export const overviewPage = {
  async render(view, ctx) {
    view.innerHTML = '<div class="empty"><div class="mark">采样中…</div></div>';
    let data;
    let patterns;
    try {
      data = await api.get('/api/overview');
      patterns = await api.get('/api/patterns', { run_id: data.serving_run_id || (data.latest_run ? data.latest_run.id : ''), limit: 8 });
    } catch (err) {
      view.innerHTML = errorBox(errorMessage(err));
      view.querySelector('#retry-btn')?.addEventListener('click', () => overviewPage.render(view, ctx));
      return;
    }
    const run = data.latest_run;
    const serving = data.serving || run;
    const identity = data.identity || {};
    const diff = data.rebuild_diff;
    const recovered = data.recovered_run;
    view.innerHTML = `
      ${recovered
        ? `<div class="notice warn" data-testid="recovery-notice"><span class="badge high">已回滚</span>
            <span>最近一次挖掘失败，当前展示的是旧版运行 ${esc(recovered.run_no)} 的名单
              （${esc(recovered.rec_count)} 条推荐 / 覆盖率 ${fmtPct(recovered.coverage_pct)}）；源会话与待处理片段未丢失。</span></div>`
        : ''}
      ${readouts([
        { label: '会话总量', value: data.sequences_total, unit: '条', hint: `均值 ${data.avg_events} 事件 / 会话` },
        { label: '推荐覆盖率', value: fmtPct(serving ? serving.coverage_pct : 0), hint: serving ? `${serving.covered_users}/${serving.active_users} 位可归属身份` : '尚未挖掘', tone: 'hot' },
        { label: '模式库', value: data.patterns_total, unit: '条', hint: serving ? `min_sup ${serving.min_support_pct}% · 最长 ${serving.max_len} 步` : '尚未挖掘' },
        { label: '待处理片段', value: (data.pending_identity || {}).pending_fragments || 0, unit: '段', hint: `不可归属 · 不参与推荐`, tone: 'cool' },
      ])}
      ${identityNotice(data)}
      <div class="grid-2">
        <section class="panel">
          <div class="panel-head"><h2>信号瀑布</h2><span class="meta">12 buckets · modules</span></div>
          <div class="panel-body">${waterfall(data.waterfall, data.modules)}</div>
        </section>
        <section class="panel">
          <div class="panel-head"><h2>模块事件量</h2><span class="meta">7d · events</span></div>
          <div class="panel-body">
            ${data.modules.length ? barList(data.modules) : emptyState({ title: '还没有埋点事件', hint: '管理员先登记事件字典。' })}
            <div class="legend" style="margin-top:12px"><span>转化会话 ${esc(data.converted_total)} / ${esc(data.sequences_total)}（${fmtPct(data.conversion_pct)}）</span></div>
          </div>
        </section>
      </div>
      <div class="grid-2">
        <section class="panel">
          <div class="panel-head"><h2>身份切分</h2><span class="meta">fragments · identities</span></div>
          <div class="panel-body">
            ${identity.fragments_total
              ? `<dl class="kv">
                  <dt>片段总数</dt><dd>${esc(identity.fragments_total)} 段</dd>
                  <dt>可归属</dt><dd>${esc(identity.attributable_fragments)} 段 · ${esc(identity.identities_total)} 个身份</dd>
                  <dt>待处理</dt><dd>${esc(identity.pending_fragments)} 段 · ${esc(identity.pending_events)} 事件</dd>
                  <dt>源会话</dt><dd>${esc(identity.session_owners_total)} 个记录账号 / ${esc(data.sequences_total)} 条会话</dd>
                </dl>
                ${(data.pending_samples || []).length ? `<h3 style="font-size:0.95rem;margin:12px 0 2px">待处理片段（前 5）</h3>${pendingList(data.pending_samples)}` : ''}`
              : emptyState({ title: '还没有身份片段', hint: '发起一次挖掘会自动按身份切分会话。' })}
          </div>
        </section>
        <section class="panel">
          <div class="panel-head"><h2>重建前后差异</h2><span class="meta">before → after</span></div>
          <div class="panel-body">
            ${diff ? diffTable(diff) : emptyState({ title: '还没有重建记录', hint: '发起一次挖掘后生成。' })}
            ${(run && (run.built || run.restored))
              ? `<div class="legend" style="margin-top:12px">
                  <span>本次重建：新建 ${esc((run.built || {}).created ?? 0)} · 复用 ${esc((run.built || {}).updated ?? 0)} 段</span>
                  ${run.restored ? `<span class="hl">已恢复旧版推荐 ${esc(run.restored.restored)} 条</span>` : ''}
                </div>`
              : ''}
          </div>
        </section>
      </div>
      <div class="grid-2">
        <section class="panel">
          <div class="panel-head"><h2>模式支持度 TOP 8</h2><span class="meta">support / confidence</span></div>
          <div class="panel-body">
            ${ladder(patterns.items)}
            <div class="track" style="margin-top:10px">${(patterns.items || [])
              .slice(0, 3)
              .map((item) => `<span class="chip board">${esc(item.seq_key.split('>')[0])} › … <i>${esc(item.length)} 步</i></span>`)
              .join('')}</div>
          </div>
        </section>
        <section class="panel">
          <div class="panel-head"><h2>最近一次挖掘</h2><span class="meta">latest run</span></div>
          <div class="panel-body">
            ${
              run
                ? `<dl class="kv">
                    <dt>运行号</dt><dd class="mono">${esc(run.run_no)}</dd>
                    <dt>状态</dt><dd>${esc(ENUMS.mineStatus[run.status] || run.status)}${run.status === 'failed' ? ' · 已回滚，沿用旧版' : ''}</dd>
                    <dt>开始</dt><dd>${fmtTime(run.started_at)}</dd>
                    <dt>本次模式</dt><dd>${esc(run.pattern_count)} 条 · top 支持度 ${fmtPct(run.top_support_pct)}</dd>
                    <dt>本次推荐</dt><dd>${esc(run.rec_count)} 条 · 覆盖 ${esc(run.covered_users)}/${esc(run.active_users)} 身份</dd>
                    ${serving && serving.id !== run.id
                      ? `<dt>正在供数</dt><dd class="mono">${esc(serving.run_no)} · ${esc(serving.rec_count)} 条推荐 · 覆盖 ${fmtPct(serving.coverage_pct)}</dd>`
                      : ''}
                  </dl>
                  <div class="legend" style="margin-top:12px">
                    <span>复核：${(data.review || []).map((item) => `${esc(item.label)} ${esc(item.count)}`).join(' · ') || '暂无'}</span>
                    <span>待复核推荐 ${esc(data.pending_total)} 条</span>
                  </div>`
                : emptyState({ title: '还没有挖掘记录', hint: '去「模式挖掘」发起一次 PrefixSpan。' })
            }
          </div>
        </section>
      </div>`;
  },
};

export default overviewPage;
