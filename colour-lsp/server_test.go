package main

import (
	"bytes"
	"encoding/json"
	"io"
	"strings"
	"testing"
)

func TestFindHexColors(t *testing.T) {
	text := "a #fff b #123456 c #abcd #12 #12345 #xyz #ABCDEF"
	matches := findHexColors(text)

	if len(matches) != 3 {
		t.Fatalf("expected 3 matches, got %d", len(matches))
	}

	want := []string{"#fff", "#123456", "#ABCDEF"}
	for i, m := range matches {
		if m.Hex != want[i] {
			t.Fatalf("match %d: expected %q, got %q", i, want[i], m.Hex)
		}
	}
}

func TestDocumentColorFromDidOpenAndDidChange(t *testing.T) {
	s := newServer(io.Discard)
	uri := "file:///tmp/test.css"

	_, err := s.handleRequest(requestMessage{
		ID:     rawID(`1`),
		Method: "textDocument/documentColor",
		Params: mustJSON(t, documentColorParams{TextDocument: textDocumentIdentifier{URI: uri}}),
	})
	if err == nil || !strings.Contains(err.Error(), "document not found") {
		t.Fatalf("expected document not found error, got %v", err)
	}

	if err := s.handleNotification(notificationMessage{
		Method: "textDocument/didOpen",
		Params: mustJSON(t, didOpenTextDocumentParams{
			TextDocument: textDocumentItem{URI: uri, LanguageID: "plaintext", Version: 1, Text: "hello #fff world"},
		}),
	}); err != nil {
		t.Fatalf("didOpen failed: %v", err)
	}

	res, err := s.handleRequest(requestMessage{
		ID:     rawID(`2`),
		Method: "textDocument/documentColor",
		Params: mustJSON(t, documentColorParams{TextDocument: textDocumentIdentifier{URI: uri}}),
	})
	if err != nil {
		t.Fatalf("documentColor after open failed: %v", err)
	}

	var infos []colorInformation
	if err := json.Unmarshal(res, &infos); err != nil {
		t.Fatalf("unmarshal result failed: %v", err)
	}
	if len(infos) != 1 {
		t.Fatalf("expected 1 color, got %d", len(infos))
	}
	if infos[0].Color != (lspColor{Red: 1, Green: 1, Blue: 1, Alpha: 1}) {
		t.Fatalf("unexpected color: %+v", infos[0].Color)
	}

	if err := s.handleNotification(notificationMessage{
		Method: "textDocument/didChange",
		Params: mustJSON(t, didChangeTextDocumentParams{
			TextDocument:   versionedTextDocumentIdentifier{URI: uri, Version: 2},
			ContentChanges: []textDocumentContentChangeEvent{{Text: "#123456 and #abc"}},
		}),
	}); err != nil {
		t.Fatalf("didChange failed: %v", err)
	}

	res, err = s.handleRequest(requestMessage{
		ID:     rawID(`3`),
		Method: "textDocument/documentColor",
		Params: mustJSON(t, documentColorParams{TextDocument: textDocumentIdentifier{URI: uri}}),
	})
	if err != nil {
		t.Fatalf("documentColor after change failed: %v", err)
	}
	if err := json.Unmarshal(res, &infos); err != nil {
		t.Fatalf("unmarshal result failed: %v", err)
	}
	if len(infos) != 2 {
		t.Fatalf("expected 2 colors, got %d", len(infos))
	}
	if infos[0].Color != (lspColor{Red: 0x12 / 255.0, Green: 0x34 / 255.0, Blue: 0x56 / 255.0, Alpha: 1}) {
		t.Fatalf("unexpected first color: %+v", infos[0].Color)
	}
	if infos[1].Color != (lspColor{Red: 0xaa / 255.0, Green: 0xbb / 255.0, Blue: 0xcc / 255.0, Alpha: 1}) {
		t.Fatalf("unexpected second color: %+v", infos[1].Color)
	}
}

func TestInitializeAndShutdown(t *testing.T) {
	s := newServer(io.Discard)
	initRes, err := s.handleRequest(requestMessage{ID: rawID(`1`), Method: "initialize", Params: mustJSON(t, initializeParams{})})
	if err != nil {
		t.Fatalf("initialize failed: %v", err)
	}

	var out initializeResult
	if err := json.Unmarshal(initRes, &out); err != nil {
		t.Fatalf("unmarshal initialize result failed: %v", err)
	}

	if out.Capabilities.ColorProvider != true {
		t.Fatalf("expected color provider enabled")
	}
	if out.Capabilities.TextDocumentSync != 1 {
		t.Fatalf("expected full text sync (1), got %v", out.Capabilities.TextDocumentSync)
	}

	if _, err := s.handleRequest(requestMessage{ID: rawID(`2`), Method: "shutdown"}); err != nil {
		t.Fatalf("shutdown failed: %v", err)
	}
	if !s.shutdownRequested {
		t.Fatalf("expected shutdownRequested true")
	}
}

func TestServeProcessesLSPFrames(t *testing.T) {
	in := bytes.NewBuffer(nil)
	out := bytes.NewBuffer(nil)
	s := newServer(out)

	writeMessage(t, in, map[string]any{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": map[string]any{}})
	writeMessage(t, in, map[string]any{"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": map[string]any{
		"textDocument": map[string]any{"uri": "file:///tmp/a.txt", "languageId": "txt", "version": 1, "text": "#fff"},
	}})
	writeMessage(t, in, map[string]any{"jsonrpc": "2.0", "id": 2, "method": "textDocument/documentColor", "params": map[string]any{
		"textDocument": map[string]any{"uri": "file:///tmp/a.txt"},
	}})
	writeMessage(t, in, map[string]any{"jsonrpc": "2.0", "id": 3, "method": "shutdown"})
	writeMessage(t, in, map[string]any{"jsonrpc": "2.0", "method": "exit"})

	if err := s.serve(in); err != nil {
		t.Fatalf("serve failed: %v", err)
	}

	responses, err := readAllResponses(out.Bytes())
	if err != nil {
		t.Fatalf("parse responses failed: %v", err)
	}

	if len(responses) != 3 {
		t.Fatalf("expected 3 responses, got %d", len(responses))
	}

	if responses[0]["id"].(float64) != 1 {
		t.Fatalf("first response should be initialize")
	}
	if responses[1]["id"].(float64) != 2 {
		t.Fatalf("second response should be documentColor")
	}
	if responses[2]["id"].(float64) != 3 {
		t.Fatalf("third response should be shutdown")
	}
}

func mustJSON(t *testing.T, v any) json.RawMessage {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("json marshal failed: %v", err)
	}
	return b
}

func writeMessage(t *testing.T, w io.Writer, msg any) {
	t.Helper()
	b, err := json.Marshal(msg)
	if err != nil {
		t.Fatalf("marshal failed: %v", err)
	}
	head := "Content-Length: " + itoa(len(b)) + "\r\n\r\n"
	if _, err := io.WriteString(w, head); err != nil {
		t.Fatalf("write head failed: %v", err)
	}
	if _, err := w.Write(b); err != nil {
		t.Fatalf("write body failed: %v", err)
	}
}

func readAllResponses(stream []byte) ([]map[string]any, error) {
	buf := bytes.NewBuffer(stream)
	var out []map[string]any
	for buf.Len() > 0 {
		msg, err := readLSPMessage(buf)
		if err != nil {
			return nil, err
		}
		var parsed map[string]any
		if err := json.Unmarshal(msg, &parsed); err != nil {
			return nil, err
		}
		out = append(out, parsed)
	}
	return out, nil
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [20]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}
