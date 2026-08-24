from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable


ATPROTO_CONTRIBUTION_LEVELS = {"unknown", "auth_only", "optional", "substantial", "core"}


PARKED_DOMAIN_MARKERS = (
    "domain is for sale",
    "this domain is for sale",
    "buy this domain",
    "domain may be for sale",
    "parked free",
    "parkingcrew",
    "sedo domain parking",
    "afternic",
    "dan.com",
    "undeveloped",
)


def unique_strings(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def compute_atproto_contribution(project: dict[str, Any]) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []

    def count_value(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def add(kind: str, values: list[Any]) -> None:
        cleaned = unique_strings([str(item).strip() for item in values if str(item).strip()])
        if cleaned:
            evidence.append({"kind": kind, "count": len(cleaned), "values": cleaned[:5]})

    resolved_nsids = list(project.get("resolved_nsids", []))
    schema_samples = list(project.get("lexicon_schema_samples", []))
    recent_records = list(project.get("recent_record_samples", []))
    collection_observations = [
        item
        for item in project.get("collection_observations", [])
        if isinstance(item, dict) and (count_value(item.get("count")) > 0 or count_value(item.get("recent_count")) > 0)
    ]
    input_nsids = list(project.get("input_nsids", []))
    external_lexicons = list(project.get("external_lexicons", []))
    relation_predicates: list[str] = []
    confirmed_repos = {str(item).rstrip("/") for item in project.get("project_repository_urls", [])}
    guarded_nsids = {
        str(item.get("value") or "")
        for field in ("blocked_identifiers", "deferred_identifiers")
        for item in project.get(field, [])
        if isinstance(item, dict) and item.get("type") == "nsid"
    }
    for item in project.get("relation_evidence", []):
        if not isinstance(item, dict):
            continue
        relation = item.get("relation") or item.get("value") or {}
        if not isinstance(relation, dict) or not relation.get("predicate"):
            continue
        predicate = str(relation["predicate"])
        subject = relation.get("subject") or {}
        obj = relation.get("object") or {}
        if predicate == "defines_schema" and (
            str(obj.get("value") or "") in guarded_nsids
            or (subject.get("type") == "repo" and str(subject.get("value") or "").rstrip("/") not in confirmed_repos)
        ):
            predicate = "mentions_collection"
        relation_predicates.append(predicate)

    add("resolved_nsids", resolved_nsids)
    if schema_samples:
        evidence.append({"kind": "lexicon_schema_samples", "count": len(schema_samples)})
    if recent_records:
        evidence.append({"kind": "recent_record_samples", "count": len(recent_records)})
    if collection_observations:
        evidence.append(
            {
                "kind": "collection_observations",
                "count": len(collection_observations),
                "record_count": sum(count_value(item.get("count")) for item in collection_observations),
                "recent_record_count": sum(count_value(item.get("recent_count")) for item in collection_observations),
            }
        )
    add("input_nsids", input_nsids)
    add("external_lexicons", external_lexicons)
    add("relation_predicates", relation_predicates)

    if resolved_nsids or schema_samples or "defines_schema" in relation_predicates:
        level, score = "core", 90
    elif recent_records or collection_observations or "uses_collection" in relation_predicates:
        level, score = "substantial", 70
    elif bool(project.get("atproto_auth_only")) and str(project.get("category") or "") in {"project", "personal_tool"}:
        level, score = "auth_only", 20
        evidence.append({"kind": "atproto_auth_only", "count": 1})
    elif input_nsids or external_lexicons or "requests_permission" in relation_predicates or "mentions_collection" in relation_predicates:
        level, score = "optional", 40
    else:
        level, score = "unknown", 0

    return {
        "atproto_contribution_level": level,
        "atproto_contribution_score": score,
        "atproto_contribution_evidence": evidence,
    }


@dataclass(frozen=True)
class OwnershipDependencies:
    normalize_nsid: Callable[[str], str]
    manual_seed_values: Callable[[dict[str, Any], str], list[str]]
    lexicon_namespace_for_project: Callable[[str], str]
    derive_authority_domain: Callable[[str], str]
    developer_controls_authority_domain: Callable[[dict[str, Any], str], bool]
    developer_holds_schema_record: Callable[[dict[str, Any], str], bool]
    ownership_anchor_service_hosts: Callable[[dict[str, Any]], set[str]]
    nsid_is_builtin_reference: Callable[[str], bool]
    project_identity_anchor_tokens: Callable[[dict[str, Any]], set[str]]
    normalize_alias_token: Callable[[str], str]
    root_domain: Callable[[str], str]


def project_owned_lexicon_nsid(
    project: dict[str, Any],
    nsid: str,
    dependencies: OwnershipDependencies,
) -> bool:
    normalized = dependencies.normalize_nsid(nsid)
    if not normalized:
        return False

    manual_nsid_seeds = set(dependencies.manual_seed_values(project, "nsid"))
    if normalized in manual_nsid_seeds:
        return True

    namespace = dependencies.lexicon_namespace_for_project(normalized)
    manual_namespaces = {
        dependencies.lexicon_namespace_for_project(seed)
        for seed in manual_nsid_seeds
        if seed
    }
    if any(
        namespace == item or namespace.startswith(f"{item}.") or item.startswith(f"{namespace}.")
        for item in manual_namespaces
    ):
        return True

    authority_domain = dependencies.derive_authority_domain(normalized)
    if authority_domain and dependencies.developer_controls_authority_domain(project, authority_domain):
        return True

    if dependencies.developer_holds_schema_record(project, normalized):
        return True

    if authority_domain:
        for host in dependencies.ownership_anchor_service_hosts(project):
            if authority_domain == host or authority_domain.endswith(f".{host}") or host.endswith(f".{authority_domain}"):
                return True

    if dependencies.nsid_is_builtin_reference(normalized):
        return False

    identity_tokens = dependencies.project_identity_anchor_tokens(project)
    namespace_tokens = {
        dependencies.normalize_alias_token(normalized),
        dependencies.normalize_alias_token(dependencies.lexicon_namespace_for_project(normalized)),
    }
    if authority_domain:
        namespace_tokens.add(dependencies.normalize_alias_token(authority_domain))
        namespace_tokens.add(dependencies.normalize_alias_token(dependencies.root_domain(authority_domain)))
        namespace_tokens.update(
            dependencies.normalize_alias_token(segment)
            for segment in authority_domain.split(".")
        )
    namespace_tokens = {token for token in namespace_tokens if token}
    if identity_tokens and namespace_tokens & identity_tokens:
        return True

    if manual_nsid_seeds or dependencies.ownership_anchor_service_hosts(project) or identity_tokens:
        return False
    return True


def infer_service_status(status_code: int, text: str) -> str:
    lowered = text.lower()
    if "domain is for sale" in lowered or "this domain is for sale" in lowered:
        return "dead"
    if status_code in {404, 410}:
        return "dead"
    if "invite only" in lowered or "waitlist" in lowered:
        return "invite_only"
    if "private beta" in lowered or "closed beta" in lowered:
        return "beta"
    if "coming soon" in lowered or "under construction" in lowered:
        return "unreleased"
    if status_code in {401, 403}:
        return "private"
    if status_code >= 500:
        return "unverified"
    if "beta" in lowered:
        return "beta"
    if status_code and status_code < 400:
        return "unverified"
    return "unknown"


def merge_service_status(current: str, new_value: str, normalize: Callable[[str | None], str]) -> str:
    rank = {
        "unknown": 0,
        "unverified": 1,
        "none": 2,
        "dead": 3,
        "live": 4,
        "private": 5,
        "unreleased": 6,
        "beta": 7,
        "invite_only": 8,
    }
    current_value = normalize(current)
    candidate_value = normalize(new_value)
    return candidate_value if rank.get(candidate_value, 0) >= rank.get(current_value, 0) else current_value


def merge_service_status_confidence(
    current: str,
    new_value: str,
    normalize: Callable[[str | None], str],
) -> str:
    rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
    current_value = normalize(current)
    candidate_value = normalize(new_value)
    return candidate_value if rank.get(candidate_value, 0) >= rank.get(current_value, 0) else current_value


def service_probe_looks_parked(result: dict[str, Any]) -> bool:
    text = "\n".join(
        [
            str(result.get("title") or ""),
            str(result.get("description") or ""),
            str(result.get("html") or "")[:12000],
        ]
    ).lower()
    return any(marker in text for marker in PARKED_DOMAIN_MARKERS)


def classify_service_probe_result(
    *,
    status_code: int,
    title: str,
    description: str,
    html: str,
    links: list[dict[str, str]],
    fetch_error: str = "",
) -> tuple[str, str, list[str]]:
    lowered = "\n".join([title, description, html[:20000]]).lower()
    evidence: list[str] = []
    if status_code:
        evidence.append(f"http_{status_code}")
    if fetch_error:
        evidence.append(fetch_error)
    if service_probe_looks_parked({"title": title, "description": description, "html": html}):
        evidence.append("parked_domain_detected")
        return "dead", "high", unique_strings(evidence)
    if fetch_error:
        return "unverified", "medium", unique_strings(evidence)
    if status_code in {404, 410}:
        evidence.append("not_found")
        return "dead", "high", unique_strings(evidence)
    if status_code >= 500:
        evidence.append("server_error")
        return "unverified", "low", unique_strings(evidence)

    login_detected = any(
        marker in lowered
        for marker in ("sign in", "log in", "login", "ログイン", "sign up", "create account", "register")
    ) or ('type="password"' in lowered or "type='password'" in lowered)
    if login_detected:
        evidence.append("login_surface_detected")
    invite_detected = any(marker in lowered for marker in ("invite only", "waitlist", "join waitlist", "request invite"))
    if invite_detected:
        evidence.append("invite_text_detected")
    beta_detected = any(marker in lowered for marker in ("private beta", "closed beta", "public beta", "beta"))
    if beta_detected:
        evidence.append("beta_text_detected")
    coming_soon_detected = any(marker in lowered for marker in ("coming soon", "under construction", "launching soon"))
    if coming_soon_detected:
        evidence.append("coming_soon_detected")
    docs_only_detected = False
    if links and not login_detected:
        paths = [urllib.parse.urlparse(str(item.get("url") or "")).path.lower() for item in links if str(item.get("url") or "")]
        docs_like = [path for path in paths if any(token in path for token in ("/docs", "/doc", "/guide", "/blog", "/about", "/faq", "/help"))]
        app_like = [path for path in paths if any(token in path for token in ("/app", "/login", "/signin", "/signup", "/register", "/download"))]
        if docs_like and not app_like:
            docs_only_detected = True
            evidence.append("docs_only_surface_detected")
    app_shell_detected = any(
        marker in lowered
        for marker in ("open app", "launch app", "download on the app store", "get it on google play", "custom feed", "timeline", "for you")
    )
    if app_shell_detected:
        evidence.append("app_shell_detected")

    if invite_detected:
        return "invite_only", "high" if login_detected else "medium", unique_strings(evidence)
    if beta_detected:
        return "beta", "medium", unique_strings(evidence)
    if coming_soon_detected and (login_detected or app_shell_detected):
        evidence.append("mixed_launch_signals_detected")
        return "unverified", "medium", unique_strings(evidence)
    if coming_soon_detected:
        return "unreleased", "medium", unique_strings(evidence)
    if status_code in {401, 403}:
        return "private", "low", unique_strings(evidence)
    if login_detected or app_shell_detected:
        return "live", "medium", unique_strings(evidence)
    if docs_only_detected:
        return "none", "medium", unique_strings(evidence)
    if status_code and status_code < 400:
        return "unverified", "low", unique_strings(evidence)
    return infer_service_status(status_code, lowered), "none", unique_strings(evidence)
