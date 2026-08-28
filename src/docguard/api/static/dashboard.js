(() => {
  const form = document.querySelector('#audit-form');
  const fileInput = document.querySelector('#document-file');
  const dropZone = document.querySelector('#drop-zone');
  const button = document.querySelector('#submit-button');
  const message = document.querySelector('#form-message');
  const reviewType = document.querySelector('#review-type');
  const reviewTypeName = document.querySelector('#review-type-name');
  const reviewTypeDescription = document.querySelector('#review-type-description');
  const taskFilenameFilter = document.querySelector('#task-filename-filter');
  const pageTabs = document.querySelector('#page-tabs');
  const navigationItems = Array.from(document.querySelectorAll('.nav-item[data-page]'));
  const approvalRulesMenu = document.querySelector('#approval-rules-menu');
  const approvalRulesTrigger = document.querySelector('#approval-rules-trigger');
  const approvalRulesDropdown = document.querySelector('#approval-rules-dropdown');
  const pagePanels = {
    home: document.querySelector('#home-page'),
    audit: document.querySelector('#audit-page'),
    tasks: document.querySelector('#tasks-page'),
    'approval-rules': document.querySelector('#approval-rules-page')
  };
  const elements = {
    title: document.querySelector('#drop-title'), description: document.querySelector('#drop-description'),
    status: document.querySelector('#task-status'), name: document.querySelector('#task-name'), id: document.querySelector('#task-id'),
    findings: document.querySelector('#findings-count'), backend: document.querySelector('#backend-name'), updated: document.querySelector('#updated-at'),
    report: document.querySelector('#report-link'), actionChain: document.querySelector('#action-chain-link'), refresh: document.querySelector('#refresh-label'), card: document.querySelector('#task-card'),
    panel: document.querySelector('#findings-panel'), list: document.querySelector('#findings-list'), descriptionPanel: document.querySelector('#findings-description'), pagination: document.querySelector('#findings-pagination'),
    summary: document.querySelector('#findings-summary'), summaryDescription: document.querySelector('#findings-summary-description'), summaryMetrics: document.querySelector('#findings-summary-metrics'), details: document.querySelector('#details-button'), continue: document.querySelector('#continue-button'),
    taskList: document.querySelector('#task-list'), evidenceDrawer: document.querySelector('#evidence-drawer'), evidenceTitle: document.querySelector('#evidence-title'),
    evidenceContent: document.querySelector('#evidence-content'), evidenceClose: document.querySelector('#evidence-close'), evidenceBackdrop: document.querySelector('#evidence-backdrop')
  };
  const approvalRuleElements = {
    outline: document.querySelector('#approval-rules-outline'),
    title: document.querySelector('#approval-rule-title'),
    description: document.querySelector('#approval-rule-description'),
    markdown: document.querySelector('#approval-rule-markdown')
  };
  let selectedFile = null;
  let selectedTaskId = null;
  let taskSelectionInitialized = false;
  let detailTaskId = null;
  let findingsPage = 1;
  let tasks = [];
  let filenameQuery = '';
  let poller = null;
  let reviewTypes = [];
  let approvalRules = [];
  let activeApprovalRuleId = null;
  let openPageIds = ['home'];
  let activePageId = 'home';
  const evidenceCache = new Map();

  const backendLabels = { stub: 'STANDARD', openclaw: 'OPENCLAW', langchain: 'LANGCHAIN' };
  const terminalStates = new Set(['completed', 'failed', 'cancelled']);
  const statusLabels = { queued:'排队中', running:'审核中', collecting:'收集中', retrying:'重试中', completed:'已完成', failed:'失败', cancelled:'已取消' };
  const severityOrder = ['重大', '一般', '优化', '观察'];
  const findingsPerPage = 10;
  const pageDefinitions = {
    home: { label: '首页' },
    audit: { label: '文档审核' },
    tasks: { label: '任务队列' },
    'approval-rules': { label: '审批规则' }
  };

  function setMessage(text = '', kind = '') { message.textContent = text; message.className = `form-message ${kind}`; }
  function renderPageTabs() {
    const tabs = openPageIds.map((pageId) => {
      const page = pageDefinitions[pageId];
      const tab = document.createElement('div');
      tab.className = `page-tab${pageId === activePageId ? ' active' : ''}`;
      tab.dataset.page = pageId;
      const label = document.createElement('button');
      label.className = 'page-tab-label';
      label.type = 'button';
      label.setAttribute('role', 'tab');
      label.setAttribute('aria-selected', String(pageId === activePageId));
      label.setAttribute('aria-controls', `${pageId}-page`);
      label.textContent = page.label;
      label.addEventListener('click', () => openPage(pageId, true));
      tab.append(label);
      if (pageId !== 'home') {
        const close = document.createElement('button');
        close.className = 'page-tab-close';
        close.type = 'button';
        close.setAttribute('aria-label', `关闭${page.label}页签`);
        close.textContent = '×';
        close.addEventListener('click', (event) => {
          event.stopPropagation();
          closePage(pageId);
        });
        tab.append(close);
      }
      return tab;
    });
    pageTabs.replaceChildren(...tabs);
  }
  function openPage(pageId, shouldScroll = false) {
    if (!pageDefinitions[pageId] || !pagePanels[pageId]) return;
    if (!openPageIds.includes(pageId)) openPageIds.push(pageId);
    activePageId = pageId;
    Object.entries(pagePanels).forEach(([id, panel]) => { panel.hidden = id !== pageId; });
    navigationItems.forEach((item) => {
      const active = item.dataset.page === pageId;
      item.classList.toggle('active', active);
      if (active) item.setAttribute('aria-current', 'page');
      else item.removeAttribute('aria-current');
    });
    approvalRulesTrigger.classList.toggle('active', pageId === 'approval-rules');
    if (pageId !== 'approval-rules') setApprovalRulesMenuOpen(false);
    renderPageTabs();
    if (shouldScroll) window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  function closePage(pageId) {
    if (pageId === 'home') return;
    const closingIndex = openPageIds.indexOf(pageId);
    if (closingIndex === -1) return;
    openPageIds.splice(closingIndex, 1);
    if (activePageId === pageId) {
      activePageId = openPageIds[Math.min(closingIndex, openPageIds.length - 1)] || 'home';
    }
    openPage(activePageId);
  }
  function updateReviewTypeDescription() {
    const selected = reviewTypes.find((item) => item.review_type_id === reviewType.value);
    reviewTypeName.textContent = selected ? `${selected.display_name} · v${selected.version}` : '—';
    reviewTypeDescription.textContent = selected?.description || '请选择平台提供的报告审核类型';
  }
  async function loadReviewTypes() {
    const response = await fetch('/api/v1/review-types');
    if (!response.ok) throw new Error('无法加载可用报告类型');
    reviewTypes = await response.json();
    reviewType.replaceChildren();
    reviewTypes.forEach((item) => {
      const option = document.createElement('option'); option.value = item.review_type_id;
      option.textContent = item.display_name; reviewType.append(option);
    });
    reviewType.disabled = reviewTypes.length === 0;
    updateReviewTypeDescription();
  }
  function approvalRuleStatus(text, kind = '') {
    const status = document.createElement('p');
    status.className = `approval-rule-reader-status ${kind}`;
    status.textContent = text;
    return status;
  }
  function setApprovalRulesMenuOpen(open) {
    approvalRulesMenu.classList.toggle('is-open', open);
    approvalRulesTrigger.setAttribute('aria-expanded', String(open));
  }
  function renderApprovalRulesMenu() {
    approvalRulesDropdown.replaceChildren();
    if (!approvalRules.length) {
      approvalRulesDropdown.append(approvalRuleStatus('暂未配置审批规则。'));
      return;
    }
    approvalRules.forEach((rule) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = `approval-rules-menu-item${rule.rule_id === activeApprovalRuleId ? ' selected' : ''}`;
      item.setAttribute('role', 'menuitem');
      const title = document.createElement('strong'); title.textContent = rule.title;
      const description = document.createElement('small'); description.textContent = rule.description || '查看审批规则';
      item.append(title, description);
      item.addEventListener('click', () => {
        setApprovalRulesMenuOpen(false);
        openApprovalRule(rule.rule_id);
      });
      approvalRulesDropdown.append(item);
    });
  }
  function renderApprovalRuleOutline(outline) {
    approvalRuleElements.outline.replaceChildren();
    if (!outline.length) {
      approvalRuleElements.outline.append(approvalRuleStatus('此规则暂无可导航的标题。'));
      return;
    }
    outline.forEach((item) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `approval-rules-outline-item outline-level-${item.level}`;
      button.textContent = item.title;
      button.addEventListener('click', () => {
        const target = document.getElementById(item.anchor);
        if (!target) return;
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        target.tabIndex = -1;
        target.focus({ preventScroll: true });
      });
      approvalRuleElements.outline.append(button);
    });
  }
  async function loadApprovalRules() {
    try {
      const response = await fetch('/api/v1/approval-rules');
      if (!response.ok) throw new Error('无法加载审批规则');
      const payload = await response.json();
      if (!Array.isArray(payload)) throw new Error('审批规则目录格式无效');
      approvalRules = payload;
      renderApprovalRulesMenu();
    } catch (error) {
      approvalRules = [];
      approvalRulesDropdown.replaceChildren(approvalRuleStatus(error.message || '审批规则不可用。', 'error'));
      throw error;
    }
  }
  async function openApprovalRule(ruleId) {
    const summary = approvalRules.find((rule) => rule.rule_id === ruleId);
    activeApprovalRuleId = ruleId;
    renderApprovalRulesMenu();
    openPage('approval-rules', true);
    approvalRuleElements.title.textContent = summary?.title || '审批规则';
    approvalRuleElements.description.textContent = summary?.description || '正在加载规则内容。';
    approvalRuleElements.outline.replaceChildren(approvalRuleStatus('正在加载章节大纲…'));
    approvalRuleElements.markdown.replaceChildren(approvalRuleStatus('正在加载 Markdown 规则内容…'));
    try {
      const response = await fetch(`/api/v1/approval-rules/${encodeURIComponent(ruleId)}`);
      if (!response.ok) throw new Error((await response.json()).detail || '无法加载审批规则内容');
      const rule = await response.json();
      if (activeApprovalRuleId !== ruleId) return;
      approvalRuleElements.title.textContent = rule.title;
      approvalRuleElements.description.textContent = rule.description || '该规则未提供摘要。';
      // The server renders Markdown with raw HTML disabled before this assignment.
      approvalRuleElements.markdown.innerHTML = rule.html;
      renderApprovalRuleOutline(Array.isArray(rule.outline) ? rule.outline : []);
    } catch (error) {
      if (activeApprovalRuleId !== ruleId) return;
      approvalRuleElements.outline.replaceChildren(approvalRuleStatus('章节大纲不可用。', 'error'));
      approvalRuleElements.markdown.replaceChildren(approvalRuleStatus(error.message || '规则内容加载失败。', 'error'));
    }
  }
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
      row.addEventListener('click', () => {
        const isSelected = task.task_id === selectedTaskId;
        selectedTaskId = isSelected ? null : task.task_id;
        taskSelectionInitialized = true;
        detailTaskId = null;
        findingsPage = 1;
        renderTaskList();
        renderSelectedTask();
      });
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
    if (!task) {
      elements.card.hidden = true;
      elements.summary.hidden = true;
      elements.panel.hidden = true;
      elements.card.classList.remove('task-expanded');
      elements.summary.classList.remove('task-expanded');
      elements.panel.classList.remove('task-expanded');
      return;
    }
    const selectedRow = elements.taskList.querySelector('.task-row.selected');
    if (selectedRow) selectedRow.after(elements.card, elements.summary, elements.panel);
    elements.card.classList.add('task-expanded');
    elements.summary.classList.add('task-expanded');
    elements.panel.classList.add('task-expanded');
    const status = task.status || 'queued'; elements.card.hidden = false; elements.card.classList.remove('task-empty'); elements.status.className = `status-pill ${status}`; elements.status.textContent = statusLabel(status);
    elements.name.textContent = task.document.filename; elements.id.textContent = `TASK / ${task.task_id}`; elements.findings.textContent = task.findings?.length ?? 0;
    elements.backend.textContent = backendLabels[task.agent_backend] || String(task.agent_backend || '—').toUpperCase(); elements.updated.textContent = formatTime(task.updated_at);
    if (task.report_markdown) { elements.report.href = `/api/v1/tasks/${task.task_id}/report.md`; elements.report.classList.remove('disabled'); elements.report.removeAttribute('aria-disabled'); }
    else { elements.report.href = '#'; elements.report.classList.add('disabled'); elements.report.setAttribute('aria-disabled', 'true'); }
    if (elements.actionChain) {
      if (task.agent_backend === 'openclaw' && task.attempts?.length) { elements.actionChain.href = `/api/v1/tasks/${task.task_id}/action-chain.md`; elements.actionChain.classList.remove('disabled'); elements.actionChain.removeAttribute('aria-disabled'); }
      else { elements.actionChain.href = '#'; elements.actionChain.classList.add('disabled'); elements.actionChain.setAttribute('aria-disabled', 'true'); }
    }
    const completed = task.status === 'completed';
    elements.details.hidden = !completed;
    elements.continue.hidden = !(task.status === 'collecting' && task.agent_backend === 'openclaw');
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
    tasks = await response.json();
    if (selectedTaskId && !tasks.some((task) => task.task_id === selectedTaskId)) selectedTaskId = null;
    if (!taskSelectionInitialized) { selectedTaskId = tasks[0]?.task_id || null; taskSelectionInitialized = true; }
    renderTaskList(); renderSelectedTask(); updatePolling();
  }

  navigationItems.forEach((item) => item.addEventListener('click', (event) => {
    event.preventDefault();
    openPage(item.dataset.page, true);
  }));
  approvalRulesTrigger.addEventListener('click', () => {
    setApprovalRulesMenuOpen(!approvalRulesMenu.classList.contains('is-open'));
  });
  approvalRulesMenu.addEventListener('pointerenter', () => setApprovalRulesMenuOpen(true));
  approvalRulesMenu.addEventListener('pointerleave', () => setApprovalRulesMenuOpen(false));
  approvalRulesMenu.addEventListener('focusin', () => setApprovalRulesMenuOpen(true));
  approvalRulesMenu.addEventListener('focusout', () => {
    window.setTimeout(() => {
      if (!approvalRulesMenu.contains(document.activeElement)) setApprovalRulesMenuOpen(false);
    }, 0);
  });
  document.addEventListener('click', (event) => {
    if (!approvalRulesMenu.contains(event.target)) setApprovalRulesMenuOpen(false);
  });
  openPage('home');
  dropZone.addEventListener('click', () => fileInput.click()); dropZone.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); fileInput.click(); } });
  fileInput.addEventListener('change', () => setFile(fileInput.files[0]));
  reviewType.addEventListener('change', updateReviewTypeDescription);
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
      if (!reviewType.value) throw new Error('请先选择报告类型');
      const taskResponse = await fetch('/api/v1/tasks', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ document:{ filename:stored.filename, content_sha256:stored.content_sha256, source_uri:stored.source_uri }, review_type_id:reviewType.value }) });
      if (!taskResponse.ok) throw new Error((await taskResponse.json()).detail || '任务创建失败');
      const task = await taskResponse.json(); selectedTaskId = task.task_id; await refreshTasks(); setMessage('新任务已加入队列；其他审核任务将继续独立监测。', 'success');
    } catch (error) { setMessage(error.message || '请求未能完成，请稍后重试。', 'error'); }
    finally { button.disabled = false; button.querySelector('span').textContent = '开始审核'; }
  });
  elements.evidenceClose.addEventListener('click', closeEvidence); elements.evidenceBackdrop.addEventListener('click', closeEvidence);
  elements.details.addEventListener('click', () => { detailTaskId = selectedTaskId; findingsPage = 1; renderSelectedTask(); });
  elements.continue.addEventListener('click', async () => {
    const task = tasks.find((item) => item.task_id === selectedTaskId); if (!task) return;
    elements.continue.disabled = true; elements.continue.querySelector('span').textContent = '…';
    try {
      const response = await fetch(`/api/v1/tasks/${task.task_id}/continue`, { method:'POST' });
      if (!response.ok) throw new Error((await response.json()).detail || '继续任务失败');
      setMessage('已在原会话中发送“继续”，正在重新收集 SSE 消息。', 'success'); await refreshTasks();
    } catch (error) { setMessage(error.message || '继续任务失败，请稍后重试。', 'error'); }
    finally { elements.continue.disabled = false; elements.continue.querySelector('span').textContent = '↻'; }
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    closeEvidence();
    setApprovalRulesMenuOpen(false);
  });
  Promise.all([loadReviewTypes(), refreshTasks()]).catch((error) => { elements.refresh.textContent = '任务列表不可用'; setMessage(error.message, 'error'); });
  loadApprovalRules().catch(() => {});
})();
