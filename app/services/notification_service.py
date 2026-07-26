import smtplib
from email.message import EmailMessage
import httpx
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class NotificationService:
    async def telegram(self, text: str) -> bool:
        if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_ADMIN_CHAT_ID:
            logger.info('Telegram disabled: %s', text)
            return False
        url = f'https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage'
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json={'chat_id': settings.TELEGRAM_ADMIN_CHAT_ID, 'text': text})
            return response.status_code < 300

    def email(self, to: str, subject: str, body: str) -> bool:
        if not settings.SMTP_HOST:
            logger.info('SMTP disabled: %s %s', subject, body)
            return False
        message = EmailMessage()
        message['From'] = settings.SMTP_USER
        message['To'] = to
        message['Subject'] = subject
        message.set_content(body)
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            if settings.SMTP_USER:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(message)
        return True

notification_service = NotificationService()
