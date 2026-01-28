import logging
import os
import sys
from pathlib import Path

import structlog
from stt_service.config import get_settings

def configure_logging():
    """Configure structlog for both console and file output."""
    settings = get_settings()
    
    # Ensure log directory exists
    log_dir = Path("/app/logs")
    if not log_dir.exists():
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            # Fallback for local development outside docker
            log_dir = Path("logs")
            log_dir.mkdir(parents=True, exist_ok=True)
            
    log_file = log_dir / "app.log"

    # Define processors
    processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    # Standard logging config to catch third-party logs (SQLAlchemy, Celery, etc.)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper()),
    )

    # Configure structlog
    structlog.configure(
        processors=processors + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Add file handler to root logger to capture everything
    try:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
        logging.getLogger().addHandler(file_handler)
    except Exception as e:
        print(f"Warning: Could not initialize file logging: {e}", file=sys.stderr)
    
    # Set levels for noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO) # Keep SQL logs as requested
