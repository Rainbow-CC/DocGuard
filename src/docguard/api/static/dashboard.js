(() => {
  const form = document.querySelector('#audit-form');
  const fileInput = document.querySelector('#document-file');
  const dropZone = document.querySelector('#drop-zone');
  const button = document.querySelector('#submit-button');
  const message = document.querySelector('#form-message');
  const backend = document.querySelector('#backend');
  const elements = {
    title: document.querySelector('#drop-title'), description: document.querySelector('#drop-description'),
    status: document.querySelector('#task-status'), name: document.querySelector('#task-name'), id: document.querySelector('#task-id'),
    findings: document.querySelector('#findings-count'), backend: document.querySelector('#backend-name'), updated: document.querySelector('#updated-at'),
    report: document.querySelector('#report-link'), refresh: document.querySelector('#refresh-label'), card: document.querySelector('#task-card'),
    panel: document.querySelector('#findings-panel'), list: document.querySelector('#findings-list'), descriptionPanel: document.querySelector('#findings-description'),
    taskList: document.querySelector('#task-list')
  };
  let selectedFile = null;
  let selectedTaskId = null;
  let tasks = [];
  let poller = null;

  const backendLabels = { stub: 'STANDARD', openclaw: 'OPENCLAW', langchain: 'LANGCHAIN' };
  const terminalStates = new Set(['completed', 'failed', 'cancelled']);
  const statusLabels = { queued:'排队中', running:'审核中', collecting:'收集中', retrying:'重试中', completed:'已完成', failed:'失败', cancelled:'已取消' };

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

  function renderTaskList() {
    elements.taskList.replaceChildren(...tasks.map((task) => {
      const row = document.createElement('button'); row.type = 'button'; row.className = `task-row${task.task_id === selectedTaskId ? ' selected' : ''}`;
      row.setAttribute('aria-pressed', String(task.task_id === selectedTaskId));
      row.addEventListener('click', () => { selectedTaskId = task.task_id; renderTaskList(); renderSelectedTask(); });
      const status = document.createElement('span'); status.className = `status-pill ${task.status}`; status.textContent = statusLabel(task.status);
      const file = document.createElement('span'); file.className = 'task-file'; const name = document.createElement('strong'); name.textContent = task.document.filename; const id = document.createElement('small'); id.textContent = `TASK / ${task.task_id.slice(0, 8)}`; file.append(name, id);
      const backendMetric = document.createElement('span'); backendMetric.className = 'queue-metric'; backendMetric.innerHTML = `<span>执行器</span><strong>${backendLabels[task.agent_backend] || String(task.agent_backend).toUpperCase()}</strong>`;
      const findingMetric = document.createElement('span'); findingMetric.className = 'queue-metric'; findingMetric.innerHTML = `<span>发现项</span><strong>${task.findings?.length ?? 0}</strong>`;
      const updated = document.createElement('time'); updated.dateTime = task.updated_at; updated.textContent = formatTime(task.updated_at);
      const open = document.createElement('span'); open.className = 'queue-open'; open.textContent = task.task_id === selectedTaskId ? '已展开 ↘' : '查看 ↗';
      row.append(status, file, backendMetric, findingMetric, updated, open); return row;
    }));
  }
  function renderSelectedTask() {
    const task = tasks.find((item) => item.task_id === selectedTaskId);
    if (!task) { elements.card.hidden = true; elements.panel.hidden = true; return; }
    const status = task.status || 'queued'; elements.card.hidden = false; elements.card.classList.remove('task-empty'); elements.status.className = `status-pill ${status}`; elements.status.textContent = statusLabel(status);
    elements.name.textContent = task.document.filename; elements.id.textContent = `TASK / ${task.task_id}`; elements.findings.textContent = task.findings?.length ?? 0;
    elements.backend.textContent = backendLabels[task.agent_backend] || String(task.agent_backend || '—').toUpperCase(); elements.updated.textContent = formatTime(task.updated_at);
    if (task.report_markdown) { elements.report.href = `/api/v1/tasks/${task.task_id}/report.md`; elements.report.classList.remove('disabled'); elements.report.removeAttribute('aria-disabled'); }
    else { elements.report.href = '#'; elements.report.classList.add('disabled'); elements.report.setAttribute('aria-disabled', 'true'); }
    if (task.findings?.length) renderFindings(task.findings); else elements.panel.hidden = true;
  }
  function renderFindings(findings) {
    elements.panel.hidden = false; elements.descriptionPanel.textContent = `${findings.length} 项已识别风险`;
    elements.list.replaceChildren(...findings.slice(0, 5).map((finding) => {
      const row = document.createElement('div'); row.className = 'finding-row';
      const severity = document.createElement('span'); severity.className = `severity ${finding.severity}`; severity.textContent = finding.severity || '观察';
      const title = document.createElement('strong'); title.textContent = finding.title || '未命名发现';
      const description = document.createElement('p'); description.textContent = finding.problem_description || finding.claim || '—';
      const dimension = document.createElement('small'); dimension.textContent = finding.review_dimension || finding.category || 'TECHNICAL AUDIT';
      row.append(severity, title, description, dimension); return row;
    }));
  }
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
  refreshTasks().catch((error) => { elements.refresh.textContent = '任务列表不可用'; setMessage(error.message, 'error'); });
})();
