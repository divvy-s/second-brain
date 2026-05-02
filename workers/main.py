"""
ARQ Worker Settings — start with:  arq workers.main.WorkerSettings

Requires Redis running at the configured URL.
Runs fetch_emails_task every 10 minutes via cron.
"""
from __future__ import annotations

from arq import cron

from workers.tasks import fetch_emails_task


class WorkerSettings:
    functions = [fetch_emails_task]
    cron_jobs = [
        cron(fetch_emails_task, minute={0, 10, 20, 30, 40, 50}),
    ]
    # Default Redis connection — override via REDIS_URL env var if needed
    redis_settings = None  # uses arq default (localhost:6379)
