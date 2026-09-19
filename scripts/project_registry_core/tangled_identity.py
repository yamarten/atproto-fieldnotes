"""Profile-independent, bidirectionally verified Tangled owner handles."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
from typing import Any, Callable

from . import http_transport
from .project_record import ATPROTO_HANDLE_RE
from .registry import BSKY_PUBLIC_API, resolve_did_document
from .reliability import run_with_retry


class OwnerUnavailable(ValueError):
    """An observed identity condition; retain the record for later evaluation."""


class OwnerResolutionError(RuntimeError):
    """A sanitized source/stage error suitable for a CLI or Web job."""


def _request(owner_did: str, stage: str, operation: Callable[[], Any]) -> Any:
    def attempt() -> Any:
        try:
            return operation()
        except urllib.error.HTTPError as exc:
            status = exc.code
            if status == 429 or status >= 500:
                exc.close()
                raise
            try:
                payload = json.loads(exc.read(4096))
            except (ValueError, UnicodeError):
                payload = {}
            finally:
                exc.close()
            error = payload.get("error") if isinstance(payload, dict) else None
            if stage == "did_document" and status in {404, 410}:
                raise OwnerUnavailable(f"did_document_http_{status}") from exc
            if stage == "resolve_handle" and status == 400 and error == "HandleNotFound":
                raise OwnerUnavailable("handle_not_found") from exc
            if (stage == "resolve_handle" and status == 400 and error == "InvalidRequest"
                    and payload.get("message") == "Unable to resolve handle"):
                # The public resolver uses this response for an unresolved handle.
                # It can include upstream DNS failure: retain/retry, never discard.
                raise OwnerUnavailable("handle_unresolved") from exc
            # Unknown 400s, auth/configuration errors and endpoint failures are global.
            raise OwnerResolutionError(f"Tangled owner {owner_did}: {stage} HTTP {status}") from exc

    try:
        value, _ = run_with_retry(attempt, sleep=lambda delay: time.sleep(min(delay, 2.0)))
        return value
    except (OwnerUnavailable, OwnerResolutionError):
        raise
    except Exception as exc:
        status = getattr(exc, "code", None)
        detail = f"HTTP {status}" if status else type(exc).__name__
        # Do not put arbitrary remote bodies, URLs or credentials in job errors.
        raise RuntimeError(f"Tangled owner {owner_did}: {stage} failed ({detail})") from exc


def resolve_owner_handle(owner_did: str) -> str:
    if not owner_did.startswith(("did:plc:", "did:web:")):
        raise OwnerUnavailable("unsupported_owner_did")
    document = _request(owner_did, "did_document", lambda: resolve_did_document(owner_did))
    if not isinstance(document, dict) or not isinstance(document.get("id"), str):
        raise RuntimeError(f"Tangled owner {owner_did}: malformed DID document")
    if document["id"] != owner_did:
        raise OwnerUnavailable("did_document_id_mismatch")
    aliases = document.get("alsoKnownAs", [])
    if not isinstance(aliases, list):
        raise RuntimeError(f"Tangled owner {owner_did}: malformed alsoKnownAs")
    handle = ""
    for alias in aliases:
        if not isinstance(alias, str) or not alias.startswith("at://"):
            continue
        candidate = alias.removeprefix("at://").lower()
        if (len(candidate) <= 253 and ATPROTO_HANDLE_RE.fullmatch(candidate)
                and candidate.rsplit(".", 1)[-1][0].isalpha()):
            handle = candidate
            break  # Only the first syntactically valid claimed handle is authoritative.
    if not handle or handle.endswith(".invalid"):
        raise OwnerUnavailable("no_valid_claimed_handle")
    params = urllib.parse.urlencode({"handle": handle})
    identity = _request(owner_did, "resolve_handle", lambda: http_transport.request_json(
        f"{BSKY_PUBLIC_API}/xrpc/com.atproto.identity.resolveHandle?{params}", timeout=15,
    ))
    if not isinstance(identity, dict) or not isinstance(identity.get("did"), str) or not identity["did"]:
        raise RuntimeError(f"Tangled owner {owner_did}: malformed handle resolution")
    if identity["did"] != owner_did:
        raise OwnerUnavailable("handle_did_mismatch")
    return handle
