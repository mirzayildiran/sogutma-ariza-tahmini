# Yol Haritası

Durum: çalışan demo prototipi. Tamamlandı işaretleri depodaki kod ve testlerle mevcut prototip kapsamını belirtir; saha doğrulaması veya üretim hazırlığı anlamına gelmez.

## Tamamlandı: demo ve entegrasyon

- [x] Fizik esaslı simülatör; soğuk oda, dondurucu ve market dolabı; beş ekipman arıza türü.
- [x] Anomali, arıza sınıfı ve kalan süre modeli; ünite grupları ayrılmış sentetik test filosu.
- [x] CSV içe alma/doğrulama, CLI tahmin ve saatlik rapor.
- [x] Streamlit filo, veri analizi, ROI ve bildirim ayarı/deneme önizleme sayfaları.
- [x] CLI e-posta, Telegram ve webhook gönderimi; gerçek gönderim açıkça etkinleştirilir.
- [x] Sensör arızası simülasyonu ve sezgisel veri kalitesi katmanı; CSV, CLI, panel ve API tahmin yollarına entegre.
- [x] FastAPI CSV/JSON tahmin uçları ve isteğe bağlı API anahtarı.
- [x] Docker imajı/Compose ve GitHub Actions pytest, Ruff ve container duman kontrolleri.

## Sonraki: doğrulama ve veri hattı

- [x] Sabit sensör kalite eşiklerini ek sentetik profil/tohumlarda ölç; sonuçları saha eşiği veya saha performansı olarak sunma.
- [x] Temel sentetik testte sınıf Brier/log-loss, koşullu ETA hatası ve ±8 °C sabit ortam kayması stres metriği üret.
- [ ] ETA belirsizliği ve olasılık kalibrasyonunu ünite bazında ayrılmış doğrulama verisinde değerlendir.
- [ ] Eşzamanlı ekipman arızası (mevcut sensör + ekipman arızası birlikte test edilir), uzun dönem mevsimsellik ve sıcak hava dalgası senaryolarını değerlendir.
- [x] MQTT v1 telemetri sözleşmesi için saf doğrulama/kanonik alan adaptörü ekle; broker, yeniden bağlantı ve kalıcı alım kapsam dışı.
- [ ] MQTT üretici/abone prototipi; zaman, birim, eksik değer ve yeniden bağlantı davranışını broker ile test et.
- [ ] Zaman serisi saklama yaklaşımını pilot gereksinimleri, saklama süresi ve işletim maliyetine göre seç.

## Pilot ve ürünleştirme: saha verisi ve gereksinim bekliyor

- [ ] Gerçek ünite/sensör verisi ve servis kayıtlarıyla gölge mod pilotu; yanlış alarm, kaçırma ve erken uyarı ölçümü.
- [ ] Ekipman, soğutucu gaz ve ünite bazlı baseline yaklaşımını pilotta karşılaştır.
- [ ] Pilot kanıtı ve destek kararı oluşursa chiller ve diğer ekipman tiplerini ekle.
- [ ] Üretim dış erişimi için panel kimlik doğrulama/ters vekil, tenant sınırları ve saklama politikası tasarla.
- [ ] Güvenilir model artefact sürümleme, atomik dağıtım, tahmin/kalite metrikleri ve alarm gözlemlenebilirliği ekle.
- [ ] Pilot altyapısına uygun bulut dağıtımını ve operasyon/runbook belgelerini hazırla.

## Çalışma ve doğrulama kuralları

- Kod değişikliklerinde `python -m pytest`, `ruff check .` ve `git diff --check` çalıştırılır.
- Kullanıcıya görünen metinler Türkçe; kaynak kod Python 3.9 ile uyumludur.
- Sentetik veri metrikleri her zaman sentetik olarak etiketlenir; saha performansı yalnızca pilot ölçümüyle raporlanır.
- CI, mevcut destek matrisi olan Python 3.9 ve 3.12'de test/Ruff; Docker imajı ve API/panel sağlık duman kontrolleri çalıştırır.
