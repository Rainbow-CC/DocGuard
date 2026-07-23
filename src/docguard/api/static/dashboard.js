(() => {
  const form = document.querySelector('#audit-form');
  const fileInput = document.querySelector('#document-file');
  const dropZone = document.querySelector('#drop-zone');
  const button = document.querySelector('#submit-button');
  const message = document.querySelector('#form-message');
  const backend = document.querySelector('#backend');
  const taskFilenameFilter = document.querySelector('#task-filename-filter');
  const elements = {
    title: document.querySelector('#drop-title'), description: document.querySelector('#drop-description'),
    status: document.querySelector('#task-status'), name: document.querySelector('#task-name'), id: document.querySelector('#task-id'),
    findings: document.querySelector('#findings-count'), backend: document.querySelector('#backend-name'), updated: document.querySelector('#updated-at'),
    report: document.querySelector('#report-link'), refresh: document.querySelector('#refresh-label'), card: document.querySelector('#task-card'),
    panel: document.querySelector('#findings-panel'), list: document.querySelector('#findings-list'), descriptionPanel: document.querySelector('#findings-description'), pagination: document.querySelector('#findings-pagination'),
    summary: document.querySelector('#findings-summary'), summaryDescription: document.querySelector('#findings-summary-description'), summaryMetrics: document.querySelector('#findings-summary-metrics'), details: document.querySelector('#details-button'),
    taskList: document.querySelector('#task-list'), evidenceDrawer: document.querySelector('#evidence-drawer'), evidenceTitle: document.querySelector('#evidence-title'),
    evidenceContent: document.querySelector('#evidence-content'), evidenceClose: document.querySelector('#evidence-close'), evidenceBackdrop: document.querySelector('#evidence-backdrop')
  };
  let selectedFile = null;
  let selectedTaskId = null;
  let detailTaskId = null;
  let findingsPage = 1;
  let tasks = [];
  let filenameQuery = '';
  let poller = null;
  const evidenceCache = new Map();

  const backendLabels = { stub: 'STANDARD', openclaw: 'OPENCLAW', langchain: 'LANGCHAIN' };
  const terminalStates = new Set(['completed', 'failed', 'cancelled']);
  const statusLabels = { queued:'排队中', running:'审核中', collecting:'收集中', retrying:'重试中', completed:'已完成', failed:'失败', cancelled:'已取消' };
  const severityOrder = ['重大', '一般', '优化', '观察'];
  const findingsPerPage = 10;

  function setMessage(text = '', kind = '') { message.textContent = text; message.className = `form-message ${kind}`; }
  function setFile(file) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.docx')) { setMessage('请选择 DOCX 格式的技术文档。', 'error'); return; }
    selectedFile = file; elements.title.textContent = file.name; elements.description.textContent = `${formatBytes(file.size)} · 已就绪，可发起新的并发审核`; dropZone.classList.add('has-file'); setMessage();
  }
  function formatBytes(bytes) { return bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
  function agentId() { return `web-${crypto.getRandomValues(new Uint32Array(1))[0].toString(36)}`; }
  function formatTime(value) { try { return new Intl.DateTimeFormat('zh-CN', { hour:'2-digit', minute:'2-digit', second:'2-digit' }).format(new Date(value)); } catch { return '—'; } }
  function statusLabel(status) { return statusLabels[status] || status; }
  function normalizedFilename(value) { return String(value || '').normalize('NFKC').toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu, ''); }
  function visibleTasks() {
    const query = normalizedFilename(filenameQuery);
    return query ? tasks.filter((task) => normalizedFilename(task.document?.filename).includes(query)) : tasks;
  }

  function renderTaskList() {
    const filteredTasks = visibleTasks();
    if (selectedTaskId && !filteredTasks.some((task) => task.task_id === selectedTaskId)) selectedTaskId = null;
    const rows = filteredTasks.map((task) => {
      const row = document.createElement('button'); row.type = 'button'; row.className = `task-row${task.task_id === selectedTaskId ? ' selected' : ''}`;
      row.setAttribute('aria-pressed', String(task.task_id === selectedTaskId));
      row.addEventListener('click', () => { selectedTaskId = task.task_id; detailTaskId = null; findingsPage = 1; renderTaskList(); renderSelectedTask(); });
      const status = document.createElement('span'); status.className = `status-pill ${task.status}`; status.textContent = statusLabel(task.status);
      const file = document.createElement('span'); file.className = 'task-file'; const name = document.createElement('strong'); name.textContent = task.document.filename; const id = document.createElement('small'); id.textContent = `TASK / ${task.task_id.slice(0, 8)}`; file.append(name, id);
      const backendMetric = document.createElement('span'); backendMetric.className = 'queue-metric'; backendMetric.innerHTML = `<span>执行器</span><strong>${backendLabels[task.agent_backend] || String(task.agent_backend).toUpperCase()}</strong>`;
      const findingMetric = document.createElement('span'); findingMetric.className = 'queue-metric'; findingMetric.innerHTML = `<span>发现项</span><strong>${task.findings?.length ?? 0}</strong>`;
      const updated = document.createElement('time'); updated.dateTime = task.updated_at; updated.textContent = formatTime(task.updated_at);
      const open = document.createElement('span'); open.className = 'queue-open'; open.textContent = task.task_id === selectedTaskId ? '已展开 ↘' : '查看 ↗';
      row.append(status, file, backendMetric, findingMetric, updated, open); return row;
    });
    if (filenameQuery.trim() && rows.length === 0) {
      const empty = document.createElement('p'); empty.className = 'task-list-empty'; empty.textContent = '未找到名称匹配的任务。'; rows.push(empty);
    }
    elements.taskList.replaceChildren(...rows);
  }
  function renderSelectedTask() {
    const task = tasks.find((item) => item.task_id === selectedTaskId);
    if (!task) { elements.card.hidden = true; elements.summary.hidden = true; elements.panel.hidden = true; return; }
    const selectedRow = elements.taskList.querySelector('.task-row.selected');
    if (selectedRow) selectedRow.after(elements.card, elements.summary, elements.panel);
    const status = task.status || 'queued'; elements.card.hidden = false; elements.card.classList.remove('task-empty'); elements.status.className = `status-pill ${status}`; elements.status.textContent = statusLabel(status);
    elements.name.textContent = task.document.filename; elements.id.textContent = `TASK / ${task.task_id}`; elements.findings.textContent = task.findings?.length ?? 0;
    elements.backend.textContent = backendLabels[task.agent_backend] || String(task.agent_backend || '—').toUpperCase(); elements.updated.textContent = formatTime(task.updated_at);
    if (task.report_markdown) { elements.report.href = `/api/v1/tasks/${task.task_id}/report.md`; elements.report.classList.remove('disabled'); elements.report.removeAttribute('aria-disabled'); }
    else { elements.report.href = '#'; elements.report.classList.add('disabled'); elements.report.setAttribute('aria-disabled', 'true'); }
    const completed = task.status === 'completed';
    elements.details.hidden = !completed;
    if (completed) renderFindingsSummary(task.findings || []); else elements.summary.hidden = true;
    if (completed && detailTaskId === task.task_id) renderFindings(task, task.findings || []); else elements.panel.hidden = true;
  }
  function renderFindingsSummary(findings) {
    elements.summary.hidden = false;
    const counts = Object.fromEntries(severityOrder.map((severity) => [severity, 0]));
    findings.forEach((finding) => { if (Object.hasOwn(counts, finding.severity)) counts[finding.severity] += 1; });
    const highest = severityOrder.find((severity) => counts[severity]) || '无';
    elements.summaryDescription.textContent = findings.length ? `最高风险：${highest}` : '未识别风险';
    const metrics = [['问题总数', findings.length, 'total'], ...severityOrder.map((severity) => [severity, counts[severity], severity])];
    elements.summaryMetrics.replaceChildren(...metrics.map(([label, value, kind]) => {
      const metric = document.createElement('div'); metric.className = `summary-metric ${kind}`;
      const title = document.createElement('span'); title.textContent = label;
      const count = document.createElement('strong'); count.textContent = value;
      metric.append(title, count); return metric;
    }));
  }
  function renderFindings(task, findings) {
    const sorted = [...findings].sort((left, right) => severityOrder.indexOf(left.severity) - severityOrder.indexOf(right.severity));
    const pages = Math.max(1, Math.ceil(sorted.length / findingsPerPage));
    findingsPage = Math.min(findingsPage, pages);
    const first = (findingsPage - 1) * findingsPerPage;
    const pageFindings = sorted.slice(first, first + findingsPerPage);
    elements.panel.hidden = false; elements.descriptionPanel.textContent = sorted.length ? `按严重级别排序 · 第 ${findingsPage}/${pages} 页` : '未识别风险';
    elements.list.replaceChildren(...pageFindings.map((finding) => {
      const row = document.createElement('div'); row.className = 'finding-row';
      const severity = document.createElement('span'); severity.className = `severity ${finding.severity}`; severity.textContent = finding.severity || '观察';
      const title = document.createElement('strong'); title.textContent = finding.title || '未命名发现';
      const description = document.createElement('p'); description.textContent = finding.problem_description || finding.claim || '—';
      const dimension = document.createElement('small'); dimension.textContent = finding.review_dimension || finding.category || 'TECHNICAL AUDIT';
      const view = document.createElement('button'); view.type = 'button'; view.className = 'evidence-button';
      const refs = finding.evidence_refs?.length || finding.evidence_ids?.length || 0; view.textContent = `查看证据 ${refs}`;
      view.addEventListener('click', () => openEvidence(task, finding));
      row.append(severity, title, description, dimension, view); return row;
    }));
    renderPagination(pages);
  }
  function renderPagination(pages) {
    elements.pagination.replaceChildren();
    if (pages <= 1) { elements.pagination.hidden = true; return; }
    elements.pagination.hidden = false;
    const addButton = (label, page, disabled = false, current = false) => {
      const button = document.createElement('button'); button.type = 'button'; button.textContent = label; button.disabled = disabled; button.className = current ? 'current' : '';
      button.setAttribute('aria-current', current ? 'page' : 'false');
      button.addEventListener('click', () => { findingsPage = page; renderSelectedTask(); }); elements.pagination.append(button);
    };
    addButton('上一页', findingsPage - 1, findingsPage === 1);
    for (let page = 1; page <= pages; page += 1) addButton(String(page), page, false, page === findingsPage);
    addButton('下一页', findingsPage + 1, findingsPage === pages);
  }
  function closeEvidence() { elements.evidenceDrawer.hidden = true; elements.evidenceBackdrop.hidden = true; elements.evidenceDrawer.setAttribute('aria-hidden', 'true'); }
  function legacyRef(value) {
    const text = String(value || '');
    const table = text.match(/txt_block(\d+)_table\1/); if (table) return { evidence_id:`table:${table[1]}`, role:'primary', quote:'', explanation:'' };
    const block = text.match(/txt_(?:block)?(\d+)/); if (block) return { evidence_id:`block:${block[1]}`, role:'primary', quote:'', explanation:'' };
    const image = text.match(/img_block\d+_image_(.+)/); if (image) return { evidence_id:`image:image-${image[1]}`, role:'primary', quote:'', explanation:'' };
    return null;
  }
  async function openEvidence(task, finding) {
    elements.evidenceTitle.textContent = finding.title || '证据复核'; elements.evidenceContent.replaceChildren(); elements.evidenceDrawer.hidden = false; elements.evidenceBackdrop.hidden = false; elements.evidenceDrawer.setAttribute('aria-hidden', 'false');
    const loading = document.createElement('p'); loading.className = 'evidence-status'; loading.textContent = '正在加载可复核证据…'; elements.evidenceContent.append(loading);
    try {
      let evidence = evidenceCache.get(task.task_id);
      if (!evidence) { const response = await fetch(`/api/v1/tasks/${task.task_id}/evidence`); if (!response.ok) throw new Error((await response.json()).detail || '证据包不可用'); evidence = await response.json(); evidenceCache.set(task.task_id, evidence); }
      elements.evidenceContent.replaceChildren(); const refs = finding.evidence_refs?.length ? finding.evidence_refs : (finding.evidence_ids || []).map(legacyRef).filter(Boolean);
      if (!refs.length) throw new Error('该 Finding 没有可展示的证据引用'); refs.forEach((ref, index) => renderEvidenceRef(evidence, ref, index));
    } catch (error) { elements.evidenceContent.replaceChildren(); const status = document.createElement('p'); status.className = 'evidence-status error'; status.textContent = error.message || '证据加载失败'; elements.evidenceContent.append(status); }
  }
  function chapterForBlock(evidence, block) { const chapter = (evidence.chapters || []).find((item) => item.id === block.chapter_id); return chapter ? `第 ${chapter.chapter_number} 章 · ${chapter.title}` : '未归属章节'; }
  function appendEvidenceNote(card, ref) { if (ref.quote) { const quote = document.createElement('blockquote'); quote.textContent = ref.quote; card.append(quote); } if (ref.explanation) { const note = document.createElement('p'); note.className = 'evidence-explanation'; note.textContent = ref.explanation; card.append(note); } }
  function renderEvidenceRef(evidence, ref, index) {
    const card = document.createElement('article'); card.className = 'evidence-card'; const label = document.createElement('small'); label.textContent = `${ref.role === 'supporting' ? '支撑证据' : '主要证据'} ${index + 1} · ${ref.evidence_id}`; card.append(label);
    const image = (evidence.candidate_images || []).find((item) => `image:${item.image_id}` === ref.evidence_id);
    if (image) { const meta = document.createElement('p'); meta.className = 'evidence-location'; meta.textContent = image.chapter_number ? `第 ${image.chapter_number} 章 · ${image.chapter_title || ''}` : '未归属章节'; card.append(meta); const figure = document.createElement('figure'); const img = document.createElement('img'); img.src = image.asset_url; img.alt = ref.quote || image.image_id; figure.append(img); if (ref.region) { const marker = document.createElement('span'); marker.className = 'evidence-region'; Object.assign(marker.style, { left:`${ref.region.x * 100}%`, top:`${ref.region.y * 100}%`, width:`${ref.region.width * 100}%`, height:`${ref.region.height * 100}%` }); figure.append(marker); } card.append(figure); appendEvidenceNote(card, ref); elements.evidenceContent.append(card); return; }
    const block = (evidence.blocks || []).find((item) => `${item.type === 'table' ? 'table' : 'block'}:${item.block_index}` === ref.evidence_id);
    if (!block) { const unavailable = document.createElement('p'); unavailable.className = 'evidence-status error'; unavailable.textContent = '该引用未在证据包中找到。'; card.append(unavailable); elements.evidenceContent.append(card); return; }
    const meta = document.createElement('p'); meta.className = 'evidence-location'; meta.textContent = `${chapterForBlock(evidence, block)} · ${ref.evidence_id}`; card.append(meta); if (block.type === 'table') card.append(renderTable(block, ref.selector)); else { const text = document.createElement('pre'); text.textContent = block.text || '（空段落）'; card.append(text); } appendEvidenceNote(card, ref); elements.evidenceContent.append(card);
  }
  function renderTable(block, selector) { const table = document.createElement('table'); table.className = 'evidence-table'; const rows = block.rows || []; const headers = rows[0] || []; rows.forEach((cells, rowIndex) => { const row = document.createElement('tr'); const match = rowIndex > 0 && selector?.row_match && Object.entries(selector.row_match).every(([key, value]) => cells[headers.indexOf(key)] === value); if (match) row.className = 'evidence-hit'; cells.forEach((value, cellIndex) => { const cell = document.createElement(rowIndex === 0 ? 'th' : 'td'); cell.textContent = value; if (match && selector?.columns?.includes(headers[cellIndex])) cell.className = 'evidence-cell-hit'; row.append(cell); }); table.append(row); }); return table; }
  function updatePolling() {
    const active = tasks.some((task) => !terminalStates.has(task.status));
    if (active && !poller) poller = setInterval(() => refreshTasks().catch(() => {}), 2000);
    if (!active && poller) { clearInterval(poller); poller = null; }
    elements.refresh.textContent = tasks.length === 0 ? '等待任务' : active ? `监测 ${tasks.filter((task) => !terminalStates.has(task.status)).length} 个进行中任务 · 每 2 秒刷新` : `${tasks.length} 个任务 · 已停止刷新`;
  }
  async function refreshTasks() {
    const response = await fetch('/api/v1/tasks'); if (!response.ok) throw new Error('无法获取任务列表');
    tasks = await response.json(); if (!tasks.some((task) => task.task_id === selectedTaskId)) selectedTaskId = tasks[0]?.task_id || null;
    renderTaskList(); renderSelectedTask(); updatePolling();
  }

  dropZone.addEventListener('click', () => fileInput.click()); dropZone.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); fileInput.click(); } });
  fileInput.addEventListener('change', () => setFile(fileInput.files[0]));
  taskFilenameFilter.addEventListener('input', () => { filenameQuery = taskFilenameFilter.value; renderTaskList(); renderSelectedTask(); });
  ['dragenter','dragover'].forEach((eventName) => dropZone.addEventListener(eventName, (event) => { event.preventDefault(); dropZone.classList.add('dragging'); }));
  ['dragleave','drop'].forEach((eventName) => dropZone.addEventListener(eventName, (event) => { event.preventDefault(); dropZone.classList.remove('dragging'); }));
  dropZone.addEventListener('drop', (event) => setFile(event.dataTransfer.files[0]));
  form.addEventListener('submit', async (event) => {
    event.preventDefault(); if (!selectedFile) { setMessage('请先选择一份 DOCX 文档。', 'error'); return; }
    button.disabled = true; button.querySelector('span').textContent = '上传中'; setMessage('正在建立受控审核任务…');
    try {
      const upload = new FormData(); upload.append('file', selectedFile);
      const uploadResponse = await fetch(`/api/v1/agents/${agentId()}/uploads`, { method:'POST', body:upload });
      if (!uploadResponse.ok) throw new Error((await uploadResponse.json()).detail || '文件上传失败');
      const stored = await uploadResponse.json(); button.querySelector('span').textContent = '提交中';
      const taskResponse = await fetch('/api/v1/tasks', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ document:{ filename:stored.filename, content_sha256:stored.content_sha256, source_uri:stored.source_uri }, agent_backend:backend.value }) });
      if (!taskResponse.ok) throw new Error((await taskResponse.json()).detail || '任务创建失败');
      const task = await taskResponse.json(); selectedTaskId = task.task_id; await refreshTasks(); setMessage('新任务已加入队列；其他审核任务将继续独立监测。', 'success');
    } catch (error) { setMessage(error.message || '请求未能完成，请稍后重试。', 'error'); }
    finally { button.disabled = false; button.querySelector('span').textContent = '开始审核'; }
  });
  elements.evidenceClose.addEventListener('click', closeEvidence); elements.evidenceBackdrop.addEventListener('click', closeEvidence);
  elements.details.addEventListener('click', () => { detailTaskId = selectedTaskId; findingsPage = 1; renderSelectedTask(); });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeEvidence(); });
  refreshTasks().catch((error) => { elements.refresh.textContent = '任务列表不可用'; setMessage(error.message, 'error'); });
})();
