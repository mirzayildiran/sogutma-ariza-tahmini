# Kendi Verinizi Kullanma: Veri Biçimi

Panelde **Kendi Verini Analiz Et** sayfasından CSV yükleyerek (`streamlit run app.py`) ya da panel
olmadan komut satırından, kendi sensör kayıtlarınızla tahmin alabilirsiniz:

```bash
python train.py                      # bir kez: modeli eğitir (models/predictor.joblib)
python predict.py veri.csv           # tahmin + Türkçe özet + saatlik rapor CSV'si
python predict.py veri.csv --setpoint 2.0 --gauge -o rapor.csv
```

Denemek için hazır bir örnek var: `python predict.py examples/ornek_veri.csv`
(10 günlük, Türkçe Excel biçiminde; 5. günden itibaren gaz kaçağı gelişen bir oda.
Yeniden üretmek için: `python examples/ornek_veri_uret.py`).

> [!IMPORTANT]
> Model **R404A** soğutucu akışkan ve simülatör verisiyle eğitildi. Başka bir gaz, farklı
> ünite tipi (dondurucu, market dolabı, chiller) ya da gerçek saha gürültüsü için sonuçlar
> **doğrulanmamıştır**; çıktıyı karar vermek için değil, ön gösterge olarak kullanın.

## 1. Sütunlar

CSV'nin ilk satırı başlık olmalıdır. Sütun sırası önemli değildir; başlıklar büyük/küçük harfe,
Türkçe karakterlere ve parantez içindeki birim ekine (`Oda Sıcaklığı (°C)`) duyarsızdır.
Tanınmayan sütunlar yok sayılır.

### Zorunlu

| Sütun (kanonik ad) | Birim | Açıklama | Kabul edilen başlık örnekleri |
|---|---|---|---|
| `timestamp` | tarih-saat | Ölçüm zamanı | `Tarih Saat`, `Zaman`, `timestamp`, `datetime`; ya da ayrı `Tarih` + `Saat` sütunları |
| `t_amb` | °C | Dış ortam (kondenser havası) sıcaklığı | `Dış Ortam Sıcaklığı`, `ambient_temp`, `dis_sicaklik` |
| `t_room` | °C | Oda (kabin) hava sıcaklığı | `Oda Sıcaklığı`, `room_temp`, `Kabin Sıcaklığı` |
| `p_suc` | bar (mutlak) | Emme (alçak taraf) basıncı | `Emme Basıncı`, `Alçak Basınç`, `suction_pressure`, `LP` |
| `p_dis` | bar (mutlak) | Basma (yüksek taraf) basıncı | `Basma Basıncı`, `Yüksek Basınç`, `discharge_pressure`, `HP` |
| `i_comp` | A | Kompresör akımı | `Kompresör Akımı`, `comp_current`, `Akım` |

### İsteğe bağlı

| Sütun | Birim | Yoksa ne olur? |
|---|---|---|
| `unit_id` | metin | Tek ünite varsayılır (`U1`). Birden çok ünite aynı dosyada olabilir (`Ünite`, `Oda No`, `Cihaz`). |
| `setpoint` | °C | `--setpoint` değeri, o da yoksa oda sıcaklığı medyanı kullanılır. |
| `comp_on` | 0/1 | `i_comp > 0,5 A` eşiğinden türetilir. |
| `defrost` | 0/1 | Hep 0 varsayılır; defrost sırasındaki batarya sıcaklığı sinyali kullanılamaz. |
| `door_open` | 0/1 | Modelde kullanılmaz. |
| `t_coil` | °C | Evaporatör batarya / defrost sensörü. Yoksa defrost tepe sıcaklığı sinyali kullanılamaz. |
| `sh` | K | Kızgınlık (superheat). Yoksa aşağıdaki `t_suc` ile türetilir; o da yoksa eksik kalır. |
| `sc` | K | Aşırı soğutma (subcooling). Yoksa `t_liq` ile türetilir; o da yoksa eksik kalır. |
| `t_suc` | °C | Emme hattı sıcaklığı. Yalnızca `sh` türetmek için okunur: `sh = t_suc − Tdoyma(p_suc)`. |
| `t_liq` | °C | Sıvı hattı sıcaklığı. Yalnızca `sc` türetmek için okunur: `sc = Tdoyma(p_dis) − t_liq`. |
| `i_fan` | A | Kondenser fan akımı. Fan arızası ayrımı zayıflar. |
| `vib` | mm/s | Kompresör titreşimi. Kompresör aşınması ayrımı zayıflar. |
| `t_dis` | °C | Basma hattı sıcaklığı. |

Boole sütunlar `0/1`, `true/false`, `evet/hayır`, `açık/kapalı`, `on/off` olabilir.

## 2. Biçim kuralları

- **Ayraç**: `;`, `,` ya da sekme otomatik algılanır.
- **Ondalık**: `12,5` ve `12.5` ikisi de okunur (Türkçe Excel dışa aktarımı için).
- **Kodlama**: UTF-8 (BOM'lu olabilir) ya da Windows-1254 (Türkçe) otomatik algılanır.
- **Zaman damgası**: `05.01.2026 14:35`, `05.01.2026 14:35:20`, `2026-01-05 14:35:00`,
  `05/01/2026 14:35` biçimleri. Gün/ay sıralı (gg.aa.yyyy) kabul edilir. Saat dilimi bilgisi atılır.
- **Boş değer**: boş hücre, `-`, `NaN`, `N/A` eksik sayılır. `Err` gibi okunamayan değerler de eksik
  sayılır ve uyarı verilir.
- Üst bilgi/açıklama satırları olmamalıdır; başlık dosyanın ilk satırıdır.

## 3. Basınç nasıl ölçülmeli?

Model **mutlak basınç (bar)** bekler. Manometre ve çoğu kontrol cihazı **efektif (gauge)** değer
gösterir; mutlak = efektif + 1,013 bar. Verileriniz efektif ise `--gauge` verin
(`load_csv(..., gauge=True)`), dönüşüm otomatik yapılır. Birim bar değilse (psi, kPa, MPa) dosyayı
vermeden önce bara çevirin. Yanlış birim, `p_suc ≥ p_dis` ya da fiziksel olmayan değerler olarak
yakalanır ve hata verir.

Basınç sensörlerini kompresörün emme ve basma hattından, kompresöre yakın bir noktadan okuyun;
kontrol cihazının ekranında gösterilen değerle aynı noktayı kullanmak tutarlılık sağlar.

## 4. Asgari veri gereksinimi

| Konu | Gereksinim |
|---|---|
| Süre | Ünite başına **en az 24 saat** geçerli veri (aksi hâlde hata). Güvenilir sonuç için **3+ gün**, tercihen birkaç hafta. İlk 12 saat öznitelik penceresi doldurmak için tahmin dışında kalır. |
| Örnekleme | **1–5 dakika** önerilir. Veri 5 dakikalık ızgaraya hizalanır (sık veri ortalanır). 5 dakikadan seyrek veride uyarı verilir; **30 dakikadan seyrek veri reddedilir**. |
| Boşluklar | 30 dakikaya kadar boşluklar son değerle doldurulur. Daha uzun boşluklar eksik kalır; verisi yarıdan azı dolu saatler rapora girmez. |
| Yinelenen satır | Aynı ünite ve zaman damgası için sonuncusu tutulur. |
| Sıralama | Satırların sıralı olması gerekmez. |
| Fiziksel sınırlar | Sıcaklıklar −60…80 °C (basma hattı −60…150), basınç 0,1…45 bar, akım 0…500 A. Küçük bir kısmı dışındaysa değerler eksik sayılır ve uyarı verilir; %10'dan fazlası dışındaysa dosya reddedilir. Kompresör çalışırken emme basıncı basma basıncına eşit/büyükse aynı kural uygulanır. |
| Kompresör | Kayıt süresince kompresör en az birkaç kez çalışmış olmalıdır; model çalışma anındaki değerleri değerlendirir. |

## 5. Eksik sensörler

Zorunlu 5 sinyal dışındaki sensörler yoksa program **çalışır**, ancak o sinyalden gelen kanıt
kullanılamaz:

- Eksik öznitelikler, modelin sağlıklı eğitim verisindeki ortalamasıyla doldurulur (`FaultPredictor`
  içinde, tahmin sırasında). Yani eksik sensör ne "arıza" ne "sağlıklı" kanıtı sayılır; yanlış alarm
  üretmez. Anomali tespiti (Isolation Forest) NaN kabul etmediği için bu doldurma zorunludur;
  sınıflandırıcı ve süre tahmincisinde de belirsiz bir karar dalı yerine öngörülebilir bir davranış
  seçilmiştir.
- Kızgınlık ve aşırı soğutma fiziksel olarak yalnızca ek sıcaklık sensörüyle (`t_suc`, `t_liq`)
  hesaplanabilir; başka sinyallerden **uydurulmaz**.
- Simülasyonla yapılan denemede (titreşim, fan akımı, `sh`, `sc`, basma sıcaklığı, batarya sıcaklığı
  yokken) sağlıklı üniteler alarm vermedi ve arızalar yine yakalandı; ancak **arıza türü yanlış
  atfedilebilir** (ör. kompresör aşınması "kondenser kirlenmesi" görünebilir). Tür tahminine
  güvenmek için ilgili sensörleri ekleyin: titreşim → kompresör, fan akımı → fan, `sh`/`sc` → gaz kaçağı.

## 6. Örnek satırlar

Türkçe Excel biçimi (`;` ayraç, `,` ondalık), `examples/ornek_veri.csv`:

```
Tarih Saat;Oda Sıcaklığı (°C);Dış Ortam Sıcaklığı (°C);Emme Basıncı (bar);Basma Basıncı (bar);Kompresör Akımı (A);...
01.01.2026 00:00;1,69;22,2;6,12;11,81;0,0;...
01.01.2026 00:05;1,88;22,2;6,13;11,75;0,0;...
```

Yalnızca zorunlu sütunlarla dosya biçimi (İngilizce başlıklar, `,` ayraç, `.` ondalık):

```
timestamp,t_amb,t_room,p_suc,p_dis,i_comp
2026-01-01 00:00:00,22.2,1.69,6.12,11.81,0.0
2026-01-01 00:05:00,22.2,1.88,6.13,11.75,0.0
2026-01-01 00:10:00,22.1,2.08,9.55,16.10,9.8
```

Birden çok ünite: her satıra bir `unit_id` sütunu ekleyin; her ünite ayrı analiz edilir.

## 7. Çıktı

`predict.py`, ünite başına son durumu yazdırır (sağlık skoru, durum, tahmini arıza türü, kalan süre,
normalden en çok sapan sinyaller) ve saatlik rapor CSV'si üretir:
`timestamp, unit_id, anomaly, p_<arıza>…, health, status, pred_fault, confidence, eta_h`.

Python'dan kullanım:

```python
from sogutma.ingest import load_csv
from sogutma.features import hourly_features

raw = load_csv("veri.csv", gauge=False, setpoint=2.0)
print(raw.attrs["rapor"].ozet())     # uyarılar, türetilen alanlar, eksik sensörler
H = hourly_features(raw)
```

Dosya kullanılamıyorsa `ValidationError` fırlatılır; `.hatalar` Türkçe mesajları içerir.

## 8. Kontrol cihazı / veri kaydedicisinden dışa aktarma (genel öneriler)

Cihazlar ve yazılımlar çok farklıdır; aşağıdakiler genel ipuçlarıdır, belirli bir üreticinin menü
yolu değildir. Ayrıntı için cihazınızın kullanım kılavuzuna ya da yazılım sağlayıcısına bakın.

- Çoğu kontrol cihazı ve izleme yazılımı geçmiş kayıtları CSV/Excel olarak dışa aktarabilir; yazılımda
  genellikle "geçmiş / trend / kayıt / rapor" adlı bir bölüm bulunur.
- Dışa aktarırken **en sık örnekleme aralığını** (1–5 dk) ve **birden çok haftalık** bir tarih aralığını
  seçin; mümkünse tüm ünitelerin aynı dosyada olmasını sağlayın.
- Alçak/yüksek basınç, oda ve dış sıcaklık, kompresör akımı kanallarının kayıt listesinde işaretli
  olduğunu doğrulayın. Kompresör akımı doğrudan yoksa bir akım trafosu/ölçüm modülü ile eklenebilir.
- Basıncın birimini (bar/psi, efektif/mutlak) dışa aktarım ayarlarında ya da kanal tanımında kontrol edin.
- Yalnızca ortalama değil, mümkünse anlık örnekler kaydedin; aşırı seyreltilmiş (ör. 1 saatlik) kayıtlar
  reddedilir.
- Excel'de "CSV (noktalı virgülle ayrılmış)" kaydı doğrudan okunur. Dosyayı Excel'de düzenlediyseniz tarih
  sütununun biçiminin değişmediğinden emin olun.
- Kayıtta cihazın saat dilimi/yaz saati kaymaları varsa zaman damgaları arasında boşluk ya da tekrar
  oluşabilir; program tekrarları siler, boşlukları yukarıdaki kurallarla ele alır.
- Titreşim ve fan akımı gibi sinyaller çoğu standart kontrol cihazında bulunmaz; bunlar için ayrı
  sensör/veri kaydedici gerekebilir. Bu sinyaller olmadan da sistem çalışır (bkz. bölüm 5).
