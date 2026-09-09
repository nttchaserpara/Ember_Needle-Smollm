"""Google Docs creation, with a separate, explicit desktop OAuth setup step."""

import argparse
import os
import webbrowser
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
API_ROOT = "https://docs.googleapis.com/v1/documents"


def _token_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".config")))
    return base / "Ember" / "google_docs_token.json"


def _save_credentials(credentials) -> None:
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(credentials.to_json(), encoding="utf-8")
    temporary.replace(path)


def connect(credentials_path: str) -> None:
    """Run only from the setup command; ordinary tool calls never start login."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
    credentials = flow.run_local_server(port=0, timeout_seconds=180)
    _save_credentials(credentials)


def _authorized_session():
    from google.auth.transport.requests import AuthorizedSession, Request
    from google.oauth2.credentials import Credentials

    path = _token_path()
    setup = 'python -m use_cases.google_docs --connect "C:\\path\\credentials.json"'
    if not path.is_file():
        raise RuntimeError(f"Google Docs is not connected. Run: {setup}")
    try:
        credentials = Credentials.from_authorized_user_file(str(path))
        if not credentials.has_scopes(SCOPES):
            raise ValueError("Missing Google Docs permission")
        if not credentials.valid and credentials.refresh_token:
            credentials.refresh(Request())
            _save_credentials(credentials)
        if not credentials.valid:
            raise ValueError("Credentials are no longer valid")
    except Exception as exc:
        raise RuntimeError(f"Google Docs authentication failed. Reconnect with: {setup}") from exc
    return AuthorizedSession(credentials)


def create_google_doc(title: str, content: str, open_after: bool = True) -> str:
    """Create and fill a document owned by the connected Google account."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("A Google Docs title is required")
    if not isinstance(content, str):
        raise ValueError("Google Docs content must be text")

    url = None
    with _authorized_session() as session:
        try:
            response = session.post(API_ROOT, json={"title": title}, timeout=30)
            response.raise_for_status()
            document_id = response.json()["documentId"]
            url = f"https://docs.google.com/document/d/{document_id}/edit"
            if content:
                response = session.post(
                    f"{API_ROOT}/{document_id}:batchUpdate",
                    json={"requests": [{"insertText": {"location": {"index": 1}, "text": content}}]},
                    timeout=30,
                )
                response.raise_for_status()
        except Exception as exc:
            if url:
                raise RuntimeError(
                    f"Google document created at {url}, but content insertion could not be confirmed. "
                    "Check the document before retrying to avoid a duplicate."
                ) from exc
            raise RuntimeError(
                "Google Docs creation could not be confirmed. Check your connection, enable the "
                "Google Docs API, and check Drive before retrying to avoid a duplicate."
            ) from exc

    result = f"Google document created: {title}\n{url}"
    if open_after:
        try:
            if not webbrowser.open(url):
                result += "\nBrowser did not open; use the link above."
        except OSError:
            result += "\nBrowser did not open; use the link above."
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Connect Ember to Google Docs")
    parser.add_argument("--connect", required=True, metavar="CREDENTIALS_JSON")
    args = parser.parse_args()
    try:
        connect(args.connect)
    except Exception as exc:
        parser.exit(1, f"Google Docs setup failed ({type(exc).__name__}). Check the credentials file and retry login.\n")
    print("Google Docs connected. You can now create documents through Ember.")
