# Yol Haritası

Projenin geliştirme planı ve ilerleme durumu. Tamamlanan maddeler işaretlenir; her madde
ayrı bir dalda geliştirilir, incelenip test edildikten sonra `main` dalına birleştirilir.

## Dalga 1: Temel altyapı
- [x] Test paketi (pytest) ve GitHub Actions CI
- [x] Gerçek veri yükleme (`sogutma/ingest.py`), toplu tahmin aracı (`predict.py`), örnek CSV, veri formatı dokümanı
- [x] Dokümantasyon: model kartı, proje başvuru taslağı, saha mimarisi

## Dalga 2: Gerçekçilik ve panel
- [x] Simülatör: dondurucu (−18 °C) ve market dolabı tipleri
- [ ] Simülatör: sensör arızaları (kayma, takılma, veri kaybı) ve modelin bunlara dayanıklılığı
- [x] Panel: CSV yükleyip kendi verisini analiz etme sayfası
- [ ] Panel: bildirim kuralları (e-posta / Telegram / webhook, deneme modu)
- [x] Panel: maliyet ve kazanç (ROI) hesaplayıcı

## Dalga 3: Entegrasyon
- [x] REST API (FastAPI) ile tahmin servisi
- [x] Docker imajı ve docker-compose
- [ ] MQTT ile canlı veri alma örneği (simülatörden yayın + abone)

## Dalga 4: Model kalitesi
- [ ] Ünite bazlı referans (baseline) normalizasyonu
- [ ] Olasılık kalibrasyonu ve kalan süre için belirsizlik aralığı
- [ ] Zorlaştırılmış test senaryoları: eş zamanlı arızalar, mevsimsel değişim

## Çalışma kuralları
- Her değişiklik testlerden (`pytest`) ve `ruff check` kontrolünden geçmeli.
- Kullanıcıya görünen metinler ve yorumlar Türkçe; kod Python 3.9 uyumlu.
- Sentetik veride ölçülen performans her yerde açıkça "sentetik" olarak belirtilir.
- Her commit'ten sonra GitHub'a gönderilir.
