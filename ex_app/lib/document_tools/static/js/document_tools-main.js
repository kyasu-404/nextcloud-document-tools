(function () {
	'use strict'

	const APP_ID = 'document_tools'
	const formats = [
		['searchable_pdf', 'PDF с OCR', 'Searchable PDF'],
		['docx', 'DOCX', 'Word-документ'],
		['txt', 'TXT', 'Обычный текст'],
		['markdown', 'Markdown', 'MD'],
		['html', 'HTML', 'Веб-страница'],
		['epub', 'EPUB', 'Электронная книга'],
	]

	let selectedFormat = 'searchable_pdf'
	let selectedFile = null
	let latestDoneJob = null

	function apiBase() {
		if (window.OC && typeof window.OC.generateUrl === 'function') {
			return window.OC.generateUrl(`/apps/app_api/proxy/${APP_ID}`)
		}
		return ''
	}

	function apiUrl(path) {
		return `${apiBase()}${path}`
	}

	function mountPoint() {
		return document.querySelector('#content') || document.querySelector('#app-content') || document.body
	}

	function render() {
		const root = document.createElement('div')
		root.id = 'document-tools-root'
		root.innerHTML = `
			<div class="dt-shell">
				<main class="dt-main">
					<div class="dt-header">
						<h1 class="dt-title">Nextcloud Document Tools</h1>
						<div class="dt-actions">
							<button class="dt-button" data-action="choose-nextcloud">Выбрать из Nextcloud</button>
						</div>
					</div>
					<section class="dt-upload" data-dropzone>
						<div>
							<p class="dt-upload-title">Перетащите файл сюда</p>
							<p class="dt-upload-hint">или нажмите «Выбрать файл»</p>
							<div class="dt-actions">
								<button class="dt-button primary" data-action="choose-file">Выбрать файл</button>
								<button class="dt-button" data-action="choose-nextcloud">Выбрать из Nextcloud</button>
							</div>
							<div class="dt-file-name" data-file-name>Файл не выбран</div>
						</div>
					</section>
					<h2 class="dt-section-title">Формат результата</h2>
					<div class="dt-format-grid">
						${formats.map(([id, label, hint]) => formatTile(id, label, hint)).join('')}
					</div>
					<div class="dt-output-actions" style="margin-top: 18px">
						<button class="dt-button success" data-action="start">Запустить обработку</button>
					</div>
					<input type="file" data-file-input hidden>
				</main>
				<aside class="dt-panel">
					<h2 class="dt-panel-title">Очередь обработки</h2>
					<div class="dt-queue" data-queue>
						<p class="dt-empty">Очередь пока пуста</p>
					</div>
					<section class="dt-result" data-result>
						<h2 class="dt-panel-title">Результат</h2>
						<div class="dt-output-actions">
							<button class="dt-button primary" data-action="download">Скачать</button>
							<button class="dt-button" data-action="save-back">Сохранить в Nextcloud</button>
							<button class="dt-button" data-action="replace">Заменить оригинал</button>
							<button class="dt-button" data-action="save-folder">Сохранить в папку</button>
						</div>
					</section>
				</aside>
			</div>
		`

		const mount = mountPoint()
		mount.innerHTML = ''
		mount.appendChild(root)
		bind(root)
		loadJobs(root)
		setInterval(() => loadJobs(root), 2500)
	}

	function formatTile(id, label, hint) {
		return `
			<button class="dt-format" data-format="${id}" aria-pressed="${id === selectedFormat ? 'true' : 'false'}">
				<strong>${label}</strong>
				<span>${hint}</span>
			</button>
		`
	}

	function bind(root) {
		const fileInput = root.querySelector('[data-file-input]')
		const dropzone = root.querySelector('[data-dropzone]')

		root.querySelectorAll('[data-action="choose-file"]').forEach((button) => {
			button.addEventListener('click', () => fileInput.click())
		})

		root.querySelectorAll('[data-action="choose-nextcloud"]').forEach((button) => {
			button.addEventListener('click', () => {
				const params = new URLSearchParams(window.location.search)
				const fileIds = params.get('fileIds')
				if (fileIds) {
					alert(`Файл из Nextcloud выбран через контекстное меню: ${fileIds}`)
				} else {
					alert('Выберите файл в приложении Files и нажмите «Конвертировать документ».')
				}
			})
		})

		fileInput.addEventListener('change', () => {
			selectedFile = fileInput.files[0] || null
			updateSelectedFile(root)
		})

		dropzone.addEventListener('dragover', (event) => {
			event.preventDefault()
			dropzone.classList.add('is-dragging')
		})
		dropzone.addEventListener('dragleave', () => dropzone.classList.remove('is-dragging'))
		dropzone.addEventListener('drop', (event) => {
			event.preventDefault()
			dropzone.classList.remove('is-dragging')
			selectedFile = event.dataTransfer.files[0] || null
			updateSelectedFile(root)
		})

		root.querySelectorAll('[data-format]').forEach((tile) => {
			tile.addEventListener('click', () => {
				selectedFormat = tile.dataset.format
				root.querySelectorAll('[data-format]').forEach((other) => {
					other.setAttribute('aria-pressed', other === tile ? 'true' : 'false')
				})
			})
		})

		root.querySelector('[data-action="start"]').addEventListener('click', () => createJob(root))
		root.querySelector('[data-action="download"]').addEventListener('click', () => {
			if (latestDoneJob) {
				window.location.href = apiUrl(`/api/jobs/${latestDoneJob.id}/download`)
			}
		})

		root.querySelector('[data-action="save-back"]').addEventListener('click', () => saveResult('save_back'))
		root.querySelector('[data-action="replace"]').addEventListener('click', () => saveResult('replace_original'))
		root.querySelector('[data-action="save-folder"]').addEventListener('click', () => saveResult('save_to_folder'))
	}

	function updateSelectedFile(root) {
		root.querySelector('[data-file-name]').textContent = selectedFile ? selectedFile.name : 'Файл не выбран'
	}

	async function createJob(root) {
		if (!selectedFile) {
			alert('Сначала выберите файл.')
			return
		}
		const form = new FormData()
		form.append('file', selectedFile)
		form.append('output_format', selectedFormat)
		const response = await fetch(apiUrl('/api/jobs'), {
			method: 'POST',
			body: form,
		})
		if (!response.ok) {
			alert('Не удалось добавить задачу в очередь.')
			return
		}
		selectedFile = null
		updateSelectedFile(root)
		loadJobs(root)
	}

	async function loadJobs(root) {
		try {
			const response = await fetch(apiUrl('/api/jobs'))
			if (!response.ok) {
				return
			}
			const data = await response.json()
			renderQueue(root, data.jobs || [])
		} catch (_error) {
			// Keep the UI quiet if the ExApp is not reachable during installation.
		}
	}

	function renderQueue(root, jobs) {
		const queue = root.querySelector('[data-queue]')
		if (!jobs.length) {
			queue.innerHTML = '<p class="dt-empty">Очередь пока пуста</p>'
			return
		}
		latestDoneJob = jobs.find((job) => job.status === 'done') || latestDoneJob
		queue.innerHTML = jobs.map(jobTemplate).join('')
		root.querySelector('[data-result]').classList.toggle('is-visible', Boolean(latestDoneJob))
	}

	function jobTemplate(job) {
		const status = statusText(job)
		const progress = Number(job.progress || 0)
		return `
			<article class="dt-job ${job.status}">
				<div class="dt-job-top">
					<span class="dt-job-file">${job.filename}</span>
					<span class="dt-job-status">${status}</span>
				</div>
				<div class="dt-progress"><span style="width: ${progress}%"></span></div>
				${job.error ? `<p class="dt-empty">${job.error}</p>` : ''}
			</article>
		`
	}

	function statusText(job) {
		if (job.status === 'queued') {
			return `${job.operation} ожидает`
		}
		if (job.status === 'running') {
			return `${job.operation} выполняется`
		}
		if (job.status === 'done') {
			return 'готово'
		}
		return 'ошибка'
	}

	async function saveResult(mode) {
		if (!latestDoneJob) {
			return
		}
		const response = await fetch(apiUrl(`/api/jobs/${latestDoneJob.id}/save`), {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ mode }),
		})
		if (!response.ok) {
			const data = await response.json().catch(() => ({}))
			alert(data.detail || 'Сохранение в Nextcloud пока недоступно.')
		}
	}

	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', render)
	} else {
		render()
	}
})()
