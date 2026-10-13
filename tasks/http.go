package main

import (
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"strings"
)

type createTaskRequest struct {
	Title string `json:"title"`
}

type errorResponse struct {
	Error string `json:"error"`
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, errorResponse{Error: msg})
}

type TaskHandler struct {
	svc *TaskService
}

func NewTaskHandler(svc *TaskService) *TaskHandler {
	return &TaskHandler{svc: svc}
}

func (h *TaskHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Path
	method := r.Method

	if path == "/health" {
		if method == http.MethodGet {
			writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
			return
		}
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}

	if path == "/tasks" || path == "/tasks/" {
		switch method {
		case http.MethodGet:
			tasks := h.svc.ListTasks()
			writeJSON(w, http.StatusOK, tasks)
			return
		case http.MethodPost:
			var req createTaskRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				writeError(w, http.StatusBadRequest, "invalid json")
				return
			}
			task, err := h.svc.CreateTask(req.Title)
			if err != nil {
				if errors.Is(err, ErrInvalidTask) {
					writeError(w, http.StatusBadRequest, "title is required")
					return
				}
				writeError(w, http.StatusBadRequest, err.Error())
				return
			}
			w.Header().Set("Location", "/tasks/"+itoa(task.ID))
			writeJSON(w, http.StatusCreated, task)
			return
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
	}

	if strings.HasPrefix(path, "/tasks/") {
		idStr := strings.TrimPrefix(path, "/tasks/")
		if idStr == "" || strings.Contains(idStr, "/") {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		id, err := strconv.Atoi(idStr)
		if err != nil || id <= 0 {
			writeError(w, http.StatusBadRequest, "invalid task id")
			return
		}
		switch method {
		case http.MethodGet:
			task, ok := h.svc.GetTask(id)
			if !ok {
				writeError(w, http.StatusNotFound, "task not found")
				return
			}
			writeJSON(w, http.StatusOK, task)
			return
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
	}

	w.WriteHeader(http.StatusNotFound)
	return
}

func itoa(i int) string {
	if i == 0 {
		return "0"
	}
	sign := ""
	if i < 0 {
		sign = "-"
		i = -i
	}
	buf := make([]byte, 0, 12)
	for i > 0 {
		buf = append(buf, byte('0'+i%10))
		i /= 10
	}
	for l, r := 0, len(buf)-1; l < r; l, r = l+1, r-1 {
		buf[l], buf[r] = buf[r], buf[l]
	}
	return sign + string(buf)
}
