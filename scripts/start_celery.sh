#!/bin/bash
# Start Celery worker and beat scheduler

# Activate virtual environment
source /home/valstan/SETKA/venv/bin/activate

# Change to project directory
cd /home/valstan/SETKA

echo "🚀 Starting Celery worker and beat scheduler..."

# Start Celery worker in background
celery -A celery_app worker --loglevel=info --logfile=logs/celery_worker.log &
WORKER_PID=$!

echo "✅ Celery worker started (PID: $WORKER_PID)"

# Start Celery beat scheduler in background
celery -A celery_app beat --loglevel=info --logfile=logs/celery_beat.log &
BEAT_PID=$!

echo "✅ Celery beat started (PID: $BEAT_PID)"

# Save PIDs
echo $WORKER_PID > logs/celery_worker.pid
echo $BEAT_PID > logs/celery_beat.pid

echo ""
echo "📊 Celery is now running!"
echo "   Worker PID: $WORKER_PID"
echo "   Beat PID: $BEAT_PID"
echo ""
echo "To stop Celery, run: ./scripts/stop_celery.sh"
echo "To view logs: tail -f logs/celery_worker.log"

