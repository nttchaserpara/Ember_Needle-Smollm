# Panduan review mentor

[Kembali ke README](../README.md)

Repo ini memperlihatkan prototipe agent OS lokal: pemilihan tool dengan Needle,
eksekusi tool Python, dan penggunaan SmolLM kecil untuk ringkasan serta balasan.
Implementasi ini menjadi bahan review engineering dan eksperimen; keberadaan
fitur belum membuktikan kontribusi riset atau reliabilitas agent secara umum.

## Urutan membaca

1. [README](../README.md): tujuan, kemampuan, batasan, dan setup.
2. [Arsitektur](ARCHITECTURE.md): pemisahan router, executor, model, dan memory.
3. [Panduan penggunaan](USAGE.md): prompt dan interpretasi hasil.
4. [Pengembangan dan evaluasi](../CONTRIBUTING.md): checks yang dapat diulang.
5. Source utama: [`run_ember.py`](../run_ember.py),
   [`needle_router.py`](../needle_router.py),
   [`emberos/tools.py`](../emberos/tools.py), dan
   [`use_cases/local_llm.py`](../use_cases/local_llm.py).

## Demo untuk review

Untuk Linux/Pi yang didukung, gunakan [instalasi APT di README](../README.md#instalasi-dengan-apt-linux--raspberry-pi)
dan jalankan `emberos-cli`. Alternatifnya, siapkan source mengikuti README.
Jalankan prompt di `You>` satu per satu. Pada instalasi APT, gunakan path
`/opt/emberos/experiments/fixtures/sample-summary.txt` untuk demo ringkasan;
path relatif pada tabel berikut berlaku jika berjalan dari root checkout.

| Langkah | Prompt | Yang diperiksa |
| --- | --- | --- |
| 1 | `How much free disk space do I have?` | Tool mengembalikan kondisi backend |
| 2 | `List the files and folders in my home directory.` | Routing listing folder tanpa menulis file |
| 3 | `/memory search disk space` | Riwayat tersimpan; angka lama bukan pembacaan baru |
| 4 | `Summarize "experiments/fixtures/sample-summary.txt"` | Setelah model siap: bedakan generasi, extractive fallback, dan kegagalan |
| 5 | `exit`, jalankan ulang, lalu `/memory` | Persistensi riwayat lintas proses |

Langkah memory membutuhkan konfigurasi default `EMBER_MEMORY=1`. Gunakan
fixture repo untuk dokumen demo; listing home dapat menampilkan nama file pribadi.

## Hal yang perlu dinilai terpisah

- **Routing:** apakah nama tool, argumen, dan keputusan menolak sudah benar?
- **Eksekusi:** apakah aksi benar-benar terjadi dan statusnya sesuai hasil?
- **Generasi:** apakah ringkasan/balasan setia pada sumber atau hasil tool?
- **Sumber daya:** ukur latensi dan RAM total proses induk serta worker pada
  perangkat target; hasil laptop bukan hasil Raspberry Pi.
- **Memory:** pisahkan persistensi database dari ketepatan pencarian dan recall.

Recall bahasa alami dan relevansi pencarian literal masih memiliki batasan.
Multi-step bersifat terbatas dan opt-in; undo hanya satu slot untuk aksi yang
didukung. Dukungan desktop bergantung pada platform. Lihat panduan terkait
sebelum menafsirkan demo sebagai keberhasilan menyeluruh.

## Bukti yang dapat direproduksi

Fixture sintetis dan kasus evaluasi ada di [`experiments/`](../experiments/).
Skrip regression dan evaluasi ada di [`scripts/`](../scripts/).
[Panduan pengembangan](../CONTRIBUTING.md) memisahkan checks dengan mock dari
evaluasi model yang dapat gagal karena keterbatasan yang sudah diketahui.

Catat commit, OS/arsitektur, versi Python, model/runtime, perintah, exit code,
dan jumlah lulus/total saat menyampaikan hasil. Repo tidak menyertakan log
pribadi atau dump percakapan sebagai bukti. Catatan riset pribadi dan bahan
presentasi tidak menjadi bagian dari paket review kode ini.

## Pemeriksaan saat kurasi repo — 23 Oktober 2026

Dijalankan pada Windows dengan Python 3.13.2, terhadap kode aplikasi commit
`a6f8fa0`; perubahan kurasi hanya menyentuh dokumentasi dan aturan ignore.
`EMBER_DATA_DIR` diarahkan ke direktori sementara selama pemeriksaan.

| Pemeriksaan | Hasil |
| --- | --- |
| `unittest`: GUI launcher dan web discovery | 4/4 lulus |
| Skrip directory listing, file reference, confirmation, web registry security, Linux adapters | Kelima skrip exit 0 |
| `evaluate_memory.py` tanpa `--with-routing` | 7/8 lulus; exit 1 |

Kasus memory yang gagal adalah `Pasted traceback is not a document discussion`:
pencarian literal masih dapat menganggap kata pada path traceback sebagai
topik percakapan. Ini keterbatasan yang sudah direpresentasikan dalam evaluator,
bukan hasil yang dihitung lulus. Perintah reproduksi ada di
[panduan pengembangan](../CONTRIBUTING.md). Pemeriksaan ini tidak mengukur
kualitas generasi, latensi/RAM model, atau perilaku perangkat Pi fisik.
