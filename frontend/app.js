// ===== Configuration =====
const API_BASE = '/api/v1';
let pollingIntervals = {};
let systemLogInterval = null;
let currentTranscript = null;

// ===== Initialize =====
document.addEventListener('DOMContentLoaded', () => {
    initDropzone();
    loadApiKey(); // Load from localStorage if present
    refreshJobs();

    // Update active job timers every second
    setInterval(updateLiveTimers, 1000);

    // Load saved settings (prompt)
    loadMemoizedPrompt();
    document.getElementById('transcriptionPrompt').addEventListener('input', saveMemoizedPrompt);
});

// ===== Settings Persistence (Prompt) =====
function loadMemoizedPrompt() {
    const savedPrompt = localStorage.getItem('stt_prompt');
    if (savedPrompt !== null) {
        document.getElementById('transcriptionPrompt').value = savedPrompt;
    }
}

function saveMemoizedPrompt(e) {
    localStorage.setItem('stt_prompt', e.target.value);
}

// ===== API Key Management =====
function loadApiKey() {
    const saved = localStorage.getItem('stt_api_key');
    if (saved) {
        document.getElementById('apiKey').value = saved;
    }
    // No default value set here to prevent leaks
}

function getApiKey() {
    const key = document.getElementById('apiKey').value;
    // Only save if it's not empty, to avoid wiping good keys
    if (key.trim()) {
        localStorage.setItem('stt_api_key', key);
    }
    return key;
}

// ===== Settings Editor Logic =====
let currentSettings = null;

async function openSettings() {
    const modal = document.getElementById('settingsModal');
    const statusDiv = document.getElementById('settingsStatus');
    statusDiv.textContent = 'Loading configuration...';
    statusDiv.className = 'settings-status info';

    modal.classList.add('open');

    try {
        const response = await fetch(`${API_BASE}/settings`, {
            headers: { 'X-API-Key': getApiKey() }
        });

        if (response.status === 401) {
            throw new Error('Unauthorized. Please enter a valid Session Key.');
        }

        if (!response.ok) throw new Error('Failed to load settings');

        const config = await response.json();
        currentSettings = config;
        renderSettingsForm(config);
        statusDiv.textContent = '';
    } catch (error) {
        statusDiv.textContent = `Error: ${error.message}`;
        statusDiv.className = 'settings-status error';
        document.getElementById('settingsContent').innerHTML = `
            <div class="error-placeholder">
                <p>⚠️ Could not load settings.</p>
                <p>${error.message}</p>
                ${error.message.includes('Unauthorized') ? '<p>Please check the Session Key in the top right.</p>' : ''}
            </div>
        `;
    }
}

function closeSettingsModal() {
    document.getElementById('settingsModal').classList.remove('open');
    currentSettings = null;
}

function renderSettingsForm(config) {
    const sidebar = document.getElementById('settingsSidebar');
    const content = document.getElementById('settingsContent');

    sidebar.innerHTML = '';
    content.innerHTML = '';

    // Create sections
    config.sections.forEach((section, index) => {
        // Sidebar Link
        const link = document.createElement('div');
        link.className = `settings-nav-item ${index === 0 ? 'active' : ''}`;
        link.textContent = section.name;
        link.onclick = () => switchSettingsTab(index);
        sidebar.appendChild(link);

        // Content Section
        const sectionDiv = document.createElement('div');
        sectionDiv.className = `settings-section ${index === 0 ? 'active' : ''}`;
        sectionDiv.id = `settings-section-${index}`;

        const title = document.createElement('h3');
        title.textContent = section.name;
        sectionDiv.appendChild(title);

        const formGrid = document.createElement('div');
        formGrid.className = 'settings-form-grid';

        section.items.forEach(item => {
            const group = document.createElement('div');
            group.className = 'settings-field';

            const label = document.createElement('label');
            label.textContent = item.key;
            label.title = item.comment || '';

            const input = document.createElement('input');
            input.type = 'text';
            input.value = item.value;
            input.dataset.sectionIndex = index;
            input.dataset.key = item.key;

            // Mask potential secrets
            if (item.key.includes('KEY') || item.key.includes('SECRET') || item.key.includes('PASSWORD')) {
                input.type = 'password';

                // Wrap in toggle group
                const wrapper = document.createElement('div');
                wrapper.className = 'input-with-toggle';

                // Create unique ID for toggle targeting
                const inputId = `setting-${index}-${item.key}`;
                input.id = inputId;

                const toggleBtn = document.createElement('button');
                toggleBtn.className = 'toggle-password';
                toggleBtn.innerHTML = `<svg viewBox="0 0 24 24"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>`;
                toggleBtn.onclick = () => togglePasswordVisibility(inputId);
                toggleBtn.title = "Toggle Visibility";

                wrapper.appendChild(input);
                wrapper.appendChild(toggleBtn);
                group.appendChild(label);
                group.appendChild(wrapper);
            } else {
                group.appendChild(label);
                group.appendChild(input);
            }

            if (item.comment) {
                const hint = document.createElement('span');
                hint.className = 'field-hint';
                hint.textContent = item.comment;
                group.appendChild(hint);
            }

            group.appendChild(label);
            group.appendChild(input);
            formGrid.appendChild(group);
        });

        sectionDiv.appendChild(formGrid);
        content.appendChild(sectionDiv);
    });
}

function switchSettingsTab(index) {
    document.querySelectorAll('.settings-nav-item').forEach((el, i) => {
        el.classList.toggle('active', i === index);
    });
    document.querySelectorAll('.settings-section').forEach((el, i) => {
        el.classList.toggle('active', i === index);
    });
}

async function saveSettings() {
    if (!currentSettings) return;

    const statusDiv = document.getElementById('settingsStatus');
    statusDiv.textContent = 'Saving...';
    statusDiv.className = 'settings-status info';

    // Collect values from inputs
    document.querySelectorAll('.settings-field input').forEach(input => {
        const sIndex = parseInt(input.dataset.sectionIndex);
        const key = input.dataset.key;

        // Find item in currentSettings object
        const section = currentSettings.sections[sIndex];
        const item = section.items.find(i => i.key === key);
        if (item) {
            item.value = input.value;
        }
    });

    try {
        const response = await fetch(`${API_BASE}/settings`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-API-Key': getApiKey()
            },
            body: JSON.stringify(currentSettings)
        });

        if (!response.ok) throw new Error('Failed to save settings');

        const result = await response.json();
        showToast('Configuration saved successfully', 'success');
        statusDiv.textContent = 'Saved!';
        statusDiv.className = 'settings-status success';

        setTimeout(() => closeSettingsModal(), 1000);

        // If API Key changed, might need to update session?
        // For now, let user manage that manually via the Session Key input.
    } catch (error) {
        statusDiv.textContent = `Error: ${error.message}`;
        statusDiv.className = 'settings-status error';
    }
}

// ===== File Upload =====
function initDropzone() {
    const dropzone = document.getElementById('dropzone');
    const fileInput = document.getElementById('fileInput');

    dropzone.addEventListener('click', () => fileInput.click());

    dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropzone.classList.add('dragover');
    });

    dropzone.addEventListener('dragleave', () => {
        dropzone.classList.remove('dragover');
    });

    dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('dragover');
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            handleFileSelect(files[0]);
        }
    });

    fileInput.addEventListener('change', () => {
        if (fileInput.files.length > 0) {
            handleFileSelect(fileInput.files[0]);
        }
    });
}

let selectedFile = null;

function handleFileSelect(file) {
    const audioExtensions = ['mp3', 'wav', 'm4a', 'flac', 'ogg', 'webm', 'aac', 'wma', 'opus'];
    const videoExtensions = ['mp4', 'mkv', 'avi', 'mov', 'wmv', 'flv', 'mpeg', 'mpg', '3gp'];
    const validExtensions = [...audioExtensions, ...videoExtensions];
    const ext = file.name.split('.').pop().toLowerCase();

    if (!validExtensions.includes(ext)) {
        showToast('Invalid file format. Please upload a supported audio or video file.', 'error');
        return;
    }

    selectedFile = file;
    document.getElementById('dropzone').style.display = 'none';
    document.getElementById('selectedFile').style.display = 'block';
    document.getElementById('fileName').textContent = file.name;
    document.getElementById('fileSize').textContent = formatFileSize(file.size);
    document.getElementById('fileType').textContent = ext.toUpperCase();
    document.getElementById('submitBtn').disabled = false;
}

function clearFile() {
    selectedFile = null;
    document.getElementById('dropzone').style.display = 'block';
    document.getElementById('selectedFile').style.display = 'none';
    document.getElementById('fileInput').value = '';
    document.getElementById('submitBtn').disabled = true;
}

function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

// ===== Submit Transcription =====
async function submitTranscription() {
    if (!selectedFile) return;

    const submitBtn = document.getElementById('submitBtn');
    const btnText = submitBtn.querySelector('.btn-text');
    const btnSpinner = submitBtn.querySelector('.btn-spinner');

    submitBtn.disabled = true;
    btnText.textContent = 'Uploading...';
    btnSpinner.style.display = 'inline-block';

    const formData = new FormData();
    formData.append('audio', selectedFile);

    const language = document.getElementById('language').value;
    const prompt = document.getElementById('transcriptionPrompt').value.trim();

    const config = {
        provider: document.getElementById('provider').value,
        language: language,
        context: prompt ? { prompt: prompt } : undefined
    };
    formData.append('config', JSON.stringify(config));

    try {
        const response = await fetch(`${API_BASE}/transcribe`, {
            method: 'POST',
            headers: {
                'X-API-Key': getApiKey()
            },
            body: formData
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to submit transcription');
        }

        const data = await response.json();
        showToast(`Job submitted: ${data.job_id.slice(0, 8)}...`, 'success');
        // Do NOT clear file - let them use it again
        // clearFile(); 
        refreshJobs();
        startPolling(data.job_id);
    } catch (error) {
        showToast(error.message, 'error');
    } finally {
        // Only re-enable button if there's still a selected file
        // (clearFile() sets selectedFile to null and shows dropzone)
        submitBtn.disabled = !selectedFile;
        btnText.textContent = 'Start Transcription';
        btnSpinner.style.display = 'none';
    }
}

// ===== Jobs Management =====
async function refreshJobs() {
    try {
        const response = await fetch(`${API_BASE}/jobs?limit=20`, {
            headers: { 'X-API-Key': getApiKey() }
        });

        if (!response.ok) throw new Error('Failed to fetch jobs');

        const data = await response.json();
        renderJobsList(data.jobs);

        // Start polling for active jobs
        data.jobs.forEach(job => {
            if (['pending', 'uploaded', 'processing'].includes(job.status)) {
                startPolling(job.job_id);
            }
        });
    } catch (error) {
        console.error('Failed to refresh jobs:', error);
    }
}

function renderJobsList(jobs) {
    const container = document.getElementById('jobsList');

    if (jobs.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <span>📋</span>
                <p>No jobs yet. Upload an audio file to get started.</p>
            </div>
        `;
        return;
    }

    container.innerHTML = jobs.map(job => renderJobCard(job)).join('');
}

function renderJobCard(job) {
    const statusIcon = {
        pending: '⏳',
        uploaded: '📤',
        processing: '⚙️',
        completed: '✅',
        failed: '❌',
        cancelled: '🚫'
    }[job.status] || '❓';

    const progress = job.total_chunks > 0
        ? Math.round((job.completed_chunks / job.total_chunks) * 100)
        : 0;

    const showProgress = ['processing', 'uploaded'].includes(job.status);
    const showChunks = job.total_chunks > 1;
    const showRetry = job.status === 'failed';
    const showCancel = ['pending', 'uploaded', 'processing'].includes(job.status);
    const showResult = job.status === 'completed';
    const showDownloadPartial = job.completed_chunks > 0 && job.status === 'failed';
    const isFinished = ['completed', 'failed', 'cancelled'].includes(job.status);
    const isActive = ['pending', 'uploaded', 'processing'].includes(job.status);

    // Calculate elapsed time for active jobs
    let elapsedStr = '--';
    if (job.created_at) {
        const createdAt = new Date(job.created_at);
        if (!isNaN(createdAt.getTime())) {
            const now = new Date();
            const elapsedMs = now - createdAt;
            const elapsedMinutes = Math.floor(elapsedMs / 60000);
            const elapsedSeconds = Math.floor((elapsedMs % 60000) / 1000);
            elapsedStr = elapsedMinutes > 0
                ? `${elapsedMinutes}m ${elapsedSeconds}s`
                : `${elapsedSeconds}s`;
        }
    }

    return `
        <div class="job-card ${isActive ? 'job-active' : ''}" id="job-${job.job_id}" data-created-at="${job.created_at}">
            <div class="job-header">
                <div class="job-info">
                    <div class="job-title-row">
                        <h3>${job.original_filename || 'Unknown file'}</h3>
                        <span class="job-id-badge" title="Copy ID" onclick="navigator.clipboard.writeText('${job.job_id}')"><span class="job-id-label">Job ID:</span> ${job.job_id}</span>
                    </div>
                    <div class="job-meta">
                        <span>🎵 ${job.duration_seconds ? job.duration_seconds.toFixed(1) + 's' : 'N/A'}</span>
                        <span>📦 ${formatFileSize(job.file_size_bytes || 0)}</span>
                        <span>🔧 ${job.provider || 'Unknown'}</span>
                        <span>📅 ${new Date(job.created_at).toLocaleDateString()}</span>
                    </div>
                </div>
                <div class="job-status">
                    ${isActive ? `<span class="status-spinner"></span>` : ''}
                    <span class="status-badge status-${job.status}">
                        ${statusIcon} ${job.status}
                    </span>
                </div>
            </div>

            ${isActive ? `
                <div class="status-detail">
                    <span class="elapsed-time">⏱️ Elapsed: ${elapsedStr}</span>
                    ${job.status === 'processing' ? `
                        <span class="processing-info">
                            ${job.total_chunks > 0
                    ? (job.completed_chunks >= job.total_chunks
                        ? 'Finalizing and merging transcript...'
                        : `Processing chunk ${job.completed_chunks + 1} of ${job.total_chunks}...`)
                    : `Processing audio chunks with ${job.provider || 'provider'}...`}
                        </span>` : ''}
                    ${job.status === 'pending' ? `<span class="processing-info">Waiting in queue...</span>` : ''}
                    ${job.status === 'uploaded' ? `<span class="processing-info">Splitting audio into chunks...</span>` : ''}
                </div>
            ` : ''}

            ${showProgress ? `
                <div class="progress-container">
                    <div class="progress-bar">
                        <div class="progress-fill ${job.status === 'processing' ? 'progress-animated' : ''}" style="width: ${progress}%"></div>
                    </div>
                    <div class="progress-text">
                        <span>${job.completed_chunks} / ${job.total_chunks} chunks</span>
                        <span>${progress}%</span>
                    </div>
                </div>
            ` : ''}

            ${job.error_message ? `
                <div class="error-message" style="color: var(--error); font-size: 0.85rem; margin-top: 0.5rem;">
                    ⚠️ ${job.error_message.slice(0, 100)}${job.error_message.length > 100 ? '...' : ''}
                </div>
            ` : ''}

            <div class="chunks-section" id="chunks-${job.job_id}"></div>

            <div class="job-actions">
                <button class="btn btn-secondary" onclick="viewLogs('${job.job_id}')">📋 Logs</button>
                ${showChunks ? `<button class="btn btn-secondary" onclick="toggleChunks('${job.job_id}')">📊 Show Chunks</button>` : ''}
                ${showResult ? `<button class="btn btn-success" onclick="viewResult('${job.job_id}')">📄 View Result</button>` : ''}
                ${showResult ? `<button class="btn btn-primary" onclick="downloadBundle('${job.job_id}')">⬇️ Download</button>` : ''}
                ${showDownloadPartial ? `<button class="btn btn-secondary" onclick="downloadPartial('${job.job_id}')">⬇️ Download Partial</button>` : ''}
                ${showRetry ? `<button class="btn btn-primary" onclick="retryJob('${job.job_id}')">🔄 Retry</button>` : ''}
                ${showCancel ? `<button class="btn btn-secondary" onclick="cancelJob('${job.job_id}')">⏹️ Cancel</button>` : ''}
                ${isFinished ? `<button class="btn btn-danger" onclick="showDeleteConfirm('${job.job_id}', '${(job.original_filename || 'Unknown').replace(/'/g, "\\'")}', ${job.total_chunks || 0})">🗑️ Delete</button>` : ''}
                ${!isFinished ? `<button class="btn btn-icon" onclick="showDeleteConfirm('${job.job_id}', '${(job.original_filename || 'Unknown').replace(/'/g, "\\'")}', ${job.total_chunks || 0})" title="Delete">🗑️</button>` : ''}
            </div>
        </div>
    `;
}

// ===== Polling =====
function startPolling(jobId) {
    if (pollingIntervals[jobId]) return;

    pollingIntervals[jobId] = setInterval(async () => {
        try {
            const response = await fetch(`${API_BASE}/jobs/${jobId}/progress?include_chunks=true`, {
                headers: { 'X-API-Key': getApiKey() }
            });

            if (!response.ok) throw new Error('Failed to fetch progress');

            const progress = await response.json();
            updateJobCard(jobId, progress);

            if (['completed', 'failed', 'cancelled'].includes(progress.status)) {
                stopPolling(jobId);
                if (progress.status === 'completed') {
                    showToast(`Job ${jobId.slice(0, 8)}... completed!`, 'success');
                }
                refreshJobs();
            }
        } catch (error) {
            console.error(`Polling error for ${jobId}:`, error);
        }
    }, 2000);
}

function stopPolling(jobId) {
    if (pollingIntervals[jobId]) {
        clearInterval(pollingIntervals[jobId]);
        delete pollingIntervals[jobId];
    }
}

function updateJobCard(jobId, progress) {
    const card = document.getElementById(`job-${jobId}`);
    if (!card) return;

    // Update status badge and classes
    const statusBadge = card.querySelector('.status-badge');
    if (statusBadge) {
        statusBadge.className = `status-badge status-${progress.status}`;
        const statusIcon = {
            pending: '⏳', uploaded: '📤', processing: '⚙️',
            completed: '✅', failed: '❌', cancelled: '🚫'
        }[progress.status] || '❓';
        statusBadge.innerHTML = `${statusIcon} ${progress.status}`;
    }

    if (['completed', 'failed', 'cancelled'].includes(progress.status)) {
        card.classList.remove('job-active');
        const spinner = card.querySelector('.status-spinner');
        if (spinner) spinner.remove();
    } else {
        card.classList.add('job-active');
    }

    // Update processing info message
    const infoSpan = card.querySelector('.processing-info');
    if (infoSpan) {
        if (progress.status === 'processing') {
            infoSpan.textContent = progress.total_chunks > 0
                ? (progress.completed_chunks >= progress.total_chunks
                    ? 'Finalizing and merging transcript...'
                    : `Processing chunk ${progress.completed_chunks + 1} of ${progress.total_chunks}...`)
                : `Processing audio chunks...`;
        } else if (progress.status === 'uploaded') {
            infoSpan.textContent = 'Splitting audio into chunks...';
        } else if (progress.status === 'pending') {
            infoSpan.textContent = 'Waiting in queue...';
        }
    }

    const progressBar = card.querySelector('.progress-fill');
    if (progressBar) {
        const percent = progress.total_chunks > 0
            ? Math.round((progress.completed_chunks / progress.total_chunks) * 100)
            : 0;
        progressBar.style.width = `${percent}%`;
        if (progress.status === 'processing') {
            progressBar.classList.add('progress-animated');
        } else {
            progressBar.classList.remove('progress-animated');
        }

        const textSpans = card.querySelectorAll('.progress-text span');
        if (textSpans.length === 2) {
            textSpans[0].textContent = `${progress.completed_chunks} / ${progress.total_chunks} chunks`;
            textSpans[1].textContent = `${percent}%`;
        }
    }

    // Update chunks if visible
    const chunksSection = document.getElementById(`chunks-${jobId}`);
    if (chunksSection && chunksSection.dataset.expanded === 'true' && progress.chunks) {
        renderChunks(jobId, progress.chunks);
    }
}

function updateLiveTimers() {
    const activeCards = document.querySelectorAll('.job-card.job-active');
    activeCards.forEach(card => {
        const createdAt = card.dataset.createdAt;
        if (!createdAt) return;

        const createdDate = new Date(createdAt);
        if (isNaN(createdDate.getTime())) return; // Guard against invalid date

        const now = new Date();
        const elapsedMs = now - createdDate;

        const elapsedMinutes = Math.floor(elapsedMs / 60000);
        const elapsedSeconds = Math.floor((elapsedMs % 60000) / 1000);
        const elapsedStr = elapsedMinutes > 0
            ? `${elapsedMinutes}m ${elapsedSeconds}s`
            : `${elapsedSeconds}s`;

        const timerSpan = card.querySelector('.elapsed-time');
        if (timerSpan) {
            timerSpan.textContent = `⏱️ Elapsed: ${elapsedStr}`;
        }
    });
}

// ===== Chunks =====
async function toggleChunks(jobId) {
    const chunksSection = document.getElementById(`chunks-${jobId}`);

    if (chunksSection.dataset.expanded === 'true') {
        chunksSection.innerHTML = '';
        chunksSection.dataset.expanded = 'false';
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/jobs/${jobId}/progress?include_chunks=true`, {
            headers: { 'X-API-Key': getApiKey() }
        });

        if (!response.ok) throw new Error('Failed to fetch chunks');

        const progress = await response.json();
        renderChunks(jobId, progress.chunks || []);
        chunksSection.dataset.expanded = 'true';
    } catch (error) {
        showToast('Failed to load chunks', 'error');
    }
}

function renderChunks(jobId, chunks) {
    const chunksSection = document.getElementById(`chunks-${jobId}`);
    if (!chunks.length) {
        chunksSection.innerHTML = '<p style="color: var(--text-muted); font-size: 0.85rem;">No chunks yet</p>';
        return;
    }

    chunksSection.innerHTML = `
        <div class="chunks-toggle" onclick="toggleChunks('${jobId}')">Hide Chunks ▲</div>
        <div class="chunks-grid">
            ${chunks.map(chunk => {
        const icon = {
            pending: '⏳',
            processing: '⚙️',
            completed: '✅',
            failed: '❌'
        }[chunk.status] || '❓';
        return `
                    <div class="chunk-badge ${chunk.status} clickable" 
                         onclick="viewChunkLog('${jobId}', ${chunk.chunk_index})"
                         title="View Log: ${chunk.error || `${chunk.start_time.toFixed(1)}s - ${chunk.end_time.toFixed(1)}s`}">
                        ${icon} #${chunk.chunk_index + 1}
                        ${chunk.attempt_count > 1 ? `(${chunk.attempt_count})` : ''}
                    </div>
                `;
    }).join('')}
        </div>
    `;
}

// Track current chunk data for download
let currentChunkData = null;
let currentChunkInfo = null;

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function formatChunkLogView(data) {
    const meta = data.metadata || {};
    const hasApiInfo = meta.prompt || meta.input_tokens || meta.raw_response;

    // ---- API Logs Section ----
    let apiSection = '';
    if (hasApiInfo) {
        const promptHtml = meta.prompt
            ? `<div class="api-log-block">
                   <div class="api-log-label">Prompt</div>
                   <pre class="api-log-pre">${escapeHtml(meta.prompt)}</pre>
               </div>`
            : '';

        const tokensHtml = (meta.input_tokens != null || meta.output_tokens != null)
            ? `<div class="api-log-block">
                   <div class="api-log-label">Token Usage</div>
                   <div class="api-log-tokens">
                       <span class="token-badge token-in">IN: ${meta.input_tokens ?? '—'}</span>
                       <span class="token-badge token-out">OUT: ${meta.output_tokens ?? '—'}</span>
                       ${meta.processing_latency_ms ? `<span class="token-badge token-latency">${meta.processing_latency_ms}ms</span>` : ''}
                       ${meta.finish_reason ? `<span class="token-badge token-finish">${escapeHtml(meta.finish_reason)}</span>` : ''}
                       ${meta.model ? `<span class="token-badge token-model">${escapeHtml(meta.model)}</span>` : ''}
                   </div>
               </div>`
            : '';

        const responseHtml = meta.raw_response
            ? `<div class="api-log-block">
                   <div class="api-log-label">API Response</div>
                   <pre class="api-log-pre api-log-response">${escapeHtml(meta.raw_response)}</pre>
               </div>`
            : '';

        apiSection = `
            <div class="api-log-section">
                <div class="api-log-header" onclick="this.parentElement.classList.toggle('collapsed')">
                    <span>API Call Details</span>
                    <span class="api-log-toggle">▼</span>
                </div>
                <div class="api-log-body">
                    ${promptHtml}
                    ${tokensHtml}
                    ${responseHtml}
                </div>
            </div>`;
    }

    // ---- Transcript Section (original data minus metadata internals) ----
    const displayData = { ...data };
    if (displayData.metadata) {
        // Show metadata without the large prompt/response fields (already shown above)
        const { prompt, raw_response, ...rest } = displayData.metadata;
        displayData.metadata = rest;
    }
    const transcriptSection = `
        <div class="api-log-section">
            <div class="api-log-header" onclick="this.parentElement.classList.toggle('collapsed')">
                <span>Chunk Transcript</span>
                <span class="api-log-toggle">▼</span>
            </div>
            <div class="api-log-body">
                <pre class="api-log-pre">${escapeHtml(JSON.stringify(displayData, null, 2))}</pre>
            </div>
        </div>`;

    return apiSection + transcriptSection;
}

async function viewChunkLog(jobId, chunkIndex) {
    try {
        const response = await fetch(`${API_BASE}/jobs/${jobId}/chunks/${chunkIndex}/log`, {
            headers: { 'X-API-Key': getApiKey() }
        });

        if (!response.ok) {
            throw new Error('Log not found');
        }

        const data = await response.json();

        // Store for download
        currentChunkData = data;
        currentChunkInfo = { jobId, chunkIndex };

        // Format chunk time range as human-readable (M:SS format)
        const formatTime = (seconds) => {
            const mins = Math.floor(seconds / 60);
            const secs = Math.floor(seconds % 60);
            return `${mins}:${secs.toString().padStart(2, '0')}`;
        };
        const startTime = data.chunk_start_time ?? 0;
        const endTime = data.chunk_end_time ?? 0;
        const timeRange = `(${formatTime(startTime)} - ${formatTime(endTime)})`;

        // Use the Logs Modal to display this
        const modal = document.getElementById('logsModal');
        document.getElementById('logsMeta').innerHTML = `
            <strong>Chunk #${chunkIndex + 1}</strong> <span class="text-muted">${timeRange}</span> <span class="text-muted" style="margin-left: 0.5rem;">[job:${jobId}]</span>
        `;

        const container = document.getElementById('logsContainer');
        container.innerHTML = formatChunkLogView(data);

        // Hide system logs if open
        document.getElementById('systemLogsContainer').style.display = 'none';

        // Show download button for chunk view
        document.getElementById('downloadChunkBtn').style.display = 'inline-flex';

        modal.classList.add('open');

    } catch (error) {
        showToast(`Could not load chunk log: ${error.message}`, 'error');
    }
}

function downloadChunkLog() {
    if (!currentChunkData || !currentChunkInfo) {
        showToast('No chunk data to download', 'error');
        return;
    }

    const { jobId, chunkIndex } = currentChunkInfo;
    const blob = new Blob([JSON.stringify(currentChunkData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `chunk-${String(chunkIndex).padStart(4, '0')}_${jobId.slice(0, 8)}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function closeLogsModal() {
    document.getElementById('logsModal').classList.remove('open');
    // Hide download button when closing
    document.getElementById('downloadChunkBtn').style.display = 'none';
    // Clear chunk data
    currentChunkData = null;
    currentChunkInfo = null;
    // Clear job logs data
    currentLogsJobId = null;
    stopSystemLogsPolling();
}

// ===== Job Actions =====
async function retryJob(jobId) {
    try {
        const response = await fetch(`${API_BASE}/jobs/${jobId}/retry`, {
            method: 'POST',
            headers: { 'X-API-Key': getApiKey() }
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to retry job');
        }

        showToast('Job queued for retry', 'success');
        refreshJobs();
        startPolling(jobId);
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function cancelJob(jobId) {
    try {
        const response = await fetch(`${API_BASE}/jobs/${jobId}/cancel`, {
            method: 'POST',
            headers: { 'X-API-Key': getApiKey() }
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to cancel job');
        }

        stopPolling(jobId);
        showToast('Job cancelled', 'info');
        refreshJobs();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function deleteJob(jobId) {
    try {
        const response = await fetch(`${API_BASE}/jobs/${jobId}`, {
            method: 'DELETE',
            headers: { 'X-API-Key': getApiKey() }
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to delete job');
        }

        const result = await response.json();
        stopPolling(jobId);
        showToast(result.message || 'Job deleted successfully', 'success');
        refreshJobs();
        closeDeleteModal();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// Store pending delete job ID
let pendingDeleteJobId = null;

function showDeleteConfirm(jobId, filename, chunkCount) {
    pendingDeleteJobId = jobId;
    const modal = document.getElementById('deleteModal');
    document.getElementById('deleteFileName').textContent = filename;
    document.getElementById('deleteChunkCount').textContent = chunkCount;
    document.getElementById('deleteJobId').textContent = jobId;
    modal.classList.add('open');
}

function closeDeleteModal() {
    document.getElementById('deleteModal').classList.remove('open');
    pendingDeleteJobId = null;
}

function confirmDelete() {
    if (pendingDeleteJobId) {
        deleteJob(pendingDeleteJobId);
    }
}

// ===== Delete All Jobs =====
function showDeleteAllConfirm() {
    document.getElementById('deleteAllModal').classList.add('open');
}

function closeDeleteAllModal() {
    document.getElementById('deleteAllModal').classList.remove('open');
}

async function confirmDeleteAll() {
    closeDeleteAllModal();
    try {
        const response = await fetch(`${API_BASE}/jobs`, {
            method: 'DELETE',
            headers: { 'X-API-Key': getApiKey() }
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to delete all jobs');
        }

        const result = await response.json();
        showToast(result.message || 'All jobs deleted', 'success');
        refreshJobs();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// Download bundle (Audio + Transcript)
async function downloadBundle(jobId) {
    try {
        showToast('Preparing download...', 'info');

        const response = await fetch(`${API_BASE}/jobs/${jobId}/download-bundle`, {
            headers: { 'X-API-Key': getApiKey() }
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to fetch download bundle');
        }

        // Get filename from header or fallback
        let filename = `${jobId}.zip`;
        const contentDisposition = response.headers.get('Content-Disposition');
        if (contentDisposition) {
            // Try filename*=UTF-8''... first (RFC 5987)
            const filenameStar = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
            if (filenameStar && filenameStar[1]) {
                filename = decodeURIComponent(filenameStar[1]);
            } else {
                // Fallback to standard filename=...
                const filenameMatch = contentDisposition.match(/filename="?([^";]+)"?/i);
                if (filenameMatch && filenameMatch[1]) {
                    filename = filenameMatch[1];
                }
            }
        }

        const blob = await response.blob();
        const url = URL.createObjectURL(blob);

        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();

        setTimeout(() => {
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }, 100);

        showToast('Download started', 'success');
    } catch (error) {
        console.error(error);
        showToast(error.message || 'Download failed', 'error');
    }
}

// Download result directly
async function downloadResult(jobId) {
    try {
        const response = await fetch(`${API_BASE}/jobs/${jobId}/result`, {
            headers: { 'X-API-Key': getApiKey() }
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to fetch result');
        }

        const result = await response.json();
        const text = result.transcript.text || result.transcript.full_text;

        const filename = getDownloadFilename(result);
        downloadTextFile(text, filename);
        showToast('Transcript downloaded', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// ===== Results =====
async function viewResult(jobId) {
    try {
        const response = await fetch(`${API_BASE}/jobs/${jobId}/result`, {
            headers: { 'X-API-Key': getApiKey() }
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to fetch result');
        }

        const result = await response.json();
        currentTranscript = result;
        showResultModal(result);
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function showResultModal(result) {
    const modal = document.getElementById('resultModal');
    const metaContainer = document.getElementById('resultMeta');
    const textarea = document.getElementById('transcriptText');

    metaContainer.innerHTML = `
        <div class="result-meta-item">
            <label>Duration</label>
            <span>${result.duration_seconds.toFixed(1)}s</span>
        </div>
        <div class="result-meta-item">
            <label>Provider</label>
            <span>${result.provider_used}</span>
        </div>
        <div class="result-meta-item">
            <label>Processing Time</label>
            <span>${result.processing_time_seconds.toFixed(1)}s</span>
        </div>
        <div class="result-meta-item">
            <label>Chunks</label>
            <span>${result.chunks_processed}</span>
        </div>
    `;

    textarea.value = result.transcript.text || result.transcript.full_text;
    modal.classList.add('open');
}

function closeModal() {
    document.getElementById('resultModal').classList.remove('open');
    currentTranscript = null;
}

function copyTranscript() {
    const textarea = document.getElementById('transcriptText');
    navigator.clipboard.writeText(textarea.value);
    showToast('Copied to clipboard', 'success');
}

// Helper to generate consistent download filename
function getDownloadFilename(result) {
    if (result.original_filename) {
        const baseName = result.original_filename.split('.').slice(0, -1).join('.') || result.original_filename;
        return `${baseName}_transcript.txt`;
    }
    const id = result.job_id || 'unknown';
    return `transcript_${id.slice(0, 8)}.txt`;
}

// Download result from the modal
function downloadTranscript() {
    if (!currentTranscript || !currentTranscript.transcript) {
        showToast('No transcript data available to download', 'error');
        return;
    }

    const text = currentTranscript.transcript.text || currentTranscript.transcript.full_text;
    if (!text) {
        showToast('Transcript is empty', 'warning');
        return; // Early return if empty
    }

    // Generate filename with guaranteed .txt extension
    let filename = 'transcript.txt';
    if (currentTranscript.original_filename) {
        const baseName = currentTranscript.original_filename.split('.').slice(0, -1).join('.') || currentTranscript.original_filename;
        filename = `${baseName}_transcript.txt`;
    } else if (currentTranscript.job_id) {
        filename = `transcript_${currentTranscript.job_id.slice(0, 8)}.txt`;
    }

    console.log('[downloadTranscript] Downloading with filename:', filename);
    downloadTextFile(text, filename);
    showToast('Transcript downloaded', 'success');
}

async function downloadPartial(jobId) {
    try {
        const response = await fetch(`${API_BASE}/jobs/${jobId}/progress?include_chunks=true`, {
            headers: { 'X-API-Key': getApiKey() }
        });

        if (!response.ok) throw new Error('Failed to fetch progress');

        // For partial download, we'd need an endpoint that returns partial results
        // For now, show a message
        showToast('Partial download: Feature requires additional API endpoint', 'info');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function downloadTextFile(content, filename) {
    console.log('[downloadTextFile] Starting download with filename:', filename);

    // Create content with BOM for UTF-8 encoding
    const BOM = '\uFEFF';
    const fullContent = BOM + content;

    // Use data URL approach (more compatible with Chrome for filename preservation)
    const dataUrl = 'data:text/plain;charset=utf-8,' + encodeURIComponent(fullContent);

    const a = document.createElement('a');
    a.href = dataUrl;
    a.download = filename;
    a.style.display = 'none';

    document.body.appendChild(a);
    console.log('[downloadTextFile] Link download attr:', a.download, 'href type:', a.href.substring(0, 30));
    a.click();

    setTimeout(() => {
        document.body.removeChild(a);
    }, 100);
}

// ===== Logs Modal =====
let currentLogsJobId = null;

async function viewLogs(jobId) {
    currentLogsJobId = jobId;
    const modal = document.getElementById('logsModal');
    const logsContainer = document.getElementById('logsContainer');
    const sysLogsContainer = document.getElementById('systemLogsContainer');
    const detailedBtn = document.getElementById('detailedLogsBtn');
    const refreshBtn = document.getElementById('refreshLogsBtn');

    // Reset view to standard logs
    logsContainer.style.display = 'block';
    sysLogsContainer.style.display = 'none';
    detailedBtn.style.display = 'inline-block';
    refreshBtn.style.display = 'inline-block';
    stopSystemLogsPolling();

    // Show loading state
    logsContainer.innerHTML = '<div class="logs-loading">Loading logs...</div>';
    modal.classList.add('open');

    try {
        const response = await fetch(`${API_BASE}/jobs/${jobId}/logs`, {
            headers: { 'X-API-Key': getApiKey() }
        });

        if (!response.ok) {
            throw new Error('Failed to fetch logs');
        }

        const data = await response.json();
        renderLogs(data);
    } catch (error) {
        logsContainer.innerHTML = `<div class="logs-error">❌ ${error.message}</div>`;
    }
}

function renderLogs(data) {
    const metaContainer = document.getElementById('logsMeta');
    const logsContainer = document.getElementById('logsContainer');

    // Render meta info
    const statusIcon = {
        pending: '⏳',
        uploaded: '📤',
        processing: '⚙️',
        completed: '✅',
        failed: '❌',
        cancelled: '🚫'
    }[data.status] || '❓';

    metaContainer.innerHTML = `
        <div class="logs-meta-grid">
            <div class="logs-meta-item">
                <span class="label">Status</span>
                <span class="status-badge status-${data.status}">${statusIcon} ${data.status}</span>
            </div>
            <div class="logs-meta-item">
                <span class="label">Provider</span>
                <span>${data.provider || 'Unknown'}</span>
            </div>
            <div class="logs-meta-item">
                <span class="label">Progress</span>
                <span>${data.completed_chunks} / ${data.total_chunks} chunks</span>
            </div>
            <div class="logs-meta-item">
                <span class="label">Created</span>
                <span>${new Date(data.created_at).toLocaleString()}</span>
            </div>
        </div>
    `;

    // Render log entries
    if (data.logs.length === 0) {
        logsContainer.innerHTML = '<p class="logs-empty">No log entries yet.</p>';
        return;
    }

    logsContainer.innerHTML = data.logs.map(log => {
        const levelIcon = {
            info: 'ℹ️',
            success: '✅',
            error: '❌',
            warning: '⚠️'
        }[log.level] || 'ℹ️';

        const time = new Date(log.timestamp).toLocaleTimeString();

        return `
            <div class="log-entry log-${log.level}">
                <span class="log-time">${time}</span>
                <span class="log-icon">${levelIcon}</span>
                <span class="log-message">${log.message}</span>
            </div>
        `;
    }).join('');
}

function refreshLogs() {
    if (currentLogsJobId) {
        viewLogs(currentLogsJobId);
    }
}

// ===== System Logs (Developer View) =====
function toggleSystemLogs() {
    const logsContainer = document.getElementById('logsContainer');
    const sysLogsContainer = document.getElementById('systemLogsContainer');
    const detailedBtn = document.getElementById('detailedLogsBtn');
    const refreshBtn = document.getElementById('refreshLogsBtn');

    if (sysLogsContainer.style.display === 'none') {
        // Switch to system logs
        logsContainer.style.display = 'none';
        sysLogsContainer.style.display = 'flex';
        detailedBtn.style.display = 'none';
        refreshBtn.style.display = 'none';
        currentSystemTab = 'all';
        updateTabButtons();
        startSystemLogsPolling();
    } else {
        // Switch back to job logs
        logsContainer.style.display = 'block';
        sysLogsContainer.style.display = 'none';
        detailedBtn.style.display = 'inline-block';
        refreshBtn.style.display = 'inline-block';
        stopSystemLogsPolling();
    }
}

function switchSystemTab(tab) {
    currentSystemTab = tab;
    updateTabButtons();
    fetchSystemLogs(); // Refresh view
}

function updateTabButtons() {
    const tabs = document.querySelectorAll('.system-tabs .tab-btn');
    tabs.forEach(btn => {
        const tabOnClick = btn.getAttribute('onclick');
        if (tabOnClick) {
            const match = tabOnClick.match(/'([^']+)'/);
            if (match && match[1]) {
                const tabName = match[1];
                btn.classList.toggle('active', tabName === currentSystemTab);
            }
        }
    });
}

function startSystemLogsPolling() {
    if (systemLogInterval) clearInterval(systemLogInterval);
    fetchSystemLogs(); // Initial fetch
    systemLogInterval = setInterval(fetchSystemLogs, 3000);
}

function stopSystemLogsPolling() {
    if (systemLogInterval) {
        clearInterval(systemLogInterval);
        systemLogInterval = null;
    }
}

async function fetchSystemLogs() {
    if (!currentLogsJobId) return;

    try {
        const response = await fetch(`${API_BASE}/jobs/${currentLogsJobId}/system-logs`, {
            headers: { 'X-API-Key': getApiKey() }
        });

        if (!response.ok) throw new Error('Failed to fetch system logs');

        const data = await response.json();
        renderSystemLogs(data.logs);
    } catch (error) {
        console.error('System logs error:', error);
    }
}

function renderSystemLogs(logs) {
    const container = document.getElementById('systemLogsContent');
    if (!container) return;

    if (logs.length === 0) {
        container.innerHTML = '<div class="logs-empty">No system logs found for this job ID yet. Waiting for events...</div>';
        return;
    }

    // Filter logs based on current tab
    const filteredLogs = logs.filter(line => {
        if (currentSystemTab === 'all') return true;

        const lineUpper = line.toUpperCase();

        if (currentSystemTab === 'api') {
            return line.includes('Gemini') || line.includes('generativeai') ||
                line.includes('wav.am') || line.includes('HiSpeech') ||
                line.includes('ElevenLabs') || line.includes('elevenlabs') ||
                line.includes('API request') || line.includes('API response');
        }

        if (currentSystemTab === 'sql') {
            return line.includes('sqlalchemy.engine') ||
                line.includes('SELECT ') ||
                line.includes('INSERT ') ||
                line.includes('UPDATE ') ||
                line.includes('COMMIT') ||
                line.includes('BEGIN');
        }

        if (currentSystemTab === 'worker') {
            return line.includes('celery') ||
                line.includes('ForkPoolWorker') ||
                line.includes('MainProcess') ||
                line.includes('heartbeat');
        }

        if (currentSystemTab === 'errors') {
            return line.includes('Traceback') ||
                line.includes('Exception') ||
                lineUpper.includes('ERROR') ||
                lineUpper.includes('FAIL');
        }

        return true;
    });

    if (filteredLogs.length === 0) {
        container.innerHTML = `<div class="logs-empty">No logs matching the "${currentSystemTab}" filter.</div>`;
        return;
    }

    const html = filteredLogs.map(line => {
        let className = 'sys-log-line';

        // Basic Syntax Highlighting
        const lineUpper = line.toUpperCase();
        if (lineUpper.includes('ERROR') || lineUpper.includes('FAIL')) className += ' sys-log-error';
        else if (lineUpper.includes('WARNING')) className += ' sys-log-warn';
        else if (lineUpper.includes('INFO')) className += ' sys-log-info';
        else if (lineUpper.includes('SUCCESS')) className += ' sys-log-success';

        // Specific highlighting for Truth segments
        let formattedLine = line;

        // Highlight SQL
        if (line.includes('SELECT') || line.includes('INSERT') || line.includes('UPDATE') || line.includes('COMMIT') || line.includes('BEGIN')) {
            formattedLine = `<span class="sys-log-sql">${line}</span>`;
        }

        // Highlight provider API calls
        if (line.includes('API request') || line.includes('API response')) {
            formattedLine = `<div class="sys-log-api">${line}</div>`;
        }

        // Highlight Worker events
        if (line.includes('ForkPoolWorker') || line.includes('celery')) {
            formattedLine = line.replace(/(ForkPoolWorker-\d+|celery)/g, '<span class="sys-log-worker">$1</span>');
        }

        // Highlight Tracebacks
        if (line.includes('Traceback') || line.includes('File "') || line.includes('Exception')) {
            formattedLine = `<span class="sys-log-trace">${line}</span>`;
        }

        // Extract and wrap timestamp if possible (ISO format 2026-...)
        formattedLine = formattedLine.replace(/^(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2},\d{3})/, '<span class="sys-log-timestamp">$1</span>');
        formattedLine = formattedLine.replace(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z?)/, '<span class="sys-log-timestamp">$1</span>');

        return `<span class="${className}">${formattedLine}</span>`;
    }).join('');

    container.innerHTML = html;
    // Auto-scroll to bottom if at bottom
    container.scrollTop = container.scrollHeight;
}

// Close logs modal on outside click
document.getElementById('logsModal')?.addEventListener('click', (e) => {
    if (e.target.id === 'logsModal') {
        closeLogsModal();
    }
});

// ===== Toast Notifications =====
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <span>${message}</span>
    `;
    container.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, 4000);
}

// Close modal on outside click
document.getElementById('resultModal').addEventListener('click', (e) => {
    if (e.target.id === 'resultModal') {
        closeModal();
    }
});

// ===== UI Helpers =====
function togglePasswordVisibility(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;

    const isPassword = input.type === 'password';
    input.type = isPassword ? 'text' : 'password';

    // Optional: Update icon style if needed
}

