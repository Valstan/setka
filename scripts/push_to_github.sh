#!/bin/bash
# Script to push SETKA to GitHub

echo "═══════════════════════════════════════════════════════════"
echo "📤 Pushing SETKA to GitHub"
echo "═══════════════════════════════════════════════════════════"
echo ""

cd /home/valstan/SETKA

# Check if already has remote
if git remote | grep -q origin; then
    echo "✅ Remote 'origin' already exists"
    git remote -v
else
    echo "📝 Adding remote origin..."
    git remote add origin https://github.com/Valstan/setka.git
    echo "✅ Remote added"
fi

echo ""
echo "🚀 Pushing to GitHub..."
echo ""
echo "⚠️  You will need to enter your GitHub credentials:"
echo "   Username: Valstan"
echo "   Password: Your Personal Access Token"
echo ""
echo "   Get token here: https://github.com/settings/tokens"
echo "   Required scope: repo"
echo ""

git push -u origin main

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Successfully pushed to GitHub!"
    echo "🔗 Repository: https://github.com/Valstan/setka"
else
    echo ""
    echo "❌ Push failed. Possible reasons:"
    echo "   1. Repository doesn't exist - create at https://github.com/new"
    echo "   2. Wrong credentials"
    echo "   3. Network issues"
fi
