# Pengembangan dan evaluasi

[Kembali ke README](README.md) · [Arsitektur](docs/ARCHITECTURE.md)

Siapkan venv dan `requirements.txt` mengikuti README. Semua perintah di bawah
dijalankan dari root repo dengan Python venv. Pada PowerShell, ganti `python`
dengan `.\venv\Scripts\python.exe`; pada Linux gunakan `./venv/bin/python`.

## Regression checks terarah

```bash
python -m unittest scripts.test_gui_launcher scripts.test_web_discovery -v
python scripts/test_directory_listing.py
python scripts/test_file_reference.py
python scripts/test_confirmation.py
python scripts/test_web_registry_security.py
python scripts/test_linux_adapters.py
```

Checks ini memakai mock dan/atau file sementara untuk memeriksa routing
tertentu, konfirmasi, registry web, dan adapter. Beberapa mengimpor Needle,
sehingga komponen native-nya tetap harus tersedia. Ini bukan benchmark kualitas
model atau bukti bahwa semua perangkat desktop sudah didukung.

Untuk mengisolasi log/data aplikasi selama checks, set `EMBER_DATA_DIR` ke
folder sementara sebelum menjalankannya, lalu hapus variabel itu dari sesi
shell setelah selesai. Jangan menunjuk ke database percakapan pribadi.

Tes companion memiliki dependensi tambahan:

```bash
python -m pip install -r desktop/companion/requirements.txt
python -m unittest scripts.test_emberos_companion -v
```

## Evaluasi yang bisa menunjukkan keterbatasan model

```bash
python scripts/evaluate_memory.py --output logs/memory-content.json
python scripts/evaluate_routing.py --output logs/routing.json
python scripts/evaluate_memory.py --with-routing --output logs/memory-routing.json
```

Evaluasi memory menggunakan database sementara; routing mengevaluasi keputusan
tanpa mengeksekusi tool nyata. Jangan menganggap exit code nonzero sebagai
kesalahan instalasi saja: periksa kasus yang gagal. Suite memory memuat kasus
traceback yang menyingkap keterbatasan relevansi pencarian literal; keberhasilan
penyimpanan tidak otomatis berarti seluruh kasus recall lulus.

Untuk ringkasan dan pengukuran sumber daya, siapkan model/runtime lalu jalankan:

```bash
python scripts/benchmark_local_llm.py --document experiments/fixtures/sample-summary.txt
python scripts/benchmark_local_llm.py --direct-tool --document experiments/fixtures/sample-summary.txt
```

`--direct-tool` melewati router sehingga tidak mewakili seluruh alur agent.
Detail pengukuran ada di [panduan local LLM](docs/setup/LOCAL_LLM.md).
Skrip smoke lain dapat menjalankan tool nyata; baca opsi dan implementasinya
sebelum menjalankan semuanya pada komputer pribadi.

## Menyampaikan hasil dan perubahan

- Catat commit, platform/arsitektur, versi Python, model/runtime, perintah,
  exit code, serta jumlah kasus lulus/total.
- Pisahkan hasil dengan mock, inferensi model, dan uji perangkat fisik.
- Pertahankan status gagal, unsupported, dan fallback di laporan.
- Saat menambah tool, selaraskan skema Needle, registry, implementasi platform,
  serta pembatasan/konfirmasi pada UI yang relevan.
- Perbarui panduan dan gunakan fixture sintetis; jangan commit data pengguna.

Workflow di [`.github/workflows/debian-packages.yml`](.github/workflows/debian-packages.yml)
membangun dan menjalankan smoke test paket serta companion. Status build
tidak menggantikan evaluasi akurasi model atau pengujian langsung di Pi.
