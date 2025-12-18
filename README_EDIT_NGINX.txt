╔══════════════════════════════════════════════════════════════╗
║  ВАЖНО: Редактирование конфигурации nginx                    ║
╚══════════════════════════════════════════════════════════════╝

❌ НЕ РЕДАКТИРУЙТЕ напрямую:
   /etc/nginx/conf.d/setka.conf
   
   Этот файл требует прав root и Cursor не может его сохранить!

✅ РЕДАКТИРУЙТЕ этот файл:
   /home/valstan/SETKA/config/setka.conf.editable
   
   Или используйте симлинк:
   /home/valstan/SETKA/setka.conf.local

📝 ПОСЛЕ РЕДАКТИРОВАНИЯ:
   1. Сохраните файл (Ctrl+S)
   2. Запустите скрипт:
      /home/valstan/SETKA/scripts/apply_nginx_config.sh
   
   Скрипт автоматически:
   - Создаст бэкап
   - Проверит синтаксис
   - Применит изменения
   - Перезагрузит nginx

⚡ БЫСТРАЯ КОМАНДА:
   cd /home/valstan/SETKA
   nano config/setka.conf.editable
   ./scripts/apply_nginx_config.sh

📚 Документация:
   /home/valstan/SETKA/HOW_TO_EDIT_NGINX.md

