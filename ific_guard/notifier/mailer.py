"""Email delivery. Real mail is only sent when IFIC_EMAIL_BACKEND=smtp."""
import logging
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from ..config import Settings

log = logging.getLogger(__name__)


def build_message(settings: Settings, recipients: list[str], subject: str, body: str) -> EmailMessage:
    message = EmailMessage()  # refuses line breaks in headers, so source data cannot inject any
    message["From"] = settings.mail_from
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain="ific-guard")
    message.set_content(body)
    return message


def send(settings: Settings, recipients: list[str], subject: str, body: str, key: str) -> None:
    if settings.email_backend == "smtp" and not recipients:
        raise ValueError("No recipients are configured. Set IFIC_MAIL_TO and IFIC_OPS_MAIL_TO.")
    message = build_message(settings, recipients or ["(no recipients configured)"], subject, body)
    if settings.email_backend == "console":
        log.info("email (console backend, not sent):\n%s", message)
    elif settings.email_backend == "file":
        outbox = settings.data_dir / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        safe_key = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
        (outbox / f"{safe_key}.eml").write_bytes(message.as_bytes())
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password or "")
            smtp.send_message(message)
