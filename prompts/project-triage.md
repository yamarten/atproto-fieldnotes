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
