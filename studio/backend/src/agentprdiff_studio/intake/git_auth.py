"""Non-interactive auth for ``git clone`` / ``git fetch`` in Studio.

The Studio container has no TTY, so git can't prompt for credentials. Two
private-repo paths are supported:

1. **SSH** — ``git@github.com:owner/repo.git`` URLs. The operator mounts
   their host's ``~/.ssh`` into the container (see studio/README.md);
   ``openssh-client`` is installed in the Studio image so git can shell
   out to ``ssh``. Nothing in this module is required for that path —
   we just turn off interactive prompts.

2. **HTTPS + token** — ``https://github.com/owner/repo.git`` URLs.
   The operator saves a GitHub PAT (or GitLab / Bitbucket equivalent)
   in Studio's Secrets page; this module reads that token and configures
   the git subprocess with ``http.<host>.extraheader: Authorization: ...``
   via the ``GIT_CONFIG_COUNT`` / ``GIT_CONFIG_KEY_N`` / ``GIT_CONFIG_VALUE_N``
   transient-config env vars. The token never appears in the URL (so it
   can't leak into git's reflog, ``~/.gitconfig``, or process-listing
   ``ps`` output of any child) and never appears in our own logs.

Secret naming convention (looked up in this order, project-scoped winning
over global):

* ``GITHUB_TOKEN``     — github.com hosts
* ``GITLAB_TOKEN``     — gitlab.com hosts
* ``BITBUCKET_TOKEN``  — bitbucket.org hosts
* ``GIT_HTTPS_TOKEN``  — fallback for any HTTPS host

The fallback lets adopters point Studio at self-hosted GitHub Enterprise
or self-hosted GitLab without baking the hostname into Studio.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

# Tokens we redact in logs / error strings. Covers:
#   gh[poure]_…             classic + fine-grained PAT prefixes (GitHub)
#   github_pat_…            fine-grained PAT (long form, GitHub)
#   glpat-…                 GitLab PAT
#   bb-…                    Bitbucket app password (loose; broaden if needed)
#   basic-auth in URLs:     ``https://user:pass@host`` → ``https://***@host``
_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"gh[poure]_[A-Za-z0-9_]{20,}"), "***redacted***"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "***redacted***"),
    (re.compile(r"glpat-[A-Za-z0-9_\-]{20,}"), "***redacted***"),
    (
        # https://user:pass@host  →  https://***@host (and same for http://)
        re.compile(r"(https?://)[^/@\s:]+:[^/@\s]+@"),
        r"\1***@",
    ),
]


# Default mapping of host → preferred secret name. Falls back to
# ``GIT_HTTPS_TOKEN`` for hosts not listed.
_DEFAULT_HOST_SECRETS: dict[str, str] = {
    "github.com": "GITHUB_TOKEN",
    "gitlab.com": "GITLAB_TOKEN",
    "bitbucket.org": "BITBUCKET_TOKEN",
}
FALLBACK_TOKEN_NAME = "GIT_HTTPS_TOKEN"


@dataclass(frozen=True)
class GitAuth:
    """Optional auth context for one clone/fetch invocation.

    ``token`` is the bearer / PAT string and ``host`` is the hostname the
    token grants access to (e.g. ``"github.com"``). The token is consumed
    once via :func:`build_clone_env` and never stored on disk.
    """

    token: str
    host: str


def detect_scheme(source: str) -> str:
    """Return the source's transport scheme.

    Returns one of:

    * ``"ssh"``    — ``git@host:owner/repo``, ``ssh://...``, ``git+ssh://...``
    * ``"https"``  — ``https://...``
    * ``"http"``   — ``http://...``
    * ``"file"``   — ``file://...`` or any plain filesystem path
    * ``"other"``  — anything we don't recognise (treated as no-auth-needed)
    """
    if not source:
        return "other"
    s = source.strip()
    # SSH shorthand: user@host:path  (no scheme prefix, presence of ':' after the host)
    if re.match(r"^[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:", s):
        return "ssh"
    parsed = urlparse(s)
    scheme = (parsed.scheme or "").lower()
    if scheme in {"ssh", "git+ssh"}:
        return "ssh"
    if scheme == "https":
        return "https"
    if scheme == "http":
        return "http"
    if scheme == "file":
        return "file"
    # No scheme at all → local path
    if "://" not in s:
        return "file"
    return "other"


def host_of(source: str) -> str | None:
    """Best-effort hostname extraction for HTTPS URLs.

    Returns ``None`` for SSH-shorthand / local paths / unparseable inputs.
    Used to pick the right token secret and to scope the
    ``http.<host>.extraheader`` config.
    """
    s = source.strip()
    if "://" not in s:
        return None
    try:
        return (urlparse(s).hostname or "").lower() or None
    except ValueError:
        return None


def preferred_secret_names(host: str) -> list[str]:
    """Ordered list of secret names to try for a given host.

    Most specific (host-mapped) first, ``GIT_HTTPS_TOKEN`` fallback last.
    Callers iterate this list and use the first one present in the
    decrypted secrets dict.
    """
    out: list[str] = []
    mapped = _DEFAULT_HOST_SECRETS.get(host)
    if mapped:
        out.append(mapped)
    if FALLBACK_TOKEN_NAME not in out:
        out.append(FALLBACK_TOKEN_NAME)
    return out


def resolve_auth(source: str, secret_lookup: Mapping[str, str]) -> GitAuth | None:
    """Pick a token from ``secret_lookup`` matching the source's host.

    Returns ``None`` if the source isn't HTTPS, the host can't be
    determined, or no matching secret is present.
    """
    if detect_scheme(source) not in ("https", "http"):
        return None
    host = host_of(source)
    if not host:
        return None
    for name in preferred_secret_names(host):
        token = secret_lookup.get(name)
        if token:
            return GitAuth(token=token, host=host)
    return None


def build_clone_env(
    base_env: Mapping[str, str],
    auth: GitAuth | None,
) -> dict[str, str]:
    """Build the env dict for a git clone/fetch subprocess.

    Always sets ``GIT_TERMINAL_PROMPT=0`` so git can't block on stdin even
    when the operator forgot to configure auth.

    When ``auth`` is present, injects a transient git config entry that
    sends ``Authorization: bearer <token>`` to the host. The token does
    not appear in the URL we pass to git — only in this env-injected
    config — which keeps it out of git's reflog, the workspace's
    ``.git/config``, and ``ps``-visible argv lists.
    """
    env = dict(base_env)
    env["GIT_TERMINAL_PROMPT"] = "0"

    if auth is None:
        return env

    # Don't clobber an existing GIT_CONFIG_COUNT the caller may have set;
    # bump it by one and append our extraheader entry as the next index.
    try:
        existing = int(env.get("GIT_CONFIG_COUNT", "0") or "0")
    except ValueError:
        existing = 0
    idx = existing
    env["GIT_CONFIG_COUNT"] = str(existing + 1)
    env[f"GIT_CONFIG_KEY_{idx}"] = f"http.https://{auth.host}/.extraheader"
    env[f"GIT_CONFIG_VALUE_{idx}"] = f"Authorization: bearer {auth.token}"
    return env


def redact(text: str) -> str:
    """Replace any token-shaped substring in ``text`` with ``***redacted***``.

    Used on log lines, error messages, and any JSON we return to the UI.
    Cheap regex pass — doesn't pretend to be exhaustive. Tokens we
    actively *use* (the one in the GitAuth we built) are stripped by
    :func:`redact_with_auth` which adds an exact-string substitution.
    """
    if not text:
        return text
    out = text
    for pattern, replacement in _REDACT_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def redact_with_auth(text: str, auth: GitAuth | None) -> str:
    """:func:`redact` plus an exact-string substitution for the active token.

    Catches the case where the regex-shape patterns miss a custom-format
    token (e.g. a self-hosted GitLab using non-default PAT layout).
    """
    out = redact(text)
    if auth and auth.token and auth.token in out:
        out = out.replace(auth.token, "***redacted***")
    return out


def explain_clone_failure(
    err_message: str,
    *,
    source: str,
    had_auth: bool,
) -> str:
    """Map a git error string to a human-readable next step.

    Returns the original (redacted) message plus an action-oriented hint
    when we recognise a familiar failure mode; otherwise just the redacted
    original. Hint phrasing assumes the operator is the one reading this
    in the Studio UI.
    """
    msg = redact(err_message or "")
    scheme = detect_scheme(source)
    msg_lower = msg.lower()

    hint = None
    if "could not read username" in msg_lower or "authentication failed" in msg_lower:
        if scheme in ("https", "http"):
            host = host_of(source) or "the remote host"
            mapped = _DEFAULT_HOST_SECRETS.get(host, FALLBACK_TOKEN_NAME)
            if had_auth:
                hint = (
                    f"Authentication to {host} failed even with a token. "
                    f"Check that the {mapped} secret has repo read access "
                    "and hasn't been revoked or expired."
                )
            else:
                hint = (
                    f"This looks like a private repo on {host}. Save a "
                    f"personal access token as a Studio Secret named "
                    f"{mapped} (or GIT_HTTPS_TOKEN for self-hosted hosts), "
                    "then click Sync again. Alternatively switch to an "
                    "SSH URL and mount your ~/.ssh into the container."
                )
    elif "cannot run ssh" in msg_lower or "no such file or directory" in msg_lower and scheme == "ssh":
        hint = (
            "SSH isn't available in this Studio container. Rebuild the "
            "image (newer Studio images include openssh-client), or "
            "switch this project to an HTTPS URL with a token saved in "
            "Studio Secrets."
        )
    elif "permission denied" in msg_lower and "publickey" in msg_lower:
        hint = (
            "SSH key authentication was rejected. Make sure your SSH key "
            "is mounted into the Studio container at /root/.ssh (read-only "
            "is fine), and that the key has push/clone access to this "
            "repo. Or switch this project to HTTPS + a token saved in "
            "Studio Secrets."
        )
    elif "host key verification failed" in msg_lower:
        hint = (
            "The remote host's SSH key isn't in known_hosts. Mount your "
            "host's ~/.ssh/known_hosts into the Studio container, or "
            "add a known_hosts file with the remote's key fingerprint."
        )

    if hint:
        return f"{msg}\n\n{hint}"
    return msg


def looks_like_embedded_credential(source: str) -> bool:
    """Detect ``https://user:token@host`` URLs.

    Studio refuses to accept these — the token would land in DB and logs
    where the structured secrets store is the appropriate home.
    """
    if not source:
        return False
    return bool(re.match(r"^https?://[^/@\s]+:[^/@\s]+@", source.strip()))
