# Helix GitHub Link Script

This repository contains a small shell script for Helix that builds a GitHub URL for the current file and selection.

It uses:
- `%{current_working_directory}` for the editor cwd
- `%{buffer_name}` for the current file path
- `%{cursor_line}`
- `%{selection_line_start}`
- `%{selection_line_end}`

The script does this:
1. `cd` to the Helix cwd
2. `cd` again to the file's directory
3. run `gh browse <filename> --no-browser`
4. append one of:
- `?plain=1#L23` for a single line
- `?plain=1#L23-L27` for a selection

If no selection exists, Helix reports the same start/end line and the script uses a single-line suffix.

## Requirements

- `gh` CLI authenticated for the target repository
- One clipboard tool for copy mode (`pbcopy`, `wl-copy`, `xclip`, `xsel`, or `clip.exe`) if you want auto-copy

## Script

- `./hx-gh-link.sh <cwd> <buffer_name> <cursor_line> <selection_start> <selection_end> [copy|open|print|osc52]`

Default mode is `copy`. The script always prints the final URL to stdout.
If no clipboard utility is found in `copy` mode, it emits an OSC52 sequence.

## Helix Configuration

Put something like this into your Helix `config.toml` (update the script path if needed):

```toml
[keys.normal]
# Copy URL for current line/selection to clipboard and echo URL.
space.g.y = ":sh /workspace/helix-open-on-gh/hx-gh-link.sh \"%{current_working_directory}\" \"%{buffer_name}\" \"%{cursor_line}\" \"%{selection_line_start}\" \"%{selection_line_end}\" copy"

# Open URL in browser and echo URL.
space.g.o = ":sh /workspace/helix-open-on-gh/hx-gh-link.sh \"%{current_working_directory}\" \"%{buffer_name}\" \"%{cursor_line}\" \"%{selection_line_start}\" \"%{selection_line_end}\" open"
```

If you only want to print the URL in Helix without copy/open side effects, replace `copy`/`open` with `print`.
If you want explicit OSC52 behavior, use `osc52`.
