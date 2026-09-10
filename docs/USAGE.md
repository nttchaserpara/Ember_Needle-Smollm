# Panduan penggunaan terminal Ember

Diperbarui: 10 September 2026. Panduan ini mengikuti kemampuan yang sudah ada,
termasuk batasan yang masih ditemukan. Semua prompt untuk Ember memakai bahasa
Inggris. Jalankan satu permintaan, tunggu hasilnya, lalu lanjutkan.

Langsung ke: [Windows](#2-menjalankan-di-windows--terminal-vs-code),
[prompt sederhana](#3-prompt-sederhana-untuk-mulai),
[summarize](#4-summarize-dokumen), [memory](#5-memory-percakapan),
[Pi/SSH](#7-menjalankan-di-pi-melalui-ssh).

## 1. Bedakan terminal dengan prompt Ember

- **PowerShell Windows** menampilkan awalan seperti `PS C:\...>`.
- **Shell Pi melalui SSH** menampilkan awalan seperti `natta@emberos-zero2w:~ $`.
- **Ember yang sedang berjalan** menampilkan `You>`.

Perintah `python`, `pip`, `cd`, setup, dan benchmark dijalankan di shell.
Kalimat permintaan dan `/memory` dimasukkan di `You>`. Salin hanya isi blok
perintah, tanpa awalan terminal, `You>`, hasil, atau traceback.

Ketik berikut di Ember untuk kembali ke shell:

```text
exit
```

Setiap pesan sekarang diproses sebagai permintaan baru. Tidak ada menu sesi
1/2/3 atau kewajiban menjawab menu sebelum mengirim permintaan berikutnya.

## 2. Menjalankan di Windows / terminal VS Code

Buka folder proyek di VS Code, lalu buka **Terminal > New Terminal** dengan
PowerShell. Pastikan terminal berada di folder yang berisi `run_ember.py`:

```powershell
Get-Location
Test-Path .\run_ember.py
```

Hasil pemeriksaan kedua harus `True`. Jika `False`, pindah ke folder proyek
yang benar dahulu. Gunakan tanda kutip pada path yang mengandung spasi atau `&`.

**Setup pertama kali**, jika folder `venv` belum ada:

```powershell
python -m venv venv
```

Pasang kebutuhan dasar menggunakan interpreter proyek:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

**Setiap ingin menjalankan Ember:**

```powershell
.\venv\Scripts\python.exe run_ember.py
```

Perintah ini tidak memerlukan aktivasi venv. Kalau terminal sudah menampilkan
`(venv)` dari lingkungan proyek ini, `python run_ember.py` juga bisa dipakai.
Pemakaian pertama Needle memerlukan koneksi untuk mengambil komponen native-nya.
Model SmolLM2 belum diperlukan untuk mencoba disk, tasks, atau penyimpanan memory.

Setelah memperbarui kode, keluar dari Ember dan jalankan lagi. Pasang ulang
requirements yang bersangkutan jika dependensinya berubah; venv tidak perlu
dibuat ulang setiap menjalankan aplikasi.

## 3. Prompt sederhana untuk mulai

Masukkan di `You>`, satu per satu:

```text
How much free disk space do I have?
```

```text
disk space
```

Keduanya memilih `disk_usage` dalam pengujian Windows terbaru. Hasilnya adalah
kondisi disk komputer yang menjalankan Ember, dengan baris tool di bawahnya.

Untuk melihat daftar tugas yang sudah tersimpan:

```text
Show my pending tasks
```

Daftar bisa kosong. Routing tugas pernah berhasil pada evaluasi lokal, tetapi
variasi kalimat dan perilaku di Pi tetap perlu diuji. Sapaan seperti `hello`
belum berarti tersedia chatbot percakapan umum dengan respons LLM.

## 4. Summarize dokumen

### Persiapan Windows, sebelum masuk ke Ember

Jalankan dari root proyek. Bila masih di `You>`, ketik `exit` dahulu:

```powershell
.\venv\Scripts\python.exe scripts/setup_local_llm.py
```

Script berada di **`scripts/`**, bukan langsung di root. Script ini mengambil
dan memverifikasi model serta runtime Windows x64. Lokasinya:

- `models/SmolLM2-135M-Instruct-Q4_K_M.gguf`
- `runtimes/llama.cpp/`

Setup cukup dilakukan saat menyiapkan instalasi atau memperbaiki file yang
belum tersedia. Ember mengelola `llama-server` untuk setiap pekerjaan dokumen;
tidak perlu menjalankan server terpisah secara manual.

Untuk `.txt`, requirements dasar cukup. Untuk PDF, Word, dan spreadsheet,
pasang tambahan berikut sebelum menjalankan Ember lagi:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-documents.txt
.\venv\Scripts\python.exe run_ember.py
```

Jika hanya mencoba `.txt`, cukup jalankan kembali `run_ember.py`.

### Coba file contoh yang sudah ada

Di `You>`, gunakan path relatif berikut ketika Ember dimulai dari root proyek:

```text
Summarize "experiments/fixtures/sample-summary.txt"
```

File pendek ini berisi laporan pengujian audio: pengujian Jumat, sepuluh
pemeriksaan lulus, dan pengujian berikutnya Senin. Isinya tidak perlu dibuat
atau ditempelkan sebagai beberapa pesan ke Ember.

Untuk mencoba dokumen yang lebih panjang:

```text
Summarize "experiments/fixtures/pi-long-report.txt"
```

Ini laporan fiktif untuk menguji proses dan kualitas ringkasan. Hasil generasi
bisa mencapai batas token dan memakai fallback; belum menjadi contoh kualitas
ringkasan panjang yang dinyatakan lulus.

### Coba file milikmu

Ganti path contoh di bawah dengan file yang benar-benar ada:

```text
Summarize "D:\Downloads\my-report.txt"
```

```text
Summarize "D:\Downloads\my-document.pdf"
```

Path dengan spasi tetap harus berada dalam tanda kutip. Untuk mengambil path
absolut file contoh di PowerShell, lakukan **sebelum membuka Ember**:

```powershell
(Resolve-Path .\experiments\fixtures\sample-summary.txt).Path
```

Tempel path yang dihasilkan setelah kata `Summarize`, di dalam tanda kutip.
PDF hasil scan tanpa teks membutuhkan OCR; pemasangan requirements dokumen
belum menyediakan alur OCR tersebut.

### Memahami hasil summarize

- Ringkasan model: worker lokal menjalankan SmolLM2 dan berhenti setelah job.
- `Extractive summary`: fallback memilih kalimat sumber; ini bukan bukti
  bahwa generasi LLM berhasil.
- `unresolved_tool_request`: router menolak permintaan sebelum tool berjalan.
- Pesan file tidak ditemukan atau OCR dibutuhkan: dokumen belum dapat diringkas.

Jika routing gagal, uji tool secara langsung dari **shell**, setelah `exit`:

```powershell
.\venv\Scripts\python.exe scripts/benchmark_local_llm.py --direct-tool --document experiments/fixtures/sample-summary.txt
```

Mode ini benar-benar mencoba summarize, tetapi melewati Needle. RAM-nya tidak
mewakili keseluruhan agent. Untuk benchmark melalui router:

```powershell
.\venv\Scripts\python.exe scripts/benchmark_local_llm.py --document experiments/fixtures/sample-summary.txt
```

Benchmark dokumen ini tidak membuka database percakapan CLI; ia menguji job
dokumen, bukan seluruh alur memory. Detail konfigurasi ada di
[panduan local LLM](setup/LOCAL_LLM.md).

## 5. Memory percakapan

Memory aktif secara default. Saat startup, pastikan ada pesan
`Local conversation history enabled`. Riwayat disimpan lokal di
`data/conversation.sqlite3` dan tetap ada setelah aplikasi ditutup.

### Percobaan pertama

Di `You>`, lakukan satu per satu:

```text
How much free disk space do I have?
```

```text
/memory search disk space
```

Hasil pencarian adalah catatan jawaban sebelumnya, lengkap dengan waktu dan
statusnya. Angka disk yang tersimpan bukan pembacaan kondisi disk baru.

Untuk melihat sampai lima catatan terbaru:

```text
/memory
```

Setelah mencoba summarize, pencarian judul file juga bisa dicoba:

```text
/memory search sample-summary
```

Untuk membuktikan penyimpanan bertahan, ketik `exit`, jalankan Ember lagi dengan
perintah Windows atau Pi, lalu ulangi `/memory search disk space`.

Pencarian ini mencocokkan semua kata topik secara literal. Belum ada pemahaman
sinonim atau konteks topik secara semantik. Kata dalam path yang pernah ditempel
sebagai pesan juga bisa cocok. Jika tidak ada kecocokan, hasil tidak diganti
dengan riwayat lain yang tidak terkait.

### Prompt memory bahasa alami: masih eksperimen

Kalimat berikut boleh diuji, tetapi pada pengujian terbaru masih ditolak atau
belum menghasilkan recall yang benar:

```text
What did we discuss about disk space?
```

```text
Show my recent conversations.
```

```text
Which documents did we talk about last time?
```

Gunakan `/memory` untuk mengakses data saat routing bahasa alami belum mampu
menangani kalimat tersebut. Penyimpanan riwayat tidak melatih bobot model.

### Pengaturan opsional

Untuk **menghapus seluruh riwayat percakapan**, masukkan di `You>` hanya jika
memang ingin mengosongkannya:

```text
/memory clear
```

Ini tidak menghapus notes, tasks, atau log tool yang disimpan terpisah.

Jika startup menyebut memory dinonaktifkan, keluar dari Ember. Di PowerShell,
aktifkan untuk proses berikutnya:

```powershell
$env:EMBER_MEMORY = '1'
.\venv\Scripts\python.exe run_ember.py
```

Di Pi:

```bash
EMBER_MEMORY=1 ./venv/bin/python run_ember.py
```

Ganti `1` dengan `0` untuk menjalankan tanpa membaca/menyimpan history.

## 6. Fitur Windows dengan persiapan tambahan

### Audio

Keluar dari Ember, pasang requirements audio, lalu jalankan lagi:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-audio.txt
.\venv\Scripts\python.exe run_ember.py
```

Di `You>`:

```text
what is my current volume
```

Prompt berikut benar-benar mengubah volume:

```text
change my volume to 44
```

```text
turn up my volume by 12 steps
```

`44` adalah target persen; `12 steps` adalah langkah relatif. Lihat
[panduan audio](setup/AUDIO_TOOLS.md) untuk perilaku mute dan platformnya.

### Notes / dokumen

Pasang `requirements-documents.txt` seperti pada bagian summarize dan restart
Ember. Untuk mencoba membuat catatan di Windows:

```text
Create a note titled "Terminal test" with content "Check the report on Friday" in Notepad
```

Ini membuat catatan tersimpan dan file `.txt`, lalu mencoba membukanya di
Notepad. Notes terpisah dari memory percakapan. Routing masih dapat menolak
permintaan; periksa status dan path hasil sebelum menganggap catatan dibuat.

Pembuatan Excel dan koneksi Google Docs memerlukan persiapan yang dijelaskan
di [panduan dokumen](setup/DOCUMENT_TOOLS.md). Google Docs membutuhkan OAuth
terlebih dahulu; integrasi tersebut belum otomatis terhubung saat menjalankan
Ember. Notepad dan launcher Windows belum tersedia sebagai aplikasi di Pi headless.

## 7. Menjalankan di Pi melalui SSH

Dari terminal komputer yang digunakan untuk mengakses Pi, sambungkan SSH.
Ganti `USER_PI` dan `IP_PI` dengan akun serta alamat Pi milikmu:

```powershell
ssh USER_PI@IP_PI
```

Setelah awalan terminal menunjukkan akun Pi, jalankan perintah Linux berikut.
Sesuaikan folder jika hasil clone disimpan di lokasi lain:

```bash
cd ~/Ember_Needle-Smollm
pwd
ls run_ember.py
```

Untuk instalasi baru, ikuti [setup Pi](setup/LOCAL_LLM.md#pi-zero-2-w-setup-after-cloning)
terlebih dahulu: OS 64-bit, Python, venv, dan requirements dasar. Ringkasnya,
setelah paket sistem yang diperlukan tersedia:

```bash
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
```

Jika venv sudah siap, langsung jalankan:

```bash
./venv/bin/python run_ember.py
```

Prompt disk, `/memory`, dan file contoh di atas dapat dipakai untuk pengujian
Pi. File dan pembacaan sistem berasal dari Pi. Path `D:\Downloads\...` milik
Windows tidak tersedia di Pi; gunakan path relatif fixture atau path Linux
yang benar. Pi masih perlu validasi perangkat; ini belum merupakan port lengkap.

Untuk summarize dengan LLM, **keluar dari Ember dahulu** dan siapkan model:

```bash
./venv/bin/python scripts/setup_local_llm.py --model-only
```

Perintah itu hanya menyiapkan model. Pi juga memerlukan `llama-server` Linux
ARM64. Jika sudah memiliki build yang kompatibel, arahkan ke path sebenarnya:

```bash
export EMBER_LLAMA_SERVER="$HOME/projects/llama.cpp/build/bin/llama-server"
./venv/bin/python run_ember.py
```

Kalau belum memiliki runtime, ikuti langkah build pada panduan setup. Build di
Zero 2 W bisa memakan waktu berjam-jam. File GGUF dapat disalin dari Windows;
venv dan executable Windows tidak dapat digunakan sebagai runtime Linux.

Untuk benchmark di shell Pi:

```bash
./venv/bin/python scripts/benchmark_local_llm.py --document experiments/fixtures/sample-summary.txt
```

Tambahkan `--direct-tool` bila ingin mengisolasi tool dari routing, dengan
batasan pengukuran yang dijelaskan pada bagian summarize.

### Hasil percobaan Pi, 10 September 2026

Pada percobaan pengguna, disk dan perintah `/memory` berhasil. Prompt summarize
file pendek ditolak dengan confidence `0.28`; tool belum berjalan dan tidak ada
child process yang teramati. Ini belum menguji generasi SmolLM2 atau membuktikan
model/runtime sudah siap. Startup pertama juga mengunduh komponen Needle;
durasi startup tersebut tidak mewakili startup dengan cache yang sudah tersedia.

Jika mengalami hasil yang sama, ketik `exit` di Ember, lalu jalankan kedua
perintah berikut **di shell Pi**, satu per satu:

```bash
./venv/bin/python scripts/benchmark_local_llm.py --direct-tool --document experiments/fixtures/sample-summary.txt
```

```bash
./venv/bin/python scripts/benchmark_local_llm.py --document experiments/fixtures/sample-summary.txt
```

Perintah pertama menguji tool/runtime tanpa Needle. Perintah kedua merekam
keputusan router beserta `routing_diagnostics`. Periksa `generation_outcome`,
`result`, dan `model_process_observed`. Hanya `model_summary_completed` yang
menyatakan job menghasilkan ringkasan model; extractive fallback bisa muncul
ketika model/runtime belum tersedia atau generasi gagal. Kehadiran child process
saja tidak membuktikan keberhasilan, dan RAM mode langsung tidak mencakup Needle.

Pada percobaan yang sama, volume gagal mengimpor `comtypes` dan brightness
mencoba menjalankan `powershell`. Keduanya masih memakai implementasi Windows;
persiapan paket audio Windows bukan port audio/brightness untuk Linux.

## 8. Membaca status dan mengetahui batas saat ini

- `tool=disk_usage` menunjukkan tool yang benar-benar dipanggil. Periksa juga
  hasilnya, bukan confidence saja.
- `[RESULT]` bisa berasal dari tool lama yang belum menyediakan status rinci;
  label ini belum membuktikan keberhasilan.
- `unresolved_tool_request` berarti permintaan belum dapat dijalankan dengan
  andal. Pesan berikutnya tetap menjadi permintaan baru.
- `whole-system RAM` mencakup OS dan aplikasi lain. Jangan dijumlahkan lagi
  dengan RSS Ember.
- `sampled peak RSS (Ember + children)` adalah perkiraan puncak selama job.
  `Ember RSS now` diukur setelah job, saat model mungkin sudah dilepas.

Respons singkat dan klarifikasi yang dihasilkan LLM, pemahaman history yang
andal, serta multi-step OS actions masih pekerjaan lanjutan. Pertanyaan RAM
tersedia juga masih bisa salah diarahkan ke informasi RAM terpasang.

Evaluasi tanpa menjalankan tindakan OS tersedia dari shell Windows:

```powershell
.\venv\Scripts\python.exe scripts/evaluate_routing.py --output logs/routing.json
.\venv\Scripts\python.exe scripts/evaluate_memory.py --with-routing --output logs/memory-routing.json
```

Pada Pi, ganti interpreter dengan `./venv/bin/python`. Hasil memory memakai
database sementara dan masih memiliki kasus gagal yang diketahui; evaluasi
bukan langkah wajib untuk memakai aplikasi.

## Pemeliharaan panduan

Setiap penambahan, perubahan, atau penghapusan fitur harus disertai pembaruan
`docs/USAGE.md` dalam pekerjaan yang sama: langkah awal, perintah shell, prompt
yang bisa dicoba, platform, hasil yang diharapkan, dan keterbatasannya. Perbarui
tanggal di atas serta tautan README/setup yang terdampak. Tandai contoh yang
belum diuji atau masih gagal; jangan menyajikan rencana sebagai fitur tersedia.

Panduan diperbarui bersama perubahan kode, bukan dihasilkan otomatis oleh Ember.
