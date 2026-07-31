import logging
import sys
from logging.config import dictConfig


def configure_logging() -> None:
    dictConfig({
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'default': {'format': '%(asctime)s | %(levelname)s | %(name)s | %(message)s'},
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'stream': sys.stdout,
                'formatter': 'default',
            },
        },
        'root': {'handlers': ['console'], 'level': 'INFO'},
        'loggers': {
            'uvicorn': {'level': 'INFO'},
            'sqlalchemy.engine': {'level': 'WARNING'},
        },
    })


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
