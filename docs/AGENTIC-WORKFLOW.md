# Agentic Engineering Workflow

Dokumen ini mendefinisikan workflow engineering yang dapat digunakan ulang oleh
coding agent untuk mengerjakan perubahan pada sebuah repository secara aman,
terverifikasi, dan mudah diaudit. Workflow ini netral terhadap bahasa/framework
dan dirancang untuk dipakai pada project Go, Laravel (PHP), dan Java Spring Boot
tanpa mengasumsikan semua project memakai perintah test yang sama.

> Prinsip utama: **jangan pernah mengklaim sesuatu berhasil jika perintahnya
> belum benar-benar dijalankan dan outputnya belum benar-benar diamati.**

---

## 0. Prinsip Umum

1. **Audit sebelum mengubah.** Selalu pahami struktur, status Git, dan file yang
   ada sebelum menyentuh apa pun.
2. **Scope terkunci.** Hanya ubah file yang benar-benar diperlukan untuk tugas.
3. **Eksekusi nyata, bukan asumsi.** Setiap klaim hasil (test lulus, build sukses)
   harus berasal dari output perintah yang benar-benar dijalankan.
4. **Bedakan rencana dan realisasi.** Selalu tandai secara eksplisit mana perintah
   *direncanakan* dan mana yang *sudah dijalankan* beserta hasil aslinya.
5. **Setujui tindakan berisiko.** Minta persetujuan pengguna sebelum tindakan
   destruktif, deployment, perubahan database production, atau perubahan di luar
   scope.
6. **Jujur soal keterbatasan.** Hanya nyatakan kemampuan yang benar-benar dimiliki
   agent berdasarkan tool yang tersedia di sesi berjalan.
7. **Tidak ada commit/push tanpa perintah eksplisit.** Operasi Git destruktif
   dilarang kecuali diminta secara eksplisit.

---

## 1. Requirement Analysis & Repository Audit

**Tujuan:** memahami permintaan, batasan, dan kondisi awal repository.

Langkah:
1. Baca ulang permintaan; identifikasi deliverable, batasan, dan larangan.
2. Audit path workspace aktif dan konfirmasi lokasinya.
3. Periksa status Git: branch, commit terakhir, file tracked/untracked, perubahan
   yang belum di-commit.
4. Inventaris struktur folder dan file (termasuk file tersembunyi/config).
5. Baca file yang relevan **secara langsung** (jangan andalkan ringkasan lama).
6. Catat tool/versi yang tersedia (mis. bahasa, package manager, test runner).
7. Identifikasi konflik atau file tak terduga; bila ada, **berhenti dan laporkan**
   sebelum menimpa/menghapus.

Keluaran: ringkasan kondisi awal + daftar file relevan + daftar risiko/kendala.

**Contoh audit command (lintas project):**

```
git status --short
git branch
git log --oneline -10
git ls-files
git ls-files --others --exclude-standard
```

> Catatan portabilitas: shell pada sesi ini adalah Windows PowerShell 5.1. Hindari
> util Unix (`head`, `tail`, `grep`) pada pipeline; gunakan cmdlet PowerShell
> (`Select-Object -First`, `Select-String`) atau tool bawaan agent (Grep/Read/Glob).

---

## 2. Planning Sebelum Perubahan File

**Tujuan:** menyetujui pendekatan sebelum menyentuh kode.

Langkah:
1. Rumuskan rencana singkat (bullet) dan alasan tiap langkah.
2. Daftarkan file yang akan **dibuat**, **diubah**, dan **tidak disentuh**.
3. Nyatakan asumsi dan dependensi (mis. apakah dependency eksternal boleh ditambah).
4. Tentukan strategi verifikasi (perintah test/build/lint apa yang akan dipakai).
5. Identifikasi titik risiko dan rencana mitigasinya.
6. Bila rencana menyentuh tindakan destruktif/deployment/DB production/out-of-scope:
   **minta persetujuan dulu.**

Keluaran: rencana + daftar file + strategi verifikasi (belum dieksekusi).

---

## 3. Implementation dengan Scope yang Jelas

**Tujuan:** mengimplementasikan perubahan minimal yang memenuhi requirement.

Langkah:
1. Ikuti konvensi yang ada (struktur, penamaan, gaya, library yang sudah dipakai).
2. Pertahankan pemisahan tanggung jawab (mis. handler/service/domain terpisah).
3. Jangan menambah dependency eksternal kecuali diizinkan eksplisit.
4. Jangan mengubah/menghapus file di luar scope atau di luar workspace.
5. Jangan menambahkan komentar kode kecuali diminta.
6. Simpan perubahan kecil dan terfokus agar mudah di-review.

Aturan per-stack (ilustratif, bukan asumsi):
- **Go:** struct/interface idiom, error wrapping, `context` untuk operasi I/O,
  package kecil dan terfokus.
- **Laravel:** ikuti konvensi PSR & struktur `app/`, gunakan Artisan/Composer
  bila tersedia; migrasi tidak dijalankan ke DB production tanpa izin.
- **Spring Boot:** ikuti konvensi Maven/Gradle, anotasi Spring, dan struktur paket;
  jangan menyentuh konfigurasi production tanpa izin.

---

## 4. Automated Testing & Perbaikan Berdasarkan Error Aktual

**Tujuan:** membuktikan perubahan bekerja lewat eksekusi nyata.

Langkah:
1. Identifikasi perintah test yang benar untuk project (lihat tabel di bawah).
2. Jalankan test; **salin output aslinya**, jangan meringkas dari ingatan.
3. Jika gagal: baca pesan error aktual, analisis akar masalah, lakukan perbaikan
   terbatas, lalu jalankan ulang.
4. Jangan menandai "lulus" sebelum output sukses benar-benar terlihat.
5. Tambahkan test untuk kasus baru/regresi bila relevan.

**Tabel perintah verifikasi (rencanakan sesuai project, jangan diasumsikan sama):**

| Stack | Install deps | Format | Lint/Static | Test | Build |
|---|---|---|---|---|---|
| Go | `go mod download` | `gofmt -l .` / `gofmt -w .` | `go vet ./...` | `go test ./...` | `go build ./...` |
| Laravel | `composer install` | `./vendor/bin/pint` | `./vendor/bin/phpstan analyse` | `php artisan test` atau `./vendor/bin/phpunit` | `php artisan optimize` (opsional) |
| Spring Boot | `./mvnw dependency:resolve` / `gradlew` | (formatter plugin) | `./mvnw checkstyle:check` / `spotbugs` | `./mvnw test` / `./gradlew test` | `./mvnw package` / `./gradlew build` |

> Perintah di tabel adalah **kandidat**. Konfirmasi perintah aktual dari
> `README`, `Makefile`, `composer.json`, `pom.xml`/`build.gradle`, atau `go.mod`
> sebelum dipakai.

---

## 5. Code Review & Pemeriksaan Diff (Termasuk File Untracked)

**Tujuan:** meninjau perubahan secara eksplisit sebelum menyimpulkan.

Langkah:
1. Tinjau perubahan tracked: `git diff` dan/atau `git diff --cached`.
2. Untuk repo tanpa commit, `git diff` kosong — maka **isi file untracked harus
   dibaca eksplisit** (mis. via tool Read) dan dilaporkan sebagai bukti.
3. Cantumkan daftar file untracked: `git ls-files --others --exclude-standard`.
4. Verifikasi tidak ada file di luar scope yang berubah.
5. Cek konsistensi gaya, dead code, dan error handling.

Keluaran: daftar file + ringkasan diff/isi file + pernyataan eksplisit untuk file
yang belum di-track.

---

## 6. Security Review & Pemeriksaan Regresi

**Tujuan:** memastikan perubahan tidak membuka celah dan tidak merusak yang lama.

Checklist security:
- Tidak ada secret/kredensial yang di-hardcode, di-log, atau di-commit.
- Validasi input memadai (batas panjang, tipe, whitelist) dan error tidak
  membocorkan detail internal.
- Tidak menambah dependency tanpa penilaian (supply chain); lebih baik standard
  library bila memungkinkan.
- Operasi sensitif (auth, deserialisasi, eksekusi perintah, I/O file) ditinjau.
- Tidak ada bypass autentikasi/otorisasi atau penurunan konfigurasi keamanan.

Checklist regresi:
- Test lama (bukan hanya yang baru) tetap dijalankan dan lulus.
- Perilaku publik yang sudah ditetapkan tetap terjaga (mis. validator menolak
  input yang sama seperti sebelumnya).
- Tidak mengubah kontrak API tanpa dokumentasi/versioning.

---

## 7. Final Report

**Tujuan:** laporan yang dapat diaudit ulang.

Wajib memuat:
1. Daftar perubahan: file dibuat, diubah, dan tidak disentuh; file dihapus (jika ada).
2. **Perintah yang benar-benar dijalankan** dengan **output aktual** (verbatim).
3. Pemisahan jelas antara perintah **direncanakan** vs **dijalankan**.
4. Ringkasan diff (tracked) dan isi file untracked yang diperiksa eksplisit.
5. Cara menjalankan/memverifikasi hasil (mis. cara run server, cara jalankan test).
6. Keterbatasan dan risiko yang masih tersisa.
7. Konfirmasi tidak ada commit/push/deployment yang dilakukan (kecuali diminta).

Template singkat:

```
## Perubahan
- Dibuat: <file>
- Diubah: <file>
- Tidak disentuh: <file/area>

## Perintah dijalankan (output aktual)
$ <command>
<output>
$ <command>
<output>

## Verifikasi diff / untracked
<ringkasan atau isi file>

## Cara menjalankan
<langkah>

## Keterbatasan / risiko
- <item>
```

---

## 8. Protocol Bukti & Keterlacakan (Evidence & Traceability)

Bagian ini memperkuat keterlacakan: mendeteksi perubahan pada file tracked maupun
untracked, merekam perintah verifikasi beserta exit code dan hasil aktual, serta
membedakan jenis bukti. **Catatan penting:** `git diff` yang kosong **bukan** bukti
bahwa workspace tidak berubah — pada repo tanpa commit (unborn branch) seluruh file
untracked dan `git diff` selalu kosong. Karena itu diff saja tidak cukup.

### 8.1 Mendeteksi perubahan tracked & untracked

Jangan andalkan `git diff` saja. Urutan pemeriksaan yang aman:

1. Status lengkap termasuk file untracked per-file:
   `git status --porcelain=v1 -uall`
2. Daftar tracked: `git ls-files`
3. Daftar untracked: `git ls-files --others --exclude-standard`
4. **Manifes hash** seluruh file sebagai snapshot (lihat 8.2). Bandingkan manifes
   "sebelum" dan "sesudah"; inilah bukti perubahan yang tidak bergantung diff.

### 8.2 Snapshot integritas dengan hash (SHA-256)

- Windows PowerShell:
  `Get-ChildItem -Recurse -File | ForEach-Object { (Get-FileHash $_.FullName -Algorithm SHA256).Hash }`
- Alternatif lintas platform (konten Git): `git hash-object <file>`
- Simpan manifes ke `docs/evidence/<TASK>-baseline.sha256` sebelum perubahan dan
  `docs/evidence/<TASK>-final.sha256` sesudahnya, lalu bandingkan.

> **Batas kejujuran:** hash SHA-256 hanya membuktikan bahwa isi file **identik**
> dengan saat snapshot dibuat. Hash **bukan** bukti bahwa isi file itu benar,
> aman, atau memenuhi requirement. Hash tidak mendeteksi file yang belum pernah
> di-snapshot, dan bukan pengganti review manusia.

### 8.3 Merekam perintah, exit code, dan hasil aktual

Simpan ke `docs/evidence/<TASK>-EVIDENCE.md` (atau lampirkan verbatim di laporan):

```
$ <perintah>
exit=<kode>
<output aktual apa adanya>
```

- Catat exit code secara eksplisit (mis. `$LASTEXITCODE` di PowerShell).
- Tempel output **verbatim**; jangan meringkas dari ingatan.
- Bila perintah gagal, tampilkan error aslinya (workflow §0 butir 6).

### 8.4 Membedakan bukti langsung, laporan agent, dan asumsi

Setiap pernyataan wajib diberi label:

| Label | Arti | Contoh |
|---|---|---|
| **Bukti langsung** | Dapat direproduksi/diinspeksi pada sesi ini | Output `go test` yang baru dijalankan; isi file yang dibaca via Read |
| **Laporan agent** | Klaim dari sesi/laporan sebelumnya yang tidak tersimpan | "Task 2 melaporkan test lulus 0.004s" |
| **Asumsi** | Belum divalidasi, dugaan, atau ekspektasi | "Sepertinya regex ini menangani unicode" |

Larangan: menyajikan **laporan agent** atau **asumsi** sebagai **bukti langsung**.

### 8.5 Membandingkan kondisi awal dan akhir tanpa bergantung `git diff`

1. Ambil manifes hash awal (§8.2) sebelum mengubah apa pun.
2. Lakukan perubahan.
3. Ambil manifes hash akhir.
4. Bandingkan: file baru, file terhapus, file berubah (hash berbeda).
5. Untuk file berubah, baca isi eksplisit (Read) dan cantumkan ringkasan.

Dengan cara ini, perubahan pada repo tanpa commit tetap terdeteksi dan terlacak.

### 8.6 Melindungi file pengguna & approval gate

- Sebelum menyentuh file, tentukan kepemilikannya: **dibuat sesi ini** atau
  **file pengguna/eksisting**.
- File pengguna: **jangan** dihapus/ditimpa tanpa persetujuan eksplisit.
- Operasi berisiko (hapus/timpa, migrasi DB production, deployment/push, perubahan
  di luar scope, dependency besar) → **berhenti, minta persetujuan** (lihat
  "Aturan Persetujuan"). Selama belum disetujui, statusnya **direncanakan**.
- Bila ada ketidakpastian kepemilikan/scope: berhenti dan tanyakan, jangan menebak.

---

## Aturan Persetujuan (Approval Gates)

Agent **wajib berhenti dan meminta persetujuan** sebelum:
- Menghapus atau menimpa file/data, terutama yang bukan hasil sesi ini.
- Menjalankan migrasi/DDL/query terhadap database production.
- Deployment, rilis, publish, atau push ke remote.
- Mengubah file di luar scope tugas atau di luar workspace.
- Menambah/mengganti dependency besar atau mengubah konfigurasi keamanan.

> Sampai persetujuan diberikan, langkah tersebut berstatus **direncanakan**, bukan
> dijalankan.

---

## Batasan Kemampuan Agent (Kejujuran Kapabilitas)

- Agent hanya dapat melakukan hal yang didukung **tool yang benar-benar tersedia**
  pada sesi berjalan. Tool yang tidak tersedia tidak boleh diklaim.
- Menjalankan server/menunggu proses lama bergantung pada timeout shell; proses
  latar belakang terbatas dan harus dimatikan/dibersihkan setelah selesai.
- Agent tidak memiliki akses langsung ke layanan eksternal (cloud, DB production,
  registry privat) kecuali tool dan kredensialnya tersedia dan diizinkan.
- Tanpa perintah, agent **tidak** melakukan commit/push/deployment.
- Output harus selalu menampilkan error apa adanya; bila tool gagal, laporkan
  pesan error yang sesungguhnya, bukan interpretasi.

---

## Ringkasan Alur (Quick Checklist)

- [ ] Requirement dipahami & batasan dicatat.
- [ ] Workspace + status Git diaudit; file relevan dibaca langsung.
- [ ] Rencana + daftar file + strategi verifikasi disetujui.
- [ ] Implementasi minimal, sesuai konvensi, tanpa dependency tak sah.
- [ ] Test dijalankan; error aktual dianalisis; perbaikan & tes ulang.
- [ ] Diff + file untracked ditinjau eksplisit.
- [ ] Security & regresi diperiksa.
- [ ] Manifes hash awal/akhir diambil & dibandingkan (§8.2, §8.5).
- [ ] Perintah, exit code, dan output aktual direkam (§8.3).
- [ ] Setiap klaim diberi label bukti langsung / laporan agent / asumsi (§8.4).
- [ ] Final report: perubahan, perintah nyata, hasil aktual, keterbatasan.
- [ ] Approval gate dipatuhi; tidak ada commit/push tanpa izin.

---

# Orchestrator — Sentinel Auto-Agentic Orchestrator (MVP)

Bagian ini mendokumentasikan **orchestrator Python** yang menjalankan OpenCode
(`opencode run`) secara non-interaktif terhadap repository target (default
workspace ini; `repo_root` dapat dioverride) dengan kontrol keamanan yang
ditegakkan **di luar prompt model** (di kode Python).

## Ringkasan alur

`audit → plan → implement → test → review → bounded fix (maks. 3) → report`

- `read_only`: tanpa menulis file. Tahap: `audit → analyze → test → review → report`.
- `implement`: menulis hanya pada `allowed_paths`. Tahap:
  `audit → plan → validate_plan → implement → test → review → report`.
  Perbaikan (fix) dijalankan internal di dalam tahap `test`, maksimum
  `max_fix_attempts` (default 3).
- **Review independen (implement):** setelah test, model **reviewer** (berbeda
  dari model coder, read-only, tanpa edit/bash/web) meninjau diff + hasil QA nyata
  dan mengembalikan JSON `PASS|FAIL|BLOCKED` + temuan. Temuan → dikembalikan ke
  coder untuk fix → QA & review diulang, maks. `review.max_rounds` (default 3).
  Verdict (beserta bukti per round) ditulis ke
  `runs/<id>/06-review-roundN.json` dan `runs/<id>/07-review-verdict.json`.
  `PASS` → lanjut; `FAIL` → task gagal; `BLOCKED` (bukti kurang/JSON tidak valid/
  reviewer menyentuh file/out-of-scope saat fix) → task berhenti.

## Prasyarat

- Python 3.10+ (diverifikasi pada 3.13.7).
- OpenCode CLI di `PATH` (diverifikasi 1.19.0). Perintah non-interaktif:
  `opencode run`.
- PyYAML (satu-satunya dependency, karena task didefinisikan dalam YAML):
  `python -m pip install pyyaml`
- Git di `PATH` (hanya operasi baca).

## Struktur file

```
orchestrator/
  runner.py     # entry point + loop task queue
  opencode.py   # eksekusi subprocess aman + timeout + capture
  policy.py     # batas repo, scope, cek keamanan perintah, redaksi secret
  workflow.py   # tahapan audit→plan→implement→test→review→bounded fix
  gitutil.py    # helper Git read-only + manifes hash (change attribution)
  state.py      # state resumable (tulis atomik)
  tasks.py      # loader YAML
  config.py     # loader konfigurasi
orchestrator.config.json
tasks/sentinel/*.yaml
state/state.json
runs/<run_id>/...
tests/          # self-test orchestrator (unittest)
```

## Setup (Windows PowerShell)

```
cd D:\AI\free-agentic-lab
python -m pip install pyyaml
python -m unittest discover -s tests -t . -v
```

## Perintah

```
# lihat antrian & status task
python -m orchestrator.runner --list
python -m orchestrator.runner --status

# jalankan satu task berikutnya (berhenti setelah satu task)
python -m orchestrator.runner --once

# jalankan task tertentu
python -m orchestrator.runner --task sentinel-stage0-verification

# meninjau rencana tanpa memanggil model
python -m orchestrator.runner --task <id> --dry-run

# memberi persetujuan eksplisit per aksi (implement/dependency-install/db-migration/start-service)
python -m orchestrator.runner --task <id> --approve implement
python -m orchestrator.runner --task <id> --approve dependency-install

# melanjutkan run yang terputus (WAJIB; keputusan pemulihan eksplisit)
python -m orchestrator.runner --once --resume

# menjalankan ulang task yang sudah selesai (WAJIB --force)
python -m orchestrator.runner --task <id> --force

# override repo / batas fix
python -m orchestrator.runner --repo D:\Naufal\Example\Pentest\sentinel --max-fix-attempts 3
```

Exit code: `0` sukses/antrian kosong, `1` ada task gagal, `2` berhenti di
approval/blocked/perlu tinjauan, `3` error konfigurasi/state/lock,
`4` berhenti karena **provider limit** (lanjutkan dengan `--resume`).

## Konfigurasi (`orchestrator.config.json`)

- `repo_root`: root repository yang dikerjakan (default `"."` = workspace
  ini; dapat dioverride dengan `--repo`).
- `max_fix_attempts`: batas perbaikan per task (default 3).
- `ignore_dirs` / `ignore_globs`: dikecualikan dari manifes (build artifacts
  seperti `.next`, `node_modules`, `test-results`, `*.tsbuildinfo`,
  `next-env.d.ts`).
- `protected_paths`: file yang **tidak pernah** boleh diubah (`.env`, `*.pem`,
  `*.key`, `secrets/**`, `deployments/**`, `migrations/**`, `scanner/**`,
  `internal/**`).
- `opencode`: `command`, `model` (default `opencode/big-pickle`),
  `timeout_seconds`, `agent`, `extra_args`, `agent_config` (default
  `orchestrator/opencode.agents.json`, dikirim lewat env `OPENCODE_CONFIG`).
  `--auto` **tidak pernah** dipakai.
- `roles`: pemisahan model per peran.
  - `coder`: `model` (primer) + `fallback_models` (dipakai saat provider limit).
  - `reviewer`: `model` **wajib berbeda** dari semua model coder (independence);
    agent `orchestrator-reviewer` = read-only (edit/bash/webfetch/websearch deny).
  - `qa`: `commands` — perintah verifikasi lokal deterministik (tanpa model),
    dijalankan lewat `check_command_safety`.
- `review`: `enabled`, `max_rounds` (default 3), `require_independent_model`
  (default true).
- `provider_limits`: `transient_retries` (ulang per model saat sinyal provider
  limit) dan `backoff_seconds`. Bila semua model per peran habis →
  run **di-checkpoint & di-pause** (exit `4`), bukan menuangkan beban ke model
  yang sama/berganti diam-diam.
- `safe_commands`: regex tambahan untuk allowlist perintah (selain allowlist
  bawaan di `policy`). Perintah tak dikenal/ambigu tetap **deny** (fail-closed).

## Format task YAML

```yaml
id: my-task
title: "Judul"
mode: read_only          # read_only | implement
priority: 100
approval_required: false # true → butuh --approve sebelum implement
goal: |
  Instruksi tugas.
allowed_paths: ["apps/web/**"]   # wajib kosong untuk read_only
forbidden_paths: []              # opsional, ditambah protected_paths
tests:
  - name: typecheck
    command: "pnpm run typecheck"
    cwd: "apps/web"
    timeout_seconds: 600
prompts:
  analyze: |
    (opsional) prompt kustom per tahap: analyze/plan/implement/fix
```

## Model keamanan (ditegakkan di kode, bukan di prompt)

1. **Batas repo & scope.** `policy` memvalidasi task; perubahan file dihitung
   lewat manifes SHA-256 dan diklasifikasi `allowed`/`out-of-scope`/`protected`.
   Pelanggaran → task **blocked**, tanpa menimpa/menghapus apa pun.
2. **Atribusi perubahan.** Manifes sebelum/sesudah membedakan perubahan yang
   sudah ada sebelumnya dari perubahan selama run (tidak bergantung `git diff`,
   tetap benar pada repo tanpa commit).
3. **Tanpa operasi Git destruktif.** Tidak ada `commit/push/reset/clean/stash/
   checkout/add`; `policy` menolak perintah tersebut. HEAD diperiksa sebelum vs
   sesudah; bila berubah → blocked.
4. **Approval gate (per aksi).** Task `approval_required` memerlukan `--approve
   implement`; perintah test ber-kategori approval memerlukan `--approve
   dependency-install|db-migration|start-service`. Persetujuan **sekali pakai**,
   terikat ke (task, aksi, run_id). Persetujuan untuk satu aksi **tidak**
   mengizinkan aksi lain.
5. **Tanpa scanning/pentest target.** Perintah `nmap/sqlmap/nc/dll.` ditolak.
6. **Tanpa secret di task/log.** Nilai menyerupai secret diredaksi; task dengan
   key/nilai menyerupai secret ditolak saat validasi.
7. **Bukti nyata.** Test dijalankan oleh orchestrator; exit code & output
   disimpan sebagai bukti. Klaim model "test lulus" tidak pernah dipakai.
8. **Fail-closed drift guard per tahap.** Setelah setiap tahap, manifes
   dihitung ulang dan dibandingkan dengan baseline (termasuk file untracked,
   karena `git diff` tidak cukup pada repo tanpa commit). Mode `read_only`:
   **perubahan apa pun** → blocked. Mode `implement`: hanya perubahan yang
   terklasifikasi `allowed` dalam scope yang dibiarkan; perubahan di luar scope
   → blocked. HEAD yang berubah → blocked. Tidak ada auto-restore/penghapusan.
   Catatan batas: deteksi berjalan **setelah** tahap — ini "deteksi+block",
   bukan sandbox OS.
9. **Satu runner aktif.** Runner mengunci state (file lock PID). Bila lock sudah
   dipegang, proses kedua berhenti dengan exit code `3` agar tidak ada dua
   runner yang menulis run yang sama.
10. **Perintah fail-closed.** `check_command_safety` mengklasifikasi perintah
    dalam urutan: deny-pattern → shell-metachar (`; & | < > ` $ { } ( )`) → 
    approval → allowlist eksplisit → **deny**. Tidak ada asumsi aman: perintah
    tak dikenal maupun yang bisa menyelundupkan perintah lain selalu ditolak.
    Hanya perintah dari field `tests` task (yang sudah melewati human review)
    yang dieksekusi; output model tidak pernah dieksekusi.
11. **OpenCode tanpa auto-approve.** `--auto` tidak pernah dipakai, sehingga
    permission/tool-call yang butuh persetujuan muncul sebagai exit non-0 —
    dianggap kegagalan, bukan bypass. Agent coder maupun reviewer dibatasi lewat
    `orchestrator/opencode.agents.json`.
12. **Reviewer read-only dua lapis.** Agent reviewer men-deny edit/bash/
    webfetch/websearch, dan workflow memotret manifes sebelum/sesudah panggilan
    reviewer — perubahan apa pun oleh reviewer → `BLOCKED`.
13. **Model per peran, tanpa silent switch.** Reviewer tidak pernah memakai model
    coder; provider-limit memakai fallback, lalu retry transien berbatas, lalu
    **pause dengan checkpoint** (bukan loop abadi, bukan ganti model diam-diam).

## Recovery / resume

- State di `state/state.json` (tulis atomik). Run aktif menyimpan `run_id`,
  stage, jumlah `attempts`, `last_exit_code`, dan status.
- Run yang terputus **tidak** dilanjutkan otomatis: runner mewajibkan
  `--resume` sebagai keputusan pemulihan eksplisit. Sebelum lanjut, manifes
  segar dibandingkan dengan checkpoint terakhir
  `runs/<id>/manifest-latest.json` (ditulis setelah tiap tahap; bila belum ada,
  dipakai `manifest-baseline.json`). Bila ada drift, run ditandai `needs_review`
  dan berhenti dengan exit code `2`. Tahap yang sudah selesai (penanda
  `runs/<id>/stages/<stage>.done`) dilewati.
- **Provider limit = named pause.** Bila semua model per peran mencapai
  provider limit, run disimpan berstatus `paused_provider_limit` bersama
  `runs/<id>/terminal.json` (stage, reason, models_tried) dan exit code `4`;
  resume dilakukan dengan `--resume` setelah limit pulih.
- Task yang sudah `completed` **tidak** dijalankan ulang diam-diam; perlu
  `--force`.
- State korup **tidak** di-reset diam-diam: loader melempar error dan berhenti.
- Bila ragu soal scope/integritas/atribusi → `blocked`.

## Keterbatasan (MVP)

- Tidak ada sandbox OS; pencegahan berbasis penolakan perintah + deteksi
  perubahan. Model tetap berjalan dengan `--dir` repo.
- Orchestrator memvalidasi perubahan **setelah** tahap, bukan mencegah write
  tingkat OS.
- `validate_plan` hanya menandai penyebutan perintah terlarang untuk ditinjau
  manusia (bukan auto-block, karena bisa berupa larangan negatif).
- Belum ada notifikasi eksternal; kontrol lewat CLI.

## Cara menguji orchestrator

```
python -m unittest discover -s tests -t . -v
```

Menguji: pemilihan antrian & persistensi state, resume pasca-interupsi, batas
retry, blokir pelanggaran scope, penanganan kegagalan/timeout OpenCode, dan
pembuatan bukti/log, serta tidak ada commit/operasi Git destruktif.
