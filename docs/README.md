# Dokümantasyon

Yapay zekâ destekli soğutma arıza tahmini projesinin belgeleri. Ana sayfa için
[depo README'sine](../README.md) bakın; kaynak kod: <https://github.com/mirzayildiran/sogutma-ariza-tahmini>

| Belge | İçerik | Kimin için? |
|---|---|---|
| [Model Kartı](model-karti.md) | Modelin amacı, kullanım sınırları, veri (simülatör), öznitelikler, modeller, sağlık skoru formülü, değerlendirme sonuçları, sınırlılıklar ve pilotta doğrulanması gerekenler | Teknik ekip, değerlendiriciler, ortaklar |
| [Proje Dokümanı](proje-dokumani.md) | Ar-Ge destek başvurusu için taslak: problem, hedefler, yöntem, iş paketleri ve zaman çizelgesi, riskler, ticarileşme, bütçe kalemleri, ekip | Yönetim, başvuru hazırlığı |
| [Saha Mimarisi](saha-mimarisi.md) | Demo'dan gerçek sahaya geçiş: sensörler, örnekleme, ağ geçidi, MQTT, depolama, model sunumu ve yeniden eğitim, uyarı akışı, güvenlik | Saha/IoT ve yazılım ekibi |
| [Veri formatı](veri-formati.md) | CSV/JSON ölçüm biçimi, birimler, doğrulama ve sensör kalite kuralları | Saha/IoT ve veri ekibi |
| [REST API ve Docker](api.md) | Tahmin servisi: uç noktalar (CSV / JSON), örnek curl istekleri, API anahtarı, hata kodları, uvicorn ve docker compose ile çalıştırma, ağ geçidinden saatlik veri gönderme örneği | Yazılım ve saha/IoT ekibi |
| [Bildirimler](bildirimler.md) | E-posta / Telegram / webhook uyarıları: kurallar (süreklilik, cooldown, sessiz saat), ayar başvurusu, ortam değişkenleri, cron örneği, güvenlik | Saha/IoT ve bakım ekibi |

## Önemli not

Projedeki tüm sayısal sonuçlar **sentetik (simülatör) veri** üzerinde ölçülmüştür ve gerçek saha
performansını göstermez. Proje ve mimari belgelerindeki hedefler ve sensör/örnekleme değerleri **öneridir**;
`[...]` ile işaretli alanlar doldurulacak yer tutuculardır. Köşeli parantezli alanlar için gerçek veri ve
kaynak eklenmeden belge kullanıma hazır sayılmamalıdır.

## Önerilen okuma sırası

1. [Depo README](../README.md): ürüne genel bakış ve demo.
2. [Model Kartı](model-karti.md): modelin ne yaptığı ve ne yapmadığı.
3. [Saha Mimarisi](saha-mimarisi.md): gerçek sahaya geçiş.
4. [Proje Dokümanı](proje-dokumani.md): proje çerçevesi.
