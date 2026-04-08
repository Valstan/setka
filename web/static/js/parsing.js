document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('parsing-form');
    const includeOptions = document.getElementById('includeOptions');
    const progressBar = document.getElementById('parsingProgress');
    const statusText = document.getElementById('parsingStatus');
    const errorBlock = document.getElementById('parsingError');
    const downloadBlock = document.getElementById('downloadBlock');
    const downloadLink = document.getElementById('downloadLink');
    const startButton = document.getElementById('startParsingBtn');
    const videoDownloads = document.getElementById('videoDownloads');
    const videoList = document.getElementById('videoList');
    const videoReport = document.getElementById('videoReport');
    const videoReportList = document.getElementById('videoReportList');
    const videoReportLink = document.getElementById('videoReportLink');
    const videoLimitBlock = document.getElementById('videoLimitBlock');
    const videoLimitProgress = document.getElementById('videoLimitProgress');
    const videoLimitText = document.getElementById('videoLimitText');

    let pollingTimer = null;
    let currentJobId = null;

    function setStatus(message, progress = null) {
        statusText.textContent = message;
        if (progress !== null) {
            const pct = Math.max(0, Math.min(100, progress));
            progressBar.style.width = `${pct}%`;
            progressBar.textContent = `${pct}%`;
        }
    }

    function showError(message) {
        errorBlock.textContent = message;
        errorBlock.classList.remove('d-none');
    }

    function clearError() {
        errorBlock.textContent = '';
        errorBlock.classList.add('d-none');
    }

    function keepTextSelected() {
        const textCheckbox = document.getElementById('includeText');
        if (textCheckbox) {
            textCheckbox.checked = true;
        }
    }

    async function startParsing(payload) {
        const response = await fetch('/api/parsing/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Ошибка запуска' }));
            throw new Error(error.detail || 'Ошибка запуска');
        }

        return response.json();
    }

    async function fetchStatus(jobId) {
        const response = await fetch(`/api/parsing/status/${jobId}`);
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Ошибка статуса' }));
            throw new Error(error.detail || 'Ошибка статуса');
        }
        return response.json();
    }

    async function skipVideo(videoId) {
        if (!currentJobId) return;
        await fetch(`/api/parsing/skip/${currentJobId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ video_id: videoId })
        });
    }

    function renderVideoDownloads(items) {
        if (!items || !items.length) {
            videoDownloads.classList.add('d-none');
            videoList.innerHTML = '';
            return;
        }
        videoDownloads.classList.remove('d-none');
        videoList.innerHTML = items.map(item => {
            const pct = Math.max(0, Math.min(100, item.progress || 0));
            const statusText = item.status || 'pending';
            const button = (statusText === 'downloading')
                ? `<button class="btn btn-sm btn-outline-secondary ms-2" data-video-id="${item.id}">Пропустить</button>`
                : '';
            return `
                <div class="mb-2">
                    <div class="d-flex justify-content-between align-items-center">
                        <span>${item.title || item.id}</span>
                        <span class="text-muted">${statusText}</span>
                    </div>
                    <div class="progress" style="height: 10px;">
                        <div class="progress-bar" style="width: ${pct}%"></div>
                    </div>
                    <div class="mt-1">${button}</div>
                </div>
            `;
        }).join('');

        videoList.querySelectorAll('button[data-video-id]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const videoId = btn.getAttribute('data-video-id');
                btn.disabled = true;
                await skipVideo(videoId);
            });
        });
    }

    function renderVideoReport(items) {
        if (!items || !items.length) {
            videoReport.classList.add('d-none');
            videoReportList.innerHTML = '';
            if (videoReportLink) {
                videoReportLink.classList.add('d-none');
            }
            return;
        }
        videoReport.classList.remove('d-none');
        videoReportList.innerHTML = items.map(item => `<li>${item}</li>`).join('');
    }

    function renderVideoLimit(limitInfo) {
        if (!limitInfo || !videoLimitBlock) {
            videoLimitBlock.classList.add('d-none');
            return;
        }
        const used = limitInfo.used_mb || 0;
        const max = limitInfo.max_mb || 0;
        const pct = max > 0 ? Math.min(100, Math.round((used / max) * 100)) : 0;
        videoLimitBlock.classList.remove('d-none');
        if (videoLimitProgress) {
            videoLimitProgress.style.width = `${pct}%`;
        }
        if (videoLimitText) {
            videoLimitText.textContent = `${used}MB / ${max}MB`;
        }
    }

    async function pollStatus() {
        if (!currentJobId) return;
        try {
            const status = await fetchStatus(currentJobId);
            setStatus(status.message || 'Выполняется...', status.progress || 0);
            renderVideoDownloads(status.video_downloads || []);
            renderVideoReport(status.video_reports || []);
            renderVideoLimit(status.video_limit || null);
            if (videoReportLink && status.report_url) {
                videoReportLink.href = status.report_url;
                videoReportLink.classList.remove('d-none');
            }

            if (status.status === 'error') {
                showError(status.error || 'Произошла ошибка');
                startButton.disabled = false;
                clearInterval(pollingTimer);
            }

            if (status.status === 'done') {
                startButton.disabled = false;
                downloadLink.href = status.download_url;
                downloadBlock.classList.remove('d-none');
                clearInterval(pollingTimer);
            }
        } catch (err) {
            showError(err.message);
            startButton.disabled = false;
            clearInterval(pollingTimer);
        }
    }

    keepTextSelected();

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        clearError();
        downloadBlock.classList.add('d-none');
        setStatus('Запуск...', 0);
        startButton.disabled = true;

        const payload = {
            source: document.getElementById('sourceInput').value.trim(),
            download_attachments: document.getElementById('downloadAttachments').checked,
            include_text: document.getElementById('includeText').checked,
            include_photos: document.getElementById('includePhotos').checked,
            include_videos: document.getElementById('includeVideos').checked,
            include_audio: document.getElementById('includeAudio').checked,
            include_links: document.getElementById('includeLinks').checked,
            include_docs: document.getElementById('includeDocs').checked,
            include_polls: document.getElementById('includePolls').checked,
            date_from: document.getElementById('dateFrom').value || null,
            date_to: document.getElementById('dateTo').value || null,
            limit: parseInt(document.getElementById('limitInput').value, 10) || 200
        };

        try {
            const startResult = await startParsing(payload);
            currentJobId = startResult.job_id;
            setStatus(startResult.message || 'Парсинг запущен', 1);
            pollingTimer = setInterval(pollStatus, 2000);
        } catch (err) {
            showError(err.message);
            startButton.disabled = false;
        }
    });
});
