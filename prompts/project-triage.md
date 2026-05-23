# Project Triage Prompt

local LLM に渡すための最小プロンプトです。

## System

You are classifying newly discovered AT Protocol projects.
Return JSON only.
Be conservative.
Do not invent facts that are not present in the evidence.
For human-readable text fields, write in Japanese.
Keep schema enums, URLs, handles, DIDs, and NSIDs unchanged.
Do not insert spaces between Japanese text and adjacent English words or digits.

## User Template

```text
Classify this candidate.

Goal:
- Keep independent atproto projects
- Keep infra tools separately
- Exclude libraries, auth-only services, and personal-only tools unless name-only tracking is useful

Output schema:
{
  "category": "project | lexicon | infra_tool | name_only | library | personal_tool | auth_only | reject",
  "status": "candidate | tracked | published | invite_only | beta | inactive | rejected",
  "name": "string",
  "tagline": "string",
  "tagline_is_provisional": true,
  "summary": "string",
  "features": ["string"],
  "inspired_by": ["string"],
  "source_code_urls": ["string"],
  "service_urls": ["string"],
  "service_status": "none | unknown | live | invite_only | beta | private | dead",
  "lexicons": ["string"],
  "why_keep": "string",
  "why_reject": "string",
  "watch_reason": "string",
  "manual_review_recommended": true,
  "manual_review_kind": "other | editorial_refresh | deep_source_material | service_status_manual_check | identity_resolution | category_recheck",
  "manual_review_priority": "none | low | medium | high",
  "manual_review_reason": "string",
  "confidence": 0.0
}

Language requirements:
- `name`: keep the original project name when evidence suggests one
- `tagline`: Japanese short noun phrase, ideally one compound word, no particles, max 30 characters
- `tagline_is_provisional`: true when the tagline is inferred or tentative rather than directly evidenced
- `summary`: Japanese
- `summary`: Japanese, and do not insert spaces between Japanese text and adjacent English words or digits
- `features`: Japanese short bullet-style strings
- `features`: Japanese short bullet-style strings, and do not insert spaces between Japanese text and adjacent English words or digits
- `inspired_by`: common service names may stay in their original language
- `why_keep`: Japanese
- `why_reject`: Japanese
- `watch_reason`: Japanese
- `manual_review_recommended`: true when a human should inspect rich docs, a usable service, or complicated evidence that the shallow automated pass may not fully digest
- `manual_review_kind`: choose `editorial_refresh` for weak tagline/summary/features, `deep_source_material` for rich docs/pages worth reading more deeply, `service_status_manual_check` for usable-looking but still uncertain services, `identity_resolution` for conflicting identities or ownership, `category_recheck` for likely category drift, `other` when none of those fit
- `manual_review_priority`: use `high` for likely high-value manual review, `medium` for useful but not urgent review, `low` for optional review, `none` when no manual review is recommended
- `manual_review_reason`: Japanese short explanation of what the human should inspect and why

Decision hints:
- If it mainly exposes a new service, app, or infra component on atproto, prefer keep
- If it mainly defines lexicons or schema conventions for third parties to implement, prefer lexicon
- If it is just a library, prefer library
- If it only uses atproto for sign-in and not data model / records, prefer auth_only
- If evidence suggests only the author uses it, prefer personal_tool
- If evidence is too thin but the name should be remembered, prefer name_only
- If it is PDS / relay / moderation / dashboard / hosting / indexing infra, prefer infra_tool

Evidence:
{{evidence_json}}
```
