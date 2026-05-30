#!/bin/bash
# Daily summary to Telegram admin. Run by cron at 09:00 MSK.
cd /opt/skazka-bot
docker compose exec -T web python /app/scripts/daily_report.py >> /opt/skazka-bot/backups/daily_report.log 2>&1
