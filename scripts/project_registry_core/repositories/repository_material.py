from __future__ import annotations

import json
import tomllib
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from ..identity import identifiers as identifier_core
from ..transport import github_api
from .. import project_record
from .repository_sources import github_repo_file_text


ENRICH_IO_MAX_WORKERS = 4


def github_repo_candidate_lexicon_paths(tree: list[dict[str, Any]]) -> list[str]:
    scored: list[tuple[int, str]] = []
    for item in tree:
        if str(item.get("type") or "") != "blob":
            continue
        path = str(item.get("path") or "").strip()
        lowered = path.lower()
        if not path or not lowered.endswith(".json") or int(item.get("size") or 0) > 200_000:
            continue
        score = 0
        if "/lexicon/" in f"/{lowered}/" or "/lexicons/" in f"/{lowered}/":
            score += 5
        if "/schema/" in f"/{lowered}/" or "/schemas/" in f"/{lowered}/":
            score += 2
        if any(token in lowered for token in (".lexicon.json", "lexicon.", "/lexicon", "/lexicons")):
            score += 3
        basename = path.rsplit("/", 1)[-1]
        basename_nsid = basename[:-5] if basename.lower().endswith(".json") else ""
        if identifier_core.is_valid_nsid(basename_nsid):
            score += 2
        if score > 0:
            scored.append((score, path))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return project_record.unique_list([path for _, path in scored[:16]])


def repo_json_looks_like_lexicon(path: str, payload: dict[str, Any], nsid: str) -> bool:
    lowered = str(path or "").lower()
    nsid = identifier_core.normalize_nsid(nsid)
    if not identifier_core.is_valid_nsid(nsid):
        return False
    if isinstance(payload.get("lexicon"), int) and isinstance(payload.get("defs"), dict):
        return True
    if isinstance(payload.get("defs"), dict) and ("/lexicon" in lowered or "/lexicons" in lowered):
        return True
    basename = lowered.rsplit("/", 1)[-1]
    nsid_tail = nsid.split(".")[-1].lower()
    nsid_pathish = nsid.lower().replace(".", "/")
    return bool(
        basename.endswith(f"{nsid_tail}.json")
        or nsid.lower() in basename
        or nsid_pathish.replace("/", ".") in lowered.replace("/", ".")
    )


def load_repo_files(
    repo_url: str,
    paths: list[str],
    *,
    ref: str,
    file_text: Callable[..., str],
) -> dict[str, str]:
    ordered_paths = project_record.unique_list([str(path or "") for path in paths if str(path or "")])
    if not ordered_paths:
        return {}
    with ThreadPoolExecutor(max_workers=min(ENRICH_IO_MAX_WORKERS, len(ordered_paths))) as executor:
        futures = {path: executor.submit(file_text, repo_url, path, ref=ref) for path in ordered_paths}
        output: dict[str, str] = {}
        for path in ordered_paths:
            try:
                output[path] = str(futures[path].result() or "")
            except github_api.GitHubRateLimitError:
                raise
            except Exception:
                output[path] = ""
        return output


def repo_lexicon_hints(
    repo_url: str,
    *,
    ref: str,
    tree: list[dict[str, Any]],
    file_text: Callable[..., str] | None = None,
) -> list[dict[str, Any]]:
    load_file = file_text or github_repo_file_text
    hints: list[dict[str, Any]] = []
    seen: set[str] = set()
    paths = github_repo_candidate_lexicon_paths(tree)
    texts = load_repo_files(repo_url, paths, ref=ref, file_text=load_file)
    for path in paths:
        text = texts.get(path, "")
        if not text:
            continue
        try:
            payload = json.loads(text)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        nsid = identifier_core.normalize_nsid(str(payload.get("id") or ""))
        if not repo_json_looks_like_lexicon(path, payload, nsid) or nsid in seen:
            continue
        seen.add(nsid)
        hints.append({"nsid": nsid, "path": path, "schema": payload})
    return hints


def repo_manifest_hints(
    repo_url: str,
    *,
    ref: str,
    tree: list[dict[str, Any]],
    file_text: Callable[..., str] | None = None,
) -> list[dict[str, Any]]:
    load_file = file_text or github_repo_file_text
    manifest_names = {"package.json", "pyproject.toml", "cargo.toml"}
    paths = [
        str(item.get("path") or "")
        for item in tree
        if str(item.get("type") or "") == "blob"
        and str(item.get("path") or "").lower().rsplit("/", 1)[-1] in manifest_names
        and int(item.get("size") or 0) <= 200_000
    ]
    paths.sort(key=lambda path: (path.count("/"), path))
    hints: list[dict[str, Any]] = []
    texts = load_repo_files(repo_url, paths[:4], ref=ref, file_text=load_file)
    for path in paths[:4]:
        text = texts.get(path, "")
        basename = path.lower().rsplit("/", 1)[-1]
        if basename == "package.json":
            try:
                payload = json.loads(text)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            project_payload = payload
            dependency_fields = ("dependencies", "devDependencies", "peerDependencies")
            scripts = payload.get("scripts") if isinstance(payload.get("scripts"), dict) else {}
            bin_value = payload.get("bin")
            bin_names = (
                sorted(str(name) for name in bin_value)[:24]
                if isinstance(bin_value, dict)
                else ([str(payload.get("name") or "")] if isinstance(bin_value, str) else [])
            )
            import_pointer = next(
                (f"/{key}" for key in ("exports", "main", "module", "types", "typings") if payload.get(key)),
                "",
            )
            manifest_hint = {
                "path": path,
                "manifest_kind": "npm",
                "name": str(payload.get("name") or ""),
                "description": str(payload.get("description") or "")[:1000],
                "private": bool(payload.get("private")),
                "scripts": sorted(str(name) for name in scripts)[:24],
                "bin": bin_names,
                "bin_pointer": "/bin" if bin_names else "",
                "exports": bool(payload.get("exports")),
                "main": str(payload.get("main") or "")[:500],
                "module": str(payload.get("module") or "")[:500],
                "types": str(payload.get("types") or payload.get("typings") or "")[:500],
                "browser": bool(payload.get("browser")),
                "import_pointer": import_pointer,
            }
        else:
            try:
                payload = tomllib.loads(text)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            if basename == "pyproject.toml":
                project_payload = payload.get("project") if isinstance(payload.get("project"), dict) else {}
                tool = payload.get("tool") if isinstance(payload.get("tool"), dict) else {}
                poetry = tool.get("poetry") if isinstance(tool.get("poetry"), dict) else {}
                scripts = project_payload.get("scripts") if isinstance(project_payload.get("scripts"), dict) else {}
                bin_pointer = "/project/scripts"
                if not scripts and isinstance(poetry.get("scripts"), dict):
                    scripts = poetry["scripts"]
                    bin_pointer = "/tool/poetry/scripts"
                bin_names = sorted(str(name) for name in scripts)[:24]
                dependency_fields = ("dependencies",)
                manifest_hint = {
                    "path": path,
                    "manifest_kind": "python",
                    "name": str(project_payload.get("name") or poetry.get("name") or ""),
                    "description": str(project_payload.get("description") or poetry.get("description") or "")[:1000],
                    "private": False,
                    "scripts": bin_names,
                    "bin": bin_names,
                    "bin_pointer": bin_pointer if bin_names else "",
                }
            else:
                project_payload = payload.get("package") if isinstance(payload.get("package"), dict) else {}
                bins = payload.get("bin") if isinstance(payload.get("bin"), list) else []
                bin_names = [str(item.get("name") or "") for item in bins if isinstance(item, dict) and item.get("name")]
                library = payload.get("lib") if isinstance(payload.get("lib"), dict) else {}
                dependency_fields = ("dependencies", "dev-dependencies", "build-dependencies")
                manifest_hint = {
                    "path": path,
                    "manifest_kind": "cargo",
                    "name": str(project_payload.get("name") or ""),
                    "description": str(project_payload.get("description") or "")[:1000],
                    "private": bool(project_payload.get("publish") is False),
                    "scripts": [],
                    "bin": bin_names[:24],
                    "bin_pointer": "/bin" if bin_names else "",
                    "exports": bool(library),
                    "main": str(library.get("path") or "")[:500],
                    "import_pointer": "/lib" if library else "",
                }
        dependencies: dict[str, str] = {}
        dependency_sources = [payload, project_payload]
        if basename == "pyproject.toml":
            dependency_sources.append(poetry)
        for source in dependency_sources:
            for field_name in dependency_fields:
                values = source.get(field_name) if isinstance(source, dict) else None
                if isinstance(values, dict):
                    candidates = values.items()
                elif isinstance(values, list):
                    candidates = ((str(value).split(maxsplit=1)[0], value) for value in values)
                else:
                    continue
                dependencies.update({
                    str(name): str(version)
                    for name, version in candidates
                    if "atproto" in str(name).lower() or "bluesky" in str(name).lower()
                })
        manifest_hint["protocol_dependencies"] = dependencies
        hints.append(manifest_hint)
    return hints


def github_repo_configuration_paths(tree: list[dict[str, Any]]) -> list[str]:
    candidates: list[tuple[int, str]] = []
    exact_names = {
        "mkdocs.yml", "mkdocs.yaml", "docusaurus.config.js", "docusaurus.config.ts",
        "vite.config.js", "vite.config.ts", "astro.config.mjs", "astro.config.ts",
        "conf.py", "_config.yml", "_config.yaml", "cname", "manifest.json",
    }
    for item in tree:
        if str(item.get("type") or "") != "blob" or int(item.get("size") or 0) > 200_000:
            continue
        path = str(item.get("path") or "")
        lowered = path.lower()
        basename = lowered.rsplit("/", 1)[-1]
        score = 0
        is_root_or_shallow = lowered.count("/") <= 1
        is_extension_path = any(
            segment in f"/{lowered}/" for segment in ("/plugin/", "/plugins/", "/extension/", "/extensions/")
        )
        if basename in exact_names and basename != "manifest.json":
            score += 6
        if basename == "manifest.json" and (is_root_or_shallow or is_extension_path):
            score += 6
        if "/.vitepress/" in f"/{lowered}/":
            score += 2
        if lowered.startswith(".github/workflows/") and any(token in basename for token in ("docs", "pages")):
            score += 5
        if basename in {"dockerfile", "docker-compose.yml", "docker-compose.yaml", "fly.toml", "netlify.toml", "vercel.json"}:
            score += 4
        if score:
            candidates.append((score, path))
    candidates.sort(key=lambda item: (-item[0], item[1].count("/"), item[1]))
    return [path for _, path in candidates[:6]]


def repo_configuration_hints(
    repo_url: str,
    *,
    ref: str,
    tree: list[dict[str, Any]],
    file_text: Callable[..., str] | None = None,
) -> list[dict[str, Any]]:
    load_file = file_text or github_repo_file_text
    hints: list[dict[str, Any]] = []
    paths = github_repo_configuration_paths(tree)
    texts = load_repo_files(repo_url, paths, ref=ref, file_text=load_file)
    for path in paths:
        text = texts.get(path, "")
        if not text:
            continue
        lowered = path.lower()
        hint: dict[str, Any] = {"path": path, "content_excerpt": text[:4000]}
        if lowered.endswith("manifest.json"):
            try:
                payload = json.loads(text)
            except Exception:
                payload = {}
            if isinstance(payload, dict) and isinstance(payload.get("manifest_version"), int):
                hint["kind"] = "browser_extension_manifest"
                hint["manifest_version"] = int(payload["manifest_version"])
                hint["name"] = str(payload.get("name") or "")[:500]
        elif lowered.rsplit("/", 1)[-1] == "cname":
            hint["kind"] = "documentation_cname"
            hint["url_or_host"] = text.strip().splitlines()[0][:500] if text.strip() else ""
        elif any(token in lowered for token in ("mkdocs.", "docusaurus.config", ".vitepress/", "conf.py", "_config.", "astro.config")):
            hint["kind"] = "documentation_config"
        elif lowered.startswith(".github/workflows/") and any(token in lowered for token in ("docs", "pages")):
            hint["kind"] = "documentation_deployment"
        else:
            hint["kind"] = "deployment_config"
        hints.append(hint)
    return hints


def github_repo_semantic_paths(tree: list[dict[str, Any]]) -> list[str]:
    scored: list[tuple[int, str]] = []
    extensions = (".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs", ".swift", ".kt", ".java")
    for item in tree:
        if str(item.get("type") or "") != "blob":
            continue
        path = str(item.get("path") or "")
        lowered = path.lower()
        if not lowered.endswith(extensions) or int(item.get("size") or 0) > 120_000:
            continue
        score = 3 if any(segment in f"/{lowered}" for segment in ("/src/", "/app/", "/server/", "/packages/")) else 0
        if any(segment in f"/{lowered}" for segment in ("/vendor/", "/node_modules/", "/generated/")):
            score -= 10
        if any(segment in f"/{lowered}" for segment in ("/test/", "/tests/", "/fixtures/", "/examples/")):
            score -= 2
        scored.append((score, path))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [path for score, path in scored if score > -10][:4]


def repo_semantic_hints(
    repo_url: str,
    *,
    ref: str,
    tree: list[dict[str, Any]],
    file_text: Callable[..., str] | None = None,
) -> list[dict[str, Any]]:
    load_file = file_text or github_repo_file_text
    hints: list[dict[str, Any]] = []
    paths = github_repo_semantic_paths(tree)
    texts = load_repo_files(repo_url, paths, ref=ref, file_text=load_file)
    for path in paths:
        text = texts.get(path, "")
        if not text:
            continue
        per_file_count = 0
        for line_number, line in enumerate(text.splitlines(), start=1):
            nsids = identifier_core.extract_nsids_from_text(line)
            if not nsids:
                continue
            lowered = line.lower()
            write = any(token in lowered for token in ("createrecord", "putrecord", "deleterecord", "applywrites"))
            read = any(token in lowered for token in ("getrecord", "listrecords", "subscribe", "searchposts", "gettimeline"))
            operation = "write" if write else "read" if read else "mention"
            for nsid in nsids:
                if per_file_count >= 2:
                    break
                hints.append({
                    "nsid": nsid,
                    "path": path,
                    "line": line_number,
                    "predicate": "uses_collection" if operation in {"read", "write"} else "mentions_collection",
                    "operation": operation,
                    "excerpt": line.strip()[:1200],
                })
                per_file_count += 1
            if per_file_count >= 2:
                break
    return hints[:8]
