#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: hx-gh-link.sh <cwd> <buffer_name> <cursor_line> <selection_start> <selection_end> [copy|open|print|osc52]

Build a GitHub URL for the current file/selection using `gh browse` and append:
  ?plain=1#L<line>         for a single line
  ?plain=1#L<start>-L<end> for a multi-line selection
USAGE
}

if [[ $# -lt 5 || $# -gt 6 ]]; then
  usage >&2
  exit 2
fi

cwd=$1
buffer_name=$2
cursor_line=$3
sel_start=$4
sel_end=$5
mode=${6:-copy}

if [[ "$buffer_name" == "[scratch]" ]]; then
  echo "Cannot build a GitHub URL for scratch buffers." >&2
  exit 1
fi

if ! [[ "$cursor_line" =~ ^[0-9]+$ && "$sel_start" =~ ^[0-9]+$ && "$sel_end" =~ ^[0-9]+$ ]]; then
  echo "Line values must be positive integers." >&2
  exit 1
fi

if (( sel_start > sel_end )); then
  tmp=$sel_start
  sel_start=$sel_end
  sel_end=$tmp
fi

start_line=$sel_start
end_line=$sel_end

# When there is no selection, Helix reports start=end. Prefer cursor line in that case.
if (( start_line == end_line )); then
  start_line=$cursor_line
  end_line=$cursor_line
fi

file_dir=$(dirname -- "$buffer_name")
file_name=$(basename -- "$buffer_name")

cd -- "$cwd"
cd -- "$file_dir"

base_url=$(gh browse "$file_name" --no-browser)
base_url=${base_url%%$'\r'}

if (( start_line == end_line )); then
  fragment="?plain=1#L${start_line}"
else
  fragment="?plain=1#L${start_line}-L${end_line}"
fi

full_url="${base_url}${fragment}"

copy_to_clipboard() {
  if command -v pbcopy >/dev/null 2>&1; then
    printf '%s' "$full_url" | pbcopy
    return 0
  fi
  if command -v wl-copy >/dev/null 2>&1; then
    printf '%s' "$full_url" | wl-copy
    return 0
  fi
  if command -v xclip >/dev/null 2>&1; then
    printf '%s' "$full_url" | xclip -selection clipboard
    return 0
  fi
  if command -v xsel >/dev/null 2>&1; then
    printf '%s' "$full_url" | xsel --clipboard --input
    return 0
  fi
  if command -v clip.exe >/dev/null 2>&1; then
    printf '%s' "$full_url" | clip.exe
    return 0
  fi
  return 1
}

emit_osc52() {
  # OSC52 clipboard write for terminal sessions (including many remote setups).
  b64=$(printf '%s' "$full_url" | base64 | tr -d '\r\n')
  if [[ -n "${TMUX:-}" ]]; then
    # tmux passthrough wrapper
    printf '\033Ptmux;\033\033]52;c;%s\007\033\\' "$b64"
  else
    printf '\033]52;c;%s\007' "$b64"
  fi
}

open_url() {
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$full_url" >/dev/null 2>&1 &
    return 0
  fi
  if command -v open >/dev/null 2>&1; then
    open "$full_url" >/dev/null 2>&1 &
    return 0
  fi
  if command -v cmd.exe >/dev/null 2>&1; then
    cmd.exe /c start "" "$full_url" >/dev/null 2>&1
    return 0
  fi
  return 1
}

case "$mode" in
  copy)
    if ! copy_to_clipboard; then
      emit_osc52
    fi
    ;;
  osc52)
    emit_osc52
    ;;
  open)
    if ! open_url; then
      echo "Could not find a browser opener; URL printed below." >&2
    fi
    ;;
  print)
    ;;
  *)
    echo "Unknown mode: $mode" >&2
    usage >&2
    exit 2
    ;;
esac

printf '%s\n' "$full_url"
