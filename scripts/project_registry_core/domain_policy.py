from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, Callable

import tldextract

from . import identifiers


ROOT = Path(__file__).resolve().parents[2]
DOMAIN_OVERRIDES_PATH = ROOT / "config" / "domain-overrides.json"
PUBLIC_SUFFIX_DATA_CANDIDATES = [ROOT / "vendor" / "public_suffix_list.dat"]
TLDEXTRACT_CACHE_DIR = ROOT / "data" / "tldextract-cache"
KNOWN_MULTI_TENANT_SUFFIXES = {
    "vercel.app",
    "microcosm.blue",
    "leaflet.pub",
    "pages.dev",
    "netlify.app",
    "web.app",
    "firebaseapp.com",
    "onrender.com",
    "fly.dev",
    "workers.dev",
    "github.io",
}
SPECIAL_USE_TLDS = {
    "test",
    "example",
    "invalid",
    "localhost",
    "local",
    "internal",
    "alt",
    "onion",
    "home",
    "corp",
    "lan",
}
_CONFIGURED_MULTI_TENANT_SUFFIXES: set[str] | None = None
_TLD_EXTRACTORS: dict[str, Any] = {}
_KNOWN_ICANN_TLDS: set[str] | None = None


def load_domain_overrides() -> dict[str, Any]:
    if not DOMAIN_OVERRIDES_PATH.exists():
        return {}
    return json.loads(DOMAIN_OVERRIDES_PATH.read_text())


def configured_multi_tenant_suffixes() -> set[str]:
    global _CONFIGURED_MULTI_TENANT_SUFFIXES
    if _CONFIGURED_MULTI_TENANT_SUFFIXES is not None:
        return _CONFIGURED_MULTI_TENANT_SUFFIXES
    configured: set[str] = set()
    try:
        config = load_domain_overrides()
    except Exception:
        config = {}
    values = config.get("custom_multi_tenant_suffixes", [])
    if isinstance(values, list):
        for item in values:
            suffix = str(item or "").strip().lower().rstrip(".")
            if suffix and "." in suffix:
                configured.add(suffix)
    _CONFIGURED_MULTI_TENANT_SUFFIXES = configured
    return configured


def reset_domain_extractors() -> None:
    global _TLD_EXTRACTORS
    _TLD_EXTRACTORS = {}


def reset_domain_config_cache() -> None:
    global _CONFIGURED_MULTI_TENANT_SUFFIXES
    _CONFIGURED_MULTI_TENANT_SUFFIXES = None
    reset_domain_extractors()


def normalize_multi_tenant_suffix(value: str) -> str:
    suffix = str(value or "").strip().lower().rstrip(".")
    if not suffix or "." not in suffix or not re.fullmatch(r"[a-z0-9.-]+", suffix):
        raise ValueError(f"invalid multi-tenant suffix: {value}")
    if any(not label for label in suffix.split(".")):
        raise ValueError(f"invalid multi-tenant suffix: {value}")
    return suffix


def add_configured_multi_tenant_suffix(suffix: str) -> bool:
    normalized = normalize_multi_tenant_suffix(suffix)
    config = load_domain_overrides()
    values = config.get("custom_multi_tenant_suffixes", [])
    if not isinstance(values, list):
        raise ValueError("custom_multi_tenant_suffixes must be a JSON array")
    existing = [normalize_multi_tenant_suffix(str(item)) for item in values]
    if normalized in existing:
        return False
    config["custom_multi_tenant_suffixes"] = existing + [normalized]
    DOMAIN_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOMAIN_OVERRIDES_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    reset_domain_config_cache()
    return True


def load_known_icann_tlds() -> set[str]:
    global _KNOWN_ICANN_TLDS
    if _KNOWN_ICANN_TLDS is not None:
        return _KNOWN_ICANN_TLDS
    known: set[str] = set()
    for path in PUBLIC_SUFFIX_DATA_CANDIDATES:
        if not path.exists():
            continue
        try:
            in_private = False
            for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("// ===BEGIN PRIVATE DOMAINS==="):
                    in_private = line.startswith("// ===BEGIN PRIVATE DOMAINS===")
                    continue
                if line.startswith("//"):
                    continue
                if in_private:
                    continue
                rule = line.lstrip("!.")
                label = rule.rsplit(".", 1)[-1].lower()
                if re.fullmatch(r"[a-z]{2,24}", label):
                    known.add(label)
            if known:
                break
        except Exception:
            continue
    _KNOWN_ICANN_TLDS = known
    return known


def tldextract_extractor(mode: str = "local") -> Any:
    if mode in _TLD_EXTRACTORS:
        return _TLD_EXTRACTORS[mode]
    local_sources = [
        path.resolve().as_uri()
        for path in PUBLIC_SUFFIX_DATA_CANDIDATES
        if path.exists()
    ]
    if not local_sources:
        raise RuntimeError("vendored public suffix list is missing")
    kwargs: dict[str, Any] = {
        "include_psl_private_domains": True,
        "extra_suffixes": sorted(configured_multi_tenant_suffixes()),
        "cache_dir": str(TLDEXTRACT_CACHE_DIR),
        "suffix_list_urls": tuple(local_sources),
        "fallback_to_snapshot": False,
    }
    TLDEXTRACT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    extractor = tldextract.TLDExtract(**kwargs)
    _TLD_EXTRACTORS[mode] = extractor
    return extractor


def extract_registrable_domain_with_tldextract(
    host: str,
    *,
    include_configured_suffixes: bool = True,
) -> str:
    lowered = str(host or "").strip().lower().rstrip(".")
    if not lowered:
        return ""
    result = tldextract_extractor("local")(lowered)
    return str(
        getattr(result, "top_domain_under_public_suffix", "") or ""
    ).strip().lower().rstrip(".")


def authority_domain_allowed_for_import(domain: str) -> bool:
    lowered = str(domain or "").strip().lower().rstrip(".")
    if not lowered or "." not in lowered:
        return False
    tld = lowered.rsplit(".", 1)[-1]
    if tld in SPECIAL_USE_TLDS:
        return False
    if not re.fullmatch(r"[a-z]{2,24}", tld):
        return False
    known_tlds = load_known_icann_tlds()
    if known_tlds and tld not in known_tlds:
        return False
    return True


def root_domain(host: str, *, include_configured_suffixes: bool = True) -> str:
    lowered = str(host or "").strip().lower().rstrip(".")
    if not lowered:
        return ""
    suffixes = configured_multi_tenant_suffixes() | KNOWN_MULTI_TENANT_SUFFIXES
    for suffix in sorted(suffixes, key=len, reverse=True):
        if lowered == suffix:
            return suffix
        if lowered.endswith(f".{suffix}"):
            labels = [label for label in lowered.split(".") if label]
            suffix_labels = [label for label in suffix.split(".") if label]
            if len(labels) > len(suffix_labels):
                return ".".join(labels[-(len(suffix_labels) + 1):])
            return lowered
    registrable = extract_registrable_domain_with_tldextract(
        lowered,
        include_configured_suffixes=include_configured_suffixes,
    )
    if registrable:
        return registrable
    labels = [label for label in lowered.split(".") if label]
    if len(labels) >= 2:
        return ".".join(labels[-2:])
    return lowered


def is_same_site_family(
    url_a: str,
    url_b: str,
    *,
    normalize_url: Callable[[str], str] | None = None,
) -> bool:
    normalizer = normalize_url or identifiers.normalize_url
    host_a = urllib.parse.urlparse(normalizer(url_a)).netloc
    host_b = urllib.parse.urlparse(normalizer(url_b)).netloc
    if not host_a or not host_b:
        return False
    root_a = root_domain(host_a)
    root_b = root_domain(host_b)
    return bool(root_a and root_b and root_a == root_b)
