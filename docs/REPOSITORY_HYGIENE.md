# File yang dibagikan dan file lokal

[Kembali ke README](../README.md)

Gunakan link repo atau clone Git untuk berbagi source. ZIP seluruh folder
kerja dapat ikut membawa file yang diabaikan Git.

| Kelompok | Masuk Git? | Alasan |
| --- | --- | --- |
| Source Python, requirements, scripts | Ya | Diperlukan untuk menjalankan dan memeriksa implementasi |
| README, docs, konfigurasi CI/build | Ya | Panduan dan proses build yang dapat ditinjau |
| Fixture sintetis, kasus evaluasi JSON | Ya | Input evaluasi yang dapat diulang |
| Manifest/checksum model | Ya | Identitas model/runtime yang digunakan |
| Bobot model, runtime unduhan, venv | Tidak | Hasil setup lokal, besar atau spesifik platform |
| `data/`, `logs/`, `tools.idx` | Tidak | History, notes/tasks, log, atau indeks yang dihasilkan saat runtime |
| `.local/`, `docs/research/`, draft riset/PPT pribadi | Tidak | Catatan kerja dan materi yang belum dikurasi untuk review kode |
| `tmp/`, cache, `build/`, `dist/` | Tidak | File sementara dan hasil build |
| `.env`, OAuth JSON/token, private key | Tidak | Konfigurasi atau kredensial lokal |

Aturan lengkap berada di [`.gitignore`](../.gitignore). File lokal yang
dikecualikan tetap ada di komputer; aturan ini tidak menghapusnya.
Eksperimen Python ad hoc di `experiments/` dan hasil lokal di
`experiments/results/` dikecualikan. Pindahkan checks yang ingin direproduksi
reviewer ke `scripts/`, beserta fixture sintetis dan petunjuk eksekusinya.

## Sebelum commit berikutnya

```bash
git status --short
git diff --stat
git diff --cached --name-only
git diff --cached
```

Stage file yang memang ingin dibagikan secara eksplisit. Periksa nama dan isi
file, terutama hasil evaluasi yang mungkin memuat path atau teks pengguna.
Jangan memakai `git add -f` untuk memasukkan model, data pribadi, atau token.

`.gitignore` bukan pemindai rahasia dan tidak mengeluarkan file yang sudah
tracked dari Git atau menghapus history commit. Jika suatu file lokal baru
perlu dikecualikan, pastikan statusnya dengan `git check-ignore -v PATH`.
Aturan nama file tidak menjamin bahwa seluruh kredensial dengan nama lain
akan terdeteksi.
