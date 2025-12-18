/**
 * Quill Editor Utility
 * Утилита для инициализации визуального редактора Quill на любой странице
 */

// Глобальная функция для инициализации редактора
function initQuillEditor(containerId, textareaId, options = {}) {
    // Проверяем, загружен ли Quill
    if (typeof Quill === 'undefined') {
        console.error('Quill editor not loaded. Please include Quill CSS and JS files.');
        return null;
    }

    const defaultOptions = {
        theme: 'snow',
        modules: {
            toolbar: [
                [{ 'header': [1, 2, 3, false] }],
                ['bold', 'italic', 'underline', 'strike'],
                [{ 'list': 'ordered'}, { 'list': 'bullet' }],
                [{ 'color': [] }, { 'background': [] }],
                [{ 'align': [] }],
                ['link', 'image'],
                ['clean']
            ]
        },
        placeholder: 'Введите текст...',
        bounds: '#' + containerId
    };

    const editorOptions = Object.assign({}, defaultOptions, options);
    
    // Создаем контейнер для редактора, если его нет
    let container = document.getElementById(containerId);
    if (!container) {
        console.error('Editor container not found: #' + containerId);
        return null;
    }

    // Скрываем textarea
    const textarea = document.getElementById(textareaId);
    if (textarea) {
        textarea.style.display = 'none';
    }

    // Инициализируем Quill
    const quill = new Quill('#' + containerId, editorOptions);

    // Синхронизируем содержимое с textarea
    quill.on('text-change', function() {
        if (textarea) {
            // Для VK постов используем только текст (без HTML)
            textarea.value = quill.getText();
        }
    });

    // Загружаем начальное содержимое из textarea
    if (textarea && textarea.value) {
        quill.root.innerHTML = textarea.value;
    }

    return quill;
}

// Функция для получения текста из редактора
function getEditorText(quillInstance) {
    if (!quillInstance) return '';
    return quillInstance.getText();
}

// Функция для получения HTML из редактора
function getEditorHTML(quillInstance) {
    if (!quillInstance) return '';
    return quillInstance.root.innerHTML;
}

// Функция для установки текста в редактор
function setEditorText(quillInstance, text) {
    if (!quillInstance) return;
    quillInstance.setText(text);
}

// Функция для установки HTML в редактор
function setEditorHTML(quillInstance, html) {
    if (!quillInstance) return;
    quillInstance.root.innerHTML = html;
}

// Экспорт для использования в других скриптах
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        initQuillEditor,
        getEditorText,
        getEditorHTML,
        setEditorText,
        setEditorHTML
    };
}

