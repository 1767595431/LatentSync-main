/* Digital Human Workbench */
const API = window.APP_BASE || '';
const PAGES = {
  home: { title: '工作台', desc: '最新作品与运行状态一览' },
  works: { title: '作品库', desc: '形象 → 成品的完整合成记录' },
  avatars: { title: '形象库', desc: '数字人模板视频管理' },
  create: { title: '创建作品', desc: '选择形象，上传音频，一键合成' },
  settings: { title: '设置', desc: '服务状态与访问会话' },
};

const STATUS = {
  wait: { label: '等待', cls: 'badge-wait', tag: 'tag-wait' },
  run: { label: '合成中', cls: 'badge-run', tag: 'tag-run' },
  done: { label: '已完成', cls: 'badge-done', tag: 'tag-done' },
  error: { label: '失败', cls: 'badge-error', tag: 'tag-error' },
};

const BAKE = {
  ready: { label: '可用', cls: 'badge-done' },
  processing: { label: '转码中', cls: 'badge-bake' },
  missing: { label: '转码中', cls: 'badge-wait' },
  error: { label: '转码失败', cls: 'badge-error' },
};

let avatarCounts = { public: 0, private: 0 };
let avatarReadyCounts = { public: 0, private: 0 };
let avatarsPageMeta = { total: 0, page: 1, pages: 1 };
let avatarsList = [];
let avatarsMap = {};
let homeTasksSnapshot = [];
let worksPageItems = [];
let worksPageMeta = { total: 0, page: 1, pages: 1 };
let workFilter = 'all';
let pickedAvatarId = '';
let pollTimer = null;
let bakePollTimer = null;
let currentPage = 'home';
let homeFilter = null;
let systemStats = null;
let worksPage = 1;
let avatarTab = 'public';
let avatarPublicPage = 1;
let avatarPrivatePage = 1;
let selectedWorkIds = new Set();
let taskLogPollTimer = null;
let taskLogOpenId = null;

const PAGE_SIZE = { works: 12, avatars: 12 };
const WORK_CARD_MIN_PX = 300;
const WORK_GRID_GAP_PX = 22;
const WORK_CARD_EST_H = 360;
let lastWorksPageSize = PAGE_SIZE.works;

/** 按作品库网格可用宽高估算每页条数，尽量铺满一屏 */
function calcWorksPageSize() {
  const grid = $('#worksGrid');
  const width = Math.max(
    280,
    grid?.clientWidth || (($('.content')?.clientWidth || window.innerWidth) - 48),
  );
  const cols = Math.max(
    1,
    Math.floor((width + WORK_GRID_GAP_PX) / (WORK_CARD_MIN_PX + WORK_GRID_GAP_PX)),
  );
  const topChrome = ($('.topbar')?.offsetHeight || 72) + ($('#worksToolbar')?.offsetHeight || 48) + 160;
  const availH = Math.max(420, window.innerHeight - topChrome);
  const rows = Math.max(2, Math.floor((availH + WORK_GRID_GAP_PX) / (WORK_CARD_EST_H + WORK_GRID_GAP_PX)));
  // API 上限 100；至少两行满列
  return Math.max(cols * 2, Math.min(100, cols * rows));
}

function worksPageSize() {
  const n = calcWorksPageSize();
  PAGE_SIZE.works = n;
  return n;
}

const HOME_FILTERS = {
  'avatar-public': { label: '公共形象' },
  'avatar-private': { label: '个人形象' },
  'avatar-all': { label: '全部形象' },
  done: { label: '已完成作品' },
  run: { label: '进行中作品' },
  wait: { label: '等待中作品' },
  baking: { label: '转码处理中' },
};

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

function esc(s) {
  if (s == null) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
}

function toast(msg, type = 'info') {
  const el = document.createElement('div');
  el.className = `toast toast-${type === 'error' ? 'err' : 'ok'}`;
  el.textContent = msg;
  $('#toasts').appendChild(el);
  FX?.toastIn(el);
  const ms = type === 'error' ? 8000 : 4000;
  setTimeout(async () => {
    await FX?.toastOut(el);
    el.remove();
  }, ms);
}

async function api(path, opts = {}) {
  const timeoutMs = opts.timeoutMs ?? 20000;
  const { timeoutMs: _drop, ...fetchOpts } = opts;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(API + path, { credentials: 'same-origin', ...fetchOpts, signal: ctrl.signal });
    if ((res.headers.get('content-type') || '').includes('json')) {
      return { ok: res.ok, data: await res.json() };
    }
    return { ok: res.ok, data: res };
  } catch (e) {
    if (e?.name === 'AbortError') {
      return { ok: false, data: { code: 1, msg: '请求超时，后台任务繁忙时请稍后再试', success: false } };
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

function mediaUrl(path) {
  if (!path) return '';
  if (path.startsWith('http://') || path.startsWith('https://')) return path;
  const prefixed = window.apiUrl ? window.apiUrl(path) : (API + (path.startsWith('/') ? path : `/${path}`));
  let normalized = prefixed.replace(/\\/g, '/');
  // Idempotent encoding: callers often pass already-mediaUrl()'d paths into posterHtml.
  // Re-encoding would turn "%E6" into "%25E6" and break Chinese result thumbnails (404).
  try {
    normalized = decodeURIComponent(normalized);
  } catch (_) {
    /* keep raw if malformed */
  }
  return normalized
    .split('/')
    .map((seg, i) => (i === 0 || seg === '' ? seg : encodeURIComponent(seg)))
    .join('/');
}

function posterHtml(src, alt = '', { eager = false } = {}) {
  if (!src) {
    return `<div class="placeholder"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="4" width="20" height="16" rx="2"/></svg><span>暂无预览</span></div>`;
  }
  const loading = eager ? 'eager' : 'lazy';
  return `<img src="${esc(mediaUrl(src))}" alt="${esc(alt)}" loading="${loading}" decoding="async">`;
}

function avatarCoverPoster(av) {
  if (!av) return '';
  return avatarPreviewThumb(av) || (av.thumbnail ? mediaUrl(av.thumbnail) : '');
}

/** 成品列表封面：成品缩略图 → 形象缩略图 → 占位（不用 video 当封面） */
function resultCoverPoster(t, av) {
  if (t.result_thumbnail) return mediaUrl(t.result_thumbnail);
  return avatarCoverPoster(av || avatarForTask(t));
}

function playOverlay() {
  return `<div class="poster-overlay"></div><div class="play-fab"><svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg></div>`;
}

function formatDateTime(ts) {
  if (!ts) return '—';
  const s = String(ts).trim();
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/.test(s)) return s.slice(0, 19);
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s.slice(0, 19);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function renderPager(el, page, pages, total, pageSize) {
  if (!el) return;
  el.classList.remove('hidden');
  const p = Math.max(1, page || 1);
  const pg = Math.max(1, pages || 1);
  const key = `${p}|${pg}|${total}|${pageSize}`;
  if (el.dataset.pagerKey === key) return;
  el.dataset.pagerKey = key;
  el.innerHTML = `
    <button type="button" class="btn btn-ghost btn-sm" data-page="${p - 1}" ${p > 1 ? '' : 'disabled'}>上一页</button>
    <span class="pager-label">第 ${p} / ${pg} 页 · 共 ${total} 项 · 每页 ${pageSize} 条</span>
    <button type="button" class="btn btn-ghost btn-sm" data-page="${p + 1}" ${p < pg ? '' : 'disabled'}>下一页</button>`;
}

function setBarHtml(el, html) {
  if (!el) return false;
  if (el.dataset.staticHtml === html) return false;
  el.dataset.staticHtml = html;
  el.innerHTML = html;
  return true;
}

function mergeAvatarsIntoMap(items) {
  (items || []).forEach((a) => {
    if (a?.identifier) avatarsMap[a.identifier] = a;
  });
}

function getPrivateAvatarSearch() {
  if (currentPage === 'create') return ($('#createPrivateSearch')?.value || '').trim();
  return ($('#avatarPrivateSearch')?.value || '').trim();
}

async function fetchAvatarsPage(page, opts = {}) {
  const tab = opts.type || avatarTab;
  const p = page ?? (tab === 'public' ? avatarPublicPage : avatarPrivatePage);
  const privQ = tab === 'private' ? (opts.username ?? getPrivateAvatarSearch()) : '';
  if (opts.scope !== 'all' && tab === 'private' && !privQ) {
    avatarsPageMeta = { total: 0, page: 1, pages: 1 };
    return { items: [], total: 0, page: 1, pages: 1, page_size: opts.page_size || PAGE_SIZE.avatars };
  }
  const params = new URLSearchParams({
    page: String(p),
    page_size: String(opts.page_size || PAGE_SIZE.avatars),
  });
  if (opts.bake_status) params.set('bake_status', opts.bake_status);
  if (opts.scope !== 'all') {
    params.set('type', tab === 'private' ? 'private' : 'public');
    const privQ = tab === 'private' ? (opts.username ?? getPrivateAvatarSearch()) : '';
    if (privQ) params.set('username', privQ);
  }
  const { data: r } = await api(`/api/avatars?${params}`);
  if (r.code !== 0) return null;
  const d = r.data;
  if (d.counts) avatarCounts = d.counts;
  if (d.ready_counts) avatarReadyCounts = d.ready_counts;
  mergeAvatarsIntoMap(d.items);
  avatarsPageMeta = { total: d.total ?? 0, page: d.page ?? 1, pages: d.pages ?? 1 };
  if (tab === 'public') avatarPublicPage = avatarsPageMeta.page;
  else avatarPrivatePage = avatarsPageMeta.page;
  return d;
}

async function fetchWorksPage(page) {
  const p = page ?? worksPage;
  const pageSize = worksPageSize();
  lastWorksPageSize = pageSize;
  const params = new URLSearchParams({
    page: String(p),
    page_size: String(pageSize),
  });
  const userQ = ($('#workUserSearch')?.value || '').trim();
  const q = ($('#workSearch')?.value || '').trim();
  if (userQ) params.set('username', userQ);
  if (workFilter !== 'all') params.set('status', workFilter);
  if (q) params.set('keyword', q);
  const { data: r } = await api(`/api/tasks?${params}`);
  if (r.code !== 0) return null;
  const d = r.data;
  worksPageItems = mergeRunProgressWatermark(worksPageItems, d.tasks || []);
  worksPageMeta = { total: d.total ?? 0, page: d.page ?? 1, pages: d.pages ?? 1 };
  worksPage = worksPageMeta.page;
  worksPageItems.forEach((t) => {
    if (t.avatar_identifier) {
      avatarsMap[t.avatar_identifier] = {
        identifier: t.avatar_identifier,
        name: t.avatar_name,
        thumbnail: t.avatar_thumbnail,
        preview_video_path: t.avatar_preview_video,
        video_path: t.avatar_video_path,
        bake_status: t.avatar_bake_status,
      };
    }
  });
  const active = worksPageItems.filter((t) => t.status === 'run' || t.status === 'wait');
  await Promise.all(active.slice(0, 10).map(enrichTaskProgress));
  return d;
}

async function loadHomeTasksSnapshot() {
  const { data: r } = await api('/api/tasks?page=1&page_size=100');
  if (r.code !== 0) return;
  homeTasksSnapshot = mergeRunProgressWatermark(homeTasksSnapshot, r.data?.tasks ?? []);
  homeTasksSnapshot.forEach((t) => {
    if (t.avatar_identifier) {
      avatarsMap[t.avatar_identifier] = {
        identifier: t.avatar_identifier,
        name: t.avatar_name,
        thumbnail: t.avatar_thumbnail,
        preview_video_path: t.avatar_preview_video,
        video_path: t.avatar_video_path,
        bake_status: t.avatar_bake_status,
      };
    }
  });
  const active = homeTasksSnapshot.filter((t) => t.status === 'run' || t.status === 'wait');
  await Promise.all(active.slice(0, 10).map(enrichTaskProgress));
}

/** 运行中任务跨轮询保留进度高水位，避免整表替换时条回跳 */
function mergeRunProgressWatermark(prevList, nextList) {
  const prevMap = new Map((prevList || []).map((t) => [t.task_id, t]));
  return (nextList || []).map((t) => {
    if (t.status !== 'run') return t;
    const old = prevMap.get(t.task_id);
    if (!old) return t;
    const prev = Number(old.progress ?? 0);
    const next = Number(t.progress ?? 0);
    if (Number.isFinite(prev) && Number.isFinite(next) && prev >= 0 && next >= 0) {
      return { ...t, progress: Math.max(prev, next) };
    }
    return t;
  });
}

function bakeBadge(a) {
  const st = BAKE[a.bake_status] || BAKE.missing;
  const pct = a.bake_status === 'processing' ? ` ${a.bake_progress || 0}%` : '';
  return `<span class="badge-status ${st.cls}" data-bake-badge>${st.label}${pct}</span>`;
}

function isAvatarReady(a) {
  return a?.bake_status === 'ready';
}

function avatarPreviewVideo(a) {
  if (!a) return '';
  // 未烧制完成禁止播原视频：否则轮询只更新徽章时，预览仍指向嘴型未闭的模板片
  if (a.bake_status !== 'ready') return '';
  if (a.preview_video_path) return mediaUrl(a.preview_video_path);
  const vp = a.video_path || '';
  if (!vp) return '';
  if (vp.includes('_silent.')) return mediaUrl(vp);
  const m = String(vp).match(/^(.+\/)([^/]+?)(\.[^./]+)$/);
  if (m) return mediaUrl(`${m[1]}${m[2]}_silent${m[3]}`);
  return '';
}

/** 给预览 URL 可选加版本戳；烧制刚完成时 bust，避免浏览器仍播缓存旧片 */
function avatarPreviewVideoFresh(a, { bust = false } = {}) {
  const url = avatarPreviewVideo(a);
  if (!url) return '';
  if (!bust) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}v=${Date.now()}`;
}

function taskAvatarName(t, av) {
  return av?.name || t.avatar_name || t.avatar_identifier || '—';
}

function avatarPreviewTitle(a) {
  return `${a?.name || '形象'} · 闭嘴预览`;
}

function avatarPreviewThumb(a) {
  if (!a) return '';
  if (a.bake_status === 'ready' && a.preview_thumbnail) return mediaUrl(a.preview_thumbnail);
  return a.thumbnail ? mediaUrl(a.thumbnail) : '';
}

function avatarsBaking() {
  return Object.values(avatarsMap).filter((a) => a.bake_status === 'processing');
}

function avatarsBakingCount() {
  const local = avatarsBaking().length;
  return Math.max(systemStats?.baking ?? 0, local);
}

async function refreshSystemStatsQuiet() {
  try {
    const { data: r } = await api('/api/system/status');
    if (r.code === 0) {
      systemStats = r.data;
    }
  } catch { /* ignore */ }
}

async function pollAvatarPagesQuiet() {
  FX?.setSilent(true);
  try {
    await refreshAvatarCounts();
    if (currentPage === 'avatars') await renderAvatars({ animate: false });
    else if (currentPage === 'create') await renderCreateAvatars({ animate: false });
  } catch { /* ignore */ } finally {
    FX?.setSilent(false);
  }
}

async function refreshAvatarCounts() {
  const { data: r } = await api('/api/avatars?type=public&page=1&page_size=1');
  if (r.code === 0) {
    if (r.data?.counts) avatarCounts = r.data.counts;
    if (r.data?.ready_counts) avatarReadyCounts = r.data.ready_counts;
  }
}

async function loadAvatarsData() {
  await refreshAvatarCounts();
  if (currentPage === 'avatars') await renderAvatars();
  else if (currentPage === 'create') await renderCreateAvatars();
  await refreshSystemStatsQuiet();
  scheduleBakePoll();
}

function renderStatsRow({ animate = true } = {}) {
  const av = systemStats?.avatars || {
    public: avatarCounts.public,
    private: avatarCounts.private,
    total: avatarCounts.public + avatarCounts.private,
  };
  const tk = systemStats?.tasks || { done: 0, run: 0, wait: 0 };
  const baking = systemStats?.baking ?? avatarsBaking().length;
  const totalAv = av.total ?? (av.public + av.private);
  const pills = [
    ['avatar-public', '公共形象', av.public, 'avatar-stat'],
    ['avatar-private', '个人形象', av.private, 'avatar-stat'],
    ['avatar-all', '全部形象', totalAv, 'avatar-stat'],
    ['done', '完成', tk.done, 'task-stat'],
    ['run', '进行中', tk.run, 'task-stat'],
    ['wait', '等待', tk.wait, 'task-stat'],
    ['baking', '转码处理', baking, 'bake-stat'],
  ];
  const row = $('#statRow');
  if (!row) return;
  if (!animate && pills.every(([key, , val]) => {
    const btn = row.querySelector(`[data-home-filter="${key}"]`);
    if (!btn) return false;
    const v = btn.querySelector('.v');
    if (!v) return false;
    v.textContent = String(val);
    btn.classList.toggle('active', homeFilter === key);
    return true;
  })) {
    return;
  }
  row.innerHTML = pills.map(([key, label, val, cls]) =>
    `<button type="button" class="stat-pill clickable ${cls}${homeFilter === key ? ' active' : ''}" data-home-filter="${key}"><div class="v">${val}</div><div class="l">${label}</div></button>`
  ).join('');
  if (FX?.shouldAnimate(animate)) FX.revealChildren(row, '.stat-pill');
}

function canPatchTaskCards(container, items) {
  if (!container) return false;
  const cards = [...container.querySelectorAll('.media-card[data-task-id], .work-card[data-task-id]')];
  if (!items.length) return cards.length === 0;
  if (cards.length !== items.length) return false;
  return items.every((t, i) => cards[i].dataset.taskId === t.task_id);
}

function patchPosterImg(posterEl, src, alt, { eager = true } = {}) {
  if (!posterEl) return;
  const url = src ? mediaUrl(src) : '';
  if (url) {
    const img = posterEl.querySelector('img');
    if (img) {
      if (img.getAttribute('src') !== url) img.setAttribute('src', url);
      return;
    }
    posterEl.querySelector('video')?.remove();
    posterEl.querySelector('.placeholder')?.remove();
    posterEl.insertAdjacentHTML('afterbegin', posterHtml(url, alt, { eager }));
    return;
  }
  if (!posterEl.querySelector('.placeholder') && !posterEl.querySelector('img')) {
    posterEl.insertAdjacentHTML('afterbegin', posterHtml('', alt));
  }
}

function patchMediaCard(card, t, isDoneFocus) {
  const st = STATUS[t.status] || STATUS.wait;
  const pct = Math.max(0, Math.min(100, t.progress ?? 0));
  const msg = t.progress_message || t.error_message || '';
  card.dataset.status = t.status || '';
  const badge = card.querySelector('.badge-status');
  if (badge) {
    badge.className = `badge-status ${st.cls}`;
    badge.textContent = st.label;
  }
  const ring = card.querySelector('.progress-ring');
  if (t.status === 'run' || t.status === 'wait') {
    if (!ring) {
      const poster = card.querySelector('.poster');
      poster?.insertAdjacentHTML(
        'beforeend',
        `<div class="progress-ring"><div class="bar"><i style="width:${pct}%"></i></div><span>${pct}% ${esc(msg)}</span></div>`,
      );
    } else {
      const bar = ring.querySelector('.bar i');
      const span = ring.querySelector('span');
      if (bar) bar.style.width = `${pct}%`;
      if (span) span.textContent = `${pct}% ${msg}`;
      ring.classList.remove('hidden');
    }
  } else if (ring) {
    ring.remove();
  }
  const av = avatarForTask(t);
  const poster = card.querySelector('.poster');
  const rv = resultVideo(t);
  if (isDoneFocus && t.status === 'done') {
    patchPosterImg(poster, resultCoverPoster(t, av), t.task_name);
    if (rv) {
      card.dataset.play = rv;
      const full = resultVideoFull(t);
      if (full && full !== rv) card.dataset.playFallback = full;
      else delete card.dataset.playFallback;
    }
  } else if (!isDoneFocus) {
    patchPosterImg(poster, avatarCoverPoster(av), t.task_name);
    const preview = avatarPreviewVideo(av);
    if (preview) card.dataset.play = preview;
    delete card.dataset.playFallback;
  }
  // Keep play overlay on poster when playable.
  if (poster && card.dataset.play && !poster.querySelector('.play-fab')) {
    poster.insertAdjacentHTML('beforeend', playOverlay());
  }
}

function workCardActionsHtml(t) {
  const av = avatarForTask(t);
  const avPlay = avatarPreviewVideo(av);
  const avTitle = avatarPreviewTitle(av);
  const rv = resultVideo(t);
  const isDone = t.status === 'done';
  let actions = '';
  if (avPlay) actions += `<button class="btn btn-ghost btn-sm" data-play="${esc(avPlay)}" data-title="${esc(avTitle)}">播放形象</button>`;
  if (isDone && rv) {
    const full = resultVideoFull(t);
    const fb = full && full !== rv ? ` data-play-fallback="${esc(full)}"` : '';
    actions += `<button class="btn btn-primary btn-sm" data-play="${esc(rv)}"${fb} data-title="${esc(t.task_name)}">播放成品</button>`;
  }
  if (isDone && t.result_path) actions += `<a class="btn btn-ghost btn-sm" href="${API}/api/tasks/${esc(t.task_id)}/download" target="_blank">下载</a>`;
  if (t.status === 'error') actions += `<button class="btn btn-ghost btn-sm" data-retry="${esc(t.task_id)}">重试</button>`;
  actions += `<button class="btn btn-ghost btn-sm" data-del="${esc(t.task_id)}">删除</button>`;
  return actions;
}

function workCardResultInner(t, av) {
  const st = STATUS[t.status] || STATUS.wait;
  const rv = resultVideo(t);
  const isDone = t.status === 'done';
  const resultCover = isDone ? resultCoverPoster(t, av) : '';
  if (isDone && rv) {
    return resultCover
      ? posterHtml(resultCover, '成品', { eager: true }) + playOverlay()
      : '<div class="placeholder"><span>成品</span></div>' + playOverlay();
  }
  if (isDone) return '<div class="placeholder"><span>成品生成中</span></div>';
  return `<div class="placeholder"><span>${st.label}</span></div>`;
}

function patchWorkCard(card, t) {
  const st = STATUS[t.status] || STATUS.wait;
  const pct = Math.max(0, Math.min(100, t.progress ?? 0));
  const msg = t.progress_message || t.error_message || '';
  card.dataset.status = t.status || '';

  const tag = card.querySelector('.work-meta .tag');
  if (tag) {
    tag.className = `tag ${st.tag}`;
    tag.textContent = st.label;
  }

  const timeDd = card.querySelector('.work-info-row:last-child dd');
  if (timeDd) {
    const base = formatDateTime(t.created_at);
    timeDd.textContent = t.total_duration_text ? `${base} · ${t.total_duration_text}` : base;
  }

  let prog = card.querySelector('.work-progress');
  if (t.status === 'run' || t.status === 'wait') {
    if (!prog) {
      const body = card.querySelector('.work-body');
      const actions = body?.querySelector('.card-actions');
      const html = `<div class="work-progress"><div class="bar"><i style="width:${pct}%"></i></div><div class="msg">${pct}% · ${esc(msg || '处理中…')}</div></div>`;
      if (actions) actions.insertAdjacentHTML('beforebegin', html);
      else body?.insertAdjacentHTML('beforeend', html);
    } else {
      const bar = prog.querySelector('.bar i');
      const msgEl = prog.querySelector('.msg');
      if (bar) bar.style.width = `${pct}%`;
      if (msgEl) msgEl.textContent = `${pct}% · ${msg || '处理中…'}`;
    }
  } else if (t.status === 'error' && msg) {
    if (!prog) {
      const body = card.querySelector('.work-body');
      const actions = body?.querySelector('.card-actions');
      const html = `<div class="work-progress"><div class="msg" style="color:var(--danger)">${esc(msg)}</div></div>`;
      if (actions) actions.insertAdjacentHTML('beforebegin', html);
      else body?.insertAdjacentHTML('beforeend', html);
    } else {
      prog.innerHTML = `<div class="msg" style="color:var(--danger)">${esc(msg)}</div>`;
    }
  } else if (prog && (t.status === 'done' || t.status === 'error')) {
    prog.remove();
  }

  const av = avatarForTask(t);
  const previews = card.querySelectorAll('.work-previews .poster');
  const avatarPoster = previews[0];
  const resultPosterEl = previews[1];
  if (avatarPoster) {
    const avThumb = avatarCoverPoster(av);
    const avPlay = avatarPreviewVideo(av);
    const avName = taskAvatarName(t, av);
    const avTitle = avatarPreviewTitle(av);
    patchPosterImg(avatarPoster, avThumb, avName, { eager: true });
    avatarPoster.dataset.play = avPlay || '';
    avatarPoster.dataset.title = avTitle;
    if (avPlay && !avatarPoster.querySelector('.play-fab')) {
      avatarPoster.insertAdjacentHTML('beforeend', playOverlay());
    }
    if (!avPlay) avatarPoster.querySelector('.play-fab')?.remove();
  }
  if (resultPosterEl) {
    const rv = resultVideo(t);
    const full = resultVideoFull(t);
    const innerKey = `${t.status}|${rv || ''}|${resultCoverPoster(t, av) || ''}`;
    if (resultPosterEl.dataset.patchKey !== innerKey) {
      resultPosterEl.dataset.patchKey = innerKey;
      const label = resultPosterEl.querySelector('.label-tag');
      const labelHtml = label ? label.outerHTML : '<span class="label-tag">成品</span>';
      resultPosterEl.innerHTML = labelHtml + workCardResultInner(t, av);
    }
    resultPosterEl.dataset.play = t.status === 'done' && rv ? rv : '';
    if (t.status === 'done' && rv && full && full !== rv) resultPosterEl.dataset.playFallback = full;
    else delete resultPosterEl.dataset.playFallback;
    resultPosterEl.dataset.title = t.task_name || '';
  }

  const actionsEl = card.querySelector('.card-actions');
  if (actionsEl) {
    const key = `${t.status}|${avatarPreviewVideo(av) || ''}|${resultVideo(t) || ''}|${t.result_path || ''}`;
    if (actionsEl.dataset.patchKey !== key) {
      actionsEl.dataset.patchKey = key;
      actionsEl.innerHTML = workCardActionsHtml(t);
    }
  }
}

function renderHomeDefaultSections({ animate = true } = {}) {
  const done = homeTasksSnapshot.filter((t) => t.status === 'done').slice(0, 12);
  const running = homeTasksSnapshot.filter((t) => t.status === 'run' || t.status === 'wait').slice(0, 8);
  const recentEl = $('#recentWorks');
  const activeEl = $('#activeWorks');
  const avatarsEl = $('#homeAvatars');
  if (!animate && canPatchTaskCards(recentEl, done)) {
    [...recentEl.querySelectorAll('.media-card[data-task-id]')].forEach((card, i) => patchMediaCard(card, done[i], true));
  } else {
    recentEl.innerHTML = done.length
      ? done.map((t) => renderMediaCard(t, true)).join('')
      : '<div class="empty-state" style="padding:40px;min-width:280px"><div class="ico">🎬</div><h3>暂无成品</h3><p>完成合成后将在此展示</p></div>';
  }
  if (!animate && canPatchTaskCards(activeEl, running)) {
    [...activeEl.querySelectorAll('.media-card[data-task-id]')].forEach((card, i) => patchMediaCard(card, running[i], false));
  } else {
    activeEl.innerHTML = running.length
      ? running.map((t) => renderMediaCard(t, false)).join('')
      : '<div class="empty-state" style="padding:40px;min-width:280px"><p>当前没有进行中的任务</p></div>';
  }
  const readyAvatars = avatarsList.filter(isAvatarReady).slice(0, 10);
  if (!animate && canPatchAvatarGrid(avatarsEl, readyAvatars)) {
    patchAvatarGridCards(avatarsEl, readyAvatars, { compact: true });
  } else if (!animate && !readyAvatars.length && avatarsEl.querySelector('.empty-state') && !avatarsEl.querySelector('[data-avatar-id]')) {
    /* keep empty */
  } else {
    avatarsEl.innerHTML = readyAvatars.length
      ? readyAvatars.map((a) => renderAvatarCard(a, { compact: true })).join('')
      : '<div class="empty-state" style="padding:40px"><p>暂无可用形象</p></div>';
  }
  if (FX?.shouldAnimate(animate)) {
    FX.revealChildren(recentEl);
    FX.revealChildren(activeEl);
    FX.revealChildren(avatarsEl);
  }
}

async function getHomeFilteredModel() {
  if (homeFilter === 'done' || homeFilter === 'wait') {
    const list = homeTasksSnapshot.filter((t) => t.status === homeFilter);
    return {
      kind: 'work',
      items: list,
      html: list.length
        ? `<div class="home-filter-grid">${list.map((t) => renderWorkCard(t)).join('')}</div>`
        : '<div class="empty-state"><p>暂无匹配作品</p></div>',
    };
  }
  if (homeFilter === 'run') {
    const list = homeTasksSnapshot.filter((t) => t.status === 'run');
    return {
      kind: 'work',
      items: list,
      html: list.length
        ? `<div class="home-filter-grid">${list.map((t) => renderWorkCard(t)).join('')}</div>`
        : '<div class="empty-state"><p>暂无进行中的任务</p></div>',
    };
  }
  if (homeFilter === 'baking') {
    const d = await fetchAvatarsPage(1, { bake_status: 'processing', scope: 'all', page_size: 50 });
    const list = d?.items || [];
    return {
      kind: 'avatar',
      items: list,
      html: list.length
        ? `<div class="home-filter-grid">${list.map((a) => renderAvatarCard(a)).join('')}</div>`
        : '<div class="empty-state"><p>暂无转码中的形象</p><p class="hint-text">转码状态请在形象库查看</p></div>',
    };
  }
  if (homeFilter === 'avatar-public') {
    const d = await fetchAvatarsPage(1, { type: 'public', page_size: 24 });
    const list = d?.items || [];
    return {
      kind: 'avatar',
      items: list,
      html: list.length
        ? `<div class="home-filter-grid">${list.map((a) => renderAvatarCard(a)).join('')}</div>`
        : '<div class="empty-state"><p>暂无公共形象</p></div>',
    };
  }
  if (homeFilter === 'avatar-private') {
    const q = ($('#homePrivateSearch')?.value || '').trim();
    const d = await fetchAvatarsPage(1, { type: 'private', ...(q ? { username: q } : {}), page_size: 24 });
    const list = d?.items || [];
    const empty = q
      ? '<div class="empty-state"><p>未找到该用户的个人形象</p></div>'
      : '<div class="empty-state"><p>请输入用户ID查看个人形象，不会列出其他人的个人库</p></div>';
    return {
      kind: 'avatar',
      items: list,
      search: q,
      html: `<div class="section-head compact" style="margin-bottom:16px">
      <input type="search" class="search-input" id="homePrivateSearch" placeholder="输入用户名筛选个人形象…" value="${esc(q)}">
    </div>${list.length ? `<div class="home-filter-grid">${list.map((a) => renderAvatarCard(a)).join('')}</div>` : empty}`,
    };
  }
  if (homeFilter === 'avatar-all') {
    const pub = await fetchAvatarsPage(1, { type: 'public', page_size: 24 });
    const list = [...(pub?.items || [])];
    return {
      kind: 'avatar',
      items: list,
      html: list.length
        ? `<div class="home-filter-grid">${list.map((a) => renderAvatarCard(a)).join('')}</div>`
        : '<div class="empty-state"><p>暂无公共形象</p></div>',
    };
  }
  return { kind: 'empty', items: [], html: '' };
}

function setHomeFilter(key) {
  homeFilter = homeFilter === key ? null : key;
  renderHomeContent();
}

async function renderHomeContent({ animate = true } = {}) {
  renderStatsRow({ animate });
  const bar = $('#homeFilterBar');
  const defaultView = $('#homeDefaultView');
  const filteredView = $('#homeFilteredView');
  if (!homeFilter) {
    bar?.classList.add('hidden');
    defaultView?.classList.remove('hidden');
    filteredView?.classList.add('hidden');
    const readyData = await fetchAvatarsPage(1, { bake_status: 'ready', page_size: 10 });
    avatarsList = readyData?.items || [];
    renderHomeDefaultSections({ animate });
    return;
  }
  bar?.classList.remove('hidden');
  defaultView?.classList.add('hidden');
  filteredView?.classList.remove('hidden');
  const label = HOME_FILTERS[homeFilter]?.label || homeFilter;
  const el = $('#homeFilterLabel');
  if (el) el.textContent = `当前筛选：${label}`;
  const model = await getHomeFilteredModel();
  const ids = (model.items || []).map((it) => it.task_id || it.identifier).join(',');
  const staticKey = `${homeFilter}|${model.kind}|${ids}|${model.search || ''}`;
  const grid = filteredView?.querySelector('.home-filter-grid');
  if (!animate && filteredView?.dataset.staticKey === staticKey) {
    if (model.kind === 'work' && model.items.length && grid && canPatchTaskCards(grid, model.items)) {
      [...grid.querySelectorAll('.work-card[data-task-id], .media-card[data-task-id]')].forEach((card, i) => {
        const item = model.items[i];
        if (card.classList.contains('work-card')) patchWorkCard(card, item);
        else patchMediaCard(card, item, homeFilter === 'done');
      });
      return;
    }
    if (model.kind === 'avatar' && model.items.length && grid && canPatchAvatarGrid(grid, model.items)) {
      patchAvatarGridCards(grid, model.items);
      return;
    }
    if (!model.items.length) return;
  }
  if (filteredView) {
    filteredView.dataset.staticKey = staticKey;
    filteredView.innerHTML = model.html;
  }
  if (FX?.shouldAnimate(animate)) {
    FX.reveal($('#homeFilterBar'), 'fadeInDown', 300);
    FX.revealChildren(filteredView);
  }
}

function scheduleBakePoll() {
  const baking = avatarsBakingCount() > 0;
  if (!baking) {
    if (bakePollTimer) {
      clearInterval(bakePollTimer);
      bakePollTimer = null;
    }
    return;
  }
  if (bakePollTimer) return;
  bakePollTimer = setInterval(async () => {
    FX?.setSilent(true);
    try {
      await refreshSystemStatsQuiet();
      const baking = avatarsBakingCount() > 0;
      if (!baking) {
        clearInterval(bakePollTimer);
        bakePollTimer = null;
      }
      if (currentPage === 'home') await renderHomeContent({ animate: false });
      else if (currentPage === 'avatars') await renderAvatars({ animate: false });
      else if (currentPage === 'create') await renderCreateAvatars({ animate: false });
    } finally {
      FX?.setSilent(false);
    }
  }, 4000);
}

function avatarForTask(t) {
  const m = avatarsMap[t.avatar_identifier];
  if (m && (m.thumbnail || m.preview_video_path || m.name)) {
    return {
      ...m,
      name: m.name || t.avatar_name,
      thumbnail: m.thumbnail || t.avatar_thumbnail,
      preview_video_path: m.preview_video_path || t.avatar_preview_video,
      video_path: m.video_path || t.avatar_video_path,
      bake_status: m.bake_status || t.avatar_bake_status,
      missing: !!t.avatar_missing,
    };
  }
  if (t.avatar_identifier || t.avatar_thumbnail || t.avatar_name) {
    return {
      identifier: t.avatar_identifier,
      name: t.avatar_name || t.avatar_identifier,
      thumbnail: t.avatar_thumbnail,
      preview_video_path: t.avatar_preview_video,
      video_path: t.avatar_video_path,
      bake_status: t.avatar_bake_status,
      missing: !!t.avatar_missing,
    };
  }
  return null;
}

function resultPoster(t) {
  return t.result_thumbnail || null;
}

function resultVideoFull(t) {
  if (t?.result_path) return mediaUrl(t.result_path);
  return '';
}

/** 预览优先 LBR（更小）；损坏时由 lightbox 回退到完整成品 */
function resultVideo(t) {
  if (t?.result_path_lbr) return mediaUrl(t.result_path_lbr);
  return resultVideoFull(t);
}

function resultPlayAttrs(t) {
  const primary = resultVideo(t);
  const full = resultVideoFull(t);
  if (!primary) return '';
  const fallback = full && full !== primary ? ` data-play-fallback="${esc(full)}"` : '';
  return `data-play="${esc(primary)}"${fallback}`;
}

/* ---- Navigation ---- */

function isMobileLayout() {
  return window.matchMedia('(max-width: 768px)').matches;
}

async function go(page) {
  const same = page === currentPage;
  const prev = $('.page.active');
  if (!same && prev && FX?.shouldAnimate(true)) await FX.fadeOut(prev, 200);
  currentPage = page;
  $$('.nav-item').forEach((n) => n.classList.toggle('active', n.dataset.page === page));
  $$('.page').forEach((p) => p.classList.toggle('active', p.id === `page-${page}`));
  const meta = PAGES[page] || { title: page, desc: '' };
  $('#pageTitle').textContent = meta.title;
  $('#pageDesc').textContent = meta.desc;
  refreshPage();
  setupPolling();
  if (isMobileLayout() && !same) {
    window.scrollTo(0, 0);
  }
  const next = $(`#page-${page}`);
  if (!same && next && FX?.shouldAnimate(true)) {
    FX.reveal(next, 'fadeIn', 360);
    FX.reveal($('.topbar'), 'fadeInDown', 300);
  }
}

function refreshPage() {
  if (currentPage === 'home') refreshHome(false);
  if (currentPage === 'works') renderWorks();
  if (currentPage === 'avatars') renderAvatars();
  if (currentPage === 'create') {
    renderAvatarPicker();
  }
  if (currentPage === 'settings') renderSettings();
  checkReady();
}

function setupPolling() {
  clearInterval(pollTimer);
  let pollBusy = false;
  pollTimer = setInterval(() => {
    if (pollBusy) return;
    pollBusy = true;
    (async () => {
      try {
        FX?.setSilent(true);
        await checkReady();
        if (['home', 'works'].includes(currentPage)) await loadTasksAndRender(true);
        if (['avatars', 'create'].includes(currentPage)) await pollAvatarPagesQuiet();
      } finally {
        FX?.setSilent(false);
        pollBusy = false;
      }
    })();
  }, 4000);
}

const IDLE_EVENTS = ['mousedown', 'mousemove', 'keydown', 'scroll', 'touchstart', 'click', 'wheel'];
let idleTimer = null;
let idleMs = 30 * 60 * 1000;
let lastHeartbeatAt = 0;
let idleWatching = false;
let loggingOut = false;

async function forceLogout(reason) {
  if (loggingOut) return;
  loggingOut = true;
  try { sessionStorage.removeItem('dh_admin_gate_tab'); } catch { /* ignore */ }
  try {
    await fetch(API + '/api/admin/logout', { method: 'POST', credentials: 'same-origin' });
  } catch { /* ignore */ }
  const q = reason === 'idle' ? '?idle=1' : '';
  location.replace(`${API}/login.html${q}`);
}

function bumpIdle() {
  if (loggingOut) return;
  if (idleTimer) clearTimeout(idleTimer);
  idleTimer = setTimeout(() => forceLogout('idle'), idleMs);
  const now = Date.now();
  if (now - lastHeartbeatAt < 30000) return;
  lastHeartbeatAt = now;
  fetch(API + '/api/admin/heartbeat', { method: 'POST', credentials: 'same-origin' })
    .then((res) => res.json().catch(() => ({})))
    .then((body) => {
      if (body && body.code !== 0) forceLogout('expired');
    })
    .catch(() => {});
}

function startIdleWatch(seconds) {
  if (Number.isFinite(seconds) && seconds > 0) {
    idleMs = seconds * 1000;
  }
  if (idleWatching) {
    bumpIdle();
    return;
  }
  idleWatching = true;
  IDLE_EVENTS.forEach((name) => window.addEventListener(name, bumpIdle, { passive: true }));
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') bumpIdle();
  });
  bumpIdle();
}

async function checkReady() {
  try {
    const session = await api('/api/admin/session');
    if (session?.data?.idle_seconds) startIdleWatch(session.data.idle_seconds);
    if (session?.data?.enabled && session?.data?.ok === false) {
      forceLogout('expired');
      return;
    }
    const { data } = await api('/api/system/ready');
    const ok = data?.data?.ready;
    $('#readyChip').className = 'chip ' + (ok ? 'ok' : 'bad');
    $('#readyChip').textContent = ok ? '● 服务就绪' : '● 未就绪';
    $('#statusDot').className = 'status-dot ' + (ok ? 'ok' : 'bad');
    if (data?.data?.checks) {
      const labels = { ffmpeg: 'FFmpeg', gpu: 'GPU', model: '合成模型' };
      setBarHtml(
        $('#healthChips'),
        Object.entries(data.data.checks)
          .map(([k, v]) => `<span class="health-chip ${v ? 'ok' : 'fail'}">${v ? '✓' : '✗'} ${labels[k] || k}</span>`)
          .join(''),
      );
    }
    const gpuBox = $('#gpuChips');
    if (gpuBox) {
      const gpus = data?.data?.gpus || [];
      setBarHtml(
        gpuBox,
        gpus.length
          ? gpus.map((g) => {
              const busy = !!g.busy;
              return `<span class="health-chip ${busy ? 'fail' : 'ok'}">GPU ${g.id} ${busy ? '合成中' : '空闲'}</span>`;
            }).join('')
          : '<span class="health-chip fail">未配置</span>',
      );
    }
  } catch {
    $('#readyChip').className = 'chip bad';
    $('#readyChip').textContent = '● 连接失败';
    $('#statusDot').className = 'status-dot bad';
  }
}

/* ---- Tasks ---- */

async function loadTasksAndRender(silent = false) {
  const animate = !silent;
  FX?.setSilent(silent);
  try {
    await refreshAvatarCounts();
    try {
      await loadHomeTasksSnapshot();
      selectedWorkIds.forEach((id) => {
        if (!homeTasksSnapshot.some((t) => t.task_id === id) && !worksPageItems.some((t) => t.task_id === id)) {
          selectedWorkIds.delete(id);
        }
      });
      if (currentPage === 'home') await renderHomeContent({ animate });
      if (currentPage === 'works') await renderWorks({ animate });
    } catch (e) {
      console.error(e);
    }
  } finally {
    FX?.setSilent(false);
  }
}

async function enrichTaskProgress(t) {
  try {
    const { data: d } = await api(`/api/tasks/${t.task_id}`);
    if (d.code === 0 && d.data) {
      if (d.data.progress != null) {
        const next = Number(d.data.progress);
        const prev = Number(t.progress ?? 0);
        if (t.status === 'run' && next >= 0 && prev >= 0) t.progress = Math.max(prev, next);
        else t.progress = next;
      }
      if (d.data.progress_message) t.progress_message = d.data.progress_message;
      if (d.data.status) t.status = d.data.status;
    }
  } catch { /* ignore */ }
}

async function refreshHome(showToast = true) {
  try {
    const { data: r } = await api('/api/system/status');
    if (r.code === 0) {
      systemStats = r.data;
      scheduleBakePoll();
    }
  } catch { /* ignore */ }
  await loadTasksAndRender();
  if (showToast) toast('已刷新', 'success');
}

function renderMediaCard(t, isDoneFocus) {
  const st = STATUS[t.status] || STATUS.wait;
  const av = avatarForTask(t);
  const rv = resultVideo(t);
  const pct = Math.max(0, Math.min(100, t.progress ?? 0));
  const msg = t.progress_message || t.error_message || '';
  const posterSrc = isDoneFocus && t.status === 'done'
    ? resultCoverPoster(t, av)
    : avatarCoverPoster(av);
  const posterContent = posterHtml(posterSrc, t.task_name, { eager: isDoneFocus });
  const progressBlock = t.status === 'run' || t.status === 'wait'
    ? `<div class="progress-ring"><div class="bar"><i style="width:${pct}%"></i></div><span>${pct}% ${esc(msg)}</span></div>`
    : '';
  const playSrc = t.status === 'done' && rv ? rv : avatarPreviewVideo(av);
  const playFallback = t.status === 'done' ? resultVideoFull(t) : '';
  const playAttrs = playSrc
    ? `data-play="${esc(playSrc)}"${playFallback && playFallback !== playSrc ? ` data-play-fallback="${esc(playFallback)}"` : ''} data-title="${esc(t.task_name)}"`
    : `data-title="${esc(t.task_name)}"`;
  return `<article class="media-card" data-task-id="${esc(t.task_id)}" ${playAttrs}>
    <div class="poster">${posterContent}<span class="badge-status ${st.cls}">${st.label}</span>${progressBlock}${playSrc ? playOverlay() : ''}</div>
    <div class="card-body"><h3>${esc(t.task_name)}</h3><div class="sub">${esc(t.username || '')} · ${esc(av?.name || t.avatar_identifier || '—')}${t.quality_label ? ' · ' + esc(t.quality_label) : ''}</div></div>
  </article>`;
}

function renderWorkCard(t, opts = {}) {
  const { selectable = false, selected = false } = opts;
  const st = STATUS[t.status] || STATUS.wait;
  const av = avatarForTask(t);
  const avName = taskAvatarName(t, av);
  const avThumb = avatarCoverPoster(av);
  const avPlay = avatarPreviewVideo(av);
  const avTitle = avatarPreviewTitle(av);
  const rv = resultVideo(t);
  const pct = Math.max(0, Math.min(100, t.progress ?? 0));
  const msg = t.progress_message || t.error_message || '';
  const isDone = t.status === 'done';
  const avatarInner = avThumb
    ? posterHtml(avThumb, avName, { eager: true }) + (avPlay ? playOverlay() : '')
    : `<div class="placeholder"><span>${av?.missing || !av ? '形象已删除' : '形象'}</span></div>`;
  const resultInner = workCardResultInner(t, av);
  const actions = workCardActionsHtml(t);
  const progressHtml = (t.status === 'run' || t.status === 'wait') && pct >= 0
    ? `<div class="work-progress"><div class="bar"><i style="width:${pct}%"></i></div><div class="msg">${pct}% · ${esc(msg || '处理中…')}</div></div>`
    : (t.status === 'error' && msg ? `<div class="work-progress"><div class="msg" style="color:var(--danger)">${esc(msg)}</div></div>` : '');
  const check = selectable
    ? `<label class="card-check" onclick="event.stopPropagation()"><input type="checkbox" data-select-work="${esc(t.task_id)}" ${selected ? 'checked' : ''}></label>`
    : '';
  return `<article class="work-card${selected ? ' is-selected' : ''}" data-task-id="${esc(t.task_id)}" data-status="${esc(t.status)}">${check}
    <div class="work-previews">
      <div class="poster" data-play="${esc(avPlay)}" data-title="${esc(avTitle)}"><span class="label-tag">形象</span>${avatarInner}</div>
      <div class="poster" data-play="${esc(isDone && rv ? rv : '')}"${(() => { const full = resultVideoFull(t); return isDone && rv && full && full !== rv ? ` data-play-fallback="${esc(full)}"` : ''; })()} data-title="${esc(t.task_name)}"><span class="label-tag">成品</span>${resultInner}</div>
      <div class="arrow">→</div>
    </div>
    <div class="work-body">
      <div class="work-meta"><h3>${esc(t.task_name)}</h3><span class="tag ${st.tag}">${st.label}</span></div>
      <dl class="work-info">
        <div class="work-info-row"><dt>用户</dt><dd>${esc(t.username || '—')}</dd></div>
        <div class="work-info-row"><dt>形象</dt><dd>${esc(avName)}</dd></div>
        <div class="work-info-row"><dt>质量</dt><dd>${esc(t.quality_label || '标准')}</dd></div>
        <div class="work-info-row"><dt>时间</dt><dd>${esc(formatDateTime(t.created_at))}${t.total_duration_text ? ` · ${esc(t.total_duration_text)}` : ''}</dd></div>
      </dl>
      ${progressHtml}
      <div class="card-actions">${actions}</div>
    </div>
  </article>`;
}

function updateWorksToolbar(pageItems) {
  const btn = $('#worksBatchDel');
  const meta = $('#worksPagerMeta');
  const all = $('#worksSelectAll');
  const n = selectedWorkIds.size;
  if (btn) {
    btn.textContent = n ? `删除选中 (${n})` : '删除选中';
    btn.classList.toggle('hidden', n === 0);
  }
  if (meta && pageItems) {
    const userQ = ($('#workUserSearch')?.value || '').trim();
    const base = pageItems.length ? `本页 ${pageItems.length} 项` : '';
    meta.textContent = userQ ? `用户 ${userQ} · ${base}` : base;
  }
  if (all && pageItems) {
    const ids = pageItems.map((t) => t.task_id);
    all.checked = ids.length > 0 && ids.every((id) => selectedWorkIds.has(id));
    all.indeterminate = !all.checked && ids.some((id) => selectedWorkIds.has(id));
  }
}

async function renderWorks({ animate = true } = {}) {
  const userQ = ($('#workUserSearch')?.value || '').trim();
  const data = await fetchWorksPage();
  const grid = $('#worksGrid');
  const toolbar = $('#worksToolbar');
  if (!data) return;
  const items = worksPageItems;
  const { page, pages, total } = worksPageMeta;
  if (!items.length) {
    toolbar?.classList.add('hidden');
    const emptyMsg = userQ
      ? `<div class="empty-state"><div class="ico">📽</div><h3>该用户暂无作品</h3><p>用户标识「${esc(userQ)}」下没有匹配的任务</p></div>`
      : `<div class="empty-state"><div class="ico">📽</div><h3>暂无作品</h3><p>去「创建」页开始第一个合成吧</p></div>`;
    if (animate || !grid.querySelector('.empty-state') || grid.querySelector('[data-task-id]')) {
      grid.innerHTML = emptyMsg;
    }
    renderPager($('#worksPager'), page, pages, total, PAGE_SIZE.works);
    if (FX?.shouldAnimate(animate)) {
      FX.revealChildren(grid);
      FX.reveal($('#worksPager'), 'fadeIn', 300);
    }
    return;
  }
  toolbar?.classList.remove('hidden');
  if (!animate && canPatchTaskCards(grid, items)) {
    [...grid.querySelectorAll('.work-card[data-task-id]')].forEach((card, i) => {
      patchWorkCard(card, items[i]);
    });
  } else {
    grid.innerHTML = items.map((t) => renderWorkCard(t, { selectable: true, selected: selectedWorkIds.has(t.task_id) })).join('');
  }
  renderPager($('#worksPager'), page, pages, total, PAGE_SIZE.works);
  updateWorksToolbar(items);
  if (FX?.shouldAnimate(animate)) {
    FX.revealChildren(grid);
    FX.reveal($('#worksPager'), 'fadeIn', 300);
    FX.reveal($('#worksToolbar'), 'fadeIn', 300);
  }
}

async function batchDeleteWorks() {
  const ids = [...selectedWorkIds];
  if (!ids.length) return;
  if (!confirm(`确定删除选中的 ${ids.length} 个作品？`)) return;
  let ok = 0;
  let fail = 0;
  for (const id of ids) {
    const { data: r } = await api(`/api/tasks/${encodeURIComponent(id)}`, { method: 'DELETE' });
    if (r.code === 0) {
      ok++;
      selectedWorkIds.delete(id);
    } else fail++;
  }
  toast(`已删除 ${ok} 个${fail ? `，失败 ${fail} 个` : ''}`, fail ? 'error' : 'success');
  await loadTasksAndRender();
}

function renderAvatarBakeProgress(a) {
  if (a.bake_status === 'processing') {
    const pct = Math.max(0, Math.min(100, Number(a.bake_progress) || 0));
    const msg = a.bake_message || '转码中…';
    return `<div class="avatar-bake-progress is-processing">
      <div class="bake-bar"><i style="width:${pct}%"></i></div>
      <p class="bake-msg bake-msg-full" title="${esc(msg)}">${esc(msg)} · ${pct}%</p>
    </div>`;
  }
  if (a.bake_status === 'error') {
    const msg = a.bake_message || '转码失败，请重新上传';
    return `<div class="avatar-bake-progress is-error">
      <p class="bake-msg bake-msg-full bake-msg-error">${esc(msg)}</p>
    </div>`;
  }
  return '';
}

function canPatchAvatarGrid(grid, items) {
  if (!grid) return false;
  const cards = [...grid.querySelectorAll('.media-card[data-avatar-id]')];
  if (!items.length) return cards.length === 0;
  if (cards.length !== items.length) return false;
  return items.every((a, i) => cards[i].dataset.avatarId === a.identifier);
}

function patchAvatarGridCards(grid, items, { pickMode = false } = {}) {
  const cards = [...grid.querySelectorAll('.media-card[data-avatar-id]')];
  items.forEach((a, i) => {
    const card = cards[i];
    if (!card || card.dataset.avatarId !== a.identifier) return;
    const ready = isAvatarReady(a);
    const baking = a.bake_status === 'processing';
    const wasBaking = card.classList.contains('is-baking');
    const thumb = avatarPreviewThumb(a) || '';
    const pv = avatarPreviewVideoFresh(a, { bust: wasBaking && ready });
    const previewTitle = avatarPreviewTitle(a);

    card.classList.toggle('is-ready', ready);
    card.classList.toggle('card-disabled', !ready);
    card.classList.toggle('is-baking', baking);

    // 关键：静默轮询必须同步预览地址，否则徽章已是「可用」却仍播烧制前绑死的原视频
    if (pv) {
      card.dataset.play = pv;
      card.dataset.title = previewTitle;
    } else {
      delete card.dataset.play;
      delete card.dataset.title;
    }

    let poster = card.querySelector('.poster');
    if (poster) {
      poster.classList.toggle('poster-playable', !!pv);
      if (pv) {
        poster.dataset.play = pv;
        poster.dataset.title = previewTitle;
        if (!poster.querySelector('.play-fab')) {
          poster.insertAdjacentHTML('beforeend', playOverlay());
        }
      } else {
        delete poster.dataset.play;
        delete poster.dataset.title;
        poster.querySelector('.play-fab')?.remove();
        poster.querySelector('.poster-overlay')?.remove();
      }
      patchPosterImg(poster, thumb, a.name);
    }

    const bakeBadgeEl = card.querySelector('[data-bake-badge]');
    if (bakeBadgeEl) {
      const st = BAKE[a.bake_status] || BAKE.missing;
      const pct = a.bake_status === 'processing' ? ` ${a.bake_progress || 0}%` : '';
      const nextText = `${st.label}${pct}`;
      const nextCls = `badge-status ${st.cls}`;
      if (bakeBadgeEl.className !== nextCls) bakeBadgeEl.className = nextCls;
      if (bakeBadgeEl.textContent !== nextText) bakeBadgeEl.textContent = nextText;
    }

    const body = card.querySelector('.card-body');
    const actions = body?.querySelector('.card-actions');
    if (body && actions) {
      let prog = body.querySelector('.avatar-bake-progress');
      if (a.bake_status === 'processing') {
        const pct = Math.max(0, Math.min(100, Number(a.bake_progress) || 0));
        const msg = a.bake_message || '转码中…';
        if (!prog) {
          actions.insertAdjacentHTML('beforebegin', renderAvatarBakeProgress(a));
        } else {
          prog.className = 'avatar-bake-progress is-processing';
          const bar = prog.querySelector('.bake-bar i');
          const msgEl = prog.querySelector('.bake-msg');
          if (bar) bar.style.width = `${pct}%`;
          if (msgEl) {
            const text = `${msg} · ${pct}%`;
            if (msgEl.textContent !== text) {
              msgEl.textContent = text;
              msgEl.title = msg;
            }
          }
        }
      } else if (a.bake_status === 'error') {
        const msg = a.bake_message || '转码失败，请重新上传';
        if (!prog || !prog.classList.contains('is-error')) {
          prog?.remove();
          actions.insertAdjacentHTML('beforebegin', renderAvatarBakeProgress(a));
        } else {
          const msgEl = prog.querySelector('.bake-msg');
          if (msgEl && msgEl.textContent !== msg) msgEl.textContent = msg;
        }
      } else if (prog) {
        prog.remove();
      }

      const sub = body.querySelector('.sub');
      if (sub) {
        const next = `${a.identifier}${a.type === 'private' && a.username ? ` · ${a.username}` : ''}${pv ? ' · 点击封面预览形象' : ''}`;
        if (sub.textContent !== next) sub.textContent = next;
      }

      let previewBtn = actions.querySelector('button[data-play]');
      if (pv) {
        if (!previewBtn) {
          actions.insertAdjacentHTML(
            'afterbegin',
            `<button type="button" class="btn btn-primary btn-sm" data-play="${esc(pv)}" data-title="${esc(previewTitle)}">预览形象</button>`,
          );
          previewBtn = actions.querySelector('button[data-play]');
        } else {
          previewBtn.dataset.play = pv;
          previewBtn.dataset.title = previewTitle;
        }
      } else if (previewBtn) {
        previewBtn.remove();
      }
    }

    if (pickMode) {
      card.dataset.pickReady = ready ? '1' : '0';
      card.dataset.pickVideo = pv || '';
      card.dataset.pickThumb = thumb || '';
      card.classList.toggle('is-selected', a.identifier === pickedAvatarId);
      const pickBtn = card.querySelector('[data-pick]');
      if (pickBtn) pickBtn.disabled = !ready;
    }
  });
}

function renderAvatarCard(a, opts = {}) {
  const { compact = false, pickMode = false } = opts;
  const thumb = avatarPreviewThumb(a) || '';
  const pv = avatarPreviewVideoFresh(a);
  const ready = isAvatarReady(a);
  const baking = a.bake_status === 'processing';
  const previewBtn = pv
    ? `<button type="button" class="btn btn-primary btn-sm" data-play="${esc(pv)}" data-title="${esc(avatarPreviewTitle(a))}">预览形象</button>`
    : '';
  let actions;
  if (pickMode) {
    actions = `${previewBtn}<button type="button" class="btn btn-primary btn-sm" data-pick="${esc(a.identifier)}" ${ready ? '' : 'disabled'}>选择</button>`;
  } else {
    actions = compact
      ? `${previewBtn}<button type="button" class="btn btn-ghost btn-sm" data-use="${esc(a.identifier)}" ${ready ? '' : 'disabled'}>用于创建</button>`
      : `${previewBtn}<button type="button" class="btn btn-danger btn-sm" data-del-av="${esc(a.identifier)}">删除</button>`;
  }
  const previewTitle = esc(avatarPreviewTitle(a));
  const posterTag = pv
    ? `<div class="poster poster-playable" data-play="${esc(pv)}" data-title="${previewTitle}">`
    : '<div class="poster">';
  const selectedCls = pickMode && a.identifier === pickedAvatarId ? ' is-selected' : '';
  const pickAttrs = pickMode
    ? ` data-pick-id="${esc(a.identifier)}" data-pick-video="${esc(pv || '')}" data-pick-thumb="${esc(thumb)}" data-pick-ready="${ready ? '1' : '0'}"`
    : '';
  return `<article class="media-card${selectedCls}${ready ? ' is-ready' : ' card-disabled'}${baking ? ' is-baking' : ''}" data-avatar-id="${esc(a.identifier)}"${pickAttrs}${!pickMode && pv ? ` data-play="${esc(pv)}" data-title="${previewTitle}"` : ''}>
    ${posterTag}${posterHtml(thumb, a.name)}<span class="badge-status badge-done">${a.type === 'public' ? '公共' : '个人'}</span>${bakeBadge(a)}${pv ? playOverlay() : ''}</div>
    <div class="card-body">
      <h3>${esc(a.name)}</h3>
      <div class="sub">${esc(a.identifier)}${a.type === 'private' && a.username ? ' · ' + esc(a.username) : ''}${pv ? ' · 点击封面预览形象' : ''}</div>
      ${renderAvatarBakeProgress(a)}
      <div class="card-actions">${actions}</div>
    </div>
  </article>`;
}

async function renderCreateAvatars({ animate = true } = {}) {
  const grid = $('#createAvatarGrid');
  if (!grid) return;
  const privQ = ($('#createPrivateSearch')?.value || '').trim();
  const filterWrap = $('#createPrivateFilterWrap');
  const hint = $('#createPrivateHint');
  await refreshAvatarCounts();
  if ($('#createAvatarTabPublicCount')) $('#createAvatarTabPublicCount').textContent = String(avatarReadyCounts.public);
  if ($('#createAvatarTabPrivateCount')) $('#createAvatarTabPrivateCount').textContent = String(avatarReadyCounts.private);
  $$('#createAvatarTypeTabs [data-avatar-tab]').forEach((btn) => btn.classList.toggle('active', btn.dataset.avatarTab === avatarTab));
  filterWrap?.classList.toggle('hidden', avatarTab !== 'private');
  if (hint) hint.classList.toggle('hidden', avatarTab !== 'private');

  const data = await fetchAvatarsPage(undefined, { username: privQ, bake_status: 'ready' });
  if (!data) return;
  const items = data.items || [];
  const { page, pages, total } = avatarsPageMeta;

  if (!animate && canPatchAvatarGrid(grid, items)) {
    patchAvatarGridCards(grid, items, { pickMode: true });
  } else if (avatarTab === 'public') {
    grid.innerHTML = items.length
      ? items.map((a) => renderAvatarCard(a, { pickMode: true })).join('')
      : '<div class="empty-state inline"><p>暂无可用公共形象，请先在形象库上传并完成转码</p></div>';
  } else {
    grid.innerHTML = items.length
      ? items.map((a) => renderAvatarCard(a, { pickMode: true })).join('')
      : `<div class="empty-state inline"><p>${privQ ? '该用户暂无可用个人形象' : '请输入用户ID查看个人形象'}</p></div>`;
  }
  renderPager($('#createAvatarPager'), page, pages, total, PAGE_SIZE.avatars);
  syncAvatarsUploadPanelHeight();
  if (animate && FX?.shouldAnimate(true)) {
    FX.revealChildren(grid);
    FX.reveal($('#createAvatarPager'), 'fadeIn', 300);
    if (filterWrap && avatarTab === 'private') FX.reveal(filterWrap, 'fadeInDown', 280);
  }
}

function pickAvatarFromCard(id, video, thumb) {
  if (!id) return;
  const card = $(`#createAvatarGrid .media-card[data-pick-id="${CSS.escape(id)}"]`);
  if (card && card.dataset.pickReady !== '1') {
    toast('该形象仍在转码中，请稍后再选', 'error');
    return;
  }
  selectAvatar(id, video, thumb);
  renderCreateAvatars();
  syncAvatarsUploadPanelHeight();
}

function syncSplitUploadPanelHeight() {
  document.querySelectorAll('#page-avatars .avatars-upload-col, #page-create .avatars-upload-col').forEach((col) => {
    col.style.minHeight = '';
  });
}

function syncAvatarsUploadPanelHeight() {
  requestAnimationFrame(syncSplitUploadPanelHeight);
}

async function renderAvatars({ animate = true } = {}) {
  const grid = $('#avatarGrid');
  if (!grid) return;
  const privQ = ($('#avatarPrivateSearch')?.value || '').trim();
  const filterWrap = $('#avatarPrivateFilterWrap');
  const hint = $('#avatarPrivateHint');
  await refreshAvatarCounts();
  if ($('#avatarTabPublicCount')) $('#avatarTabPublicCount').textContent = String(avatarCounts.public);
  if ($('#avatarTabPrivateCount')) $('#avatarTabPrivateCount').textContent = String(avatarCounts.private);
  $$('#avatarTypeTabs [data-avatar-tab]').forEach((btn) => btn.classList.toggle('active', btn.dataset.avatarTab === avatarTab));
  filterWrap?.classList.toggle('hidden', avatarTab !== 'private');
  if (hint) hint.classList.toggle('hidden', avatarTab !== 'private');

  const data = await fetchAvatarsPage(undefined, { username: privQ });
  if (!data) return;
  const items = data.items || [];
  const { page, pages, total } = avatarsPageMeta;

  if (!animate && canPatchAvatarGrid(grid, items)) {
    patchAvatarGridCards(grid, items);
  } else if (avatarTab === 'public') {
    grid.innerHTML = items.length
      ? items.map((a) => renderAvatarCard(a)).join('')
      : '<div class="empty-state inline"><p>暂无公共形象，可在右侧上传</p></div>';
  } else {
    grid.innerHTML = items.length
      ? items.map((a) => renderAvatarCard(a)).join('')
      : `<div class="empty-state inline"><p>${privQ ? '未找到该用户的个人形象' : '请输入用户ID查看个人形象'}</p></div>`;
  }
  renderPager($('#avatarPager'), page, pages, total, PAGE_SIZE.avatars);
  syncAvatarsUploadPanelHeight();
  if (animate && FX?.shouldAnimate(true)) {
    FX.revealChildren(grid);
    FX.reveal($('#avatarPager'), 'fadeIn', 300);
    if (filterWrap && avatarTab === 'private') FX.reveal(filterWrap, 'fadeInDown', 280);
  }
  scheduleBakePoll();
}

function renderAvatarPicker() {
  renderCreateAvatars();
}

function selectAvatar(id, video, thumb) {
  pickedAvatarId = id;
  $('#pickedAvatar').value = id;
  const stage = $('#createPreview');
  if (!video) {
    stage.innerHTML = '<div class="empty-stage"><p>转码完成后可预览</p></div>';
    return;
  }
  stage.innerHTML = `<video src="${esc(video)}" autoplay muted loop playsinline poster="${esc(thumb)}"></video>`;
  FX?.reveal(stage, 'fadeIn', 360);
}

async function handleTaskAction(e) {
  const taskLog = e.target.dataset.taskLog;
  const stop = e.target.dataset.stop;
  const retry = e.target.dataset.retry;
  const del = e.target.dataset.del;
  const play = e.target.dataset.play;
  if (taskLog) { openTaskLog(taskLog, e.target.dataset.taskName, e.target.dataset.taskStatus); return; }
  if (play) {
    openLightbox(play, e.target.dataset.title, e.target.dataset.playFallback || '');
    return;
  }
  if (stop) {
    const { data: r } = await api(`/api/tasks/${stop}/stop`, { method: 'POST' });
    toast(r.msg || '已停止', r.code === 0 ? 'success' : 'error');
    loadTasksAndRender();
  }
  if (retry) {
    const { data: r } = await api(`/api/tasks/${retry}/retry`, { method: 'POST' });
    toast(r.msg || '已重试', r.code === 0 ? 'success' : 'error');
    loadTasksAndRender();
  }
  if (del && confirm('删除此作品？')) {
    const { data: r } = await api(`/api/tasks/${del}`, { method: 'DELETE' });
    toast(r.msg || '已删除', r.code === 0 ? 'success' : 'error');
    selectedWorkIds.delete(del);
    loadTasksAndRender();
  }
}

function formatFileSize(bytes) {
  if (!bytes && bytes !== 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function maxSynthesisSeconds() {
  const n = Number(systemStats?.features?.max_synthesis_duration_seconds);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

function formatDurationCn(sec) {
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return `${s}秒`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `${m}分${r}秒` : `${m}分钟`;
}

function durationLimitMessage(kind, actualSec, maxSec) {
  const label = kind === 'audio' ? '音频' : '形象视频';
  return `${label}时长 ${formatDurationCn(actualSec)} 超过上限 ${formatDurationCn(maxSec)}`;
}

function probeFileDuration(file) {
  return new Promise((resolve, reject) => {
    if (!file) return reject(new Error('未选择文件'));
    const url = URL.createObjectURL(file);
    const isAudio = (file.type || '').startsWith('audio/') || /\.(wav|mp3|m4a|aac|flac|ogg)$/i.test(file.name);
    const el = document.createElement(isAudio ? 'audio' : 'video');
    const cleanup = () => {
      el.removeAttribute('src');
      el.load();
      URL.revokeObjectURL(url);
    };
    el.preload = 'metadata';
    el.onloadedmetadata = () => {
      const d = el.duration;
      cleanup();
      if (!Number.isFinite(d) || d <= 0) reject(new Error('无法读取媒体时长'));
      else resolve(d);
    };
    el.onerror = () => {
      cleanup();
      reject(new Error('无法读取媒体时长'));
    };
    el.src = url;
  });
}

async function assertFileWithinLimit(file, kind) {
  const maxSec = maxSynthesisSeconds();
  const duration = await probeFileDuration(file);
  if (maxSec > 0 && Math.round(duration) > maxSec) {
    throw new Error(durationLimitMessage(kind, duration, maxSec));
  }
  return duration;
}

function setAvatarVideoLabel(file) {
  const el = $('#avatarVideoLabel');
  if (!el) return;
  if (!file) {
    el.innerHTML = '拖拽或 <strong>点击选择</strong> 视频文件';
    return;
  }
  el.innerHTML = `已选择：<strong>${esc(file.name)}</strong>（${formatFileSize(file.size)}）`;
}

function onAvatarVideoPicked(file) {
  setAvatarVideoLabel(file);
  if (!file) return;
  const form = $('#avatarForm');
  if (!form) return;
  const stem = file.name.replace(/\.[^.]+$/i, '').trim();
  const nameInput = form.elements.name;
  if (nameInput && !nameInput.value.trim()) nameInput.value = stem || file.name;
  assertFileWithinLimit(file, 'video')
    .then((d) => {
      const el = $('#avatarVideoLabel');
      if (el) el.innerHTML = `已选择：<strong>${esc(file.name)}</strong>（${formatDurationCn(d)}，${formatFileSize(file.size)}）`;
    })
    .catch((err) => {
      toast(err.message, 'error');
      const input = form.querySelector('input[name="video"]');
      if (input) input.value = '';
      setAvatarVideoLabel(null);
    });
}

/* ---- Forms ---- */

$('#avatarType')?.addEventListener('change', (e) => {
  $('#avatarUserWrap').classList.toggle('hidden', e.target.value !== 'private');
});

$('#avatarForm')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const file = form.video.files[0];
  if (!file) return toast('请选择视频', 'error');
  try {
    await assertFileWithinLimit(file, 'video');
  } catch (err) {
    return toast(err.message, 'error');
  }
  const btn = $('#avatarSubmit');
  const bar = $('#uploadBar');
  const progress = $('#uploadProgress');
  const status = $('#uploadStatus');
  const setP = (p) => {
    const pct = Math.max(0, Math.min(100, Math.round(p)));
    if (bar) bar.style.width = pct + '%';
    if (status) status.textContent = pct + '%';
  };
  const resetUploadUi = () => {
    form.reset();
    setAvatarVideoLabel(null);
    $('#avatarUserWrap')?.classList.toggle('hidden', true);
    if (bar) bar.style.width = '0%';
    if (status) status.textContent = '0%';
    progress?.classList.add('hidden');
  };
  btn.disabled = true;
  progress?.classList.remove('hidden');
  setP(0);
  try {
    const chunkSize = 8 * 1024 * 1024;
    const total = Math.ceil(file.size / chunkSize);
    const init = new FormData();
    init.append('stage', 'init');
    ['name', 'type'].forEach((k) => init.append(k, form[k].value));
    if (form.type.value === 'private') init.append('username', form.username.value);
    init.append('filename', file.name);
    init.append('filesize', String(file.size));
    init.append('chunk_size', String(chunkSize));
    const { data: ir } = await api('/api/avatars/upload', { method: 'POST', body: init });
    if (ir.code !== 0) throw new Error(ir.msg);
    const uid = ir.data.upload_id;
    for (let i = 0; i < total; i++) {
      const c = new FormData();
      c.append('stage', 'chunk');
      c.append('upload_id', uid);
      c.append('chunk_index', String(i));
      c.append('total_chunks', String(total));
      c.append('chunk', file.slice(i * chunkSize, (i + 1) * chunkSize));
      setP(((i + 1) / total) * 100);
      const { data: cr } = await api('/api/avatars/upload', { method: 'POST', body: c });
      if (cr.code !== 0) throw new Error(cr.msg);
    }
    const done = new FormData();
    done.append('stage', 'complete');
    done.append('upload_id', uid);
    const { data: fr } = await api('/api/avatars/upload', { method: 'POST', body: done });
    if (fr.code !== 0) throw new Error(fr.msg);
    setP(100);
    toast('上传成功，正在处理', 'success');
    const uploadedType = form.type.value;
    const uploadedUser = form.username?.value?.trim() || '';
    resetUploadUi();
    if (uploadedType === 'private' && uploadedUser) {
      avatarTab = 'private';
      avatarPrivatePage = 1;
      if ($('#avatarPrivateSearch')) $('#avatarPrivateSearch').value = uploadedUser;
      if ($('#createPrivateSearch')) $('#createPrivateSearch').value = uploadedUser;
    }
    await loadAvatarsData();
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    btn.disabled = false;
  }
});

$('#taskForm')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!pickedAvatarId) return toast('请先选择形象', 'error');
  const form = e.target;
  const audioFile = form.audio.files[0];
  if (!audioFile) return toast('请选择音频', 'error');
  try {
    await assertFileWithinLimit(audioFile, 'audio');
  } catch (err) {
    return toast(err.message, 'error');
  }
  const fd = new FormData(form);
  fd.set('avatar_identifier', pickedAvatarId);
  const steps = form.querySelector('input[name="steps"]:checked')?.value || '30';
  fd.set('steps', steps);
  $('#taskSubmit').disabled = true;
  try {
    const { data: r } = await api('/api/tasks/create', { method: 'POST', body: fd });
    if (r.code !== 0) throw new Error(r.msg);
    toast('已开始合成，可在作品库查看进度', 'success');
    form.reset();
    $('#audioLabel').textContent = '选择 wav / mp3 音频';
    await loadTasksAndRender();
    go('works');
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    $('#taskSubmit').disabled = false;
  }
});

$('#btnAdminLogout')?.addEventListener('click', async () => {
  if (!confirm('退出后需重新输入访问密钥，确定？')) return;
  try { sessionStorage.removeItem('dh_admin_gate_tab'); } catch { /* ignore */ }
  try {
    await fetch(API + '/api/admin/logout', { method: 'POST', credentials: 'same-origin' });
  } catch { /* ignore */ }
  location.replace(API + '/login.html');
});

/* ---- Task log viewer ---- */

function stopTaskLogPoll() {
  if (taskLogPollTimer) {
    clearInterval(taskLogPollTimer);
    taskLogPollTimer = null;
  }
}

async function refreshTaskLogView() {
  if (!taskLogOpenId) return;
  const pre = $('#taskLogContent');
  const footer = $('#taskLogFooter');
  const meta = $('#taskLogMeta');
  if (!pre) return;
  const wasAtBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 48;
  try {
    const { data: r } = await api(`/api/tasks/${encodeURIComponent(taskLogOpenId)}/log?tail=500`);
    if (r.code !== 0) {
      pre.textContent = r.msg || '读取日志失败';
      return;
    }
    const d = r.data || {};
    const st = STATUS[d.task_status] || STATUS.wait;
    const pct = d.progress != null ? `${d.progress}%` : '—';
    const pmsg = d.progress_message ? ` · ${d.progress_message}` : '';
    if (meta) {
      meta.textContent = `${taskLogOpenId} · ${st.label} · ${pct}${pmsg}`;
    }
    if (d.content) {
      pre.textContent = d.content;
    } else if (!d.exists) {
      pre.textContent = '（暂无日志行，Worker 启动后将自动出现…）';
    } else {
      pre.textContent = '（日志文件为空）';
    }
    if (footer) {
      const live = d.task_status === 'run' || d.task_status === 'wait';
      const path = d.log_path || '—';
      const html = live
        ? `<span class="task-log-live">实时刷新中</span> · ${esc(path)}${d.updated_at ? ` · 进度更新 ${esc(d.updated_at)}` : ''}`
        : `${esc(path)}${d.updated_at ? ` · 最后更新 ${esc(d.updated_at)}` : ''}`;
      setBarHtml(footer, html);
    }
    if ($('#taskLogAutoScroll')?.checked && (wasAtBottom || d.task_status === 'run')) {
      pre.scrollTop = pre.scrollHeight;
    }
    if (d.task_status !== 'run' && d.task_status !== 'wait') {
      stopTaskLogPoll();
    }
  } catch (err) {
    pre.textContent = `读取日志失败: ${err.message || err}`;
  }
}

function openTaskLog(taskId, taskName, taskStatus) {
  if (!taskId) return;
  taskLogOpenId = taskId;
  stopTaskLogPoll();
  const overlay = $('#taskLogOverlay');
  const title = $('#taskLogTitle');
  const pre = $('#taskLogContent');
  const footer = $('#taskLogFooter');
  if (title) title.textContent = taskName ? `日志 · ${taskName}` : `任务日志`;
  if (pre) pre.textContent = '';
  if (footer) footer.textContent = '加载中…';
  overlay?.classList.remove('hidden');
  overlay?.setAttribute('aria-hidden', 'false');
  FX?.openOverlay?.(overlay, overlay?.querySelector('.task-log-panel'));
  refreshTaskLogView();
  const live = taskStatus === 'run' || taskStatus === 'wait';
  if (live) {
    taskLogPollTimer = setInterval(refreshTaskLogView, 2000);
  }
}

async function closeTaskLog() {
  stopTaskLogPoll();
  taskLogOpenId = null;
  const overlay = $('#taskLogOverlay');
  await FX?.closeOverlay?.(overlay, overlay?.querySelector('.task-log-panel'));
  overlay?.classList.add('hidden');
  overlay?.setAttribute('aria-hidden', 'true');
}

$('#taskLogClose')?.addEventListener('click', closeTaskLog);
$('#taskLogRefresh')?.addEventListener('click', () => refreshTaskLogView());
$('#taskLogOverlay')?.addEventListener('click', (e) => {
  if (e.target.id === 'taskLogOverlay') closeTaskLog();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && taskLogOpenId) closeTaskLog();
});

/* ---- Lightbox ---- */

function _mediaUrlBust(url) {
  if (!url) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}_=${Date.now()}`;
}

function openLightbox(src, title, fallbackSrc = '') {
  if (!src) return;
  $('#lbTitle').textContent = title || '';
  const v = $('#lbVideo');
  v.setAttribute('playsinline', '');
  v.setAttribute('webkit-playsinline', '');
  v.playsInline = true;
  // Reset previous error handlers / src before assigning
  v.onerror = null;
  v.removeAttribute('src');
  v.load();
  let usedFallback = false;
  const tryPlay = () => v.play().catch(() => {});
  v.onerror = () => {
    if (!usedFallback && fallbackSrc && fallbackSrc !== src) {
      usedFallback = true;
      console.warn('[lightbox] primary media failed, fallback to full result', src, '->', fallbackSrc);
      v.src = _mediaUrlBust(fallbackSrc);
      tryPlay();
      return;
    }
    toast('视频无法播放（文件可能损坏或格式不支持）', 'error');
  };
  // 烧制刚覆盖的预览易被浏览器缓存旧坏文件；打开时带时间戳绕过缓存
  v.src = _mediaUrlBust(src);
  const lb = $('#lightbox');
  const inner = lb?.querySelector('.lightbox-inner');
  document.body.style.overflow = 'hidden';
  FX?.openOverlay(lb, inner);
  // Mobile Safari often needs a direct user-gesture play; retry once after metadata.
  tryPlay();
  v.addEventListener('loadeddata', tryPlay, { once: true });
}

async function closeLightbox() {
  const lb = $('#lightbox');
  const inner = lb?.querySelector('.lightbox-inner');
  await FX?.closeOverlay(lb, inner);
  const v = $('#lbVideo');
  v.pause();
  v.onerror = null;
  v.removeAttribute('src');
  v.load();
  document.body.style.overflow = '';
}

$('#lbClose')?.addEventListener('click', closeLightbox);
$('#lightbox')?.addEventListener('click', (e) => { if (e.target.id === 'lightbox') closeLightbox(); });
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && $('#lightbox')?.classList.contains('open')) closeLightbox();
});

/* ---- UI wiring ---- */

$$('.nav-item').forEach((b) => b.addEventListener('click', () => go(b.dataset.page)));
$$('[data-goto]').forEach((b) => b.addEventListener('click', () => go(b.dataset.goto)));
$('#btnRefresh')?.addEventListener('click', () => refreshPage());
$('#workSearch')?.addEventListener('input', () => { worksPage = 1; renderWorks(); });
$('#workUserSearch')?.addEventListener('input', () => { worksPage = 1; renderWorks(); });
$('#avatarPrivateSearch')?.addEventListener('input', () => { avatarPrivatePage = 1; renderAvatars(); });
$$('#avatarTypeTabs .tab, #createAvatarTypeTabs .tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    const next = tab.dataset.avatarTab;
    if (next !== avatarTab) {
      if (next === 'public') avatarPublicPage = 1;
      else avatarPrivatePage = 1;
    }
    avatarTab = next;
    renderAvatars();
    renderCreateAvatars();
  });
});
$('#createPrivateSearch')?.addEventListener('input', () => { avatarPrivatePage = 1; renderCreateAvatars(); });
$('#homeFilterClear')?.addEventListener('click', () => { homeFilter = null; renderHomeContent(); });
$('#statRow')?.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-home-filter]');
  if (btn) setHomeFilter(btn.dataset.homeFilter);
});
$('#homeFilteredView')?.addEventListener('input', (e) => {
  if (e.target.id === 'homePrivateSearch') renderHomeContent();
});
$('#worksSelectAll')?.addEventListener('change', (e) => {
  worksPageItems.forEach((t) => {
    if (e.target.checked) selectedWorkIds.add(t.task_id);
    else selectedWorkIds.delete(t.task_id);
  });
  renderWorks();
});
$('#worksBatchDel')?.addEventListener('click', () => batchDeleteWorks());
$('#worksPager')?.addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-page]');
  if (!btn || btn.disabled) return;
  worksPage = Number(btn.dataset.page);
  renderWorks();
});
$('#avatarPager')?.addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-page]');
  if (!btn || btn.disabled) return;
  const p = Number(btn.dataset.page);
  if (avatarTab === 'public') avatarPublicPage = p;
  else avatarPrivatePage = p;
  renderAvatars();
});
$('#createAvatarPager')?.addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-page]');
  if (!btn || btn.disabled) return;
  const p = Number(btn.dataset.page);
  if (avatarTab === 'public') avatarPublicPage = p;
  else avatarPrivatePage = p;
  renderCreateAvatars();
});
$('#createAvatarGrid')?.addEventListener('click', (e) => {
  const pickBtn = e.target.closest('[data-pick]');
  if (pickBtn) {
    const card = pickBtn.closest('.media-card');
    pickAvatarFromCard(pickBtn.dataset.pick, card?.dataset.pickVideo, card?.dataset.pickThumb);
    return;
  }
  handleTaskAction(e);
});

$$('#workTabs .tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    $$('#workTabs .tab').forEach((t) => t.classList.remove('active'));
    tab.classList.add('active');
    workFilter = tab.dataset.filter;
    worksPage = 1;
    selectedWorkIds.clear();
    renderWorks();
  });
});

document.querySelector('#audioDrop input')?.addEventListener('change', async (e) => {
  const f = e.target.files[0];
  if (!f) {
    $('#audioLabel').textContent = '选择 wav / mp3 音频';
    return;
  }
  try {
    const d = await assertFileWithinLimit(f, 'audio');
    $('#audioLabel').textContent = `${f.name}（${formatDurationCn(d)}）`;
  } catch (err) {
    toast(err.message, 'error');
    e.target.value = '';
    $('#audioLabel').textContent = '选择 wav / mp3 音频';
  }
});

document.querySelector('#avatarDrop input')?.addEventListener('change', (e) => {
  onAvatarVideoPicked(e.target.files?.[0] || null);
});

function renderSettings() {
  checkReady();
}

async function init() {
  document.body.addEventListener('click', async (e) => {
    const playBtn = e.target.closest('button[data-play]');
    if (playBtn?.dataset.play) {
      openLightbox(playBtn.dataset.play, playBtn.dataset.title, playBtn.dataset.playFallback || '');
      return;
    }
    const logBtn = e.target.closest('[data-task-log]');
    if (logBtn?.dataset.taskLog) {
      openTaskLog(logBtn.dataset.taskLog, logBtn.dataset.taskName, logBtn.dataset.taskStatus);
      return;
    }
    const card = e.target.closest('[data-play]');
    if (card?.dataset.play && !e.target.closest('button, a, label')) {
      openLightbox(card.dataset.play, card.dataset.title, card.dataset.playFallback || '');
      return;
    }
    const poster = e.target.closest('.poster[data-play]');
    if (poster?.dataset.play && !e.target.closest('button, a, label')) {
      openLightbox(poster.dataset.play, poster.dataset.title, poster.dataset.playFallback || '');
      return;
    }
    if (e.target.closest('.avatar-page-grid .poster') && !e.target.closest('button, a, label')) {
      const avCard = e.target.closest('.media-card');
      if (avCard && !avCard.dataset.play) {
        toast('转码完成后可预览', 'info');
        return;
      }
    }
    const rebake = e.target.dataset.rebake;
    if (rebake) {
      const { data: r } = await api(`/api/avatars/${encodeURIComponent(rebake)}/rebake`, { method: 'POST' });
      toast(r.msg || '已提交', r.code === 0 ? 'success' : 'error');
      await loadAvatarsData();
      if (currentPage === 'home') await renderHomeContent();
      return;
    }
    const use = e.target.dataset.use;
    const delAv = e.target.dataset.delAv;
    if (use) {
      const a = avatarsMap[use];
      if (!isAvatarReady(a)) {
        const msg = a?.bake_status === 'missing' ? '形象还在转码，请稍后再用' : '形象仍在转码中，请稍后再用';
        toast(msg, 'error');
        return;
      }
      pickedAvatarId = use;
      go('create');
      selectAvatar(use, avatarPreviewVideo(a), avatarPreviewThumb(a));
      return;
    }
    if (delAv && confirm('删除此形象？')) {
      const { data: r } = await api(`/api/avatars/${encodeURIComponent(delAv)}`, { method: 'DELETE' });
      toast(r.msg || '已删除', r.code === 0 ? 'success' : 'error');
      await loadAvatarsData();
      return;
    }
    if (e.target.closest('#worksGrid')) await handleTaskAction(e);
  });

  document.body.addEventListener('change', (e) => {
    if (e.target.matches('[data-select-work]')) {
      const id = e.target.dataset.selectWork;
      if (e.target.checked) selectedWorkIds.add(id);
      else selectedWorkIds.delete(id);
      renderWorks();
    }
  });

  await loadAvatarsData();
  await loadTasksAndRender(true);
  let syncUploadPanelTimer;
  let worksResizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(syncUploadPanelTimer);
    syncUploadPanelTimer = setTimeout(syncAvatarsUploadPanelHeight, 120);
    clearTimeout(worksResizeTimer);
    worksResizeTimer = setTimeout(() => {
      if (currentPage !== 'works') return;
      const next = calcWorksPageSize();
      if (next === lastWorksPageSize) return;
      worksPage = 1;
      renderWorks({ animate: false });
    }, 180);
  });
  currentPage = '';
  startIdleWatch();
  go('home');
}

init();
