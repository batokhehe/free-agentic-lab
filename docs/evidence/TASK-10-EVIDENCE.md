# TASK-10 Evidence Log — Orchestrator Hardening (Drift Protection, State/Resume, Approval Gates)

Task: 10 — Harden Sentinel Auto-Agentic Orchestrator
Tanggal: 2026-10-12
Workspace: `D:\AI\free-agentic-lab`
Shell: Windows PowerShell 5.1 · Python 3.13.7 · Git (unborn branch `main`, no commits)

**Boundary untuk task ini:** pekerjaan hanya di dalam `D:\AI\free-agentic-lab`.
Repositori Sentinel (`D:\Naufal\Example\Pentest\sentinel`) **tidak dibaca, tidak
disentuh, dan tidak dijalankan model apapun** terhadapnya. Tidak ada
commit/push pada workspace ini.

---

## 1. Ringkasan

Task ini meng-hardening orchestrator dengan tiga perubahan keamanan inti plus
pengujian otomatis:

| Fase | Isi | Status |
|---|---|---|
| 0 | Baseline + inspeksi kode & dokumen | selesai |
| 1 | Fail-closed drift protection per tahap | selesai |
| 2 | State/resume yang dapat diandalkan | selesai |
| 3 | Approval gates ditegakkan di Python | selesai |
| 4 | Tes (fixture sementara, tanpa akses Sentinel) | 56 tes OK |
| 5 | Verifikasi & laporan (dokumen ini) | selesai |

---

## 2. File yang diubah / dibuat

**Diubah (source orchestrator):**

| File | Perubahan |
|---|---|
| `orchestrator/workflow.py` | `_guard_after_stage` (drift check fail-closed setiap tahap), `_describe_changes`, persisten `RunContext.last_exit_code`, `_run_tests_once` mengonsumsi approval per-aksi, `do_implement` gate `implement`, atribusi scope setelah tahap |
| `orchestrator/state.py` | `begin_run` mencatat `attempts`/`last_exit_code`/`status`, helper `active_attempts`/`set_active_exit_code`/`get_active_summary`/`mark_needs_review`, approval single-use & run-scoped, single-runner lock bidir PID, migrasi key `approvals`, `finish_run` menyimpan `attempts`/`last_exit_code` ke riwayat |
| `orchestrator/runner.py` | Ditulis ulang: `--resume` wajib untuk run terputus, `_resume_manifest_check` (drift → `needs_review`), `--force` untuk task selesai, `--approve ACTION` (append, divalidasi), single-runner lock, exit 0/1/2/3 |
| `orchestrator/policy.py` | `approval_action`, `KNOWN_APPROVAL_ACTIONS`, `validate_repo_boundary`, `check_command_safety` menyertakan `action` |
| `orchestrator/opencode.py` | (pra-hardening, masih berlaku) unwrap shim `opencode.CMD` → `.exe` agar prompt multiline tidak terpotong |

**Diubah (dokumen):** `docs/AGENTIC-WORKFLOW.md` — seksi Perintah (flag baru),
Model keamanan (poin 8–9), Recovery/resume.

**Dibuat:**

| File | Isi |
|---|---|
| `tests/test_hardening.py` | 17 tes baru (12 skenario wajib + tambahan) |
| `docs/evidence/TASK-10-EVIDENCE.md` | dokumen ini |

State/run yang dibuat selama verifikasi disimpan di direktori `runs/` dan
`state/` milik workspace; tidak ada modifikasi pada repository Sentinel.

---

## 3. Commands aktual + exit code

### 3.1 Kompilasi syntax

```
$ python -m compileall -q orchestrator tests
exit=0
```

### 3.2 Test suite penuh

```
$ python -m unittest discover -s tests -t . -q
Ran 56 tests in 39.308s
OK
exit=0
```

Rincian: `tests/test_hardening.py` (17), `tests/test_runner.py`, `tests/test_workflow.py`,
`tests/test_state.py`, `tests/test_policy.py`, `tests/test_opencode.py`.

### 3.3 Git workspace (bukti tidak ada commit)

```
$ git status --porcelain=v1 -uall
?? docs/AGENTIC-WORKFLOW.md          (seluruh workspace untracked)
?? orchestrator/...                  (semua module)
?? tests/...                         (helpers + 6 file test)
?? runs/20261012T072547+0000-...-stage0-verification-p8pp/   (run lama, tidak disentuh)
?? state/state.json
exit=0

$ git log --oneline
fatal: your current branch 'main' does not have any commits yet
exit=128 (expected: repo tanpa commit)

$ git branch --show-current
main
exit=0
```

Tidak ada `git add`, `git commit`, `git push`, `git restore`, `git reset`,
`git clean`, `git checkout`, `git stash` dijalankan pada task ini.

---

## 4. Skenario yang diuji (fase 4)

Semua uji memakai repo/state fixture di direktori sementara; **tidak ada akses
ke Sentinel** (`D:\Naufal\Example\Pentest\sentinel`).

| # | Skenario | Cover di `tests/test_hardening.py` | Harapan |
|---|---|---|---|
| 1 | File tracked diubah saat read-only | `test_read_only_modifies_tracked_file_blocks` | blocked; file TIDAK di-restore |
| 2 | File untracked dibuat saat read-only | `test_read_only_creates_untracked_file_blocks` | blocked; file TIDAK dihapus |
| 3 | File dihapus saat read-only | `test_read_only_deletes_file_blocks` | blocked; tidak ada auto-restore |
| 4 | Perubahan di luar scope implement | `test_out_of_scope_implement_change_blocks` | blocked |
| 5 | Aksi approval tanpa approval | `test_approval_categorized_command_without_approval_rejected` | `ApprovalRequired` |
| 6 | Aksi deny selalu blocked | `test_deny_categorized_command_always_blocked` | `TaskBlocked` |
| 7 | Approval satu aksi ≠ aksi lain | `test_approval_for_one_action_does_not_authorize_another` | `ApprovalRequired` |
| 8 | Approval sekali pakai | `test_approval_is_consumed_once` | run ke-2 butuh approval baru |
| 9 | Batas retry ditegakkan | `test_fix_attempts_capped_by_config` | `TaskFailed`, `attempts` terpersist |
| 10 | Task selesai tidak dijalankan ulang | `test_completed_task_not_rerun_without_force` | tanpa `--force` → exit 3 |
| 11 | Run terputus butuh `--resume` eksplisit | `test_ambiguous_interruption_requires_explicit_resume` | tanpa `--resume` → exit 3 |
| 12 | Drift saat run terputus → tinjauan | `test_interrupted_run_with_drift_requires_recovery_review` | exit 2, `needs_review` |
| 13 | State korup gagal aman | `test_corrupted_state_fails_safely` | exit 3; file tidak di-reset |
| 14 | Dua runner bersamaan ditolak | `test_duplicate_concurrent_runner_rejected` | exit 3 via lock |
| 15 | Batas repo (boundary) | `test_boundary_validation_*` | path keluar `workspace_root` ditolak |

Skenario 1–6, 9, 11–12 disebutkan dalam `docs/AGENTIC-WORKFLOW.md` §9,
§"Recovery / resume", dan folder `runs/<id>/drift-*.json` / `scope-violations-*.json`
sebagai bukti tersimpan.

---

## 5. Jaminan keamanan yang tersedia (ditegakkan di kode, bukan prompt)

1. **Fail-closed drift guard per tahap.** Baseline manifes SHA-256 + state per
   file (termasuk *untracked*, karena `git diff` tidak cukup di repo tanpa
   commit). `read_only`: perubahan apa pun → blocked. `implement`: hanya
   perubahan `allowed` dalam scope yang bertahan; `protected`/`out-of-scope`
   → blocked. HEAD berubah → blocked.
2. **Tidak ada auto-restore/penghapusan.** Guard hanya mendeteksi & memblokir;
   file hasil perubahan dibiarkan untuk tinjauan manusia.
3. **Run terputus tidak dilanjutkan otomatis.** `--resume` = keputusan
   pemulihan eksplisit; manifes segar vs stored baseline dibandingkan dahulu;
   drift → `needs_review` + exit 2.
4. **Approval single-use & run-scoped** (`state["approvals"]`), dikunci ke
   (task, aksi, run_id); `--approve ACTION` divalidasi terhadap
   `KNOWN_APPROVAL_ACTIONS`; aksi yang tidak dikenal → fail-closed.
5. **Satu runner aktif.** Lock file berisi PID dengan reclaim bila PID mati;
   runner kedua mendapat exit 3.
6. **Perintah arbitrer dari model tidak pernah dieksekusi.** Model hanya
   menghasilkan teks; satu-satunya command yang dieksekusi adalah `tests` dari
   task yang sudah ditinjau manusia dan lolos `check_command_safety`.
7. **State korup tidak di-reset diam-diam**; `StateError` → exit 3.
8. **Boundary workspace.** `repo_root`, `runs_dir`, `tasks_dir`, `state_file`
   harus resolve di dalam `workspace_root` (via `realpath`, menutup symlink).

## 6. Keterbatasan (dilaporkan jujur)

1. **Deteksi pasca-tahap, bukan OS sandbox.** Guard mendeteksi perubahan
   *setelah* tahap selesai dan menghentikan run. Perubahan sudah terjadi di
   disk sebelum diblokir. Ini "detect-and-block", bukan sandbox file system.
2. **Satu lock per workspace**, bukan per repo — dua workspace berbeda boleh
   berjalan bersamaan.
3. **Hash manifold bukan bukti niat.** Hash sama ≠ isi benar; hash beda ≠
   perubahan buruk. Blocking tetap membutuhkan manusia untuk memutuskan.
4. **Model masih bisa menyentuh file selama satu tahap** sebelum guard
   berjalan pada batas tahap; granularitas deteksi = panjang tahap.
5. **Insiden lama (pra-hardening, di luar task ini):** run `stage0-verification`
   sebelumnya memodifikasi `sentinel/apps/web/pnpm-lock.yaml` sebelum diblokir.
   Task ini **tidak** menyentuh Sentinel; tidak ada salinan bytes asli yang
   terverifikasi di luar laporan sebelumnya.

## 7. Kesimpulan & perintah verifikasi aktual

- `python -m compileall -q orchestrator tests` → **exit=0**
- `python -m unittest discover -s tests -t . -q` → **Ran 56 tests, OK, exit=0**
- `git status --porcelain=v1 -uall` → seluruh workspace untracked; **tidak ada
  commit**; `git log` → belum ada commit (unborn branch)
- Sentinel **tidak diakses** selama task ini; tidak ada model dijalankan
  terhadapnya; tidak ada commit/push pada workspace.