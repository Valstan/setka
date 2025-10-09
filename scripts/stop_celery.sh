#!/bin/bash
# Stop Celery worker and beat scheduler

cd /home/valstan/SETKA

echo "🛑 Stopping Celery..."

# Stop worker
if [ -f logs/celery_worker.pid ]; then
    WORKER_PID=$(cat logs/celery_worker.pid)
    if kill -0 $WORKER_PID 2>/dev/null; then
        kill $WORKER_PID
        echo "✅ Celery worker stopped (PID: $WORKER_PID)"
    else
        echo "⚠️ Celery worker not running"
    fi
    rm logs/celery_worker.pid
else
    echo "⚠️ No worker PID file found"
fi

# Stop beat
if [ -f logs/celery_beat.pid ]; then
    BEAT_PID=$(cat logs/celery_beat.pid)
    if kill -0 $BEAT_PID 2>/dev/null; then
        kill $BEAT_PID
        echo "✅ Celery beat stopped (PID: $BEAT_PID)"
    else
        echo "⚠️ Celery beat not running"
    fi
    rm logs/celery_beat.pid
else
    echo "⚠️ No beat PID file found"
fi

# Kill any remaining celery processes
pkill -f "celery -A celery_app" 2>/dev/null

echo ""
echo "✅ Celery stopped"

