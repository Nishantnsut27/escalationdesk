"""Report setup readiness without printing secrets; optionally generate the local encryption key."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate-key", action="store_true", help="Append a new encryption key to ignored backend/.env only if none is configured.")
    args = parser.parse_args()
    from backend import config
    from backend.sentry_client import configured, cipher, SentryError
    if args.generate_key:
        if config.CONNECTION_KEY:
            print("Encryption key already configured; preserved.")
        else:
            from cryptography.fernet import Fernet
            path = ROOT / "backend" / ".env"
            # A blank root entry would override this new backend-only value.
            import os
            if "DESK_CONNECTION_KEY" in os.environ:
                raise SystemExit("Remove the blank DESK_CONNECTION_KEY override first; no file was changed.")
            with path.open("a", encoding="utf-8") as file:
                file.write("\nDESK_CONNECTION_KEY=" + Fernet.generate_key().decode() + "\n")
            print("Generated encryption key in ignored backend/.env. Restart the backend and preserve this key with your deployment secrets.")
            return
    fields = {"HTTPS workspace": bool(config.PUBLIC_URL), "Sentry app slug": bool(config.SENTRY_APP_SLUG),
        "Sentry client ID": bool(config.SENTRY_CLIENT_ID), "Sentry client secret": bool(config.SENTRY_CLIENT_SECRET)}
    try:
        cipher()
        fields["Credential encryption key"] = True
    except SentryError:
        fields["Credential encryption key"] = False
    for label, ready in fields.items():
        print(label + ": " + ("configured" if ready else "MISSING"))
    print("OAuth configuration: " + ("ready for live verification" if configured() else "pending"))
    print("No provider request made. See docs/sentry-setup.md for integration and verification steps.")


if __name__ == "__main__":
    main()
