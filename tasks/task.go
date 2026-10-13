package main

import (
	"errors"
	"strings"
	"sync"
)

var (
	ErrInvalidTask = errors.New("invalid task: title is required")
)

type Task struct {
	ID    int    `json:"id"`
	Title string `json:"title"`
}

type TaskService struct {
	mu    sync.RWMutex
	tasks []Task
	next  int
}

func NewTaskService() *TaskService {
	return &TaskService{
		tasks: make([]Task, 0, 16),
		next:  1,
	}
}

func (s *TaskService) ListTasks() []Task {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]Task, len(s.tasks))
	copy(out, s.tasks)
	return out
}

func (s *TaskService) GetTask(id int) (Task, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, t := range s.tasks {
		if t.ID == id {
			return t, true
		}
	}
	return Task{}, false
}

func (s *TaskService) CreateTask(title string) (Task, error) {
	t := strings.TrimSpace(title)
	if t == "" {
		return Task{}, ErrInvalidTask
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	task := Task{ID: s.next, Title: t}
	s.next++
	s.tasks = append(s.tasks, task)
	return task, nil
}
