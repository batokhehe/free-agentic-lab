# TASK-11 Evidence Log — Orchestrator Hardening (Fail-Closed Commands, Role Models, Independent Review, Provider-Limit Pause/Resume)

Task: 11 — Harden orchestrator (command policy fail-closed, role-based models,
independent review loop, provider-limit checkpoint/pause/resume)
Tanggal: 2026-10-13
Workspace: `D:\AI\free-agentic-lab`
Shell: Windows PowerShell 5.1 · Python 3.13.7 · OpenCode CLI 1.19.0
Git (unborn branch `main`, no commits)

**Boundary:** semua pekerjaan hanya di dalam `D:\AI\free-agentic-lab`. Sentinel
(`D:\Naufal\Example\Pentest\sentinel`) **tidak dibaca/ubah**; tidak ada
commit/push; tidak ada akses provider berbayar.

---

## 1. Ringkasan

Melanjutkan hardening orchestrator berdasar persyaratan:

| # | Persyaratan | Implementasi | Status |
|---|---|---|---|
| 1 | Fail-open → fail-closed | `policy.check_command_safety`: penolakan berbasis allowlist eksplisit; perintah tak dikenal/ambigu (meta `; & \| < > ` $ { } ( )`) → `deny` | selesai |
| 2 | Tool-call OpenCode vs guard orkestrator | Tanpa `--auto`; config agent (edit/bash/webfetch/websearch) + snapshot guard per tahap; probe nyata → 403 FreeTier (didokumentasikan, tidak bypass) | selesai (dokumentasi jujur) |
| 3 | Model per-role (coder ≠ reviewer, fallback coder, QA lokal deterministik) | `roles` di config: `coder.model`+`fallback_models`, `reviewer.model`, `qa.commands` | selesai |
| 4 | Provider-limit checkpoint/pause/resume; tanpa silent same-model review; tanpa loop abadi | `ProviderLimitReached` → `mark_paused`; retry transien berbatas per model; fallback; `manifest-latest.json` tiap tahap; resume wajib `--resume` | selesai |
| 5 | Loop review: QA → review model lain read-only → temuan → coder fix → QA/review ulang, max rounds → PASS/FAIL/BLOCKED | `do_review` + `_parse_review` + verifikasi reviewer read-only via snapshot | selesai |
| 6 | Regression tests: unknown cmd, exec paths, independence reviewer, limits, retries, checkpoint/resume, out-of-scope | `tests/test_review_and_providers.py` (30 tes) + update fixture lama | selesai |
| 7 | Fixture repo + provider failure dimock; tanpa Sentinel | semua tes pakai dir temp + `FakeOpenCode`/`FakeCommand` | selesai |
| 8 | Jalankan tes relevan lalu penuh; lapor command/exit/file/limit | dokumen ini | selesai |

---

## 2. File yang diubah / dibuat

**Diubah:**
| File | Perubahan |
|---|---|
| `orchestrator/policy.py` | allowlist `_SAFE` + `_SHELL_META_RE` + `check_command_safety` fail-closed (deny → meta → approval → safe-ok → deny); `extra_safe`; `validate_task` memakai `safe_commands` config |
| `orchestrator/config.py` | default `roles`/`review`/`provider_limits`/`safe_commands`; `_normalize_roles` / `_normalize_review` / `_normalize_provider_limits`; `role_models`, `role_agent`, `qa_commands`; ConfigError bila reviewer==coder |
| `orchestrator/workflow.py` | `run_model` (role, fallback, retry berbatas, deteksi limit), `_assert_reviewer_independent`, `_effective_opencode_config`, prompt & parser review (`_review_prompt`, `_review_fix_prompt`, `_parse_review`), `do_review` loop (QA → reviewer read-only guard → fix → ulang, max_rounds), `ProviderLimitReached` → `mark_paused`, `manifest-latest.json` per tahap, verdict review di report, branch dry-run "DRY-RUN (not reviewed)" |
| `orchestrator/runner.py` | `EXIT_PAUSED=4`, `_resume_manifest_check` pakai `manifest-latest.json`, penanganan hasil "paused" di queue & post-process |
| `orchestrator/state.py` | (dari task sebelumnya) `mark_paused`/`is_paused`/`get_active_summary` dipakai baru |
| `orchestrator/opencode.py` | `detect_provider_limit`, urutan pola limit (lebih spesifik dulu), `env` di `_run_popen`, `OPENCODE_CONFIG` di `run_opencode` |
| `orchestrator.config.json` | `repo_root: "."` (hapus referensi Sentinel), `roles` coder/reviewer/QA, `review`, `provider_limits`, `agent_config`, `ignore_dirs` += `runs`, `state` |
| `tests/test_workflow.py` | fixture perintah tes → allowlisted (`go vet ./...`, `python -m pytest tests`) |
| `tests/test_hardening.py` | fixture serupa + `test_unclassifiable_approval_fails_closed` kini menuntut `deny` |
| `tests/helpers.py` | `FakeOpenCode` routing role (coder vs reviewer, default reviewer = JSON PASS), `implement_task`, `make_ctx(..., config=...)` |

**Dibuat:**
```text
orchestrator/opencode.agents.json   # permission coder vs reviewer (reviewer read-only deny)
tests/test_review_and_providers.py  # 30 tes regression baru
docs/evidence/TASK-11-EVIDENCE.md   # dokumen ini
```

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
Ran 86 tests in 85.345s
OK
exit=0
```
Rincian: baseline 56 (test_policy/test_opencode/test_runner/test_state/test_workflow/test_hardening)
+ 30 baru (`tests/test_review_and_providers.py`).

### 3.3 Integrasi config default + dry-run dengan task yang ada
```
$ python -m orchestrator.runner --task sentinel-stage0-verification --dry-run --force
review: verdict=DRY-RUN (not reviewed) after round 0: dry-run: no real model invoked
run finished: status=completed
Completed: task=sentinel-stage0-verification status=completed run_id=20261013T033510Z-...
exit=0
```
Tidak ada file repositori diubah oleh run ini (artefak di `runs/<id>/` dan `state/`
sudah dikecualikan dari manifest).

### 3.4 Probe OpenCode nyata (bounded, sekali)
```
$ env:OPENCODE_CONFIG = "D:\AI\free-agentic-lab\orchestrator\opencode.agents.json"
$ opencode run --agent orchestrator-reviewer --dir D:\AI\free-agentic-lab --format json "<review prompt>"
{"type":"error",...,"error":{"name":"APIError","data":{"message":"OpenCode's free tier can only
be used from within OpenCode","statusCode":403,...},"status":403...}}
exit=1
```
`opencode auth list` → 0 credentials. Probe TIDAK memodifikasi apa pun dan TIDAK
mengeksekusi tool sebarang; ini dokumentasi jujur pemblokir live-run.

### 3.5 Git workspace (bukti tidak ada commit)
```
$ git status --porcelain=v1 -uall
?? docs/  ?? emailvalidator/ ?? go.mod ?? orchestrator.config.json ?? orchestrator/
?? runs/  ?? state/ ?? tasks/ ?? tests/        (seluruh workspace untracked)
exit=0
$ git log --oneline
fatal: your current branch 'main' does not have any commits yet
```

---

## 4. Skenario regression baru (`tests/test_review_and_providers.py`)

| # | Skenario | Test | Harapan |
|---|---|---|---|
| 1 | Perintah tak dikenal → deny fail-closed | `test_unknown_command_denied_fail_closed` | `category==deny` |
| 2 | Meta shell (`; && ` $ ( ) ` < >`) → deny walau prefix terlihat aman | `test_shell_metachars_deny_even_if_prefix_looks_safe` | deny |
| 3 | Allowlist build/test → ok | `test_allowlisted_build_test_commands_ok` | ok |
| 4 | Jalan eksekusi: git push/commit deny, pnpm add approval | `test_approval_and_deny_of_execution_paths` | deny/approval |
| 5 | `extra_safe` hanya dari config | `test_extra_safe_commands_only_apply_from_config` | ok saat dikirim, deny tanpa |
| 6 | Perintah tak dikenal di task → TaskBlocked | `test_unknown_command_fails_task_through_workflow` | TaskBlocked |
| 7 | Instal butuh approval sekali pakai | `test_approved_install_command_runs_only_when_approved` | Approved baru jalan |
| 8 | Agent reviewer read-only di agents.json | `test_reviewer_agent_is_read_only_in_agents_config` | edit/bash/web deny |
| 9 | `opencode run` memakai `--model/--agent` dan tanpa `--auto` | `test_run_opencode_uses_agent_command_and_never_auto` | flags benar |
| 10 | `OPENCODE_CONFIG` env dikirim dari `agent_config` | `test_open_code_config_env_is_set_from_agent_config` | env terpasang |
| 11 | Config menolak reviewer==coder | `test_config_rejects_same_model_for_coder_and_reviewer` | ConfigError |
| 12 | `run_model` reviewer overlap → block | `test_run_model_blocks_overlapping_reviewer` | TaskBlocked |
| 13 | Reviewer tanpa model → block | `test_empty_reviewer_models_block` | TaskBlocked |
| 14 | Deteksi provider limit | `test_failed_run_matching_signal_is_detected` + sukses/plain | signal atau None |
| 15 | Fallback model saat limit | `test_fallback_to_fallback_model_on_provider_limit` | urutan model benar |
| 16 | Retry transien berbatas per model | `test_transient_retries_are_bounded_per_model` | primary×2 → fallback |
| 17 | Semua model limit → pause (checkpoint) | `test_all_models_exhausted_raises_and_pauses` | paused_provider_limit, terminal.json |
| 18 | Gagal non-provider tidak di-switch diam-diam | `test_non_provider_failure_is_not_silently_switched` | model tetap |
| 19 | Exit code runner untuk paused = 4 | `test_runner_exit_code_for_paused_run_is_four` | EXIT_PAUSED |
| 20 | `manifest-latest.json` ditulis tiap tahap | `test_checkpoint_manifest_written_after_each_stage` | file ada & = snapshot |
| 21 | Resume dari checkpoint terakhir | `test_paused_run_resumes_from_latest_checkpoint` | completed, verdict PASS |
| 22 | Resume lebih suka `manifest-latest`; drift setelahnya → perlu tinjauan | `test_resume_manifest_prefers_last_checkpoint` | ok; drift→needs_review |
| 23 | Review PASS: bukti model reviewer ≠ coder | `test_review_pass_writes_evidence_with_different_model` | verdict PASS + models beda |
| 24 | Review FAIL dibatasi max_rounds | `test_review_fail_bounded_to_max_rounds` | 2 panggilan reviewer, TaskFailed |
| 25 | Output reviewer bukan JSON → BLOCKED | `test_review_bad_json_blocks_fail_closed` | BLOCKED |
| 26 | QA gagal di round review → FAIL | `test_review_qa_failure_eventually_fails` | verdict FAIL |
| 27 | Reviewer yang menulis file → BLOCKED | `test_reviewer_that_writes_files_is_blocked` | BLOCKED + changes tercatat |
| 28 | Perubahan out-of-scope saat fix review → BLOCKED | `test_out_of_scope_change_during_review_fix_blocks` | scope-violations-*.json |

---

## 5. Jaminan keamanan baru (ditegakkan di kode, bukan prompt)

1. **Command policy fail-closed.** Urutan: hard-deny → shell-metachar deny → approval
   → allowlist eksplisit → deny. Tidak ada fallback "assume safe".
2. **OpenCode tidak pernah `--auto`.** Semua permission stay gated; tool-call di luar
   approval menghasilkan exit non-0 → dianggap kegagalan (bukan bypass).
3. **Reviewer read-only dua lapis**: (a) agent `orchestrator-reviewer` dengan
   edit/bash/webfetch/websearch `deny`; (b) guard Python: snapshot before/after
   panggilan reviewer — perubahan apa pun → BLOCKED.
4. **Review independen wajib**. `require_independent_model` (default true):
   model reviewer harus berbeda dari semua model coder; overlap → ConfigError /
   TaskBlocked, bukan silent switch.
5. **Provider limit = named paused state.** Retry transien berbatas per model,
   fallback antar model, semua exhausted → `ProviderLimitReached` →
   `paused_provider_limit` + `terminal.json` + checkpoint `manifest-latest.json`.
6. **Resume hanya eksplisit** (`--resume`) dan dibandingkan terhadap checkpoint
   terakhir (`manifest-latest.json`), bukan baseline audit — perubahan allowed
   yang sudah tercatat tidak disalah-artikan sebagai drift.
7. **Loop review berbatas** (`review.max_rounds`, default 3) dengan verdict
   PASS / FAIL / BLOCKED yang ditulis sebagai bukti (`06-review-roundN.json`,
   `07-review-verdict.json`).
8. **QA deterministik lokal** (`roles.qa.commands`) berjalan via `check_command_safety`.

---

## 6. Keterbatasan (dilaporkan jujur)

1. **Live-run nyata terblokir provider.** `opencode auth list` = 0 credentials;
   probe nyata mengembalikan 403 `FreeTierError` ("free tier only within
   OpenCode"). Artinya: alur end-to-end nyata **tidak** dapat dieksekusi pada
   mesin ini — dibuktikan fail-closed (exit non-0 → tidak melanjutkan), tapi
   verifikasi permission OpenCode terhadap model hidup tidak dapat dilakukan;
   hanya diverifikasi lewat fixture + guard orkestrator + flag command-line.
2. **Deteksi pasca-tahap (detect-and-block, bukan sandbox).** Perubahan di disk
   sudah terjadi sebelum diblokir; granularitas = panjang tahap.
3. **Pengecualian `runs/` & `state/` dari manifest** (agar `repo_root="."`
   dapat dipakai): file di dalam direktori itu tidak terdeteksi sebagai drift.
   Trade-off didokumentasikan; task yang menyentuh area ini harus lewat
   review/scope manual.
4. **Reviewer tetap bisa salah menilai.** Verdict adalah bukti, bukan bukti
   kebenaran; manusia memutuskan akhir.
5. **Retry/backoff singkat.** `backoff_seconds` default kecil; provider-limit
   interval panjang tetap berujung pause (aman, sudah direncanakan).
6. **Repo ini tanpa commit (unborn `main`)** — manifest berbasis hash bekerja,
   tapi `git diff` terhadap HEAD tidak dapat dipakai sebagai sumber atribusi.

---

## 7. Kesimpulan & perintah verifikasi aktual

- `python -m compileall -q orchestrator tests` → **exit=0**
- `python -m unittest discover -s tests -t . -q` → **Ran 86 tests, OK, exit=0**
- `python -m orchestrator.runner --task ... --dry-run --force` → **exit=0** (verdict DRY-RUN jujur)
- Probe OpenCode nyata → **exit=1, 403 FreeTier** (dokumentasi, tanpa modifikasi)
- Sentinel **tidak diakses**; tidak ada commit/push.