#!/bin/bash
echo "🍾 Starting AMBAR..."
pkill -f "python bot.py" 2>/dev/null
pkill -f "python operator_bot.py" 2>/dev/null
pkill -f "python support_bot.py" 2>/dev/null
pkill -f "python api_server.py" 2>/dev/null
sleep 1

python api_server.py &
echo "✅ API server started on port ${WEBAPP_PORT:-8080} (PID $!)"
sleep 1

python bot.py &
echo "✅ Customer bot started (PID $!)"
sleep 2

python operator_bot.py &
echo "✅ Operator bot started (PID $!)"
sleep 2

python support_bot.py &
echo "✅ Support bot started (PID $!)"

echo ""
echo "All services running. Press Ctrl+C to stop."
trap "kill %1 %2 %3 %4 2>/dev/null; echo 'Stopped.'; exit" INT
wait
