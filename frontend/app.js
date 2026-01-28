// ===== Configuration =====
const API_BASE = '/api/v1';
let pollingIntervals = {};
let currentTranscript = null;

// ===== Initialize =====
document.addEventListener('DOMContentLoaded', () => {
    initDropzone();
    loadApiKey();
    refreshJobs();
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

    const config = {
        provider: document.getElementById('provider').value,
        language: document.getElementById('language').value
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

    return `
        <div class="job-card" id="job-${job.job_id}">
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
                    <span class="status-badge status-${job.status}">
                        ${statusIcon} ${job.status}
                    </span>
                </div>
            </div>

            ${showProgress ? `
                <div class="progress-container">
                    <div class="progress-bar">
                        <div class="progress-fill" style="width: ${progress}%"></div>
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

    const progressBar = card.querySelector('.progress-fill');
    if (progressBar) {
        const percent = progress.total_chunks > 0
            ? Math.round((progress.completed_chunks / progress.total_chunks) * 100)
            : 0;
        progressBar.style.width = `${percent}%`;

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
        const filename = `transcript_${jobId.slice(0, 8)}.txt`;
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

function downloadTranscript() {
    if (!currentTranscript) return;

    const text = currentTranscript.transcript.full_text;
    const filename = `transcript_${currentTranscript.job_id.slice(0, 8)}.txt`;
    downloadTextFile(text, filename);
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
