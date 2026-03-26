# LSP Capability Inspector

CLI for interrogating LSP server capabilities over stdio or TCP, saving results to JSON, and rendering comparison reports in the terminal or HTML.

## Usage

Probe a server launched over stdio:

```bash
uv run lsp-cap-inspect probe -- pylsp
```

Probe a TCP server:

```bash
uv run lsp-cap-inspect probe --transport tcp --connect 127.0.0.1:2087
```

Launch a TCP server process and then connect:

```bash
uv run lsp-cap-inspect probe --transport tcp --connect 127.0.0.1:2087 -- some-server --port 2087
```

Generate a comparative terminal report:

```bash
uv run lsp-cap-inspect report
```

Generate HTML:

```bash
uv run lsp-cap-inspect report --format html --output report.html
```
