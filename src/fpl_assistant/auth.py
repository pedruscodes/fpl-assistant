"""Connexion au compte FPL : OAuth 2 (code + PKCE) auprès d'account.premierleague.com.

Mêmes paramètres que le site fantasy.premierleague.com. La connexion se fait une fois dans
un navigateur (`fpl login`) ; le jeton de rafraîchissement obtenu (scope `offline_access`)
donne ensuite des jetons d'accès sans repasser par le navigateur.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

AUTHORITY = "https://account.premierleague.com/as"
CLIENT_ID = "bfcbaf69-aade-4c1b-8f00-c1cb8a193030"
REDIRECT_URI = "https://fantasy.premierleague.com/"
SCOPE = "openid profile email offline_access"
TOKEN_FILE = Path(
    os.environ.get("FPL_TOKEN_FILE", Path.home() / ".config" / "fpl-assistant" / "token.json")
)


@dataclass
class Login:
    url: str  # À ouvrir dans le navigateur.
    verifier: str
    state: str


def start_login() -> Login:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_urlsafe(16)
    query = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge.rstrip(b"=").decode(),
        "code_challenge_method": "S256",
    }
    return Login(f"{AUTHORITY}/authorize?{urlencode(query)}", verifier, state)


def _token(data: dict[str, str], attempts: int = 3) -> dict:
    for attempt in range(attempts):
        resp = requests.post(
            f"{AUTHORITY}/token", data={"client_id": CLIENT_ID, **data}, timeout=30
        )
        # Après une erreur serveur, on réessaie. Si la FPL avait quand même donné un nouveau
        # jeton, il est perdu de toute façon : l'essai suivant échoue et l'alerte part.
        if resp.status_code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
            break
        time.sleep(5 * 2**attempt)
    if resp.status_code >= 400:
        # Le corps OAuth (`error`, `error_description`) explique mieux qu'un code HTTP.
        raise RuntimeError(f"Connexion FPL refusée ({resp.status_code}) : {resp.text[:300]}")
    tokens = resp.json()
    if "refresh_token" not in tokens and data["grant_type"] == "authorization_code":
        raise RuntimeError("La FPL n'a pas donné de jeton durable (offline_access refusé).")
    return tokens


def finish_login(login: Login, redirected_url: str) -> dict:
    """Échange le code de l'URL de retour (après connexion) contre les jetons."""
    params = parse_qs(urlparse(redirected_url.strip()).query)
    if params.get("state") != [login.state]:
        raise RuntimeError("URL de retour invalide : relance `fpl login` et colle la bonne URL.")
    if "code" not in params:
        raise RuntimeError(f"Pas de code dans l'URL de retour : {params.get('error', ['?'])[0]}")
    return _token(
        {
            "grant_type": "authorization_code",
            "code": params["code"][0],
            "redirect_uri": REDIRECT_URI,
            "code_verifier": login.verifier,
        }
    )


def refresh(refresh_token: str) -> dict:
    """Nouveaux jetons ; le serveur peut aussi renvoyer un nouveau `refresh_token`."""
    return _token({"grant_type": "refresh_token", "refresh_token": refresh_token})


def save_refresh_token(token: str, path: Path = TOKEN_FILE) -> None:
    """Écriture atomique, lisible par toi seul : après une rotation, l'ancien jeton ne vaut
    plus rien, un fichier tronqué obligerait à se reconnecter."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".token-")  # Créé en 0600.
    with os.fdopen(fd, "w") as f:
        json.dump({"refresh_token": token}, f)
    os.replace(tmp, path)


def load_refresh_token(path: Path = TOKEN_FILE) -> str:
    """Depuis le fichier écrit par `fpl login`, sinon la variable `FPL_REFRESH_TOKEN`.

    Le fichier passe en premier : après une rotation, c'est lui qui a le jeton valide.
    """
    if path.exists():
        return json.loads(path.read_text())["refresh_token"]
    if token := os.environ.get("FPL_REFRESH_TOKEN"):
        return token
    raise RuntimeError("Pas connecté : lance `fpl login`.")


def access_token(path: Path = TOKEN_FILE) -> str:
    """Jeton d'accès frais. Si le serveur fait tourner le refresh token, le nouveau est
    réécrit dans `path`."""
    old = load_refresh_token(path)
    tokens = refresh(old)
    if tokens.get("refresh_token", old) != old:
        save_refresh_token(tokens["refresh_token"], path)
    return tokens["access_token"]
