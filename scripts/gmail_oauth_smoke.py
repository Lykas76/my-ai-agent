from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

BASE_DIR = Path(__file__).resolve().parent.parent
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
]


def main():
    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError(
            f"OAuth credentials not found: {CREDENTIALS_FILE}"
        )

    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(
            TOKEN_FILE,
            SCOPES,
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE,
                SCOPES,
            )

            creds = flow.run_local_server(
                host="localhost",
                port=0,
                open_browser=True,
            )

        TOKEN_FILE.write_text(
            creds.to_json(),
            encoding="utf-8",
        )

    service = build(
        "gmail",
        "v1",
        credentials=creds,
        cache_discovery=False,
    )

    profile = service.users().getProfile(
        userId="me"
    ).execute()

    print()
    print("=== GMAIL TEST ===")
    print("GMAIL OAUTH OK")
    print("Messages total:", profile.get("messagesTotal"))
    print("Threads total :", profile.get("threadsTotal"))
    print("History ID    :", profile.get("historyId"))
    print("Scope         : gmail.readonly")


if __name__ == "__main__":
    main()
