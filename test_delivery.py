import os
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv
from pathlib import Path

# Load .env from the root
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

def test_delivery():
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    
    # Send test to Anmol
    to_email = "anmoldubey2648@gmail.com"

    print(f"Delivering test email from {user} to {to_email}...")
    
    msg = MIMEText("This is a live delivery test to Anmol from your LiveKit Call Center.")
    msg["Subject"] = "Live Center Test: Email Delivery"
    msg["From"] = user
    msg["To"] = to_email

    try:
        server = smtplib.SMTP(host, port, timeout=15)
        server.set_debuglevel(1)
        server.starttls()
        server.login(user, password)
        server.sendmail(user, [to_email], msg.as_string())
        server.quit()
        print("\n✅ SUCCESS: Email successfully delivered to Anmol!")
    except Exception as e:
        print(f"\n❌ FAILURE: {e}")

if __name__ == "__main__":
    test_delivery()
