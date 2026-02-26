# colours-lsp Walkthrough

This is a linear walkthrough of the codebase in execution order.

## 1. Process entrypoint (`main.go`)

The program starts in `main()`:

```go
func main() {
	s := newServer(os.Stdout)
	if err := s.serve(os.Stdin); err != nil {
		log.Fatal(err)
	}
}
```

What this does:
- Creates a server instance that writes LSP responses to `stdout`.
- Starts the LSP loop reading from `stdin`.
- Exits the process with an error if the loop fails.

This is the expected stdio model for editor-launched language servers.

## 2. Core wire/message types (`server.go`)

The first section defines JSON payload shapes for LSP messages:
- `requestMessage` and `notificationMessage` for inbound messages.
- `responseMessage` and `responseError` for outbound replies.
- Request-specific params/results (`initialize*`, `didOpen*`, `didChange*`, `documentColor*`).
- LSP value objects (`lspRange`, `position`, `lspColor`, `colorInformation`).

Key detail:
- `rawID` preserves JSON-RPC id as raw JSON so ids can remain numeric or string without lossy conversion.

## 3. Server state model

`server` is intentionally small:

```go
type server struct {
	out               io.Writer
	doc               *document
	shutdownRequested bool
	exitRequested     bool
}
```

Behavior implied by this model:
- Exactly one in-memory active document (`doc`).
- No workspace-wide index, no multi-file tracking.
- Lifecycle flags for `shutdown` and `exit`.

This aligns with your “single file at a time” constraint.

## 4. Construction (`newServer`)

`newServer(out io.Writer)` injects output destination and returns a ready server.

No global variables are used; all mutable state remains in `server`.

## 5. Main protocol loop (`serve`)

`serve(in io.Reader)` is the runtime engine.

Linear flow:
1. Read one framed LSP message via `readLSPMessage`.
2. JSON-decode the envelope (`method`, optional `id`, optional `params`).
3. If `id` exists, treat as request:
   - Dispatch to `handleRequest`.
   - Build response with either `result` or `error`.
   - Write framed response via `writeLSPMessage`.
4. If `id` is absent, treat as notification:
   - Dispatch to `handleNotification`.
   - If notification was `exit`, stop loop.

Important protocol behavior:
- EOF is treated as clean shutdown.
- Any decode/framing error stops the server immediately.

## 6. Request handling (`handleRequest`)

Supported requests:

### `initialize`
Returns capabilities:

```go
initializeResult{Capabilities: serverCapabilities{TextDocumentSync: 1, ColorProvider: true}}
```

Meaning:
- Full document sync (`1` = `TextDocumentSyncKind.Full`).
- `documentColor` is supported.

### `shutdown`
- Sets `shutdownRequested = true`.
- Returns `{}` as result.

### `textDocument/documentColor`
Flow:
1. Decode `documentColorParams`.
2. Validate active document exists and URI matches request URI.
3. Compute colors with `documentColors(s.doc.text)`.
4. Return JSON array of `ColorInformation`.

Failure path:
- If no matching active doc, returns `document not found`.

### Default
- Unknown methods return `method not found: <method>`.

## 7. Notification handling (`handleNotification`)

Supported notifications:

### `initialized`
No-op (accepted for protocol compatibility).

### `textDocument/didOpen`
- Parses full text document payload.
- Replaces server state with that document.

### `textDocument/didChange`
- Requires an existing active doc with same URI.
- Requires at least one content change.
- Uses the last change’s `Text` as full replacement text.
- Updates document version.

Because this server advertises full sync, replacing entire text is correct baseline behavior.

### `textDocument/didClose`
- Clears active document if URI matches.

### `exit`
- Sets `exitRequested = true` so `serve` loop terminates.

### Default
- Unknown notifications are ignored.

## 8. Color extraction pipeline

### 8.1 Scanner (`findHexColors`)
This function scans raw text bytes for `#` and attempts longest-first matching:
- First checks `#rrggbb` (7 chars including `#`).
- Then checks `#rgb` (4 chars including `#`).
- Requires all color digits to be hex (`0-9a-fA-F`).
- Requires the following character (if present) to not be a hex digit.

This avoids partial matching inside longer hex-like tokens.

Each match returns:
- `Hex` raw matched literal
- `StartOffset` and `EndOffset` byte offsets in the document

### 8.2 Conversion (`hexToColor`)
Normalization logic:
- Removes `#`.
- Expands 3-digit format (e.g. `abc` -> `aabbcc`).
- Decodes exactly 6 hex digits to 3 bytes.
- Converts bytes to LSP float channels in range `[0, 1]`:
  - `Red = byte / 255.0`
  - `Green = byte / 255.0`
  - `Blue = byte / 255.0`
  - `Alpha = 1`

### 8.3 Range mapping (`offsetToPosition`)
Converts byte offset to LSP `(line, character)` by linear scan:
- Increments line on `\n`.
- Resets column after newline.
- Otherwise increments column per byte.

This implementation assumes ASCII-like content for exact byte-to-character mapping. For UTF-8 multibyte code points, `character` is byte-based rather than codepoint/UTF-16 based.

### 8.4 Final assembly (`documentColors`)
Pipeline:
1. `findHexColors(text)` -> match list.
2. For each match, `hexToColor`.
3. Compute start/end `position` from offsets.
4. Emit `colorInformation{Range, Color}`.

Returned value is exactly what `textDocument/documentColor` expects.

## 9. Framing/parsing LSP transport

### 9.1 Reader (`readLSPMessage`)
Reads one message from byte stream:
1. Reads one byte at a time until `\r\n\r\n` header terminator is found.
2. Parses headers and extracts `Content-Length`.
3. Reads exactly `Content-Length` bytes for JSON payload.

Validation:
- Missing `Content-Length` -> error.
- Invalid integer length -> error.
- Short body read -> error.

### 9.2 Writer (`writeLSPMessage`)
Serializes response JSON then writes:
1. `Content-Length: <n>\r\n\r\n`
2. Raw JSON body bytes.

This is standard JSON-RPC over stdio framing.

## 10. Utility helpers

- `mustMarshal`: panics on marshal failure (safe in this constrained code path where response structures are known-valid).
- `rawID.MarshalJSON`: preserves original request id bytes during response encoding.

## 11. End-to-end behavior summary

Given a normal editor session:
1. Editor starts `colours-lsp` process.
2. Sends `initialize` request.
3. Sends `initialized` notification.
4. Sends `didOpen` with full document text.
5. Sends `documentColor` request.
6. Server returns all `#rgb` / `#rrggbb` colors with ranges + normalized RGBA.
7. On edits, editor sends `didChange` full text; next `documentColor` reflects updated content.
8. On shutdown, editor sends `shutdown` then `exit`; server terminates.

## 12. Current limits and implications

Intentional limits in this version:
- Single active document only.
- No incremental range-based text edits.
- No UTF-16 position mapping.
- Unknown request methods currently map to generic method-not-found error.

These are acceptable for a first, focused implementation that provides `documentColor` correctly for simple hex formats.

## 13. About showboat execution in this environment

I attempted to follow your exact request (`uvx showboat --help`, then use `showboat note/exec`) but this sandbox blocks outbound network/DNS and `showboat` is not installed locally, so `uvx` cannot fetch it.

When run on a machine with network access, this is the command sequence to reproduce this walkthrough with showboat:

```bash
uvx showboat --help
showboat note "Walkthrough start"
showboat exec -- cat main.go
showboat exec -- sed -n '1,260p' server.go
showboat exec -- sed -n '261,520p' server.go
```

Then append the narrative sections above into `walkthrough.md`.
