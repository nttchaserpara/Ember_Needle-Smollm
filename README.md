# EmberOS — local OS agent

EmberOS adalah prototipe agent untuk menjalankan tool komputer dari permintaan
bahasa Inggris. **Needle** memilih tool dan argumennya; **SmolLM2-135M-Instruct
Q4_K_M**, melalui llama.cpp, digunakan untuk ringkasan dokumen dan sebagian
balasan setelah eksekusi. Inferensi model berjalan lokal dengan CPU.

Proyek ini mengeksplorasi agent ringan untuk Windows dan Linux/Raspberry Pi.
Ini masih prototipe: keberhasilan routing bergantung pada kalimat, platform,
dan tool yang tersedia. Belum ada klaim bahwa seluruh fitur andal pada Pi.

**Mulai dari sini:** [panduan review mentor](docs/REVIEW_GUIDE.md) ·
[panduan penggunaan](docs/USAGE.md) · [arsitektur](docs/ARCHITECTURE.md) ·
[pengembangan dan evaluasi](CONTRIBUTING.md) · [instalasi APT](#instalasi-dengan-apt-linux--raspberry-pi)

## Kemampuan dan batasnya

| Area | Implementasi saat ini | Batas yang perlu diperhatikan |
| --- | --- | --- |
| Sistem dan file | Informasi disk/RAM/OS, daftar folder, baca dan pencarian file | File dan informasi berasal dari komputer backend |
| Dokumen | Ringkasan TXT, Markdown, CSV, PDF teks, DOCX, XLSX, PPTX | Format tambahan memerlukan dependensi; `.doc` dan OCR belum didukung |
| Tasks dan notes | Penyimpanan lokal dan beberapa aksi undo | Dukungan undo terbatas pada aksi tertentu, satu slot, tanpa redo |
| Riwayat percakapan | SQLite lokal; `/memory` dan pencarian kata | Pencarian literal; recall melalui bahasa alami masih tidak andal |
| Balasan natural | SmolLM menyusun ulang hasil tool yang memenuhi syarat | Bisa kembali ke respons asli; bukan chatbot umum |
| Desktop dan GUI | CLI, web GUI, serta companion untuk backend Pi | Audio, layar, dan launcher bergantung pada OS/dependensi; Pi headless tidak memiliki desktop lokal |

Jika generasi ringkasan gagal, Ember dapat menampilkan **extractive summary**
dari kalimat sumber. Itu adalah fallback yang diberi label, bukan keberhasilan
generasi model. Riwayat dan konteks saat inferensi tidak melatih bobot model.

## Instalasi dengan APT (Linux / Raspberry Pi)

Paket tersedia untuk Raspberry Pi OS/Debian 12 **bookworm** (`arm64`/`amd64`),
Ubuntu 22.04 **jammy** (`amd64`), dan Ubuntu 24.04 **noble** (`amd64`).
Pi harus memakai OS 64-bit; paket `armhf`/32-bit belum tersedia.
Jalankan perintah berikut di terminal Linux/Pi, termasuk melalui SSH.

**Setup pertama kali:** EmberOS memakai repository APT sendiri, jadi tambahkan
repository tersebut sebelum menjalankan `sudo apt install emberos`.

```bash
sudo apt update
sudo apt install curl ca-certificates
curl -fsSL https://nttchaserpara.github.io/Ember_Needle-Smollm/install-apt.sh \
  -o /tmp/emberos-install-apt.sh && sudo sh /tmp/emberos-install-apt.sh
sudo apt update
apt-cache policy emberos
sudo apt install emberos
```

Lanjutkan instalasi setelah setup repository dan `apt update` berhasil.
`apt-cache policy emberos` harus menampilkan versi pada `Candidate`, bukan
`(none)`. Paket sudah menyertakan environment Python, Needle, model SmolLM2,
dan runtime llama.cpp, sehingga tidak perlu clone repo atau setup venv/model
secara terpisah. Ukuran unduhannya lebih besar karena model ikut dibundel.

Jalankan sebagai pengguna biasa setelah instalasi:

```bash
emberos --version
emberos-cli
```

`emberos` membuka GUI pada desktop Linux. Untuk Pi headless, gunakan
`emberos-cli` melalui SSH atau ikuti [setup companion](docs/setup/DEBIAN_PACKAGE.md)
untuk membuka GUI dari komputer lain.

Untuk memperbarui instalasi yang repository-nya sudah dikonfigurasi:

```bash
sudo apt update
sudo apt install --only-upgrade emberos
```

APT memasang **rilis paket yang dipublikasikan**, bukan otomatis commit terbaru
di `main`. Jika muncul `Unable to locate package emberos`, periksa setup
repository, hasil `apt update`, codename OS, dan arsitektur sesuai
[panduan APT lengkap](docs/setup/DEBIAN_PACKAGE.md#troubleshooting-installation).

## Menjalankan dari source

Gunakan Git dan Python **3.11.8 atau lebih baru** sebagai baseline setup
di panduan ini. Instalasi awal membutuhkan internet untuk dependensi dan
komponen native Needle; setup LLM juga mengunduh model/runtime.
Jalankan semua perintah shell dari root repo.

### Windows / PowerShell

```powershell
git clone https://github.com/nttchaserpara/Ember_Needle-Smollm.git
cd Ember_Needle-Smollm
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe run_ember.py
```

Ini cukup untuk mulai mencoba tool dasar. Untuk ringkasan dengan SmolLM,
keluar dari Ember dengan `exit`, lalu jalankan:

```powershell
.\venv\Scripts\python.exe scripts/setup_local_llm.py
.\venv\Scripts\python.exe run_ember.py
```

Setup runtime otomatis di Windows ditujukan untuk x64. Untuk format dokumen
selain teks, pasang `requirements-documents.txt`; untuk audio Windows,
lihat [setup audio](docs/setup/AUDIO_TOOLS.md).

### Linux / Raspberry Pi

Untuk Pi, gunakan Raspberry Pi OS Lite **64-bit** (`aarch64`) dan siapkan
`python3-venv`. Dari checkout baru:

```bash
git clone https://github.com/nttchaserpara/Ember_Needle-Smollm.git
cd Ember_Needle-Smollm
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
./venv/bin/python run_ember.py
```

Untuk SmolLM, ikuti [setup model dan runtime Linux](docs/setup/LOCAL_LLM.md#pi-zero-2-w-setup-after-cloning).
File GGUF bisa dipakai lintas platform, tetapi executable dan venv Windows
tidak dapat dipakai sebagai runtime Linux. Build runtime di Pi Zero 2 W dapat
memakan waktu berjam-jam. Alternatif instalasi paket dan companion dijelaskan
di [panduan Debian/APT](docs/setup/DEBIAN_PACKAGE.md).

## Demo singkat

Setelah terlihat prompt `You>`, masukkan satu permintaan, tunggu hasilnya,
lalu lanjutkan. Perintah berikut bukan perintah PowerShell/Bash:

```text
How much free disk space do I have?
List the files and folders in my home directory.
/memory
/memory search disk space
exit
```

Untuk demo ringkasan, siapkan model/runtime lebih dahulu, buka Ember kembali,
lalu gunakan fixture yang disertakan:

```text
Summarize "experiments/fixtures/sample-summary.txt"
```

Periksa apakah hasilnya ringkasan model, fallback ekstraktif, atau penolakan
routing. [Panduan penggunaan](docs/USAGE.md) menjelaskan hasil dan pemecahan
masalah, termasuk membedakan perintah shell dari prompt Ember.

Untuk GUI lokal dari source, jalankan `python web_ember.py` dengan interpreter
venv dan buka alamat bertoken yang dicetak terminal. Detail koneksi headless
ada di [panduan companion](desktop/companion/README.md).

## Peta repository

```text
run_ember.py           CLI dan penanganan request bersama
web_ember.py           Web GUI dan pembatasan tool untuk web
needle_router.py      Skema, pemilihan, dan validasi routing tool
emberos/              Registry, memory, outcomes, replies, undo, platform
use_cases/            Implementasi tool dan worker LLM lokal
scripts/              Setup, regression checks, dan evaluasi
experiments/          Kasus evaluasi JSON dan fixture teks sintetis
models/               Manifest model dan petunjuk (tanpa bobot model)
desktop/companion/    Companion untuk discovery dan koneksi SSH ke backend
packaging/            Build paket Debian dan publikasi APT
docs/                 Panduan penggunaan, review, arsitektur, dan setup
.github/workflows/    Build dan smoke test paket/companion
```

`bench_short_response.py` di root adalah entry point kompatibilitas; implementasi
benchmark berada di `scripts/bench_short_response.py`.

## Data lokal dan cakupan repo

Repo berisi source code, panduan, konfigurasi build, manifest, serta fixture
dan skrip evaluasi yang bisa dibagikan. Venv, model hasil unduhan, runtime,
database percakapan, log, build output, dan catatan pribadi dikecualikan lewat
[`.gitignore`](.gitignore). Lihat [aturan berbagi file](docs/REPOSITORY_HYGIENE.md).

Pada setup source, history tersimpan di `data/conversation.sqlite3`.
`/memory clear` hanya menghapus history, bukan notes, tasks, log, atau backup.
Gunakan `EMBER_MEMORY=0` untuk menonaktifkan history; detailnya ada di
[panduan memory](docs/CONVERSATION_MEMORY.md). Integrasi Google Docs bersifat
opsional dan memerlukan OAuth serta jaringan.

## Dokumentasi lanjutan

- [Review mentor dan urutan demo](docs/REVIEW_GUIDE.md)
- [Arsitektur dan alur request](docs/ARCHITECTURE.md)
- [Menjalankan regression checks dan membaca evaluasi](CONTRIBUTING.md)
- [Model lokal dan benchmark](docs/setup/LOCAL_LLM.md)
- [Dokumen dan Google Docs](docs/setup/DOCUMENT_TOOLS.md)
- [Balasan natural](docs/setup/NATURAL_REPLIES.md) dan [undo](docs/setup/UNDO.md)
- [Paket Debian/APT](docs/setup/DEBIAN_PACKAGE.md)

Hasil uji routing, kualitas ringkasan, latensi, dan RAM perlu dinilai terpisah.
Lolos tes dengan mock tidak membuktikan akurasi model atau performa perangkat Pi.
