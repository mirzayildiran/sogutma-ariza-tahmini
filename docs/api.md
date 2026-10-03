# REST API ve Docker

Panelin ve `predict.py`'nin kullandığı analiz akışı (CSV okuma/doğrulama → saatlik öznitelikler → model),
bir HTTP servisi olarak da sunulur. Böylece bir IoT ağ geçidi, bakım yazılımı ya da başka bir sistem
ölçümleri gönderip ünite başına sağlık skoru, olası arıza ve kalan süreyi alabilir.

> [!IMPORTANT]
> Model **sentetik (simülatör) veriyle** eğitildi; gerçek saha doğruluğu doğrulanmamıştır. API her yanıtta bunu
> `sentetik_uyari` alanıyla belirtir. Çıktı ön göstergedir; basınç şalteri, alarm cihazı ya da resmî sıcaklık
> kaydı yerine geçmez. Servis durum tutmaz (veritabanı yoktur): her çağrıda analiz edilecek veriyi gönderirsiniz.

## İçindekiler

1. [Çalıştırma](#1-çalıştırma)
2. [Uç noktalar](#2-uç-noktalar)
3. [Kimlik doğrulama](#3-kimlik-doğrulama)
4. [Hatalar](#4-hatalar)
5. [Docker ve docker compose](#5-docker-ve-docker-compose)
6. [Ağ geçidinden saatlik veri gönderme](#6-ağ-geçidinden-saatlik-veri-gönderme)
7. [Sınırlar ve güvenlik notları](#7-sınırlar-ve-güvenlik-notları)

## 1. Çalıştırma

```bash
pip install -r requirements-api.txt     # panel bağımlılıklarını da içerir
python train.py                          # bir kez: modeli eğitir (models/predictor.joblib)
uvicorn api:app --port 8000              # yalnızca bu makineden erişim
# ağdaki diğer cihazlara açmak için: --host 0.0.0.0 (önce API anahtarı belirleyin, bkz. §3)
```

Etkileşimli belgeler (OpenAPI): <http://localhost:8000/docs>. Model dosyası değiştiğinde (ör. `python train.py`
yeniden çalıştırıldığında) servis yeniden başlatmaya gerek kalmadan yeni modeli yükler.

| Ortam değişkeni | Anlamı | Varsayılan |
|---|---|---|
| `SOGUTMA_ROOT` | `models/` ve `data/` klasörlerinin üst dizini (panel de aynısını kullanır) | depo kökü |
| `SOGUTMA_API_ANAHTARI` | Ayarlıysa `/saglik` dışındaki uçlar `X-API-Anahtari` başlığı ister | ayarsız (açık, başlangıçta uyarı yazar) |
| `SOGUTMA_MAKS_YUKLEME_MB` | İstek gövdesi üst sınırı (MB) | 20 |

## 2. Uç noktalar

| Yöntem | Yol | Amaç |
|---|---|---|
| GET | `/saglik` | Canlılık: model yüklü mü, model sürümü, çalışma süresi. Kimlik doğrulama gerekmez. Model kullanılamıyorsa **503**. |
| GET | `/model` | Model sürümü, arıza türleri, ekipman tipleri ve `models/metrics.json` özeti (**sentetik** ölçümler). |
| POST | `/tahmin/csv` | CSV dosyası yükle (`multipart/form-data`, alan adı `dosya`) → tahmin. |
| POST | `/tahmin/olcumler` | JSON ölçüm listesi gönder → tahmin. |

### GET /saglik

```bash
curl http://localhost:8000/saglik
```
```json
{"durum": "ok", "model_yuklu": true, "model_surumu": 2, "calisma_suresi_sn": 3.4, "kimlik_dogrulama": false}
```

Model yoksa ya da eskiyse HTTP 503 döner ve `durum` alanı `model_yok`, `model_eski` ya da `model_okunamadi` olur.

### GET /model

```bash
curl http://localhost:8000/model
```
Yanıtın `sentetik: true` alanı ve `uyari` metni her zaman bulunur. `metrikler` içinde saatlik doğruluk,
makro F1, arıza öncesi yakalama oranı, medyan erken uyarı süresi (gün), yanlış alarm veren ünite sayısı ve
ekipman tipine göre dökümü yer alır; hepsi sentetik test filosundan gelir. Ayrıca `multiclass_brier` ve `log_loss`
ham, **kalibre edilmemiş** sınıf olasılıklarını ölçer. `eta_mae_saat` ve `eta_p90_mutlak_hata_saat`, `pred_fault` arıza dışı
sınıf olup gerçek arızaya kalan süre pozitif olan test saatlerinin koşullu ETA hatalarıdır; doğru arıza türünün tahmin edilmesini
şart koşmaz. `eta_dogru_tur_mae_saat` ve `eta_dogru_tur_p90_mutlak_hata_saat` yalnızca tür tahmini gerçek etiketle
eşleştiğinde hesaplanır. Ardışık saatler bağımsız örnekler değildir. Bu sentetik metrikler saha performansı ya da
olasılık kalibrasyonu kanıtı değildir. `ortam_kaymasi_stresi`, aynı sentetik test filosuna −8 °C ve
+8 °C sabit dış ortam ofseti uygulanan stres kontrolünü verir; gerçek mevsim veya saha testi değildir.

### POST /tahmin/csv

CSV biçimi [Veri formatı](veri-formati.md) belgesindeki ile aynıdır (Türkçe Excel çıktıları dahil).

```bash
curl -F dosya=@examples/ornek_veri.csv "http://localhost:8000/tahmin/csv"
curl -F dosya=@dondurucu.csv "http://localhost:8000/tahmin/csv?tip=dondurucu&setpoint=-20&saatlik=true"
```

Sorgu parametreleri:

| Parametre | Anlamı |
|---|---|
| `setpoint` | CSV'de set sütunu yoksa termostat set değeri (°C); verilmezse oda sıcaklığı medyanı |
| `gauge` | `true` ise basınçlar efektif (gauge) kabul edilir, +1,013 bar ile mutlağa çevrilir |
| `tip` | CSV'de `tip` sütunu yoksa ekipman tipi: `soguk_oda` (varsayılan), `dondurucu`, `market_dolabi` |
| `saatlik` | `true` ise saatlik tahmin serisi de döner |

Yanıt (kısaltılmış; örnek CSV):

```json
{
  "model_surumu": 2,
  "sentetik_uyari": "Model sentetik (simülatör) veriyle eğitildi ...",
  "uniteler": [
    {
      "unit_id": "U1",
      "zaman": "2026-01-10T23:00:00",
      "saat_sayisi": 228,
      "saglik": 13.6,
      "durum": "Kritik",
      "ariza": "gaz_kacagi",
      "ariza_adi": "Soğutucu gaz kaçağı",
      "guven": 1.0,
      "kalan_saat": 39.0,
      "olasiliklar": {"normal": 0.0, "gaz_kacagi": 1.0, "kondenser_kirlenmesi": 0.0, "...": 0.0},
      "sapmalar": [
        {"sinyal": "Kızgınlık / superheat (K)", "deger": 19.114, "normal": 5.997, "sapma_sigma": 144.84}
      ],
      "oneri": "Kaçak testi yapın (elektronik dedektör / köpük), kaçağı giderin, ...",
      "uyari_baslangic": "2026-01-06T19:00:00",
      "uyari_saat": 101,
      "sensor_sorunu": false,
      "sensor_notu": "",
      "sensor_sorunlari": []
    }
  ],
  "rapor": {
    "bilgiler": ["unit_id sütunu yok; tek ünite varsayıldı ('U1')."],
    "uyarilar": [],
    "turetilen": {"tip": "sütun yok; soğuk oda (soguk_oda) varsayıldı"},
    "eksik_sensorler": [],
    "hesaplanamayan_oznitelikler": [],
    "yinelenen_satir": 0,
    "uniteler": {"U1": {"bas": "2026-01-01T00:00:00", "bit": "2026-01-10T23:55:00", "saat": 240.0,
                        "kapsam": 1.0, "ham_satir": 2880, "orneklem_dk": 5.0}}
  },
  "saatlik": null
}
```

Alanlar:

- `durum`: `Normal`, `İzlemede` ya da `Kritik`; `saglik` 0–100 skorudur.
- `ariza` / `ariza_adi`: tahmini arıza kodu ve Türkçe adı (`normal` dahil). `guven` 0–1.
- `kalan_saat`: arızaya kalan süre, kaba tahmin; `durum` Normal ise `null`.
- `sapmalar`: aynı tipteki normal çalışmaya göre en çok sapan sinyaller (açıklama); `sapma_sigma` işaretli z-skorudur.
- `uyari_baslangic` / `uyari_saat`: sondan geriye doğru kesintisiz Normal dışı süre (0 ise uyarı yok).
- `sensor_sorunu` / `sensor_notu`: son saatte sensör sorunu varsa ayrı bir uyarı; ekipman arızası teşhisinden bağımsızdır.
- `sensor_sorunlari`: analiz aralığı boyunca sensör ve neden kodu bazında özet (`sensor`, `neden`, `ilk`, `son`, `saat`).
- Sensör denetimi sezgisel ve simülatör koşullarına göre ayarlıdır; saha alarmı olarak doğrulanmamıştır. Eşikler için
  [Veri formatı](veri-formati.md#51-sensör-sağlığı-veri-kalitesi-kontrolleri) sınırlarına bakın.
- `rapor`: veri kontrol raporu. `uyarilar` düzeltilen/atılan veriyi, `turetilen` varsayılan/türetilen alanları,
  `eksik_sensorler` ve `hesaplanamayan_oznitelikler` doğruluğu düşüren eksikleri bildirir. Gerçek veride mutlaka okuyun.
- `saatlik` (yalnızca `saatlik=true`): her ünite ve saat için tahmine ek olarak `sensor_sorunu` ve `sensor_notu` alanları.
- API'de zaman damgaları UTC'dir. Saat dilimsiz değer UTC kabul edilir; offset içeren JSON zamanları UTC'ye çevrilir.
  Tek JSON isteğinde saat dilimli ve saat dilimsiz değerleri karıştırmayın. Saatlik/ünite yanıtı UTC-naif ISO biçimindedir.
  CSV ve CLI dosyalarında da saat dilimsiz zaman UTC kabul edilir; yerel saatten gönderiyorsanız açık UTC offset'i ekleyin.

### POST /tahmin/olcumler

Bir ağ geçidi son ölçümleri JSON olarak gönderir. Yanıt biçimi `/tahmin/csv` ile aynıdır.

Her kayıt, CSV şemasının kanonik alanlarını taşır: `timestamp`, `t_amb`, `t_room`, `p_suc`, `p_dis`, `i_comp`
zorunludur (sensör okunamadıysa değer `null` olabilir, ama alan bulunmalıdır); `unit_id`, `tip`, `t_coil`, `sh`,
`sc`, `t_suc`, `t_liq`, `i_fan`, `vib`, `t_dis`, `comp_on`, `defrost`, `door_open`, `setpoint` isteğe bağlıdır.
Birimler ve eksik alanların etkisi için [Veri formatı](veri-formati.md). `setpoint`, `gauge`, `tip` ve `saatlik`
istek gövdesinde üst düzey alanlardır.

**En az bir gün (24 saat) geçerli veri** gerekir (ünite başına); güvenilir sonuç için 3+ gün önerilir. Bir istekte en fazla
500 ünite, ünite başına 366 gün ve hizalanmış toplam 1.000.000 beş dakikalık nokta kabul edilir. Servis
durum tutmadığından her çağrıda bu pencerenin tamamını gönderin (5 dakikalık örneklemde ünite başına gün başına
288 kayıt). JSON ucu ayrıca en fazla 200.000 ham ölçüm kaydı kabul eder; her iki uçtaki HTTP gövdesi varsayılan
20 MB ile sınırlıdır (`SOGUTMA_MAKS_YUKLEME_MB`).

```bash
curl -X POST http://localhost:8000/tahmin/olcumler \
  -H "Content-Type: application/json" \
  -d @istek.json
```
```json
{
  "tip": "dondurucu",
  "setpoint": -20,
  "saatlik": false,
  "olcumler": [
    {"timestamp": "2026-01-05T14:35:00Z", "unit_id": "D1", "t_amb": 24.1, "t_room": -19.6,
     "p_suc": 2.05, "p_dis": 13.8, "i_comp": 5.2, "i_fan": 0.9, "vib": 1.8,
     "comp_on": true, "defrost": false},
    {"timestamp": "2026-01-05T14:40:00Z", "unit_id": "D1", "t_amb": 24.1, "t_room": -19.5,
     "p_suc": 2.04, "p_dis": 13.9, "i_comp": 5.3, "i_fan": 0.9, "vib": 1.8,
     "comp_on": true, "defrost": false}
  ]
}
```
(Gerçek istekte en az 24 saatlik kayıt bulunmalıdır; yukarıdaki iki kayıt biçimi göstermek içindir ve
**422** döner.)

## 3. Kimlik doğrulama

`SOGUTMA_API_ANAHTARI` ortam değişkeni ayarlıysa `/saglik` dışındaki tüm uçlar `X-API-Anahtari` başlığı ister
(eksik ya da yanlışsa **401**). Anahtar kodda ya da depoda tutulmaz; ortam değişkeniyle verilir:

```bash
export SOGUTMA_API_ANAHTARI="$(openssl rand -hex 24)"
uvicorn api:app --port 8000
curl -H "X-API-Anahtari: $SOGUTMA_API_ANAHTARI" http://localhost:8000/model
```

Ayarlı değilse API herkese açıktır ve başlangıçta günlüğe uyarı yazılır. Ağa açılacak her kurulumda anahtar
belirleyin ve servisin önüne TLS sonlandıran bir ters vekil (nginx, Caddy, bulut yük dengeleyici) koyun;
API kendisi HTTPS sunmaz.

## 4. Hatalar

Hata yanıtları tek biçimdedir:

```json
{"hata": "veri_gecersiz", "mesaj": "Veri kullanılamıyor; ayrıntılar için 'ayrintilar' alanına bakın.",
 "ayrintilar": ["Zorunlu sütun(lar) eksik: t_amb (Dış ortam sıcaklığı) — kabul edilen adlar: ..."]}
```

| Kod | `hata` | Ne zaman |
|---|---|---|
| 401 | `yetkisiz` | API anahtarı eksik ya da yanlış |
| 413 | `cok_buyuk` | İstek ya da dosya üst sınırı aşıyor (varsayılan 20 MB) |
| 422 | `veri_gecersiz` | Veri ingest doğrulamasından geçmedi (zorunlu sütun yok, okunamayan zaman damgası, bilinmeyen `tip`, ünite başına 24 saatten az veri, ...) |
| 422 | `yetersiz_veri` | Dosya geçerli ama saatlik öznitelik üretmek için çok kısa ya da çok boşluklu |
| 422 | `istek_gecersiz` | İstek gövdesi/parametreleri geçersiz (ör. `olcumler[3].t_room: sayı bekleniyor`) |
| 503 | `model_yok`, `model_eski`, `model_okunamadi` | Model dosyası yok, eski sürümde ya da okunamıyor: `python train.py` çalıştırın |
| 500 | `sunucu_hatasi` | Beklenmeyen hata; ayrıntı yalnızca sunucu günlüğündedir |

## 5. Docker ve docker compose

Depoda tek bir `Dockerfile` ve iki servisli `docker-compose.yml` vardır:

| Servis | Komut | Port | Sağlık denetimi |
|---|---|---|---|
| `api` | `uvicorn api:app` | 8000 | `GET /saglik` |
| `panel` | `streamlit run app.py` | 8501 | `GET /_stcore/health` |

```bash
docker compose up --build                     # panel: http://localhost:8501   API: http://localhost:8000/docs
SOGUTMA_API_ANAHTARI="$(openssl rand -hex 24)" docker compose up -d --build   # anahtarla
docker compose ps                             # sağlık durumu
docker compose down                           # durdur (birimler kalır)
docker compose down -v                        # birimleri de sil (modeli imajdakine sıfırlar)
```

Tasarım kararları:

- **Model imaj derlenirken eğitilir** (`RUN python train.py`, sentetik veri): imaj kendi kendine yeterlidir,
  ilk istek eğitim beklemez, ilk açılışta internet gerekmez. Bedeli daha uzun derleme süresidir.
  Model sürümü değişirse (`MODEL_VERSION`) `docker compose build` ile imajı yenileyin.
- İmaj **root olmayan** kullanıcıyla (`app`, uid 10001) çalışır; pip önbelleği imaja alınmaz.
- İki servis aynı imajı kullanır ve `veri` (`/app/data`) ile `modeller` (`/app/models`) isimli birimlerini
  paylaşır. Birimler ilk kullanımda imajdaki içerikle dolar. Panel modeli yeniden eğitirse API, yeniden
  başlatmadan yeni modeli yükler. İmaj güncellendikten sonra eski birimler eski modeli tutar: API 503 verirse
  `docker compose down -v` ile birimleri sıfırlayın.
- Portlar yalnızca `127.0.0.1`'e bağlanır. Ağa açmak için `docker-compose.yml`'de önekleri kaldırın ve
  mutlaka `SOGUTMA_API_ANAHTARI` verin; panelin kendisi kimlik doğrulama içermez.
- Sağlık denetimleri `curl` gerektirmez (slim imajda yoktur), Python ile yapılır.

## 6. Ağ geçidinden saatlik veri gönderme

Ağ geçidi (ya da yerel bir betik) her saat son 3 günün 5 dakikalık ölçümlerini gönderir ve yanıttaki
uyarıya göre hareket eder. Aşağıdaki örnek yalnızca standart kütüphane kullanır; `son_olcumler()` sizin
depolama katmanınızdan (SQLite, InfluxDB, ...) kayıtları okuyan, **sizin yazmanız gereken** bir işlevdir.

```python
"""saatlik_gonder.py: son 3 günün ölçümlerini API'ye gönderir (cron: 5 * * * *)."""
import json
import os
import urllib.error
import urllib.request

API = os.environ.get("SOGUTMA_API_ADRESI", "http://localhost:8000")
ANAHTAR = os.environ.get("SOGUTMA_API_ANAHTARI", "")


def son_olcumler(gun=3):
    """[{"timestamp": "2026-01-05T14:35:00", "unit_id": "D1", "t_amb": ..., "t_room": ..., ...}, ...]"""
    raise NotImplementedError("Kendi veri kaynağınızdan okuyun")


def tahmin_al(olcumler, tip="soguk_oda"):
    govde = json.dumps({"olcumler": olcumler, "tip": tip}).encode("utf-8")
    istek = urllib.request.Request(
        f"{API}/tahmin/olcumler",
        data=govde,
        headers={"Content-Type": "application/json", "X-API-Anahtari": ANAHTAR},
    )
    try:
        with urllib.request.urlopen(istek, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:  # 4xx/5xx: Türkçe hata gövdesini yazdır
        raise SystemExit(f"API hatası {e.code}: {e.read().decode('utf-8')}")


if __name__ == "__main__":
    yanit = tahmin_al(son_olcumler())
    for u in yanit["uniteler"]:
        print(f"{u['unit_id']}: {u['durum']} (sağlık {u['saglik']:.0f}), {u['ariza_adi']}")
        if u["durum"] != "Normal":
            print(f"  Öneri: {u['oneri']}")  # burada bildirim gönderebilirsiniz
```

Bildirim (e-posta / Telegram / webhook) kuralları için [Bildirimler](bildirimler.md) belgesine; ağ geçidi
ve MQTT tasarımı için [Saha Mimarisi](saha-mimarisi.md) belgesine bakın.

## 7. Sınırlar ve güvenlik notları

- Yükleme sınırı Content-Length başlığına ve okunan gövdeye uygulanır; asıl koruma için servisin önüne
  koyacağınız ters vekilde de bir gövde boyutu sınırı tanımlayın.
- Servis analizi istek içinde, eşzamanlı çalıştırır; büyük dosyalar (birkaç yüz bin satır) saniyeler sürebilir.
  Çok sayıda istemci için birden çok işçi süreci çalıştırın (`uvicorn api:app --workers 4`).
- Model dosyası `joblib` ile okunur (pickle tabanlı): yalnızca kendi eğittiğiniz modelleri `models/`
  altına koyun, güvenmediğiniz kaynaktan model yüklemeyin.
- API anahtarı yalnızca basit bir erişim kapısıdır (tek paylaşılan anahtar); çok kiracılı kullanım, kullanıcı
  başına yetki ve hız sınırı bu sürümde yoktur (README yol haritasındaki "çoklu müşteri desteği" maddesi).
