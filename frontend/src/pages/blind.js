import {blindReviews, datasets} from '../api/entities.js';
import {downloadBlind} from '../api/client.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {table} from '../components/common/table.js';
import {BlindStatus, CandidateStatus} from '../constants/enums.js';
import {esc, date, notify, fmt} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';

const badge = v => '<span class="blind-badge ' + esc(v) + '">' + esc(BlindStatus[v] || v) + '</span>';
const candBadge = v => '<span class="cand-badge ' + esc(v) + '">' + esc(CandidateStatus[v] || v) + '</span>';
const pct = v => (v === null || v === undefined ? '—' : (v * 100).toFixed(1) + '%');

function sealPanel(r, isAdmin) {
 const s = r.seal || {};
 const rows = [
  ['封存批次指纹', esc(s.dataset_checksum ? s.dataset_checksum.slice(0, 24) + '…' : '—')],
  ['样本点数 / 调参 / 验收', esc((s.dataset_rows ?? '—') + ' / ' + (s.tuning_count ?? '—') + ' / ' + (s.holdout_count ?? '—'))],
  ['验收占比 / 随机种子', esc(((s.holdout_ratio ?? 0) * 100).toFixed(0) + '% / ' + s.seed)],
  ['参考口径（冻结）', 'eps ' + esc(s.reference_params?.eps_m) + 'm · min ' + esc(s.reference_params?.min_points)],
  ['分组登记指纹', esc(s.group_fingerprint ? s.group_fingerprint.slice(0, 24) + '…' : '—')],
  ['封存时间', date(s.frozen_at)],
 ];
 return '<section class="panel"><div class="section-title"><h2>封存验收集</h2><span>' + (s.groups_hidden ? '逐点分组对分析员隐藏' : '逐点参考分组已登记') + '</span></div>'
  + '<table class="kv-table"><tbody>' + rows.map(([k, v]) => '<tr><th>' + k + '</th><td>' + v + '</td></tr>').join('') + '</tbody></table>'
  + (s.groups_hidden
    ? '<p class="warning">验收标签与逐点分组在揭榜前封存：直接查询、下载均不返回，分析员只能在调参集上工作。</p>'
    : '<p class="hint">负责人可核查逐点分组；同批点只属一个集合，不跨调参与验收两集。</p>')
  + (isAdmin && s.groups ? '<details class="group-details"><summary>查看逐点参考分组（负责人 · ' + s.groups.length + ' 点）</summary>' + table(
     [{key: 'defect_id', label: '缺陷编号'}, {key: 'group', label: '所属集合'}, {key: 'reference_label', label: '参考标签（-1=孤立）'}],
     s.groups.map(g => ({defect_id: g[0], group: g[1] === 'holdout' ? '验收集' : '调参集', reference_label: g[2]}))) + '</details>' : '')
  + '</section>';
}

function candidatePanel(r, canWrite, isAdmin) {
 const cands = r.candidates || [];
 const sealed = r.status === 'sealed';
 const revealed = r.status === 'revealed';
 const head = '<section class="panel"><div class="section-title"><h2>冻结候选参数</h2><span>' + cands.length + ' 个候选 · 发起时冻结，拒绝换参</span></div>';
 const cols = [
  {key: 'alias', label: '候选'}, {key: 'params', label: '冻结参数 eps / min'}, {key: 'status', label: '状态'},
 ];
 const rows = cands.map(c => ({
  alias: c.alias,
  params: c.params.eps_m + 'm / ' + c.params.min_points,
  status: CandidateStatus[c.status] || c.status,
  _raw: c,
 }));
 let body = rows.length
  ? '<div class="table-wrap"><table><thead><tr>' + cols.map(c => '<th>' + esc(c.label) + '</th>').join('') + '</tr></thead><tbody>'
    + rows.map(row => '<tr><td>' + esc(row.alias) + '</td><td>' + esc(row.params) + '</td><td>' + esc(row.status) + '</td></tr>').join('')
    + '</tbody></table></div>'
  : '<div class="empty">尚无候选。在左侧按调参集结果选定参数后登记冻结。</div>';
 // 揭榜前：即便候选已算完，也只显示封存状态，不显示任何指标与逐点结果
 if (!revealed) {
  body += '<p class="warning">候选已' + (cands.some(c => c.evaluated) ? '完成评估但结果封存' : '冻结') + '：验收指标与逐点判定整组揭榜后才公开，半组结果不公开。</p>';
 }
 // 揭榜后：整组公开指标对比
 if (revealed) {
  const mcols = [
   {key: 'alias', label: '候选'}, {key: 'pair', label: '带内点对错误'}, {key: 'pair_acc', label: '点对一致率'},
   {key: 'iso', label: '孤立点误判'}, {key: 'iso_acc', label: '孤立判定率'}, {key: 'overall', label: '总一致率'},
  ];
  const mrows = cands.filter(c => c.result).map(c => {
   const m = c.result.metrics;
   return {alias: c.alias, pair: m.in_band_pair_errors + ' / ' + m.in_band_pair_total, pair_acc: pct(m.in_band_pair_accuracy),
     iso: m.isolated_misjudge + ' / ' + m.isolated_total, iso_acc: pct(m.isolated_accuracy), overall: pct(m.overall_accuracy)};
  });
  body += '<div class="section-title" style="margin-top:24px"><h2>验收集独立评估（整组公开）</h2><span>仅基于封存验收集点</span></div>' + table(mcols, mrows, '无完成候选。');
 }
 // 调参阶段登记表单（仅封存期、可写角色）
 const form = sealed && canWrite
  ? '<form id="candidate-form" class="candidate-form"><div class="parameter-grid"><label>候选别名<input name="alias" required maxlength="80" placeholder="例如：收紧半径方案"></label></div>'
  + '<div class="parameter-grid"><label>邻域半径 eps_m（20~400）<input name="eps_m" type="number" step="any" min="20" max="400" value="100" required></label>'
  + '<label>最少点数 min_points（3~12 整数）<input name="min_points" type="number" step="1" min="3" max="12" value="5" required></label></div>'
  + '<button class="primary" type="submit">冻结该候选参数 ↗</button><p class="hint">冻结后参数与样本不可更换；同一盲评内参数不可重复登记。</p></form>'
  : (sealed ? '<p class="hint">观察员仅可查看；候选登记需分析员或负责人。</p>' : '');
 return head + body + form + '</section>';
}

function pointsPanel(r) {
 if (r.status !== 'revealed') return '';
 const cands = (r.candidates || []).filter(c => c.result);
 if (!cands.length) return '';
 const first = cands[0];
 const cols = [
  {key: 'defect_id', label: '验收集缺陷点'}, {key: 'ref', label: '参考标签'},
  ...cands.map((c, i) => ({key: 'c' + i, label: c.alias + ' 判定'})),
  {key: 'flag', label: '孤立点误判候选'},
 ];
 const byId = {};
 first.result.points.forEach(p => { byId[p.defect_id] = {defect_id: p.defect_id, ref: p.reference_label === -1 ? '孤立' : '带' + p.reference_label, cells: {}, flag: []}; });
 cands.forEach((c, i) => c.result.points.forEach(p => {
  byId[p.defect_id].cells['c' + i] = p.candidate_label === -1 ? '孤立' : '带' + p.candidate_label;
  if (p.isolated_misjudge) byId[p.defect_id].flag.push(c.alias);
 }));
 const rows = Object.values(byId).map(row => ({...row, ...row.cells, flag: row.flag.join('、') || '—'}));
 return '<section class="panel"><div class="section-title"><h2>逐点验收结果</h2><span>' + rows.length + ' 个验收集点 · 来源：封存参考标签 vs 各冻结候选</span></div>'
  + table(cols, rows) + '</section>';
}

export async function blindPage(id) {
 if (!workspace.datasets) workspace.datasets = await datasets.list();
 const list = await blindReviews.list();
 workspace.blindList = list;
 const r = id ? await blindReviews.get(id) : (list[0] || null);
 if (!r) {
  return heading('06 / BLIND REVIEW', '盲评揭榜 · 独立验收', '把调参与验收分开：发起时冻结候选参数与样本，验收集标签对分析员隐藏，揭榜整组公开。')
   + '<div class="empty">尚无盲评批次。先在下方建立盲评，封存调参集与验收集。</div>' + createForm();
 }
 workspace.selectedBlind = r.id;
 const isAdmin = session.user.role === 'admin';
 const canWrite = session.user.role !== 'viewer';
 const switcher = '<div class="result-selector"><label>盲评批次<select id="select-blind">'
  + list.map(x => '<option value="' + x.id + '" ' + (x.id === r.id ? 'selected' : '') + '>' + esc(x.name) + ' · ' + esc(BlindStatus[x.status] || x.status) + '</option>').join('')
  + '</select></label>' + badge(r.status) + '<span>来源：' + esc(r.creator_email || '—') + ' · ' + date(r.created_at) + '</span>'
  + (r.revealed_at ? '<span>揭榜 ' + date(r.revealed_at) + '</span>' : '') + '</div>';
 const revealActions = isAdmin
  ? (r.status === 'sealed'
    ? '<button class="primary" id="reveal-btn" ' + (!(r.candidates || []).length ? 'disabled' : '') + '>发起揭榜（整组评估并公开） ↗</button>'
    : r.status === 'revealing'
      ? '<button class="button" id="resume-btn">中断恢复（已算候选不重复计分） ↻</button>'
      : '<button class="button" id="export-btn">下载整组揭榜结果 ↓</button>')
  : (r.status === 'revealed' ? '<button class="button" id="export-btn">下载整组揭榜结果 ↓</button>' : '<span class="hint">揭榜由业务负责人发起</span>');
 const rules = '<section class="method-sheet compact"><p class="eyebrow">BLIND PROTOCOL</p><h3>盲评规则</h3><ul>'
  + ['发起即冻结候选参数与样本，同批点不跨调参、验收两集。',
     '揭榜前验收标签与逐点结果对分析员隐藏，直接查询、下载均不泄露。',
     '揭榜仅评已冻结候选并整组公开；开始后拒绝换参、换样或再用该验收集调参。',
     '并发揭榜沿用同次评估；中断恢复只补算未完成候选，重试不重复计分，半组不公开。']
  .map(v => '<li>' + esc(v) + '</li>').join('') + '</ul>' + revealActions + '</section>';
 return heading('06 / BLIND REVIEW', '盲评揭榜 · 独立验收', '参数选择不再只盯同批结果：封存验收集独立评分，输出带内点对错误与孤立点误判。')
  + switcher
  + '<div class="two-columns"><div>' + sealPanel(r, isAdmin) + candidatePanel(r, canWrite, isAdmin) + pointsPanel(r) + '</div>' + rules + '</div>'
  + (list.length <= 1 ? createForm(true) : '');
}

function createForm(collapsed = false) {
 const canWrite = session.user.role !== 'viewer';
 const opts = (workspace.datasets || []).map(d => '<option value="' + d.id + '">' + esc(d.name) + ' · ' + d.rows + ' 条</option>').join('');
 return '<section class="panel ' + (collapsed ? 'extra-create' : '') + '"><div class="section-title"><h2>建立盲评（发起即封存）</h2><span>调参 / 验收按批次分开</span></div>'
  + '<form id="blind-form"><label>盲评名称<input name="name" required minlength="2" maxlength="160" placeholder="例如：11 月参数独立验收盲评"></label>'
  + '<label>数据批次<select name="dataset_id" required>' + opts + '</select></label>'
  + '<div class="parameter-grid"><label>验收集占比（10%~50%）<input name="holdout_ratio" type="number" step="0.05" min="0.1" max="0.5" value="0.3" required></label>'
  + '<label>分组随机种子（可复现）<input name="seed" type="number" step="1" value="1011" required></label></div>'
  + '<button class="primary" type="submit" ' + (!canWrite || !(workspace.datasets || []).length ? 'disabled' : '') + '>封存分组并建立盲评 ↗</button>'
  + '<p class="hint">按点登记参考分组并冻结样本指纹；分析员建立后看不到验收集标签。原试验工作台与结果复核不受影响，可继续使用。</p></form></section>';
}

export function bindBlind(refresh) {
 document.querySelector('#select-blind')?.addEventListener('change', e => {
  workspace.selectedBlind = Number(e.target.value); location.hash = '#/blind/' + e.target.value;
 });
 submit(document.querySelector('#blind-form'), async f => {
  await blindReviews.create({name: f.get('name'), dataset_id: Number(f.get('dataset_id')),
   holdout_ratio: Number(f.get('holdout_ratio')), seed: Number(f.get('seed'))});
  notify('盲评已建立，调参集与验收集已封存'); await refresh();
 });
 submit(document.querySelector('#candidate-form'), async f => {
  const id = workspace.selectedBlind;
  await blindReviews.addCandidate(id, {alias: f.get('alias'),
   params: {eps_m: Number(f.get('eps_m')), min_points: Number(f.get('min_points'))}});
  notify('候选参数已冻结，揭榜前不可更换'); await refresh();
 });
 document.querySelector('#reveal-btn')?.addEventListener('click', async () => {
  try {
   await blindReviews.reveal(workspace.selectedBlind);
   notify('揭榜完成，已整组公开评估结果'); await refresh();
  } catch (e) { notify(e.message, true); }
 });
 document.querySelector('#resume-btn')?.addEventListener('click', async () => {
  try {
   await blindReviews.resume(workspace.selectedBlind);
   notify('恢复完成，已算候选未重复计分'); await refresh();
  } catch (e) { notify(e.message, true); }
 });
 document.querySelector('#export-btn')?.addEventListener('click', () =>
  downloadBlind(workspace.selectedBlind).catch(e => notify(e.message, true)));
}
