(function () {
	'use strict'

	const APP_ID = 'nextcloud-document-tools'
	const formats = [
		['searchable_pdf', 'PDF с OCR', 'Searchable PDF'],
		['docx', 'DOCX', 'Word-документ'],
		['txt', 'TXT', 'Обычный текст'],
		['markdown', 'Markdown', 'MD'],
		['html', 'HTML', 'Веб-страница'],
		['epub', 'EPUB', 'Электронная книга'],
	]

	let selectedFormat = 'searchable_pdf'
	let selectedSource = null
	let latestDoneJob = null
	let jobsPollTimer = null
	let diagnosticsState = null

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
			<div class="dt-toast" data-toast aria-live="polite"></div>
			<div class="dt-shell">
				<main class="dt-main">
					<div class="dt-header">
						<h1 class="dt-title">Nextcloud Document Tools</h1>
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
					<section class="dt-file-card" data-file-card hidden></section>
					<section data-action-panel hidden>
						<h2 class="dt-section-title">Доступные действия</h2>
						<div class="dt-format-grid">
							${formats.map(([id, label, hint]) => formatTile(id, label, hint)).join('')}
						</div>
						<div class="dt-output-actions" style="margin-top: 18px">
							<button class="dt-button success" data-action="start">Запустить обработку</button>
						</div>
					</section>
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
			<div class="dt-modal-host" data-modal-host></div>
		`

		const mount = mountPoint()
		document.body.classList.add('document-tools-page')
		mount.classList.add('document-tools-host')
		mount.innerHTML = ''
		mount.appendChild(root)
		bind(root)
		loadDiagnostics(root)
		loadJobs(root)
		loadInitialContextFile(root)
		if (jobsPollTimer) {
			window.clearInterval(jobsPollTimer)
		}
		jobsPollTimer = window.setInterval(() => loadJobs(root), 3500)
	}

	function formatTile(id, label, hint) {
		return `
			<button class="dt-format" data-format="${id}" aria-pressed="${id === selectedFormat ? 'true' : 'false'}">
				<strong>${escapeHtml(label)}</strong>
				<span>${escapeHtml(hint)}</span>
			</button>
		`
	}

	function bind(root) {
		const fileInput = root.querySelector('[data-file-input]')
		const dropzone = root.querySelector('[data-dropzone]')

		root.querySelector('[data-action="choose-file"]').addEventListener('click', () => fileInput.click())
		root.querySelector('[data-action="choose-nextcloud"]').addEventListener('click', () => openCloudPicker(root))

		fileInput.addEventListener('change', () => {
			const file = fileInput.files[0] || null
			if (file) {
				selectedSource = { type: 'local', file }
				updateSelectedFile(root)
			}
		})

		dropzone.addEventListener('dragover', (event) => {
			event.preventDefault()
			dropzone.classList.add('is-dragging')
		})
		dropzone.addEventListener('dragleave', () => dropzone.classList.remove('is-dragging'))
		dropzone.addEventListener('drop', (event) => {
			event.preventDefault()
			dropzone.classList.remove('is-dragging')
			const file = event.dataTransfer.files[0] || null
			if (file) {
				selectedSource = { type: 'local', file }
				updateSelectedFile(root)
			}
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

		root.querySelector('[data-action="save-back"]').addEventListener('click', () => saveResult(root, 'save_back'))
		root.querySelector('[data-action="replace"]').addEventListener('click', () => saveResult(root, 'replace_original'))
		root.querySelector('[data-action="save-folder"]').addEventListener('click', () => saveResult(root, 'save_to_folder'))
	}

	async function loadInitialContextFile(root) {
		const params = new URLSearchParams(window.location.search)
		const fileIds = params.get('fileIds')
		if (!fileIds) {
			return
		}
		const firstId = fileIds.split(',').map((id) => id.trim()).filter(Boolean)[0]
		if (!firstId) {
			return
		}
		try {
			const response = await fetch(apiUrl(`/api/nextcloud/files/by-id/${encodeURIComponent(firstId)}`))
			if (!response.ok) {
				throw new Error(await response.text())
			}
			const file = await response.json()
			selectCloudFile(root, file)
			notify(root, `Файл из Nextcloud выбран через контекстное меню: ${file.name}`, 'success')
		} catch (_error) {
			notify(root, 'Не удалось получить файл, выбранный через контекстное меню.', 'error')
		}
	}

	function updateSelectedFile(root) {
		const label = root.querySelector('[data-file-name]')
		if (!selectedSource) {
			label.textContent = 'Файл не выбран'
			updateFileCard(root)
			updateAvailableFormats(root)
			return
		}
		if (selectedSource.type === 'local') {
			label.textContent = `${selectedSource.file.name} · с компьютера`
			updateFileCard(root)
			updateAvailableFormats(root)
			return
		}
		label.textContent = `${selectedSource.file.name} · Nextcloud`
		updateFileCard(root)
		updateAvailableFormats(root)
	}

	function updateFileCard(root) {
		const card = root.querySelector('[data-file-card]')
		if (!selectedSource) {
			card.hidden = true
			card.innerHTML = ''
			return
		}
		const file = selectedSource.file
		const info = fileInfo(file)
		card.hidden = false
		card.innerHTML = `
			<h2 class="dt-section-title">Файл</h2>
			<div class="dt-file-summary">
				<strong>${escapeHtml(info.name)}</strong>
				<span>Тип: ${escapeHtml(info.typeLabel)}</span>
				<span>Размер: ${escapeHtml(formatBytes(info.size))}</span>
				<span>OCR нужен: ${info.needsOcr ? 'да' : 'по ситуации'}</span>
				<span>Источник: ${selectedSource.type === 'local' ? 'компьютер' : 'Nextcloud'}</span>
			</div>
		`
	}

	function updateAvailableFormats(root) {
		const panel = root.querySelector('[data-action-panel]')
		if (!selectedSource) {
			panel.hidden = true
			return
		}
		panel.hidden = false
		const allowed = allowedFormatsForSource(selectedSource)
		const empty = panel.querySelector('[data-no-actions]')
		if (!allowed.length) {
			if (!empty) {
				panel.insertAdjacentHTML('beforeend', '<p class="dt-empty" data-no-actions>Для этого файла нет доступных операций: в контейнере отсутствуют нужные инструменты.</p>')
			}
		} else if (empty) {
			empty.remove()
		}
		if (allowed.length && !allowed.includes(selectedFormat)) {
			selectedFormat = allowed[0]
		}
		root.querySelectorAll('[data-format]').forEach((tile) => {
			const enabled = allowed.includes(tile.dataset.format)
			tile.hidden = !enabled
			tile.disabled = !enabled
			tile.setAttribute('aria-pressed', enabled && tile.dataset.format === selectedFormat ? 'true' : 'false')
		})
	}

	async function createJob(root) {
		if (!selectedSource) {
			notify(root, 'Сначала выберите файл.', 'error')
			return
		}
		if (!allowedFormatsForSource(selectedSource).includes(selectedFormat)) {
			notify(root, 'Для выбранного файла нет доступного действия.', 'error')
			return
		}

		const startButton = root.querySelector('[data-action="start"]')
		startButton.disabled = true
		try {
			const response = selectedSource.type === 'local'
				? await uploadLocalFile(selectedSource.file)
				: await uploadNextcloudFile(selectedSource.file)

			if (!response.ok) {
				throw new Error(await errorMessage(response))
			}
			selectedSource = null
			root.querySelector('[data-file-input]').value = ''
			updateSelectedFile(root)
			notify(root, 'Задача добавлена в очередь.', 'success')
			await loadJobs(root)
		} catch (error) {
			notify(root, error.message || 'Не удалось добавить задачу в очередь.', 'error')
		} finally {
			startButton.disabled = false
		}
	}

	function uploadLocalFile(file) {
		const params = new URLSearchParams({
			output_format: selectedFormat,
			filename: file.name,
		})
		return fetch(apiUrl(`/api/jobs/upload?${params.toString()}`), {
			method: 'POST',
			headers: { 'Content-Type': 'application/octet-stream' },
			body: file,
		})
	}

	async function loadDiagnostics(root) {
		try {
			const response = await fetch(apiUrl('/api/diagnostics'))
			if (!response.ok) {
				return
			}
			diagnosticsState = await response.json()
			const langs = diagnosticsState.tesseract_languages || []
			if (langs.length && !langs.includes('rus')) {
				notify(root, 'Русский язык OCR не найден в контейнере. Пересоберите образ с tesseract-ocr-rus.', 'error')
			}
			updateAvailableFormats(root)
		} catch (_error) {
			// Diagnostics are helpful, but the UI can work without them.
		}
	}

	function uploadNextcloudFile(file) {
		return fetch(apiUrl('/api/jobs/from-nextcloud'), {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({
				fileId: file.fileId || file.file_id,
				output_format: selectedFormat,
			}),
		})
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
			// Keep the UI quiet if the ExApp is temporarily unreachable.
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
			<article class="dt-job ${escapeHtml(job.status)}">
				<div class="dt-job-top">
					<span class="dt-job-file">${escapeHtml(job.filename)}</span>
					<span class="dt-job-status">${escapeHtml(status)}</span>
				</div>
				<div class="dt-progress"><span style="width: ${progress}%"></span></div>
				${job.stage ? `<p class="dt-job-stage">${escapeHtml(stageText(job.stage))}</p>` : ''}
				${job.error ? `<p class="dt-empty">${escapeHtml(job.error)}</p>` : ''}
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

	function stageText(stage) {
		const stages = {
			queued: 'ожидает',
			preflight: 'проверка',
			processing: 'обработка',
			done: 'готово',
			failed: 'ошибка',
		}
		return stages[stage] || stage
	}

	async function saveResult(root, mode) {
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
			notify(root, data.detail || 'Сохранение в Nextcloud пока недоступно.', 'error')
		}
	}

	function openCloudPicker(root) {
		const host = root.querySelector('[data-modal-host]')
		host.innerHTML = `
			<div class="dt-modal-backdrop" data-modal-close></div>
			<section class="dt-modal" role="dialog" aria-modal="true" aria-labelledby="dt-picker-title">
				<header class="dt-modal-header">
					<h2 id="dt-picker-title">Выберите файл из Nextcloud</h2>
					<button class="dt-icon-button" type="button" data-modal-close aria-label="Закрыть">×</button>
				</header>
				<div class="dt-picker-toolbar">
					<div class="dt-breadcrumbs" data-picker-breadcrumbs></div>
					<button class="dt-button" type="button" data-picker-up>Вверх</button>
				</div>
				<div class="dt-picker-list" data-picker-list>
					<p class="dt-empty">Загрузка файлов...</p>
				</div>
				<footer class="dt-modal-footer">
					<span class="dt-picker-selection" data-picker-selection>Файл не выбран</span>
					<div class="dt-output-actions">
						<button class="dt-button" type="button" data-modal-close>Отмена</button>
						<button class="dt-button primary" type="button" data-picker-choose disabled>Выбрать</button>
					</div>
				</footer>
			</section>
		`
		host.querySelectorAll('[data-modal-close]').forEach((button) => {
			button.addEventListener('click', () => closeCloudPicker(root))
		})
		loadCloudFolder(root, '')
	}

	function closeCloudPicker(root) {
		root.querySelector('[data-modal-host]').innerHTML = ''
	}

	async function loadCloudFolder(root, path) {
		const host = root.querySelector('[data-modal-host]')
		const list = host.querySelector('[data-picker-list]')
		const up = host.querySelector('[data-picker-up]')
		const choose = host.querySelector('[data-picker-choose]')
		const selection = host.querySelector('[data-picker-selection]')
		let selectedFile = null

		list.innerHTML = '<p class="dt-empty">Загрузка файлов...</p>'
		choose.disabled = true
		selection.textContent = 'Файл не выбран'
		up.disabled = !path
		up.onclick = () => loadCloudFolder(root, parentPath(path))

		try {
			const params = new URLSearchParams({ path })
			const response = await fetch(apiUrl(`/api/nextcloud/files?${params.toString()}`))
			if (!response.ok) {
				throw new Error(await errorMessage(response))
			}
			const data = await response.json()
			renderBreadcrumbs(root, data.path || '')
			const items = data.items || []
			if (!items.length) {
				list.innerHTML = '<p class="dt-empty">В этой папке нет файлов.</p>'
				return
			}
			list.innerHTML = items.map(pickerRow).join('')
			list.querySelectorAll('[data-picker-row]').forEach((row) => {
				row.addEventListener('click', () => {
					const item = items[Number(row.dataset.index)]
					if (item.is_dir) {
						loadCloudFolder(root, item.path)
						return
					}
					selectedFile = item
					list.querySelectorAll('[data-picker-row]').forEach((other) => other.classList.remove('is-selected'))
					row.classList.add('is-selected')
					selection.textContent = `${item.name} · ${formatBytes(item.size)}`
					choose.disabled = false
				})
			})
			choose.onclick = () => {
				if (!selectedFile) {
					return
				}
				selectCloudFile(root, selectedFile)
				closeCloudPicker(root)
			}
		} catch (error) {
			list.innerHTML = `<p class="dt-empty">${escapeHtml(error.message || 'Не удалось открыть папку.')}</p>`
		}
	}

	function renderBreadcrumbs(root, path) {
		const host = root.querySelector('[data-modal-host]')
		const breadcrumbs = host.querySelector('[data-picker-breadcrumbs]')
		const parts = path.split('/').filter(Boolean)
		const buttons = ['<button type="button" data-path="">Файлы</button>']
		parts.forEach((part, index) => {
			const current = parts.slice(0, index + 1).join('/')
			buttons.push(`<button type="button" data-path="${escapeAttribute(current)}">${escapeHtml(part)}</button>`)
		})
		breadcrumbs.innerHTML = buttons.join('<span>/</span>')
		breadcrumbs.querySelectorAll('button').forEach((button) => {
			button.addEventListener('click', () => loadCloudFolder(root, button.dataset.path || ''))
		})
	}

	function pickerRow(item, index) {
		const icon = item.is_dir ? '📁' : '📄'
		const meta = item.is_dir ? 'Папка' : `${formatBytes(item.size)} · ${item.mimetype || 'файл'}`
		return `
			<button class="dt-picker-row" type="button" data-picker-row data-index="${index}">
				<span class="dt-picker-icon">${icon}</span>
				<span>
					<strong>${escapeHtml(item.name)}</strong>
					<small>${escapeHtml(meta)}</small>
				</span>
			</button>
		`
	}

	function selectCloudFile(root, file) {
		if (file.is_dir) {
			notify(root, 'Выберите файл, не папку.', 'error')
			return
		}
		selectedSource = { type: 'nextcloud', file }
		updateSelectedFile(root)
	}

	function fileInfo(file) {
		const name = file.name || 'document'
		const ext = extension(name)
		const mime = file.type || file.mimetype || ''
		const isImage = mime.startsWith('image/') || ['png', 'jpg', 'jpeg', 'tif', 'tiff', 'bmp', 'webp'].includes(ext)
		const isPdf = mime === 'application/pdf' || ext === 'pdf'
		const isDoc = ['doc', 'docx', 'odt', 'rtf'].includes(ext)
		const isText = ['txt', 'md', 'markdown', 'html', 'htm'].includes(ext)
		const typeLabel = isPdf ? 'PDF' : isImage ? 'изображение' : isDoc ? 'документ' : isText ? 'текст/разметка' : ext.toUpperCase() || 'файл'
		return {
			name,
			ext,
			mime,
			size: file.size || 0,
			isImage,
			isPdf,
			isDoc,
			isText,
			typeLabel,
			needsOcr: isImage,
		}
	}

	function allowedFormatsForSource(source) {
		const info = fileInfo(source.file)
		let allowed
		if (info.isImage) {
			allowed = ['searchable_pdf', 'txt', 'pdf']
		} else if (info.isPdf) {
			allowed = ['searchable_pdf', 'docx', 'txt', 'markdown', 'html']
		} else if (info.isDoc) {
			allowed = ['pdf', 'markdown', 'txt', 'html']
		} else if (['md', 'markdown'].includes(info.ext)) {
			allowed = ['html', 'docx', 'pdf', 'epub', 'txt']
		} else if (['html', 'htm'].includes(info.ext)) {
			allowed = ['pdf', 'markdown', 'docx', 'txt']
		} else if (info.ext === 'txt') {
			allowed = ['pdf', 'docx', 'html', 'markdown']
		} else if (info.ext === 'epub') {
			allowed = ['pdf', 'html', 'txt']
		} else {
			allowed = ['txt']
		}
		return allowed.filter((format) => toolAvailable(format, info))
	}

	function toolAvailable(format, info) {
		if (!diagnosticsState) {
			return true
		}
		const commands = diagnosticsState.commands || {}
		const imports = diagnosticsState.imports || {}
		if (format === 'searchable_pdf' && info.isPdf) {
			return Boolean(commands.ocrmypdf || imports.paddleocr?.ok)
		}
		if (format === 'searchable_pdf' && info.isImage) {
			return Boolean(commands.tesseract || imports.paddleocr?.ok)
		}
		if (format === 'docx' && info.isPdf) {
			return Boolean(imports.pdf2docx?.ok)
		}
		if (['markdown', 'html', 'epub'].includes(format)) {
			return Boolean(commands.pandoc || imports.mammoth?.ok)
		}
		if (format === 'pdf' && info.isDoc) {
			return Boolean(commands.libreoffice || commands.soffice)
		}
		return true
	}

	function extension(name) {
		const last = String(name || '').split('.').pop()
		return last && last !== name ? last.toLowerCase() : ''
	}

	function parentPath(path) {
		const parts = path.split('/').filter(Boolean)
		parts.pop()
		return parts.join('/')
	}

	function notify(root, message, type) {
		const toast = root.querySelector('[data-toast]')
		toast.textContent = message
		toast.dataset.type = type || 'info'
		toast.classList.add('is-visible')
		window.clearTimeout(toast._timer)
		toast._timer = window.setTimeout(() => toast.classList.remove('is-visible'), 4200)
	}

	async function errorMessage(response) {
		const contentType = response.headers.get('Content-Type') || ''
		if (contentType.includes('application/json')) {
			const data = await response.json().catch(() => ({}))
			return data.detail || `Ошибка ${response.status}`
		}
		const text = await response.text().catch(() => '')
		return text || `Ошибка ${response.status}`
	}

	function formatBytes(bytes) {
		const value = Number(bytes || 0)
		if (value === 0) {
			return '0 Б'
		}
		const units = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ']
		const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
		return `${(value / Math.pow(1024, index)).toFixed(index === 0 ? 0 : 1)} ${units[index]}`
	}

	function escapeHtml(value) {
		return String(value ?? '')
			.replace(/&/g, '&amp;')
			.replace(/</g, '&lt;')
			.replace(/>/g, '&gt;')
			.replace(/"/g, '&quot;')
			.replace(/'/g, '&#039;')
	}

	function escapeAttribute(value) {
		return escapeHtml(value).replace(/`/g, '&#096;')
	}

	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', render)
	} else {
		render()
	}
})()
