"""Upstox OAuth helper. Run on YOUR machine: you type your own credentials/OTP in the browser;
this code only reads the `code` Upstox puts in the redirect URL and exchanges it for a token.

Needs env vars: UPSTOX_API_KEY, UPSTOX_API_SECRET, UPSTOX_REDIRECT_URI (must match your app).
Endpoints follow Upstox's public OAuth docs but are untested against a live account here.
The token is saved to state/token.json (chmod 600, git-ignored) and expires daily.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"


def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Set {name} (see README, 'Getting an access token').")
    return v


def login_url(api_key: str, redirect_uri: str) -> str:
    return AUTH_URL + "?" + urlencode({"client_id": api_key, "redirect_uri": redirect_uri,
                                       "response_type": "code"})


def code_from_redirect(url: str) -> str:
    q = parse_qs(urlparse(url.strip()).query)
    if "code" not in q:
        raise ValueError("No ?code= in that URL. Paste the full address you were redirected to.")
    return q["code"][0]


def exchange_code(code: str, api_key: str, api_secret: str, redirect_uri: str) -> dict:
    r = requests.post(TOKEN_URL, headers={"Accept": "application/json"}, timeout=20, data={
        "code": code, "client_id": api_key, "client_secret": api_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    r.raise_for_status()
    return r.json()


def save_token(state_dir: str | Path, payload: dict) -> Path:
    p = Path(state_dir) / "token.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"access_token": payload["access_token"],
                             "saved_at": datetime.now().isoformat(timespec="seconds")}))
    p.chmod(0o600)
    return p


def load_token(state_dir: str | Path = "state") -> str | None:
    """Env var wins; otherwise today's saved token. Yesterday's token is ignored (they expire)."""
    if os.environ.get("UPSTOX_ACCESS_TOKEN"):
        return os.environ["UPSTOX_ACCESS_TOKEN"]
    p = Path(state_dir) / "token.json"
    if p.exists():
        d = json.loads(p.read_text())
        if datetime.fromisoformat(d["saved_at"]).date() == datetime.now().date():
            return d["access_token"]
    return None


def capture_with_browser(url: str, redirect_uri: str, timeout_s: int = 300) -> str:
    """Opens a visible browser; YOU log in. Returns the code once the redirect happens."""
    from playwright.sync_api import sync_playwright  # pip install playwright && playwright install chromium

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        page = browser.new_context().new_page()
        page.goto(url)
        page.wait_for_url(lambda u: u.startswith(redirect_uri) and "code=" in u, timeout=timeout_s * 1000)
        code = code_from_redirect(page.url)
        browser.close()
        return code


def run_login(state_dir: str, browser: bool) -> str:
    key, secret, redirect = _env("UPSTOX_API_KEY"), _env("UPSTOX_API_SECRET"), _env("UPSTOX_REDIRECT_URI")
    url = login_url(key, redirect)
    if browser:
        code = capture_with_browser(url, redirect)
    else:
        print("1) Open this address, log in, approve:\n\n   " + url +
              "\n\n2) Copy the FULL address you land on (it contains ?code=...) and paste it here.")
        code = code_from_redirect(input("\nRedirected URL: "))
    path = save_token(state_dir, exchange_code(code, key, secret, redirect))
    return f"Token saved to {path} (valid for today only)."
