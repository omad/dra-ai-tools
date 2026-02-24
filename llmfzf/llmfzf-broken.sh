#!/usr/bin/env bash
set -euo pipefail
THIS_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
CACHE_JSON="${TMPDIR:-/tmp}/llmfzf-cache-$$.json"
RAW_JSON="${TMPDIR:-/tmp}/llmfzf-raw-$$.json"
cleanup() {
  rm -f "$CACHE_JSON" "$RAW_JSON"
}
trap cleanup EXIT
preview_cmd() {
  local cache="$1"
  local cid="$2"
  python3 - "$cache" "$cid" <<'PY'
import json
import sys
from datetime import datetime, timezone
cache_path, cid = sys.argv[1], sys.argv[2]
with open(cache_path, "r", encoding="utf-8") as f:
    conversations = json.load(f)
conv = next((c for c in conversations if c["conversation_id"] == cid), None)
if not conv:
    print("Conversation not found.")
    sys.exit(0)
def parse_dt(value):
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
entries = sorted(conv["entries"], key=lambda e: parse_dt(e.get("datetime_utc", "")), reverse=True)
print(f"Model: {conv['model']}")
print(f"Latest: {conv['latest_datetime']}")
print(f"Conversation ID: {conv['conversation_id']}")
print(f"Turns: {len(entries)}")
print("=" * 80)
for i, e in enumerate(entries, start=1):
    print(f"\n### Turn {i} ({e.get('datetime_utc', 'unknown time')})")
    print("\n#### Prompt\n")
    print((e.get("prompt") or "").strip() or "_(empty prompt)_")
    print("\n#### Response\n")
    print((e.get("response") or "").strip() or "_(empty response)_")
PY
}
copy_latest_response_cmd() {
  local cache="$1"
  local cid="$2"
  python3 - "$cache" "$cid" <<'PY' | pbcopy
import json
import sys
from datetime import datetime, timezone
cache_path, cid = sys.argv[1], sys.argv[2]
with open(cache_path, "r", encoding="utf-8") as f:
    conversations = json.load(f)
conv = next((c for c in conversations if c["conversation_id"] == cid), None)
if not conv:
    sys.exit(0)
def parse_dt(value):
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
entries = sorted(conv["entries"], key=lambda e: parse_dt(e.get("datetime_utc", "")), reverse=True)
latest = entries[0] if entries else {}
print((latest.get("response") or "").strip())
PY
}
obsidian_cmd() {
  local cache="$1"
  local cid="$2"
  python3 - "$cache" "$cid" <<'PY'
import json
import sys
import urllib.parse
from datetime import datetime, timezone
cache_path, cid = sys.argv[1], sys.argv[2]
with open(cache_path, "r", encoding="utf-8") as f:
    conversations = json.load(f)
conv = next((c for c in conversations if c["conversation_id"] == cid), None)
if not conv:
    sys.exit(0)
def parse_dt(value):
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
entries = sorted(conv["entries"], key=lambda e: parse_dt(e.get("datetime_utc", "")), reverse=True)
title = (conv.get("conversation_name") or "LLM Conversation").strip()
if not title:
    title = "LLM Conversation"
safe_title = "".join(ch for ch in title if ch.isalnum() or ch in " -_").strip()
safe_title = safe_title[:80] or "LLM Conversation"
frontmatter = [
    "---",
    "tags:",
    "  - llm",
    "  - llmfzf",
    f"date: {conv.get('latest_datetime', '')}",
    f"model: {conv.get('model', '')}",
    f"conversation_id: {conv.get('conversation_id', '')}",
    "tool: llm",
    f"turns: {len(entries)}",
    "---",
    "",
]
body = []
for i, e in enumerate(entries, start=1):
    body.append(f"## Turn {i} - {e.get('datetime_utc', 'unknown time')}")
    body.append("")
    body.append("### Prompt")
    body.append("")
    body.append((e.get("prompt") or "").strip() or "_(empty prompt)_")
    body.append("")
    body.append("### Response")
    body.append("")
    body.append((e.get("response") or "").strip() or "_(empty response)_")
    body.append("")
content = "\n".join(frontmatter + [f"# {safe_title}", ""] + body).rstrip() + "\n"
print(content)
encoded_title = urllib.parse.quote(f"LLM {conv.get('latest_datetime', '')[:10]} {safe_title}")
encoded_content = urllib.parse.quote(content)
print(f"obsidian://new?name={encoded_title}&content={encoded_content}", file=sys.stderr)
PY
}
build_cache_from_raw_file() {
  local cache="$1"
  local raw="$2"
  python3 - "$cache" "$raw" <<'PY'
import json
import sys
from datetime import datetime, timezone
cache_path = sys.argv[1]
raw_path = sys.argv[2]
with open(raw_path, "r", encoding="utf-8") as rf:
    logs = json.load(rf)
def parse_dt(value):
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
groups = {}
for item in logs:
    cid = item.get("conversation_id") or item.get("id") or "unknown"
    groups.setdefault(cid, []).append(item)
conversations = []
for cid, entries in groups.items():
    entries = sorted(entries, key=lambda e: parse_dt(e.get("datetime_utc", "")))
    latest = entries[-1] if entries else {}
    latest_dt = latest.get("datetime_utc", "")
    latest_prompt = (latest.get("prompt") or "").replace("\n", " ").replace("\t", " ").strip()
    if len(latest_prompt) > 130:
        latest_prompt = latest_prompt[:127] + "..."
    model = latest.get("model") or latest.get("conversation_model") or "unknown"
    name = latest.get("conversation_name") or latest_prompt or cid
    conversations.append({
        "conversation_id": cid,
        "conversation_short_id": cid[:8],
        "latest_datetime": latest_dt,
        "latest_display": (latest_dt[:16].replace("T", " ") if latest_dt else ""),
        "model": model,
        "conversation_name": name,
        "turn_count": len(entries),
        "prompt_preview": latest_prompt,
        "entries": entries,
    })
conversations.sort(key=lambda c: parse_dt(c.get("latest_datetime", "")), reverse=True)
with open(cache_path, "w", encoding="utf-8") as f:
    json.dump(conversations, f)
PY
}
list_rows_from_cache() {
  local cache="$1"
  python3 - "$cache" <<'PY'
import json
import sys
with open(sys.argv[1], "r", encoding="utf-8") as f:
    conversations = json.load(f)
print("DATE\tMODEL\tN\tCID\tPROMPT\tRAW_CID")
for c in conversations:
    row = [
        c.get("latest_display", ""),
        c.get("model", ""),
        str(c.get("turn_count", 0)),
        c.get("conversation_short_id", ""),
        c.get("prompt_preview", ""),
        c.get("conversation_id", ""),
    ]
    print("\t".join(part.replace("\n", " ").replace("\t", " ") for part in row))
PY
}
if [[ "${1:-}" == "__preview" ]]; then
  preview_cmd "$2" "$3"
  exit 0
fi
if [[ "${1:-}" == "__copy_latest_response" ]]; then
  copy_latest_response_cmd "$2" "$3"
  exit 0
fi
if [[ "${1:-}" == "__obsidian" ]]; then
  obsidian_url_file="$(mktemp)"
  obsidian_cmd "$2" "$3" > >(pbcopy >/dev/null) 2>"$obsidian_url_file"
  obsidian_url="$(cat "$obsidian_url_file")"
  rm -f "$obsidian_url_file"
  open "$obsidian_url" >/dev/null 2>&1 || true
  exit 0
fi
llm logs list --count 0 --json > "$RAW_JSON"
build_cache_from_raw_file "$CACHE_JSON" "$RAW_JSON"
list_rows_from_cache "$CACHE_JSON" \
| fzf --delimiter=$'\t' \
      --with-nth='1,2,3,4,5' \
      --accept-nth='{6}' \
      --header-lines=1 \
      --preview "$THIS_SCRIPT __preview '$CACHE_JSON' {6}" \
      --preview-window='right,70%,wrap' \
      --header $'Ctrl-Y: Copy latest response  |  Ctrl-O: Copy whole conversation + send to Obsidian' \
      --bind "ctrl-y:execute-silent($THIS_SCRIPT __copy_latest_response '$CACHE_JSON' {6})" \
      --bind "ctrl-o:execute-silent($THIS_SCRIPT __obsidian '$CACHE_JSON' {6})"