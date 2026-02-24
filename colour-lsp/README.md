# colours-lsp

A minimal single-document Language Server Protocol (LSP) server written in Go.

Current feature set:
- `textDocument/documentColor`
- Detects hex colors in the active document matching exactly `#xxx` or `#xxxxxx`

## Install

Prerequisites:
- Go 1.22+

Build from source and install the binary as `colours-lsp`:

```bash
go build -o colours-lsp .
install -m 0755 colours-lsp ~/.local/bin/colours-lsp
```

Make sure `~/.local/bin` is on your `PATH`.

Verify:

```bash
colours-lsp -h || true
```

Note: this server communicates over stdio and does not expose CLI flags yet.

## Run manually

The server is stdio-based:

```bash
colours-lsp
```

Your editor should launch it directly as an LSP command.

## Helix configuration

Add the following to your Helix `languages.toml` (usually `~/.config/helix/languages.toml`):

```toml
[language-server.colours-lsp]
command = "colours-lsp"

[[language]]
name = "css"
language-servers = ["colours-lsp"]

[[language]]
name = "scss"
language-servers = ["colours-lsp"]

[[language]]
name = "html"
language-servers = ["colours-lsp"]
```

You can attach it to other languages by adding more `[[language]]` entries.

## Development

Run tests:

```bash
go test ./...
```
