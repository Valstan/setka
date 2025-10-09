#!/bin/bash
# Create GitHub repository and push

echo "╔══════════════════════════════════════════════════════════╗"
echo "║        Creating GitHub Repository for SETKA              ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "Для создания репозитория нужен GitHub Personal Access Token"
echo ""
echo "Получите токен здесь: https://github.com/settings/tokens"
echo "  → Generate new token (classic)"
echo "  → Выберите scope: repo"
echo "  → Скопируйте токен"
echo ""
read -p "Введите ваш GitHub token: " GITHUB_TOKEN
echo ""

if [ -z "$GITHUB_TOKEN" ]; then
    echo "❌ Токен не введён!"
    exit 1
fi

echo "📝 Создаю публичный репозиторий 'setka'..."
echo ""

RESPONSE=$(curl -s -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  -d '{"name":"setka","description":"SETKA - Multimedia Management System for News Resources. Automated content distribution system for 50 regional news channels with AI analysis.","private":false,"auto_init":false,"has_issues":true,"has_wiki":true}' \
  https://api.github.com/user/repos)

if echo "$RESPONSE" | grep -q '"full_name"'; then
    echo "✅ Репозиторий создан успешно!"
    REPO_URL=$(echo "$RESPONSE" | grep -o '"html_url": *"[^"]*"' | head -1 | sed 's/"html_url": "\(.*\)"/\1/')
    echo "🔗 URL: $REPO_URL"
    echo ""
    
    echo "📤 Пушим код на GitHub..."
    cd /home/valstan/SETKA
    
    # Remove existing remote if exists
    git remote remove origin 2>/dev/null
    
    # Add remote
    git remote add origin https://github.com/Valstan/setka.git
    
    # Push
    git push -u origin main
    
    if [ $? -eq 0 ]; then
        echo ""
        echo "✅ Код успешно запушен на GitHub!"
        echo "🔗 Repository: https://github.com/Valstan/setka"
    else
        echo ""
        echo "❌ Ошибка при пуше. Попробуйте вручную:"
        echo "   git push -u origin main"
    fi
else
    echo "❌ Ошибка создания репозитория!"
    echo "Ответ GitHub:"
    echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
    echo ""
    echo "Возможные причины:"
    echo "  1. Неправильный токен"
    echo "  2. Репозиторий 'setka' уже существует"
    echo "  3. Недостаточно прав у токена"
fi
