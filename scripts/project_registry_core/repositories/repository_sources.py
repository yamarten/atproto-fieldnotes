from __future__ import annotations

import base64
import copy
import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from html.parser import HTMLParser
from typing import Any

from ..transport import http_transport
from ..transport import github_api
from ..transport import reliability as reliability_core
from . import repo_urls as repo_url_core


GITHUB_API = "https://api.github.com"
REQUEST_TIMEOUT_DEFAULT = 60.0
REQUEST_TIMEOUT_SHORT = 15.0

_GITHUB_REPO_FETCH_CACHE = reliability_core.TTLCache()
_CODEBERG_REPO_FETCH_CACHE = reliability_core.TTLCache()
_TANGLED_REPO_FETCH_CACHE = reliability_core.TTLCache()


class _HrefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.hrefs.append(str(href))


def github_headers() -> dict[str, str]:
    token = os.getenv("GITHUB_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def fetch_github_repo(
    repo_url: str,
    *,
    request_metrics: github_api.GitHubRequestMetrics | None = None,
) -> dict[str, Any]:
    cached = _GITHUB_REPO_FETCH_CACHE.get(repo_url)
    if isinstance(cached, dict):
        if request_metrics is not None:
            request_metrics.record_cache_hit()
        return copy.deepcopy(cached)
    slug = repo_url_core.extract_github_slug(repo_url)
    if slug is None:
        raise ValueError(f"not a GitHub repo URL: {repo_url}")
    headers = github_headers()
    payload = github_api.request_json(
        f"{GITHUB_API}/repos/{slug}", headers=headers, metrics=request_metrics
    )
    readme_text = ""
    try:
        readme_payload = github_api.request_json(
            f"{GITHUB_API}/repos/{slug}/readme", headers=headers, metrics=request_metrics
        )
        content = readme_payload.get("content", "")
        if content:
            readme_text = base64.b64decode(content).decode("utf-8", errors="replace")
    except github_api.GitHubRateLimitError:
        raise
    except Exception:
        readme_text = ""
    repo_tree: list[dict[str, Any]] = []
    revision = ""
    tree_revision = ""
    default_branch = str(payload.get("default_branch") or "")
    if default_branch:
        try:
            branch_ref = urllib.parse.quote(default_branch, safe="")
            branch_payload = github_api.request_json(
                f"{GITHUB_API}/repos/{slug}/branches/{branch_ref}",
                headers=headers,
                metrics=request_metrics,
            )
            branch_commit = branch_payload.get("commit") if isinstance(branch_payload.get("commit"), dict) else {}
            revision = str(branch_commit.get("sha") or "")
            tree_ref = urllib.parse.quote(revision or default_branch, safe="")
            tree_payload = github_api.request_json(
                f"{GITHUB_API}/repos/{slug}/git/trees/{tree_ref}?recursive=1",
                headers=headers,
                metrics=request_metrics,
            )
            tree_revision = str(tree_payload.get("sha") or "")
            revision = revision or tree_revision
            tree = tree_payload.get("tree", [])
            if isinstance(tree, list):
                repo_tree = [dict(item) for item in tree if isinstance(item, dict)]
        except github_api.GitHubRateLimitError:
            raise
        except Exception:
            repo_tree = []
    result = {
        "repo": payload,
        "readme_text": readme_text,
        "tree": repo_tree,
        "default_branch": default_branch,
        "revision": revision,
        "tree_revision": tree_revision,
    }
    _GITHUB_REPO_FETCH_CACHE.set(repo_url, copy.deepcopy(result), ttl=300)
    return result


def codeberg_repo_segments(repo_url: str) -> tuple[str, str]:
    normalized = repo_url_core.normalize_forge_repo_url(repo_url)
    parsed = urllib.parse.urlparse(normalized or "")
    segments = [segment for segment in parsed.path.split("/") if segment]
    if parsed.netloc != "codeberg.org" or len(segments) != 2:
        raise ValueError(f"not a Codeberg repo URL: {repo_url}")
    return segments[0], segments[1]


def codeberg_repo_file_text(repo_url: str, path: str, *, ref: str = "") -> str:
    try:
        owner, repo = codeberg_repo_segments(repo_url)
    except ValueError:
        return ""
    normalized_path = str(path or "").strip().lstrip("/")
    if not normalized_path:
        return ""
    encoded_path = "/".join(urllib.parse.quote(segment, safe="") for segment in normalized_path.split("/"))
    raw_url = (
        f"https://codeberg.org/api/v1/repos/{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(repo, safe='')}/raw/{urllib.parse.quote(ref or 'main', safe='')}/{encoded_path}"
    )
    try:
        status, body, headers = http_transport.request_bytes(raw_url, timeout=REQUEST_TIMEOUT_SHORT)
        return decode_http_text(body, headers) if status < 400 else ""
    except Exception:
        return ""


def fetch_codeberg_repo(repo_url: str) -> dict[str, Any]:
    normalized = repo_url_core.normalize_forge_repo_url(repo_url)
    if not normalized or repo_url_core.infer_repo_provider(normalized) != "codeberg.org":
        raise ValueError(f"not a Codeberg repo URL: {repo_url}")
    cached = _CODEBERG_REPO_FETCH_CACHE.get(normalized)
    if isinstance(cached, dict):
        return copy.deepcopy(cached)
    owner, repo = codeberg_repo_segments(normalized)
    api_root = f"https://codeberg.org/api/v1/repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(repo, safe='')}"
    payload = http_transport.request_json(api_root, timeout=REQUEST_TIMEOUT_DEFAULT)
    default_branch = str(payload.get("default_branch") or "main")
    tree: list[dict[str, Any]] = []
    revision = ""
    tree_revision = ""
    branch_payload: dict[str, Any] = {}
    readme_text = ""
    with ThreadPoolExecutor(max_workers=2) as executor:
        branch_future = executor.submit(
            http_transport.request_json,
            f"{api_root}/branches/{urllib.parse.quote(default_branch, safe='')}",
            timeout=REQUEST_TIMEOUT_DEFAULT,
        )
        readme_future = executor.submit(codeberg_repo_file_text, normalized, "README.md", ref=default_branch)
        try:
            branch_result = branch_future.result()
            branch_payload = dict(branch_result) if isinstance(branch_result, dict) else {}
        except Exception:
            branch_payload = {}
        try:
            readme_text = str(readme_future.result() or "")
        except Exception:
            readme_text = ""
    try:
        branch_commit = branch_payload.get("commit") if isinstance(branch_payload.get("commit"), dict) else {}
        revision = str(branch_commit.get("id") or branch_commit.get("sha") or "")
        tree_payload = http_transport.request_json(
            f"{api_root}/git/trees/{urllib.parse.quote(revision or default_branch, safe='')}?recursive=true",
            timeout=REQUEST_TIMEOUT_DEFAULT,
        )
        tree_revision = str(tree_payload.get("sha") or "")
        revision = revision or tree_revision
        if isinstance(tree_payload.get("tree"), list):
            tree = [dict(item) for item in tree_payload["tree"] if isinstance(item, dict)]
    except Exception:
        tree = []
    for path in (() if readme_text else ("README", "readme.md")):
        readme_text = codeberg_repo_file_text(normalized, path, ref=default_branch)
        if readme_text:
            break
    result = {
        "repo": payload,
        "readme_text": readme_text,
        "tree": tree,
        "default_branch": default_branch,
        "revision": revision,
        "tree_revision": tree_revision,
    }
    _CODEBERG_REPO_FETCH_CACHE.set(normalized, copy.deepcopy(result), ttl=300)
    return result


def decode_http_text(body: bytes, headers: dict[str, str]) -> str:
    charset = "utf-8"
    content_type = headers.get("content-type", "")
    charset_match = re.search(r"charset=([A-Za-z0-9_-]+)", content_type)
    if charset_match:
        charset = charset_match.group(1)
    return body.decode(charset, errors="replace")


def tangled_repo_segments(repo_url: str) -> tuple[str, str]:
    normalized = repo_url_core.normalize_tangled_repo_url(repo_url)
    if not normalized:
        raise ValueError(f"not a Tangled repo URL: {repo_url}")
    parsed = urllib.parse.urlparse(normalized)
    segments = [urllib.parse.unquote(segment) for segment in parsed.path.split("/") if segment]
    if len(segments) < 2:
        raise ValueError(f"not a Tangled repo URL: {repo_url}")
    return segments[0], segments[1]


def extract_tangled_default_branch(html: str) -> str:
    title_match = re.search(r"<title>.*?\sat\s+([^<·]+)\s+&middot;\s+Tangled</title>", html, flags=re.I | re.S)
    if title_match:
        branch = unescape(title_match.group(1)).strip()
        if branch:
            return branch
    href_match = re.search(r"/(?:[^/]+)/(?:[^/]+)/(?:tree|blob)/([^/\"?#]+)", html)
    if href_match:
        return urllib.parse.unquote(href_match.group(1))
    return "main"


def extract_tangled_repo_description(html: str) -> str:
    patterns = [
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
        r'<meta[^>]+name=["\']twitter:description["\'][^>]+content=["\'](.*?)["\']',
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if match:
            return unescape(re.sub(r"\s+", " ", match.group(1)).strip())
    return ""


def extract_tangled_repo_tree(repo_url: str, html: str) -> list[dict[str, Any]]:
    owner, repo = tangled_repo_segments(repo_url)
    parser = _HrefParser()
    try:
        parser.feed(html)
    except Exception:
        return []
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    base = repo_url_core.normalize_tangled_repo_url(repo_url) or repo_url
    for href in parser.hrefs:
        parsed = urllib.parse.urlparse(urllib.parse.urljoin(base.rstrip("/") + "/", href))
        segments = [urllib.parse.unquote(segment) for segment in parsed.path.split("/") if segment]
        if len(segments) < 5 or segments[0] != owner or segments[1] != repo:
            continue
        kind = segments[2]
        if kind not in {"blob", "tree"}:
            continue
        path = "/".join(segments[4:]).strip("/")
        item_type = "blob" if kind == "blob" else "tree"
        if not path or (item_type, path) in seen:
            continue
        seen.add((item_type, path))
        output.append({"path": path, "type": item_type, "size": 0})
    return output


def tangled_repo_file_text(repo_url: str, path: str, *, ref: str = "main") -> str:
    normalized = repo_url_core.normalize_tangled_repo_url(repo_url)
    if not normalized:
        return ""
    owner, repo = tangled_repo_segments(normalized)
    normalized_path = str(path or "").strip().lstrip("/")
    if not normalized_path:
        return ""
    encoded_path = "/".join(urllib.parse.quote(segment, safe="") for segment in normalized_path.split("/"))
    raw_url = (
        f"https://tangled.org/{urllib.parse.quote(owner, safe='._:-')}/"
        f"{urllib.parse.quote(repo, safe='._-')}/raw/{urllib.parse.quote(ref or 'main', safe='')}/{encoded_path}"
    )
    try:
        _status, body, headers = http_transport.request_bytes(raw_url, timeout=REQUEST_TIMEOUT_SHORT)
        return decode_http_text(body, headers)
    except Exception:
        return ""


def fetch_tangled_repo(repo_url: str) -> dict[str, Any]:
    normalized = repo_url_core.normalize_tangled_repo_url(repo_url)
    if not normalized:
        raise ValueError(f"not a Tangled repo URL: {repo_url}")
    cached = _TANGLED_REPO_FETCH_CACHE.get(normalized)
    if isinstance(cached, dict):
        return copy.deepcopy(cached)
    status, body, headers, final_url = http_transport.request_bytes_with_final_url(
        normalized, timeout=REQUEST_TIMEOUT_DEFAULT
    )
    html = decode_http_text(body, headers) if status < 400 else ""
    owner, repo = tangled_repo_segments(normalized)
    default_branch = extract_tangled_default_branch(html)
    tree = extract_tangled_repo_tree(normalized, html)
    readme_candidates = [
        str(item.get("path") or "")
        for item in tree
        if str(item.get("path") or "").lower().rsplit("/", 1)[-1] in {"readme.md", "readme"}
    ]
    readme_candidates.extend(["README.md", "README", "readme.md"])
    readme_text = ""
    for path in dict.fromkeys(path for path in readme_candidates if path):
        readme_text = tangled_repo_file_text(normalized, path, ref=default_branch)
        if readme_text:
            break
    result = {
        "repo": {
            "full_name": f"{owner}/{repo}",
            "name": repo,
            "owner": owner,
            "description": extract_tangled_repo_description(html),
            "html_url": normalized,
            "final_url": final_url,
        },
        "readme_text": readme_text,
        "tree": tree,
        "default_branch": default_branch,
    }
    _TANGLED_REPO_FETCH_CACHE.set(normalized, copy.deepcopy(result), ttl=300)
    return result


def github_repo_file_text(
    repo_url: str,
    path: str,
    *,
    ref: str = "",
    request_metrics: github_api.GitHubRequestMetrics | None = None,
) -> str:
    slug = repo_url_core.extract_github_slug(repo_url)
    if slug is None:
        return ""
    normalized_path = str(path or "").strip().lstrip("/")
    if not normalized_path:
        return ""
    query = f"?{urllib.parse.urlencode({'ref': ref})}" if ref else ""
    encoded_path = "/".join(urllib.parse.quote(segment, safe="") for segment in normalized_path.split("/"))
    payload = github_api.request_json(
        f"{GITHUB_API}/repos/{slug}/contents/{encoded_path}{query}",
        headers=github_headers(),
        metrics=request_metrics,
    )
    content = str(payload.get("content") or "")
    if not content:
        return ""
    try:
        return base64.b64decode(content).decode("utf-8", errors="replace")
    except Exception:
        return ""
