# Визуальный редактор (Quill)

## Источник истины

- JS утилита: `web/static/js/editor.js`
- Использование: страницы publisher (`web/templates/publisher.html` + подключение Quill CDN)

## Что важно

- Для VK постов используется **только текст** (`quill.getText()`), HTML не отправляется (VK не поддерживает HTML в постах).


