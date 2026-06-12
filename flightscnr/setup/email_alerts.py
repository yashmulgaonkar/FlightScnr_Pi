import smtplib
import socket
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
from config import EMAIL, DISTANCE_UNITS, CLOCK_FORMAT
from typing import Optional
import os

def get_timestamp():
    if CLOCK_FORMAT == "24hr":
        return datetime.now().strftime("%b %d %Y, %H:%M:%S")
    return datetime.now().strftime("%b %d %Y, %I:%M:%S %p")

def format_dist(v):
    if DISTANCE_UNITS.lower() == "metric":
        return f"{v:.5f} km"
    return f"{v:.5f} miles"

def _send(subject: str, body: str, attachment_path: Optional[str] = None):
    """Low-level email sender (internal). Supports optional attachment."""
    if not EMAIL.strip():
        return

    sender = os.environ.get("EMAIL_SENDER", "flight.tracker.alerts2025@gmail.com")
    password = os.environ.get("EMAIL_PASSWORD", "")
    receiver = EMAIL

    if not password:
        return  # No email password configured

    if attachment_path and os.path.isfile(attachment_path):
        msg = MIMEMultipart()
        msg.attach(MIMEText(body, "plain"))

        # Attach the PNG map
        with open(attachment_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{os.path.basename(attachment_path)}"',
        )
        msg.attach(part)
    else:
        msg = MIMEText(body)

    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = receiver

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.starttls()
            smtp.login(sender, password)
            smtp.send_message(msg)
    except Exception as e:
        print(f"⚠️ Failed to send email: {e}")

def send_flight_summary(subject: str, entry: dict, reason: Optional[str] = None, map_url: Optional[str] = None):
    hostname = socket.gethostname()
    body = (
        f"Timestamp: {entry.get('timestamp')}\n"
        f"Hostname: {hostname}\n"
        f"Airline: {entry.get('airline','N/A')}\n"
        f"Flight: {entry.get('callsign','N/A')}\n"
        f"From: {entry.get('origin','?')}\n"
        f"To: {entry.get('destination','?')}\n"
        f"Plane: {entry.get('plane','N/A')}\n"
    )

    if reason:
        body += f"Reason: {reason}\n"

    if "distance_origin" in entry:
        body += f"Distance_origin: {format_dist(entry['distance_origin'])}\n"
    if "distance_destination" in entry:
        body += f"Distance_destination: {format_dist(entry['distance_destination'])}\n"
    if "distance" in entry:
        body += f"Distance: {format_dist(entry['distance'])}\n"

    body += f"Direction: {entry.get('direction','N/A')}\n"

    if map_url:
        body += f"\nMap URL: {map_url}\n"

    _send(subject, body)
