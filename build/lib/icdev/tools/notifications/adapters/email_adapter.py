#!/usr/bin/env python3
# CUI // SP-CTI
"""SMTP email notification adapter."""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Any, Dict, Optional

from tools.notifications.adapters.base import NotificationAdapter


class EmailAdapter(NotificationAdapter):
    name = "email"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.smtp_host = os.environ.get(config.get("smtp_host_env", "SMTP_HOST"), "")
        self.smtp_port = config.get("smtp_port", 587)
        self.from_addr = os.environ.get(config.get("from_addr_env", "NOTIFICATION_FROM"), "")
        to_addrs_raw = os.environ.get(config.get("to_addrs_env", "NOTIFICATION_TO"), "")
        self.to_addrs = [a.strip() for a in to_addrs_raw.split(",") if a.strip()]
        self.use_tls = config.get("use_tls", True)
        self.username = os.environ.get(config.get("username_env", "SMTP_USERNAME"), "")
        self.password = os.environ.get(config.get("password_env", "SMTP_PASSWORD"), "")

    def send(self, title: str, body: str, severity: str = "info", metadata: Optional[Dict] = None) -> bool:
        if not self.smtp_host or not self.from_addr or not self.to_addrs:
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[ICDEV™ {severity.upper()}] {title}"
        msg["From"] = self.from_addr
        msg["To"] = ", ".join(self.to_addrs)

        text_part = MIMEText(body, "plain", "utf-8")
        msg.attach(text_part)

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                if self.use_tls:
                    server.starttls()
                if self.username and self.password:
                    server.login(self.username, self.password)
                server.sendmail(self.from_addr, self.to_addrs, msg.as_string())
            return True
        except (smtplib.SMTPException, OSError):
            return False
