# TASK-08 Evidence Log — GET /tasks/{id}

Task: 08 — End-to-End Coding Workflow Test
Tanggal: 2026-10-12
Workspace: `D:\AI\free-agentic-lab`
Shell: Windows PowerShell 5.1 · Go go1.26.5 windows/amd64 · Git (unborn branch)

Mengikuti `docs/AGENTIC-WORKFLOW.md` (prosedur + protokol bukti §8) dan temuan
`docs/WORKFLOW-COMPLIANCE-REVIEW.md`.

---

## 1. Rencana (sebelum eksekusi)

- **Diubah:**
  - `tasks/task.go` — tambah `TaskService.GetTask(id) (Task, bool)` (read-only, RLock).
  - `tasks/http.go` — routing detail `/tasks/{id}` di `ServeHTTP`.
  - `tasks/task_test.go` — test service + handler (table-driven) + test body JSON.
- **Dibuat:** `docs/evidence/TASK-08-EVIDENCE.md` (dokumen ini).
- **Tidak disentuh:** `tasks/main.go`, `go.mod`, `emailvalidator/*`,
  `docs/AGENTIC-WORKFLOW.md`, `docs/WORKFLOW-COMPLIANCE-REVIEW.md`,
  `docs/evidence/TASK-07-EVIDENCE.md`, konfigurasi, Git state.
- **Dilarang:** commit, push, deploy, hapus file, re-init Git, dependency baru.

**Keputusan desain (didokumentasikan):**
- ID tidak valid = non-integer (`abc`, `1.5`) atau `<= 0` (`0`, `-1`) → **400**.
- ID valid format tapi tidak ada di storage (`999`) → **404**.
- Path lebih dalam (`/tasks/1/2`) → **404** (bukan resource detail).
- Method != GET pada detail → **405** (tanpa body, konsisten dgn 405 existing).
- Error 400/404 memakai `writeError` (JSON `{"error": ...}`), konsisten dengan
  konvensi error JSON yang sudah ada.

---

## 2. Perintah yang benar-benar dijalankan (output verbatim)

### 2.1 Audit (sebelum implementasi)

```
$ git status --porcelain=v1 -uall
?? docs/AGENTIC-WORKFLOW.md
?? docs/WORKFLOW-COMPLIANCE-REVIEW.md
?? docs/evidence/TASK-07-EVIDENCE.md
?? emailvalidator/email.go
?? emailvalidator/email_test.go
?? go.mod
?? tasks/http.go
?? tasks/main.go
?? tasks/task.go
?? tasks/task_test.go
exit=0

$ git branch ; git log --oneline --all
(empty)   exit=0
$ git ls-files
(empty)   exit=0
```

### 2.2 Implementasi (gofmt, test, vet, build)

```
$ gofmt -w tasks/task.go tasks/http.go tasks/task_test.go
exit=0
$ gofmt -l tasks/task.go tasks/http.go tasks/task_test.go  -> 0 file

$ go test -count=1 ./...
ok  	github.com/free-agentic-lab/emailvalidator/emailvalidator	0.338s
ok  	github.com/free-agentic-lab/emailvalidator/tasks	0.183s
exit=0

$ go vet ./...
exit=0

$ go build ./...
exit=0
```

### 2.3 Test verbose (package tasks)

```
$ go test -count=1 -v ./tasks
=== RUN   TestCreateTaskValidation
--- PASS: TestCreateTaskValidation (0.00s)
=== RUN   TestListTasks
--- PASS: TestListTasks (0.00s)
=== RUN   TestGetTask
--- PASS: TestGetTask (0.00s)
=== RUN   TestGetTaskEndpoint
=== RUN   TestGetTaskEndpoint/found
=== RUN   TestGetTaskEndpoint/not_found
=== RUN   TestGetTaskEndpoint/invalid_id_non-numeric
=== RUN   TestGetTaskEndpoint/invalid_id_zero
=== RUN   TestGetTaskEndpoint/invalid_id_negative
=== RUN   TestGetTaskEndpoint/invalid_id_float
=== RUN   TestGetTaskEndpoint/extra_segment
=== RUN   TestGetTaskEndpoint/post_on_detail
=== RUN   TestGetTaskEndpoint/delete_on_detail
--- PASS: TestGetTaskEndpoint (0.00s)
    --- PASS: TestGetTaskEndpoint/found (0.00s)
    --- PASS: TestGetTaskEndpoint/not_found (0.00s)
    --- PASS: TestGetTaskEndpoint/invalid_id_non-numeric (0.00s)
    --- PASS: TestGetTaskEndpoint/invalid_id_zero (0.00s)
    --- PASS: TestGetTaskEndpoint/invalid_id_negative (0.00s)
    --- PASS: TestGetTaskEndpoint/invalid_id_float (0.00s)
    --- PASS: TestGetTaskEndpoint/extra_segment (0.00s)
    --- PASS: TestGetTaskEndpoint/post_on_detail (0.00s)
    --- PASS: TestGetTaskEndpoint/delete_on_detail (0.00s)
=== RUN   TestGetTaskEndpointBody
--- PASS: TestGetTaskEndpointBody (0.00s)
=== RUN   TestHealthEndpoint
--- PASS: TestHealthEndpoint (0.00s)
=== RUN   TestGetTasksEndpoint
--- PASS: TestGetTasksEndpoint (0.00s)
=== RUN   TestPostTasksEndpoint
--- PASS: TestPostTasksEndpoint (0.00s)
PASS
ok  	github.com/free-agentic-lab/emailvalidator/tasks	0.128s
exit=0
```

### 2.4 Race detector

```
$ go test -race -count=1 ./...
exit=2 -> go: -race requires cgo; enable cgo by setting CGO_ENABLED=1
$ CGO_ENABLED=1 go test -race -count=1 ./...
exit=1 -> runtime/cgo: cgo: C compiler "gcc" not found in %PATH% (build failed)
```

Race detector **tidak dapat dijalankan** karena lingkungan tidak memiliki gcc/cgo.
Ini **bukan** kegagalan tes; dicatat sebagai keterbatasan. Concurrency dijaga
secara inspeksi: `GetTask` memakai `RLock` seperti `ListTasks`.

### 2.5 Smoke test HTTP (port 18473)

```
$ go run ./tasks  (env ADDR=:18473)
GET  /health    -> 200 {"status":"ok"}
POST /tasks     -> 201 {"id":1,"title":"smoke"}
GET  /tasks/1   -> 200 {"id":1,"title":"smoke"}
GET  /tasks/999 -> 404
GET  /tasks/abc -> 400
POST /tasks/1   -> 405
GET  /tasks     -> 200 [{"id":1,"title":"smoke"}]
```

Verifikasi body error (port 18475, via ErrorDetails):
```
GET /tasks/999 -> 404 {"error":"task not found"}
GET /tasks/abc -> 400 {"error":"invalid task id"}
```

Proses server dimatikan (`Stop-Process`) dan semua log sementara di luar
workspace (`%TEMP%\opencode\t8*.{out,err}.log`) dibersihkan. Tidak ada port
pengguna yang dipegang setelah selesai.

---

## 3. Perbandingan hash baseline vs final

| File | Baseline | Final | Status |
|---|---|---|---|
| `tasks/task.go` | `72ba098b…0c9245e` | `29bbe0cf…71f1ca03` | **BERUBAH** (scope) |
| `tasks/http.go` | `dfc5f812…ab197f1` | `10ca63a1…4ef91130e` | **BERUBAH** (scope) |
| `tasks/task_test.go` | `48804576…73fde1cf` | `ef483111…7dfd00f` | **BERUBAH** (scope) |
| `tasks/main.go` | `a5e62f44…6109f13` | `a5e62f44…6109f13` | tidak berubah |
| `go.mod` | `31017a…fbeb7e` | `31017a…fbeb7e` | tidak berubah |
| `emailvalidator/email.go` | `1dd02c…b5ffd1be` | `1dd02c…b5ffd1be` | tidak berubah |
| `emailvalidator/email_test.go` | `3e6d02…dd5e8e7` | `3e6d02…dd5e8e7` | tidak berubah |
| `docs/AGENTIC-WORKFLOW.md` | `a46ee6…a3c8a093` | `a46ee6…a3c8a093` | tidak berubah |
| `docs/WORKFLOW-COMPLIANCE-REVIEW.md` | `a2ebe8…2388a1` | `a2ebe8…2388a1` | tidak berubah |
| `docs/evidence/TASK-07-EVIDENCE.md` | `9dc959…3d4873` | `9dc959…3d4873` | tidak berubah |

`git diff` dan `git diff --cached` kosong (repo belum commit), sehingga tidak
dipakai sebagai bukti; pembuktian memakai manifes hash (sesuai workbook §8.5).
Hanya 3 file scope yang berubah; tidak ada file di luar scope yang berubah.

---

## 4. Review & regresi (direct proof vs claim)

**Direct proof (dijalankan di task ini):**
- Status codes semua kasus detail endpoint — via `TestGetTaskEndpoint` (PASS).
- Body 200 = task yang dibuat; body 404/400 JSON error — via
  `TestGetTaskEndpointBody` + smoke test `ErrorDetails`.
- File aplikasi lain + `go.mod` + docs lama identik hash-nya.
- `go build`/`go vet` exit 0; seluruh test `ok`.

**Claim/laporan (tidak dijadikan independent proof):**
- Semua penjelasan perilaku Task 2–7 di dokumen lama (hanya referensi).

**Regresi:** `TestHealthEndpoint`, `TestGetTasksEndpoint`,
`TestPostTasksEndpoint` tetap PASS. Endpoint `/health`, `GET /tasks`, dan
`POST /tasks` tidak diubah pada cabang route-nya (diverifikasi isi `ServeHTTP`).

**Review input/JSON/concurrency:**
- Validasi ID: non-integer & `<= 0` → 400; `contains("/")` → 404.
- Konvensi JSON: `{"id":..., "title":...}` untuk task; `{"error": "..."}` untuk
  error; `Content-Type: application/json`.
- Concurrency: `GetTask` read-only di bawah `RLock` (inspeksi; race detector
  tidak tersedia — lihat §2.4).

---

## 5. Klasifikasi bukti

| Pernyataan | Label |
|---|---|
| `go test`, `go vet`, `go build` hasil §2.2–2.3 | **Bukti langsung** |
| Hash table §3 | **Bukti langsung** |
| Smoke test status & error body §2.5 | **Bukti langsung** |
| Race detector "tidak tersedia (no gcc)" | **Bukti langsung** (gagal lingkungan) |
| "Perilaku Task 1–7 seperti laporan lama" | **Laporan agent** |
| "Implementasi aman secara concurrency" | **Asumsi** (disimpulkan dari inspeksi; belum diverifikasi race detector) |

---

## 6. Keterbatasan tersisa

1. **Race detector tidak dapat dijalankan** (lingkungan tanpa gcc/cgo).
2. **Tanpa baseline commit** — regresi hanya dijaga oleh test + hash.
3. **Smoke test body 404/400** pada percobaan pertama tidak terbaca oleh
   `Invoke-WebRequest`; konfirmasi didapat via `ErrorDetails` dan delapan kasus
   handler test langsung (yang merupakan harness lebih otoritatif).
4. **ID besar** (`> math.MaxInt`) akan gagal `strconv.Atoi` → 400; perilaku
   dianggap sesuai "ID tidak valid".
5. **Routing tanpa framework** — tidak ada wildcard/parameter named; detail path
   diekstrak manual. Ini konsisten dengan kode existing (bukan refactor).
6. Hasil smoke test tidak otomatis; proses server dimulai/dihentikan manual.

---

## 7. Konfirmasi batasan task

- [x] Tidak commit/push/deploy/hapus file/re-init Git
- [x] `docs/AGENTIC-WORKFLOW.md` & `docs/WORKFLOW-COMPLIANCE-REVIEW.md` tidak diubah
- [x] `emailvalidator/` dan `go.mod` tidak diubah (hash identik; tidak ada kebutuhan teknis)
- [x] Tidak ada operasi destruktif / akses di luar workspace
- [x] Tidak ada dependency baru (`go.mod` hash identik)