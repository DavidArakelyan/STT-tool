// ===== Configuration =====
const API_BASE = '/api/v1';
let pollingIntervals = {};
let systemLogInterval = null;
let currentTranscript = null;

// ===== Initialize =====
document.addEventListener('DOMContentLoaded', () => {
    initDropzone();
    loadApiKey();
    refreshJobs();

    // Update active job timers every second
    setInterval(updateLiveTimers, 1000);
});

// ===== API Key Management =====
function loadApiKey() {
    const saved = localStorage.getItem('stt_api_key');
    if (saved) {
        document.getElementById('apiKey').value = saved;
    }
}

function getApiKey() {
    const key = document.getElementById('apiKey').value;
    localStorage.setItem('stt_api_key', key);
    return key;
}

function toggleApiKeyVisibility() {
    const input = document.getElementById('apiKey');
    input.type = input.type === 'password' ? 'text' : 'password';
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
    const validExtensions = ['mp3', 'wav', 'm4a', 'flac', 'ogg', 'webm', 'aac', 'wma', 'opus'];
    const ext = file.name.split('.').pop().toLowerCase();

    if (!validExtensions.includes(ext)) {
        showToast('Invalid file format. Please upload a supported audio file.', 'error');
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
        clearFile();
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
    const showRetry = job.status === 'failed';
    const showCancel = ['pending', 'uploaded', 'processing'].includes(job.status);
    const showResult = job.status === 'completed';
    const showDownloadPartial = job.completed_chunks > 0 && job.status === 'failed';
    const isFinished = ['completed', 'failed', 'cancelled'].includes(job.status);
    const isActive = ['pending', 'uploaded', 'processing'].includes(job.status);

    // Calculate elapsed time for active jobs
    const createdAt = new Date(job.created_at);
    const now = new Date();
    const elapsedMs = now - createdAt;
    const elapsedMinutes = Math.floor(elapsedMs / 60000);
    const elapsedSeconds = Math.floor((elapsedMs % 60000) / 1000);
    const elapsedStr = elapsedMinutes > 0
        ? `${elapsedMinutes}m ${elapsedSeconds}s`
        : `${elapsedSeconds}s`;

    return `
        <div class="job-card ${isActive ? 'job-active' : ''}" id="job-${job.job_id}" data-created-at="${job.created_at}">
            <div class="job-header">
                <div class="job-info">
                    <h3>${job.original_filename || 'Unknown file'}</h3>
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
                    ? `Processing chunk ${job.completed_chunks + 1} of ${job.total_chunks}...`
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
                ${showProgress ? `<button class="btn btn-secondary" onclick="toggleChunks('${job.job_id}')">📊 Show Chunks</button>` : ''}
                ${showResult ? `<button class="btn btn-success" onclick="viewResult('${job.job_id}')">📄 View Result</button>` : ''}
                ${showResult ? `<button class="btn btn-secondary" onclick="downloadResult('${job.job_id}')">⬇️ Download</button>` : ''}
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
                ? `Processing chunk ${progress.completed_chunks + 1} of ${progress.total_chunks}...`
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
                    <div class="chunk-badge ${chunk.status}" title="${chunk.error || `${chunk.start_time.toFixed(1)}s - ${chunk.end_time.toFixed(1)}s`}">
                        ${icon} #${chunk.chunk_index + 1}
                        ${chunk.attempt_count > 1 ? `(${chunk.attempt_count})` : ''}
                    </div>
                `;
    }).join('')}
        </div>
    `;
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
    document.getElementById('deleteJobId').textContent = jobId.slice(0, 8) + '...';
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
        const text = result.transcript.full_text;

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

    textarea.value = result.transcript.full_text;
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

    const text = currentTranscript.transcript.full_text;
    if (!text) {
        showToast('Transcript is empty', 'warning');
    }

    const filename = getDownloadFilename(currentTranscript);
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
    // Create blob with BOM for proper UTF-8 encoding (especially for Armenian text)
    const BOM = '\uFEFF';
    const blob = new Blob([BOM + content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);

    // Create and configure the download link
    const a = document.createElement('a');
    a.style.display = 'none';
    a.href = url;
    a.download = filename;
    a.setAttribute('download', filename); // Explicit download attribute

    // Append to body and trigger click
    document.body.appendChild(a);

    // Use setTimeout for better browser compatibility
    setTimeout(() => {
        a.click();
        // Cleanup after a delay to ensure download starts
        setTimeout(() => {
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }, 100);
    }, 0);
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

function closeLogsModal() {
    document.getElementById('logsModal').classList.remove('open');
    currentLogsJobId = null;
    stopSystemLogsPolling();
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

        if (currentSystemTab === 'gemini') {
            return line.includes('Gemini') || line.includes('generativeai');
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

        // Highlight Gemini API
        if (line.includes('Gemini API request') || line.includes('Gemini API response')) {
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
