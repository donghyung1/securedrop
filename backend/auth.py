"""Token and administrator password primitives. Never persist plaintext secrets."""

import base64
import hashlib
import hmac
import secrets


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_token():
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=2**17, r=8, p=1, maxmem=256 * 1024 * 1024, dklen=32
    )
    return (
        "scrypt$131072$8$1$"
        + base64.b64encode(salt).decode()
        + "$"
        + base64.b64encode(digest).decode()
    )


def verify_password(password, stored):
    try:
        kind, n, r, p, salt, expected = stored.split("$")
        if (kind, n, r, p) != ("scrypt", "131072", "8", "1"):
            return False
        actual = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=256 * 1024 * 1024,
            dklen=32,
        )
        return hmac.compare_digest(actual, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False
