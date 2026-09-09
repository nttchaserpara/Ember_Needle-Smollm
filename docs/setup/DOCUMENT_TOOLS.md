# Catatan dan dokumen di Ember

Jalankan Ember dari folder proyek menggunakan virtual environment:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-documents.txt
.\venv\Scripts\python.exe run_ember.py
```

## Notepad

`create_note(title, content, tags=None)` menyimpan catatan dalam database
`data/ember.db`, mengekspor file UTF-8 ke `data/notes/<id>-<judul>.txt`, lalu
membuka file tersebut di Notepad. Judul yang sama menghasilkan file berbeda.
Jika Notepad gagal dibuka, file tetap tersedia dan lokasinya ditampilkan.

Contoh perintah:

```text
Create a note titled "Belanja" with content "Beli susu dan telur" in Notepad
Buat catatan di Notepad dengan judul "Belanja" dan isi "Beli susu dan telur"
```

`search_notes` mencari salinan database. Edit manual pada file di Notepad
belum disinkronkan kembali ke database. Catatan lama tidak diekspor otomatis.

## Excel dan aplikasi spreadsheet lain

`create_spreadsheet(path, rows, sheet_name="Sheet1", open_after=True)` membuat
file `.xlsx` baru. Windows membukanya menggunakan aplikasi bawaan untuk `.xlsx`,
misalnya Excel atau LibreOffice. File yang sudah ada tidak ditimpa.
Angka dan boolean dipertahankan; string disimpan sebagai teks literal,
termasuk teks yang diawali `=`. Fitur ini belum mengedit workbook yang sudah ada.

```text
Create an Excel spreadsheet at "C:\Users\natta\Documents\belanja.xlsx" with rows [["Item", "Qty"], ["Milk", 2]]
```

`write_document` juga mendukung `.xlsx` dengan `content` berformat CSV.
Jalur CSV menyimpan nilai sebagai teks; gunakan `create_spreadsheet` untuk
angka bertipe numerik. Dukungan Word `.docx` yang sudah ada menggunakan
`python-docx`, yang termasuk dalam requirements di atas.

## Menghubungkan Google Docs sekali di awal

Integrasi menggunakan OAuth aplikasi Desktop dan Google Docs API, sesuai
[panduan resmi Google](https://developers.google.com/workspace/docs/api/quickstart/python).

1. Buat atau pilih proyek di [Google Cloud Console](https://console.cloud.google.com/).
2. Aktifkan **Google Docs API**.
3. Siapkan **Google Auth platform**: Branding, Audience, dan Data Access.
   Untuk akun Google pribadi, pilih External dan tambahkan akunmu sebagai
   test user jika aplikasi masih berstatus Testing.
4. Tambahkan scope `https://www.googleapis.com/auth/drive.file` pada Data Access.
   Scope ini digunakan untuk dokumen yang dibuat atau diberikan akses ke aplikasi,
   bukan akses umum ke seluruh Drive.
5. Pada Clients, buat OAuth client bertipe **Desktop app**, lalu unduh JSON-nya.
   Simpan di luar folder proyek, misalnya `C:\Users\natta\Documents\ember-google-credentials.json`.
6. Dari folder proyek, jalankan:

```powershell
.\venv\Scripts\python.exe -m use_cases.google_docs --connect "C:\Users\natta\Documents\ember-google-credentials.json"
```

7. Pilih akun Google di browser dan izinkan akses yang diminta. Login diberi
   waktu tiga menit; jalankan ulang perintah jika waktunya habis.

Token login disimpan di `%LOCALAPPDATA%\Ember\google_docs_token.json`, di luar
proyek. Jangan membagikan file token atau JSON kredensial. Tool memperbarui token
jika memungkinkan; bila akses dicabut atau kedaluwarsa, jalankan login ulang.

Setelah login, gunakan:

```text
Create a Google Docs document titled "Rapat" with content "Bahas jadwal proyek"
Buat dokumen di Google Docs berjudul "Rapat" dan isi "Bahas jadwal proyek"
```

`create_google_doc(title, content, open_after=True)` membuat dokumen baru di
akun yang terhubung, mengisi teks, lalu membuka tautannya. Belum ada pengeditan
dokumen Google yang sudah ada atau integrasi Google Sheets. Jika pengisian gagal
setelah dokumen dibuat, pesan kegagalan menyertakan tautannya agar bisa diperiksa.

## Verifikasi lokal

Bagian ini hanya untuk komputer pengembangan yang memiliki file tes lokal.
`experiments/*.py` diabaikan Git dan tidak diperlukan untuk menjalankan Ember;
perintah ini bukan langkah setup untuk hasil clone baru di Pi.

```powershell
.\venv\Scripts\python.exe -m unittest experiments.test_documents experiments.test_document_routing -v
```

Tes menggunakan file/database sementara, simulasi peluncuran aplikasi, dan
simulasi respons Google API. Pengujian Google Docs dengan akun asli dilakukan
setelah kredensial tersedia dan login selesai.
