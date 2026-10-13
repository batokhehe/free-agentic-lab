# TASK-07 Evidence Log — Workflow Reliability & Evidence Audit

Task: 07 — Workflow Reliability & Evidence Audit
Tanggal: 2026-10-12
Workspace: `D:\AI\free-agentic-lab`
Shell: Windows PowerShell 5.1 · Go go1.26.5 windows/amd64
Scope: **dokumentasi saja** (tidak mengubah source code, `go.mod`, atau Git state).

Dokumen ini mendemonstrasikan protocol pada `docs/AGENTIC-WORKFLOW.md` §8:
snapshot hash, perekaman perintah+exit code+output, klasifikasi bukti, dan
perbandingan kondisi awal/akhir tanpa bergantung pada `git diff`.

---

## 1. Rencana (sebelum eksekusi)

- **Diubah:** `docs/AGENTIC-WORKFLOW.md` — tambah §8 "Protocol Bukti &
  Keterlacakan" + baris checklist.
- **Dibuat:** `docs/evidence/TASK-07-EVIDENCE.md` (dokumen ini) + manifes hash.
- **Tidak disentuh:** `emailvalidator/*`, `tasks/*`, `go.mod`,
  `docs/WORKFLOW-COMPLIANCE-REVIEW.md`, konfigurasi, Git state.
- **Dilarang:** commit, push, hapus file, re-init Git, deployment, dependency baru.

---

## 2. Log perintah (dijalankan aktual, output verbatim)

### 2.1 Audit Git

```
$ git status --short
?? docs/
?? emailvalidator/
?? go.mod
?? tasks/
exit=0

$ git status --porcelain=v1 -uall
?? docs/AGENTIC-WORKFLOW.md
?? docs/WORKFLOW-COMPLIANCE-REVIEW.md
?? emailvalidator/email.go
?? emailvalidator/email_test.go
?? go.mod
?? tasks/http.go
?? tasks/main.go
?? tasks/task.go
?? tasks/task_test.go
exit=0

$ git branch
(empty)
exit=0

$ git log --oneline --all
(empty)
exit=0

$ git remote -v
origin  https://github.com/batokhehe/free-agentic-lab.git (fetch)
origin  https://github.com/batokhehe/free-agentic-lab.git (push)
exit=0

$ git ls-files
(empty)
exit=0
```

Catatan: repository belum memiliki commit (unborn branch). Karena itu seluruh
file untracked dan `git diff` selalu kosong.

### 2.2 Dokumentasi dibaca langsung

```
Read docs/AGENTIC-WORKFLOW.md        -> 246 baris (pra-edit), dibaca penuh
Read docs/WORKFLOW-COMPLIANCE-REVIEW.md -> 284 baris, dibaca penuh
```

### 2.3 Snapshot hash awal (sebelum edit)

```
$ Get-ChildItem -Recurse -File -Force | Where-Object { $_.FullName -notmatch '\\\.git\\' } | Get-FileHash -Algorithm SHA256
exit=0
c90763eda3244bfb987ce4b845afe6ccd4797c439d1efa9fe6aaa78c30d4f14a  docs\AGENTIC-WORKFLOW.md
a2ebe8d0fa272ae8142a533dc970f6b7dd235411b6adbb54f6084daf232388a1  docs\WORKFLOW-COMPLIANCE-REVIEW.md
1dd02cfb82b6dd7838fc1a9c1d1434fef1d41baed5a355cad01df176b5ffd1be  emailvalidator\email.go
3e6d02554d17e7051337df807eb9f62bc777a0baa886cf4e59dab6809dd5e8e7  emailvalidator\email_test.go
31017aedfd045889c0334adc20a563f88f6bdb35c43a09b367cb756e99fbeb7e  go.mod
dfc5f812b2ab653ce678153f816135b7537d2b1fae7f037ff159c4123ab197f1  tasks\http.go
a5e62f44533ee4a9913adca9f1bc5223c6b041cfa0d608fc16ea8e9c46109f13  tasks\main.go
72ba098b297eaa21b53c06d332c0c994cf556f3c0208d227557353c1a0c9245e  tasks\task.go
48804576d8b8096da5d1b3bbe1c439c704ead5b536739ab23ec06ab473fde1cf  tasks\task_test.go
```

### 2.4 Snapshot hash akhir (sesudah edit dokumen)

```
$ Get-ChildItem -Recurse -File -Force | Where-Object { $_.FullName -notmatch '\\\.git\\' -and $_.Name -ne 'TASK-07-EVIDENCE.md' } | Get-FileHash -Algorithm SHA256
exit=0
a46ee67b1e6388cebe9a3399957acf49545b353a46ef747707a4c030a3c8a093  docs\AGENTIC-WORKFLOW.md
a2ebe8d0fa272ae8142a533dc970f6b7dd235411b6adbb54f6084daf232388a1  docs\WORKFLOW-COMPLIANCE-REVIEW.md
1dd02cfb82b6dd7838fc1a9c1d1434fef1d41baed5a355cad01df176b5ffd1be  emailvalidator\email.go
3e6d02554d17e7051337df807eb9f62bc777a0baa886cf4e59dab6809dd5e8e7  emailvalidator\email_test.go
31017aedfd045889c0334adc20a563f88f6bdb35c43a09b367cb756e99fbeb7e  go.mod
dfc5f812b2ab653ce678153f816135b7537d2b1fae7f037ff159c4123ab197f1  tasks\http.go
a5e62f44533ee4a9913adca9f1bc5223c6b041cfa0d608fc16ea8e9c46109f13  tasks\main.go
72ba098b297eaa21b53c06d332c0c994cf556f3c0208d227557353c1a0c9245e  tasks\task.go
48804576d8b8096da5d1b3bbe1c439c704ead5b536739ab23ec06ab473fde1cf  tasks\task_test.go
```

---

## 3. Perbandingan awal vs akhir (bukti langsung)

Manifes hash "sebelum (§2.3)" dibandingkan dengan "sesudah (§2.4)":

| File | Hash awal | Hash akhir | Status |
|---|---|---|---|
| `docs/AGENTIC-WORKFLOW.md` | `c90763…d4f14a` | `a46ee6…a3c8a093` | **BERUBAH** (sesuai scope) |
| `docs/WORKFLOW-COMPLIANCE-REVIEW.md` | `a2ebe8…2388a1` | `a2ebe8…2388a1` | tidak berubah |
| `emailvalidator/email.go` | `1dd02c…b5ffd1be` | `1dd02c…b5ffd1be` | tidak berubah |
| `emailvalidator/email_test.go` | `3e6d02…dd5e8e7` | `3e6d02…dd5e8e7` | tidak berubah |
| `go.mod` | `31017a…fbeb7e` | `31017a…fbeb7e` | tidak berubah |
| `tasks/http.go` | `dfc5f8…ab197f1` | `dfc5f8…ab197f1` | tidak berubah |
| `tasks/main.go` | `a5e62f…6109f13` | `a5e62f…6109f13` | tidak berubah |
| `tasks/task.go` | `72ba09…0c9245e` | `72ba09…0c9245e` | tidak berubah |
| `tasks/task_test.go` | `488045…73fde1cf` | `488045…73fde1cf` | tidak berubah |

**Kesimpulan (bukti langsung):** hanya `docs/AGENTIC-WORKFLOW.md` yang berubah;
seluruh file aplikasi dan `go.mod` **identik** (hash sama). File baru
`docs/evidence/TASK-07-EVIDENCE.md` tidak diikutsertakan dalam manifes karena
bersifat self-referential (dokumen ini mendefinisikan dirinya sendiri).

> Batas kejujuran: hash yang sama membuktikan isi file identik dengan snapshot,
> **bukan** bukti bahwa isi benar/aman. Hash yang berbeda hanya membuktikan
> perubahan byte, bukan bahwa perubahannya diinginkan.

---

## 4. Klasifikasi bukti

| Pernyataan | Label | Dasar |
|---|---|---|
| Repo belum punya commit; 9 file untracked | **Bukti langsung** | `git status --porcelain -uall` di §2.1 |
| `docs/AGENTIC-WORKFLOW.md` berubah, file lain identik | **Bukti langsung** | Perbandingan hash §3 |
| `git diff` kosong walau workspace berubah | **Bukti langsung** | `git status` vs `git diff` (§2.1, §2.3–2.4) |
| "Task 2 test lulus 0.004s" | **Laporan agent** | Tidak tersimpan sebagai log; tidak diverifikasi di sini |
| "Artefak .exe/.log muncul di Task 4 lalu dihapus" | **Laporan agent** | Rekoleksi sesi, bukan bukti tersimpan |
| Validator tetap berperilaku sama | **Asumsi/terbatas** | Di Task ini tidak dijalankan test; hanya hash + tidak ada perubahan file yang membuktikannya |

---

## 5. Hasil perintah verifikasi aplikasi

**Tidak dijalankan pada Task 7.** Task 7 hanya menyentuh dokumentasi; tidak ada
perubahan source code yang perlu diuji. Karena itu tidak ada klaim lulus/gagal
test pada task ini. (Untuk referensi, Task 6 menjalankan `go test -count=1 ./...`
dengan hasil tercatat di `docs/WORKFLOW-COMPLIANCE-REVIEW.md` §3 — itu **laporan
agent**, bukan bukti langsung Task 7.)

---

## 6. Keterbatasan yang belum diselesaikan

1. **Tanpa baseline commit** — tidak ada regresi Git; hanya test + hash yang
   menjadi penjaga. Perubahan perilaku di luar cakupan test tidak terdeteksi.
2. **Hash belum otomatis** — snapshot masih diambil manual; belum ada skrip/Make
   target yang menjalankannya.
3. **Self-hash** — dokumen bukti tidak dapat memuat hash-nya sendiri; hash-nya
   harus dicatat eksternal.
4. **Klasifikasi bukti bergantung disiplin** — protocol mendefinisikan label, tetapi
   tidak ada mekanisme yang memaksa pelabelan otomatis.
5. **Tidak memindai riwayat Git** dan tidak melakukan audit keamanan mendalam.
6. **Verifikasi aplikasi tidak dijalankan pada task ini** (hanya dokumentasi), jadi
   integritas fungsional mengandalkan hash + laporan Task 6.
