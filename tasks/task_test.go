package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestCreateTaskValidation(t *testing.T) {
	svc := NewTaskService()
	_, err := svc.CreateTask("")
	if err == nil {
		t.Fatal("expected error for empty title")
	}
	_, err = svc.CreateTask("   ")
	if err == nil {
		t.Fatal("expected error for whitespace-only title")
	}
	_, err = svc.CreateTask("\t\n")
	if err == nil {
		t.Fatal("expected error for whitespace-only title")
	}
	task, err := svc.CreateTask("  Buy milk  ")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if task.Title != "Buy milk" {
		t.Fatalf("title not trimmed: %q", task.Title)
	}
	if task.ID != 1 {
		t.Fatalf("expected ID 1, got %d", task.ID)
	}
}

func TestListTasks(t *testing.T) {
	svc := NewTaskService()
	if got := len(svc.ListTasks()); got != 0 {
		t.Fatalf("expected 0 tasks, got %d", got)
	}
	if _, err := svc.CreateTask("A"); err != nil {
		t.Fatal(err)
	}
	if _, err := svc.CreateTask("B"); err != nil {
		t.Fatal(err)
	}
	list := svc.ListTasks()
	if len(list) != 2 {
		t.Fatalf("expected 2 tasks, got %d", len(list))
	}
	// mutate returned copy should not affect internal state
	list[0].Title = "MODIFIED"
	list2 := svc.ListTasks()
	if list2[0].Title != "A" {
		t.Fatalf("expected copy not affected, got %q", list2[0].Title)
	}
}

func TestGetTask(t *testing.T) {
	svc := NewTaskService()
	if _, ok := svc.GetTask(1); ok {
		t.Fatal("expected not found on empty service")
	}
	created, err := svc.CreateTask("A")
	if err != nil {
		t.Fatal(err)
	}
	got, ok := svc.GetTask(created.ID)
	if !ok {
		t.Fatalf("expected task %d to be found", created.ID)
	}
	if got != created {
		t.Fatalf("expected %+v, got %+v", created, got)
	}
	if _, ok := svc.GetTask(999); ok {
		t.Fatal("expected not found for missing id")
	}
}

func TestGetTaskEndpoint(t *testing.T) {
	svc := NewTaskService()
	if _, err := svc.CreateTask("first"); err != nil {
		t.Fatal(err)
	}
	h := NewTaskHandler(svc)

	tests := []struct {
		name       string
		method     string
		path       string
		wantStatus int
	}{
		{name: "found", method: http.MethodGet, path: "/tasks/1", wantStatus: http.StatusOK},
		{name: "not found", method: http.MethodGet, path: "/tasks/999", wantStatus: http.StatusNotFound},
		{name: "invalid id non-numeric", method: http.MethodGet, path: "/tasks/abc", wantStatus: http.StatusBadRequest},
		{name: "invalid id zero", method: http.MethodGet, path: "/tasks/0", wantStatus: http.StatusBadRequest},
		{name: "invalid id negative", method: http.MethodGet, path: "/tasks/-1", wantStatus: http.StatusBadRequest},
		{name: "invalid id float", method: http.MethodGet, path: "/tasks/1.5", wantStatus: http.StatusBadRequest},
		{name: "extra segment", method: http.MethodGet, path: "/tasks/1/2", wantStatus: http.StatusNotFound},
		{name: "post on detail", method: http.MethodPost, path: "/tasks/1", wantStatus: http.StatusMethodNotAllowed},
		{name: "delete on detail", method: http.MethodDelete, path: "/tasks/1", wantStatus: http.StatusMethodNotAllowed},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req := httptest.NewRequest(tt.method, tt.path, nil)
			w := httptest.NewRecorder()
			h.ServeHTTP(w, req)
			if w.Code != tt.wantStatus {
				t.Fatalf("%s %s: expected %d, got %d", tt.method, tt.path, tt.wantStatus, w.Code)
			}
		})
	}
}

func TestGetTaskEndpointBody(t *testing.T) {
	svc := NewTaskService()
	created, err := svc.CreateTask("payload")
	if err != nil {
		t.Fatal(err)
	}
	h := NewTaskHandler(svc)

	req := httptest.NewRequest(http.MethodGet, "/tasks/1", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	if ct := w.Header().Get("Content-Type"); ct != "application/json" {
		t.Fatalf("expected application/json, got %q", ct)
	}
	var got Task
	if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
		t.Fatal(err)
	}
	if got != created {
		t.Fatalf("expected %+v, got %+v", created, got)
	}

	// not found should follow the JSON error convention
	req = httptest.NewRequest(http.MethodGet, "/tasks/999", nil)
	w = httptest.NewRecorder()
	h.ServeHTTP(w, req)
	var errResp errorResponse
	if err := json.Unmarshal(w.Body.Bytes(), &errResp); err != nil {
		t.Fatalf("error body is not JSON: %v", err)
	}
	if errResp.Error == "" {
		t.Fatal("expected non-empty error message")
	}
}

func TestHealthEndpoint(t *testing.T) {
	h := NewTaskHandler(NewTaskService())
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	var resp map[string]string
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if resp["status"] != "ok" {
		t.Fatalf("expected ok, got %q", resp["status"])
	}
}

func TestGetTasksEndpoint(t *testing.T) {
	h := NewTaskHandler(NewTaskService())
	req := httptest.NewRequest(http.MethodGet, "/tasks", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	if strings.TrimSpace(w.Body.String()) != "[]" {
		t.Fatalf("expected empty array, got %q", w.Body.String())
	}
}

func TestPostTasksEndpoint(t *testing.T) {
	h := NewTaskHandler(NewTaskService())

	req := httptest.NewRequest(http.MethodPost, "/tasks", bytes.NewBufferString(`{"title":""}`))
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}

	req = httptest.NewRequest(http.MethodPost, "/tasks", bytes.NewBufferString(`{"title":"   "}`))
	w = httptest.NewRecorder()
	h.ServeHTTP(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}

	req = httptest.NewRequest(http.MethodPost, "/tasks", bytes.NewBufferString(`{bad json`))
	w = httptest.NewRecorder()
	h.ServeHTTP(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}

	req = httptest.NewRequest(http.MethodPost, "/tasks", bytes.NewBufferString(`{"title":"Test"}`))
	w = httptest.NewRecorder()
	h.ServeHTTP(w, req)
	if w.Code != http.StatusCreated {
		t.Fatalf("expected 201, got %d", w.Code)
	}
	var created Task
	if err := json.Unmarshal(w.Body.Bytes(), &created); err != nil {
		t.Fatal(err)
	}
	if created.ID != 1 || created.Title != "Test" {
		t.Fatalf("unexpected created: %+v", created)
	}
	loc := w.Header().Get("Location")
	if loc != "/tasks/1" {
		t.Fatalf("expected Location /tasks/1, got %q", loc)
	}
}
