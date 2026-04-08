import os
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv
from pathlib import Path

# Load .env from the root
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

def test_smtp():
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    to_email = os.getenv("SMTP_FROM") # send to self

    print(f"Testing SMTP with {user} on {host}:{port}...")
    
    msg = MIMEText("This is a test email from LiveKit AI Call Center.")
    msg["Subject"] = "SMTP Test"
    msg["From"] = user
    msg["To"] = to_email

    try:
        server = smtplib.SMTP(host, port, timeout=10)
        server.set_debuglevel(1)
        server.starttls()
        server.login(user, password)
        server.sendmail(user, [to_email], msg.as_string())
        server.quit()
        print("\nSUCCESS: Email sent successfully!")
    except Exception as e:
        print(f"\nFAILURE: {e}")

if __name__ == "__main__":
    test_smtp()
