package main

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strconv"
	"strings"
)

type rawID json.RawMessage

type requestMessage struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      rawID           `json:"id"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params,omitempty"`
}

type notificationMessage struct {
	JSONRPC string          `json:"jsonrpc"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params,omitempty"`
}

type responseMessage struct {
	JSONRPC string         `json:"jsonrpc"`
	ID      rawID          `json:"id"`
	Result  any            `json:"result,omitempty"`
	Error   *responseError `json:"error,omitempty"`
}

type responseError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

type initializeParams struct{}

type initializeResult struct {
	Capabilities serverCapabilities `json:"capabilities"`
}

type serverCapabilities struct {
	TextDocumentSync int  `json:"textDocumentSync,omitempty"`
	ColorProvider    bool `json:"colorProvider,omitempty"`
}

type textDocumentIdentifier struct {
	URI string `json:"uri"`
}

type versionedTextDocumentIdentifier struct {
	URI     string `json:"uri"`
	Version int    `json:"version"`
}

type textDocumentItem struct {
	URI        string `json:"uri"`
	LanguageID string `json:"languageId"`
	Version    int    `json:"version"`
	Text       string `json:"text"`
}

type didOpenTextDocumentParams struct {
	TextDocument textDocumentItem `json:"textDocument"`
}

type textDocumentContentChangeEvent struct {
	Text string `json:"text"`
}

type didChangeTextDocumentParams struct {
	TextDocument   versionedTextDocumentIdentifier  `json:"textDocument"`
	ContentChanges []textDocumentContentChangeEvent `json:"contentChanges"`
}

type didCloseTextDocumentParams struct {
	TextDocument textDocumentIdentifier `json:"textDocument"`
}

type documentColorParams struct {
	TextDocument textDocumentIdentifier `json:"textDocument"`
}

type colorInformation struct {
	Range lspRange `json:"range"`
	Color lspColor `json:"color"`
}

type lspRange struct {
	Start position `json:"start"`
	End   position `json:"end"`
}

type position struct {
	Line      int `json:"line"`
	Character int `json:"character"`
}

type lspColor struct {
	Red   float64 `json:"red"`
	Green float64 `json:"green"`
	Blue  float64 `json:"blue"`
	Alpha float64 `json:"alpha"`
}

type document struct {
	uri     string
	version int
	text    string
}

type server struct {
	out               io.Writer
	doc               *document
	shutdownRequested bool
	exitRequested     bool
}

func newServer(out io.Writer) *server {
	return &server{out: out}
}

func (s *server) serve(in io.Reader) error {
	for {
		msgBytes, err := readLSPMessage(in)
		if err != nil {
			if errors.Is(err, io.EOF) {
				return nil
			}
			return err
		}

		var envelope struct {
			Method string          `json:"method"`
			ID     json.RawMessage `json:"id"`
			Params json.RawMessage `json:"params"`
		}
		if err := json.Unmarshal(msgBytes, &envelope); err != nil {
			return err
		}

		if len(envelope.ID) > 0 {
			req := requestMessage{ID: rawID(envelope.ID), Method: envelope.Method, Params: envelope.Params}
			result, reqErr := s.handleRequest(req)
			resp := responseMessage{JSONRPC: "2.0", ID: req.ID}
			if reqErr != nil {
				resp.Error = &responseError{Code: -32601, Message: reqErr.Error()}
			} else {
				resp.Result = json.RawMessage(result)
			}
			if err := writeLSPMessage(s.out, resp); err != nil {
				return err
			}
			continue
		}

		note := notificationMessage{Method: envelope.Method, Params: envelope.Params}
		if err := s.handleNotification(note); err != nil {
			return err
		}
		if s.exitRequested {
			return nil
		}
	}
}

func (s *server) handleRequest(req requestMessage) (json.RawMessage, error) {
	switch req.Method {
	case "initialize":
		res := initializeResult{Capabilities: serverCapabilities{TextDocumentSync: 1, ColorProvider: true}}
		return mustMarshal(res), nil
	case "shutdown":
		s.shutdownRequested = true
		return mustMarshal(map[string]any{}), nil
	case "textDocument/documentColor":
		var params documentColorParams
		if err := json.Unmarshal(req.Params, &params); err != nil {
			return nil, fmt.Errorf("invalid documentColor params: %w", err)
		}
		if s.doc == nil || s.doc.uri != params.TextDocument.URI {
			return nil, errors.New("document not found")
		}
		infos := documentColors(s.doc.text)
		return mustMarshal(infos), nil
	default:
		return nil, fmt.Errorf("method not found: %s", req.Method)
	}
}

func (s *server) handleNotification(note notificationMessage) error {
	switch note.Method {
	case "initialized":
		return nil
	case "textDocument/didOpen":
		var p didOpenTextDocumentParams
		if err := json.Unmarshal(note.Params, &p); err != nil {
			return fmt.Errorf("invalid didOpen params: %w", err)
		}
		s.doc = &document{uri: p.TextDocument.URI, version: p.TextDocument.Version, text: p.TextDocument.Text}
		return nil
	case "textDocument/didChange":
		var p didChangeTextDocumentParams
		if err := json.Unmarshal(note.Params, &p); err != nil {
			return fmt.Errorf("invalid didChange params: %w", err)
		}
		if s.doc == nil || s.doc.uri != p.TextDocument.URI {
			return errors.New("document not found")
		}
		if len(p.ContentChanges) == 0 {
			return errors.New("no content changes provided")
		}
		s.doc.version = p.TextDocument.Version
		s.doc.text = p.ContentChanges[len(p.ContentChanges)-1].Text
		return nil
	case "textDocument/didClose":
		var p didCloseTextDocumentParams
		if err := json.Unmarshal(note.Params, &p); err != nil {
			return fmt.Errorf("invalid didClose params: %w", err)
		}
		if s.doc != nil && s.doc.uri == p.TextDocument.URI {
			s.doc = nil
		}
		return nil
	case "exit":
		s.exitRequested = true
		return nil
	default:
		return nil
	}
}

type hexMatch struct {
	Hex         string
	StartOffset int
	EndOffset   int
}

func findHexColors(text string) []hexMatch {
	out := make([]hexMatch, 0)
	for i := 0; i < len(text); i++ {
		if text[i] != '#' {
			continue
		}

		if i+7 <= len(text) && isHexRun(text[i+1:i+7]) && (i+7 == len(text) || !isHexByte(text[i+7])) {
			out = append(out, hexMatch{Hex: text[i : i+7], StartOffset: i, EndOffset: i + 7})
			i += 6
			continue
		}
		if i+4 <= len(text) && isHexRun(text[i+1:i+4]) && (i+4 == len(text) || !isHexByte(text[i+4])) {
			out = append(out, hexMatch{Hex: text[i : i+4], StartOffset: i, EndOffset: i + 4})
			i += 3
		}
	}
	return out
}

func documentColors(text string) []colorInformation {
	matches := findHexColors(text)
	out := make([]colorInformation, 0, len(matches))
	for _, m := range matches {
		col, ok := hexToColor(m.Hex)
		if !ok {
			continue
		}
		start := offsetToPosition(text, m.StartOffset)
		end := offsetToPosition(text, m.EndOffset)
		out = append(out, colorInformation{Range: lspRange{Start: start, End: end}, Color: col})
	}
	return out
}

func hexToColor(s string) (lspColor, bool) {
	s = strings.TrimPrefix(s, "#")
	if len(s) == 3 {
		s = strings.Repeat(string(s[0]), 2) + strings.Repeat(string(s[1]), 2) + strings.Repeat(string(s[2]), 2)
	}
	if len(s) != 6 {
		return lspColor{}, false
	}
	b, err := hex.DecodeString(s)
	if err != nil || len(b) != 3 {
		return lspColor{}, false
	}
	return lspColor{Red: float64(b[0]) / 255.0, Green: float64(b[1]) / 255.0, Blue: float64(b[2]) / 255.0, Alpha: 1}, true
}

func offsetToPosition(text string, off int) position {
	if off < 0 {
		off = 0
	}
	if off > len(text) {
		off = len(text)
	}
	line := 0
	col := 0
	for i := 0; i < off; i++ {
		if text[i] == '\n' {
			line++
			col = 0
			continue
		}
		col++
	}
	return position{Line: line, Character: col}
}

func isHexRun(s string) bool {
	for i := 0; i < len(s); i++ {
		if !isHexByte(s[i]) {
			return false
		}
	}
	return true
}

func isHexByte(b byte) bool {
	return (b >= '0' && b <= '9') || (b >= 'a' && b <= 'f') || (b >= 'A' && b <= 'F')
}

func readLSPMessage(r io.Reader) ([]byte, error) {
	var header bytes.Buffer
	window := make([]byte, 0, 4)
	one := make([]byte, 1)
	contentLength := -1
	for {
		_, err := r.Read(one)
		if err != nil {
			if errors.Is(err, io.EOF) && header.Len() == 0 {
				return nil, io.EOF
			}
			return nil, err
		}
		b := one[0]
		header.WriteByte(b)
		window = append(window, b)
		if len(window) > 4 {
			window = window[1:]
		}
		if len(window) == 4 && bytes.Equal(window, []byte("\r\n\r\n")) {
			break
		}
	}

	for _, line := range strings.Split(header.String(), "\r\n") {
		if line == "" {
			continue
		}
		parts := strings.SplitN(line, ":", 2)
		if len(parts) == 2 && strings.EqualFold(strings.TrimSpace(parts[0]), "content-length") {
			n, err := strconv.Atoi(strings.TrimSpace(parts[1]))
			if err != nil {
				return nil, fmt.Errorf("invalid content-length: %w", err)
			}
			contentLength = n
		}
	}
	if contentLength < 0 {
		return nil, errors.New("missing content-length")
	}
	body := make([]byte, contentLength)
	if _, err := io.ReadFull(r, body); err != nil {
		return nil, err
	}
	return body, nil
}

func writeLSPMessage(w io.Writer, msg any) error {
	payload, err := json.Marshal(msg)
	if err != nil {
		return err
	}
	headers := []byte(fmt.Sprintf("Content-Length: %d\r\n\r\n", len(payload)))
	if _, err := w.Write(headers); err != nil {
		return err
	}
	_, err = w.Write(payload)
	return err
}

func mustMarshal(v any) json.RawMessage {
	b, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return b
}

func (id rawID) MarshalJSON() ([]byte, error) {
	if len(id) == 0 {
		return []byte("null"), nil
	}
	return bytes.Clone(id), nil
}
