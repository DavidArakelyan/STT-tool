"""Celery application configuration."""

from celery import Celery

from stt_service.config import get_settings
from stt_service.utils.logging_config import configure_logging

# Configure logging
configure_logging()

settings = get_settings()

# Create Celery app
celery_app = Celery(
    "stt_service",
    broker=settings.celery.broker_url,
    backend=settings.celery.result_backend,
)

# Configure Celery
celery_app.conf.update(
    task_serializer=settings.celery.task_serializer,
    result_serializer=settings.celery.result_serializer,
    accept_content=settings.celery.accept_content,
    timezone=settings.celery.timezone,
    task_track_started=settings.celery.task_track_started,
    task_time_limit=settings.celery.task_time_limit,
    worker_prefetch_multiplier=settings.celery.worker_prefetch_multiplier,
    worker_concurrency=settings.celery.worker_concurrency,
    # Task routing
    task_routes={
        "stt_service.workers.tasks.process_transcription_job": {"queue": "transcription"},
        "stt_service.workers.tasks.process_chunk": {"queue": "transcription"},
        "stt_service.workers.tasks.send_webhook": {"queue": "webhooks"},
    },
    # Task retry settings
    task_default_retry_delay=60,
    task_max_retries=3,
    # Result settings
    result_expires=86400,  # 24 hours
    # Beat schedule (if needed for periodic tasks)
    beat_schedule={},
)

# Auto-discover tasks
celery_app.autodiscover_tasks(["stt_service.workers"])
