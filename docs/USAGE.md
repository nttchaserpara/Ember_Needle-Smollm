# Panduan penggunaan terminal Ember

Diperbarui: 14 September 2026. Panduan ini mengikuti kemampuan yang sudah ada,
termasuk batasan yang masih ditemukan. Semua prompt untuk Ember memakai bahasa
Inggris. Jalankan satu permintaan, tunggu hasilnya, lalu lanjutkan.

Langsung ke: [Windows](#2-menjalankan-di-windows--terminal-vs-code),
[prompt sederhana](#3-prompt-sederhana-untuk-mulai),
[summarize](#4-summarize-dokumen), [memory](#5-memory-percakapan),
[balasan natural](#balasan-natural),
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

Untuk brightness pada layar Windows yang mendukung WMI:

```text
turn my screen brightness to 30
```

Ember membaca ulang persentase dari Windows sebelum menampilkan `[OK]`.
Provider yang tidak mengembalikan `ReturnValue` sekarang tetap diperiksa lewat
pembacaan ulang, bukan langsung dianggap menolak perubahan. Error Windows dan
hasil yang tidak sesuai target tetap dilaporkan sebagai gagal atau parsial.
Perbaikan diuji dengan provider tiruan serta pemanggilan ke layar lokal pada
nilai yang sudah aktif; dukungan monitor lain dan Linux belum dibuktikan.

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

Word yang didukung adalah **`.docx`**. Format Word lama **`.doc` belum didukung**,
baik di Windows maupun Linux/Pi. Untuk file `.doc`, buka di Word atau LibreOffice,
gunakan **Save As** untuk membuat salinan `.docx`, lalu minta Ember meringkas
path salinan tersebut. Mengganti nama ekstensi saja tidak mengonversi isinya.

Perbaikan 14 September menjaga path `.doc` panjang tetap utuh. Jika model memilih
ringkasan dengan referensi file yang benar, Ember menjelaskan batasan `.doc` dan
cara konversinya. Saat confidence rendah, penjelasan diberikan tanpa menjalankan
tool; pada pemanggilan tool, statusnya `[UNAVAILABLE]`. Ini belum menambahkan
pembaca `.doc`; tidak ada ringkasan dokumen yang dibuat. Mode balasan natural
dapat memuat SmolLM untuk menyusun ulang penjelasan keterbatasan tersebut.

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

Pada uji Windows 14 September, `what's in memory now?` sekarang ditolak dengan
petunjuk perintah memory, tanpa memanggil `get_system_info`. Pertanyaan
`what did we discuss about my screen brightness?` juga masih belum menghasilkan
recall. Ini perbaikan penanganan salah rute, belum penyelesaian recall bahasa
alami. Untuk membaca catatan brightness yang tersimpan, gunakan:

```text
/memory search brightness
```

Catatan lama yang bertuliskan gagal tetap ditampilkan dengan status aslinya;
perbaikan kode tidak mengubah riwayat menjadi sukses.

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

Balasan singkat dari hasil tool sekarang dapat ditulis ulang oleh SmolLM.
Klarifikasi dengan konteks, pemahaman history yang andal, serta multi-step OS
actions masih pekerjaan lanjutan. Pertanyaan RAM tersedia juga masih bisa
salah diarahkan ke informasi RAM terpasang.

Untuk pengujian ulang setelah pembaruan, coba prompt disk pada bagian 3,
ringkasan fixture `.txt` pada bagian 4, serta `/memory` pada bagian 5.
Di Windows, kontrol volume dan brightness memerlukan perangkat yang mendukung;
di Pi, gunakan benchmark bagian 7 untuk memisahkan kegagalan routing dari
kegagalan model. Dukungan `.doc` lama dan OCR masih belum tersedia.

Evaluasi tanpa menjalankan tindakan OS tersedia dari shell Windows:

```powershell
.\venv\Scripts\python.exe scripts/evaluate_routing.py --output logs/routing.json
.\venv\Scripts\python.exe scripts/evaluate_memory.py --with-routing --output logs/memory-routing.json
```

Pada Pi, ganti interpreter dengan `./venv/bin/python`. Hasil memory memakai
database sementara dan masih memiliki kasus gagal yang diketahui; evaluasi
bukan langkah wajib untuk memakai aplikasi.

## Balasan natural

Restart Ember setelah memperbarui kode. Balasan singkat otomatis memakai
SmolLM yang sama dengan summarize. Jika generasi gagal atau mengubah fakta
yang diperiksa, Ember memakai balasan asli. Pengguna cukup menulis permintaan
biasa; tidak ada mode balasan yang perlu dipilih.

```text
what is my current volume
```

Contoh tersebut membaca volume pada Windows. SmolLM menerima pertanyaan,
hasil tool saat ini, serta maksimal dua percakapan singkat tersimpan dari tool
yang sama. Riwayat dipakai sebagai konteks, bukan kondisi perangkat sekarang.
Ini tidak melatih atau mengubah bobot model. Angka yang sama bisa menghasilkan
kalimat yang sama; variasi kata bukan ukuran pemahaman.

Status seperti `[OK]`, `[FAILED]`, dan `[PARTIAL]` tetap ditentukan oleh hasil
tool. Balasan gagal, parsial, dan penolakan wajib mempertahankan seluruh kata
dalam diagnosis asli; tambahan penyebab, lokasi, atau saran memicu fallback.
JSON, tabel, isi file, hasil pencarian history, dan ringkasan dokumen
tersaji utuh. Generasi balasan tidak menjalankan ulang tool dan tidak
memperbaiki salah rute Needle. Jika memory tidak tersedia, model tetap bisa
menjawab dari hasil saat ini. Riwayat panjang atau terpotong tidak dimasukkan
ke prompt; batasnya dijelaskan dalam [panduan balasan natural](setup/NATURAL_REPLIES.md).

Untuk pengujian pengembangan pada Pi, jalankan dari **shell**:

```bash
./venv/bin/python scripts/evaluate_replies.py --output logs/pi-replies.json
```

Skrip memakai hasil tool tiruan dan database sementara, tanpa tindakan OS.
Alur generasi sama dengan aplikasi. Laporan mencatat keluaran model, fallback,
konteks terpakai, waktu, dan puncak RAM proses. Hasil Windows tidak menjamin
kecocokan atau kecepatan di Pi Zero 2 W; pengukuran fisik masih diperlukan.

Benchmark worker kini memakai `llama-server` produksi pada kedua mode,
tanpa membutuhkan `llama-cli` atau mengubah path dalam kode:

```bash
./venv/bin/python scripts/bench_short_response.py --mode both --n 10 --output logs/pi-worker-modes.json
./venv/bin/python scripts/bench_short_response.py --mode sleep-wake --sleep-idle 90 --output logs/pi-sleep-wake.json
```

Mode `sleep-wake` menunggu idle lebih dari 90 detik, mencatat status tidur dan
RSS, lalu mengukur generasi setelah bangun. Detail dan batas interpretasinya
ada di [panduan worker](setup/LOCAL_LLM.md#persistent-worker-and-sleep-measurement).
Ini opsi benchmark pengembangan; penggunaan normal tidak menambah perintah chat.

## Undo satu tindakan terakhir

Setelah restart dengan kode terbaru, lakukan satu tindakan yang didukung lalu
ketik permintaan biasa berikut di `You>`:

```text
undo it
```

Contoh Windows: ubah volume ke 70, lalu `undo it` untuk kembali ke nilai yang
terbaca sebelum perubahan. Nilai sebelumnya dicatat oleh executor; model tidak
menebaknya dari chat. Jika volume sudah 70 sejak awal, tindakan itu tidak membuat
perubahan dan tidak menyediakan undo. Pemeriksaan perangkat memakai identitas
endpoint, nilai volume, dan status mute.

Dukungan saat ini:

- **Windows:** set/up/down volume, toggle mute, dan set brightness. Untuk beberapa
  monitor, masing-masing level sebelumnya dipulihkan dengan identitas monitor.
- **Windows dan Linux/Pi:** penambahan, penyelesaian, dan penghapusan satu task.
  Undo mengembalikan ID, isi, tanggal, prioritas dan status task yang tercatat.
- File, shell, launcher, penghapusan massal task dan tindakan lain belum mempunyai
  pemulihan. Jika tindakan terakhir itu belum didukung, undo menjelaskannya dan
  tidak melompati tindakan tersebut untuk membatalkan tindakan yang lebih lama.

Hanya satu slot selama sesi berjalan, tanpa redo atau pemulihan setelah restart.
Permintaan baca seperti `list_tasks`, history, atau volume saat ini tidak
menghabiskannya. Permintaan yang ditolak router tidak menjalankan tindakan,
sehingga slot tetap. Tindakan yang benar-benar mulai dieksekusi lalu gagal atau
parsial menggantikan slot lama dengan status tidak bisa dipulihkan dengan aman.

Jika keadaan diubah dari luar Ember, undo menolak menimpanya. Upaya pemulihan
yang sudah dimulai menghabiskan slot, termasuk ketika gagal atau terputus;
Ember tidak mencoba ulang otomatis. Jika tidak ada snapshot sebelum perubahan,
tindakan biasa tetap tersedia tetapi undo untuknya tidak tersedia.

Routing tetap memakai Needle dengan threshold dan validasi yang sama. `undo it`
sudah dipilih dengan benar pada pengujian Windows. Variasi seperti `nevermind
undo it` dan `undo the volume change` masih dapat ditolak; penambahan tool undo
belum menyelesaikan seluruh variasi bahasa. Multi-step dan toleransi typo belum
ditambahkan. Setelah Needle memilih undo, target literal `volume`, `brightness`,
atau `task` dipertahankan dari permintaan meskipun model menghilangkannya.
Target model yang bertentangan, tidak disebut, atau lebih dari satu ditolak.
Penjagaan argumen ini tidak mengubah penolakan router menjadi tindakan undo.

Pengujian aplikasi memakai task dan history sementara dari **shell**:

```powershell
.\venv\Scripts\python.exe scripts/verify_undo.py --output logs/undo.json
```

Pada Pi, gunakan `./venv/bin/python`. Skrip memakai Needle dan SmolLM asli,
mengizinkan hanya eksekusi undo atas task sementara, dan memeriksa bahwa undo
kedua tidak membatalkan tindakan yang lebih lama. Data pengguna dan perangkat
volume/brightness tidak diubah. Panduan [undo](setup/UNDO.md) mencatat kontrak,
command evaluasi routing, dan batas pengujian fisik.

## Pemeliharaan panduan

Setiap penambahan, perubahan, atau penghapusan fitur harus disertai pembaruan
`docs/USAGE.md` dalam pekerjaan yang sama: langkah awal, perintah shell, prompt
yang bisa dicoba, platform, hasil yang diharapkan, dan keterbatasannya. Perbarui
tanggal di atas serta tautan README/setup yang terdampak. Tandai contoh yang
belum diuji atau masih gagal; jangan menyajikan rencana sebagai fitur tersedia.

Panduan diperbarui bersama perubahan kode, bukan dihasilkan otomatis oleh Ember.
