# Model Kartı — Soğutma Arıza Tahmini (Demo Prototip)

> **Durum:** Demo prototip. Model yalnızca **sentetik (simülatör) veri** ile eğitilmiş ve değerlendirilmiştir.
> Bu belgedeki başarım sayıları gerçek saha performansını **göstermez**; yalnızca yöntemin simülatör
> ortamında çalıştığının kanıtıdır. Gerçek performans pilot çalışmayla ölçülmelidir (bkz. [Pilotta doğrulanması gerekenler](#9-pilotta-doğrulanması-gerekenler)).

Sayısal sonuçlar, `python train.py` çalıştırılarak üretilen `models/metrics.json` dosyasından alınmıştır
(eğitim ve test verisi sabit rastgele tohumlarla üretildiği için sonuçlar yeniden üretilebilir).
Belgedeki kod ayrıntıları `train.py` ve `sogutma/` paketindeki kaynak koda dayanır.

## İçindekiler

1. [Genel bilgi](#1-genel-bilgi)
2. [Amaç ve kullanım alanı](#2-amaç-ve-kullanım-alanı)
3. [Veri: fizik esaslı simülatör](#3-veri-fizik-esaslı-simülatör)
4. [Öznitelikler](#4-öznitelikler)
5. [Modeller ve gerekçeleri](#5-modeller-ve-gerekçeleri)
6. [Sağlık skoru](#6-sağlık-skoru)
7. [Değerlendirme sonuçları](#7-değerlendirme-sonuçları)
8. [Sınırlılıklar ve riskler](#8-sınırlılıklar-ve-riskler)
9. [Pilotta doğrulanması gerekenler](#9-pilotta-doğrulanması-gerekenler)

---

## 1. Genel bilgi

| Alan | Değer |
|---|---|
| Ad | Soğutma Arıza Tahmini — kestirimci bakım modeli |
| Sürüm | Demo prototip |
| Görev | Soğuk oda soğutma ünitelerinde arıza türü tespiti, arızaya kalan süre tahmini, 0–100 sağlık skoru |
| Girdi | Saatlik öznitelikler (5 dakikalık sensör verisinden, 12 saatlik kayan pencere ile) |
| Çıktı | Sağlık skoru, durum (Normal / İzlemede / Kritik), olası arıza türü, güven, tahmini arızaya kalan süre, anomali skoru |
| Kütüphane | scikit-learn (`IsolationForest`, `HistGradientBoostingClassifier`, `HistGradientBoostingRegressor`) |
| Kod | `sogutma/model.py`, `sogutma/features.py`, `sogutma/simulator.py`, `train.py` |
| Lisans | MIT (bkz. [LICENSE](../LICENSE)) |

```mermaid
flowchart LR
    A["Sensör verisi<br/>5 dakikada bir"] --> B["Saatlik öznitelikler<br/>12 saatlik pencere"]
    B --> C["Anomali tespiti<br/>Isolation Forest"]
    B --> D["Arıza sınıflandırma<br/>Gradient Boosting"]
    B --> E["Kalan süre regresyonu<br/>Gradient Boosting"]
    C --> F["Sağlık skoru<br/>0-100"]
    D --> F
    F --> G["Durum ve uyarı"]
    D --> G
    E --> G
```

## 2. Amaç ve kullanım alanı

### Amaç

Soğuk odalardaki soğutma sistemlerinde, ürün bozulmasına yol açan arızaları **gerçekleşmeden önce**
fark etmek ve servis ekibine arıza türü, aciliyet ve önerilen aksiyon bilgisiyle haber vermek.

### Hedeflenen kullanım

- Servis ve bakım planlamasına **karar destek** aracı olarak kullanım (hangi ünite, hangi arıza şüphesi, yaklaşık ne kadar süre var).
- Teknik ekibin ziyaret önceliklerini belirlemesi.
- Pilot saha çalışmalarında modelin gerçek veride davranışının incelenmesi (gölge modu).
- Arıza senaryolarının eğitim/gösterim amaçlı canlandırılması (Streamlit paneli).

### Kapsam dışı kullanımlar

- **Güvenlik fonksiyonu olarak kullanım:** Model; yüksek basınç şalteri, termik röle, alarm cihazları gibi koruma ekipmanlarının yerine geçmez. Soğutma sistemini otomatik olarak durdurmak/devreye almak için kullanılmamalıdır.
- **Resmî sıcaklık kaydı veya gıda güvenliği (ör. HACCP) kaydı** yerine kullanım.
- Gerçek veriyle doğrulanmadan **ticari taahhüt** (ör. "arızaların %X'ini yakalarız") verilmesi.
- Modelin eğitildiği koşulların dışındaki sistemlerde kullanım: dondurucular (−18 °C), market dolapları, chiller'lar, R404A dışı gazlar, çok kompresörlü/ kaskad sistemler (bkz. [Sınırlılıklar](#8-sınırlılıklar-ve-riskler)).
- Birden fazla arızanın aynı anda var olduğu veya arızanın sensör bozukluğundan kaynaklandığı durumlarda tek başına karar verme.

## 3. Veri: fizik esaslı simülatör

Gerçek saha verisi henüz yoktur. Veri, `sogutma/simulator.py` içindeki **basitleştirilmiş, fizik esaslı bir
soğuk oda simülatörü** ile üretilir. Simülatör termodinamik bir "dijital ikiz" değildir; arızaların
sensörlerde bıraktığı yönsel izleri (ör. gaz kaçağında emme basıncı düşer, kızgınlık artar) elle kurulmuş
denklemlerle taklit eder.

### Modellenen sistem

| Öğe | Simülatördeki karşılığı |
|---|---|
| Zaman adımı | 5 dakika; senaryo uzunluğu 30 gün |
| Soğutucu akışkan | R404A; doyma basıncı için yaklaşık üstel eğri: `p = exp(1.766 + 0.02838·T)` (bar, mutlak; T °C) |
| Termostat | Oda sıcaklığı `hedef + 1 K` üstüne çıkınca kompresör çalışır, `hedef − 1 K` altına inince durur |
| Defrost | 6 saatte bir, 20 dakika süreyle; defrostta kompresör durur ve batarya ısıtılır |
| Kapı açılışları | Her adımda olasılıkla; 07:00–19:00 arası %8, diğer saatlerde %1 (ünite kapı trafiği katsayısı ile çarpılır) |
| Dış ortam | 24 °C + ünite sapması + günlük rastgele sapma (σ = 2 K) + sinüs biçimli günlük döngü (±6 K) + ölçüm gürültüsü |
| Sensör gürültüsü | Gauss gürültüsü eklenir |
| Üretilen sinyaller | Dış ortam ve oda sıcaklığı, evaporatör batarya sıcaklığı, emme/basma basıncı, kızgınlık, aşırı soğutma, kompresör akımı, fan akımı, titreşim, basma hattı sıcaklığı, kompresör/defrost/kapı durumları |

Kompresör durduğunda basınç, kızgınlık, aşırı soğutma ve akım gibi sinyaller anlamsızdır; öznitelik
çıkarımında bu sinyaller yalnızca kompresör çalışırken (ve defrostta değilken) kullanılır.

### Arıza türleri

Beş arıza türü modellenmiştir; her ünitede **tek bir arıza** vardır veya hiç yoktur.

| Anahtar | Arıza | Simülatörde etki edilen sinyaller (şiddet arttıkça) |
|---|---|---|
| `gaz_kacagi` | Soğutucu gaz kaçağı | Soğutma kapasitesi düşer; emme basıncı düşer; kızgınlık yükselir; aşırı soğutma azalır; kompresör akımı biraz düşer; basma hattı sıcaklığı yükselir |
| `kondenser_kirlenmesi` | Kondenser kirlenmesi | Yoğuşma sıcaklığı (dolayısıyla basma basıncı ve kompresör akımı) artar; kapasite düşer |
| `evaporator_buzlanma` | Evaporatör buzlanması / defrost arızası | Evaporasyon sıcaklığı ve kızgınlık düşer; kapasite düşer; defrostta batarya yeterince ısınmaz |
| `kompresor_asinmasi` | Kompresör aşınması | Titreşim (şiddetin karesiyle) ve kompresör akımı artar; basma hattı sıcaklığı yükselir; kapasite düşer |
| `fan_arizasi` | Kondenser fanı arızası | Fan motor akımı ve yoğuşma sıcaklığı artar; kapasite düşer |

### Şiddet eğrisi ve arıza anı

Her arıza, `fault_start_h` anında başlar ve `fault_duration_h` süresince ilerleyen bir **şiddet** (0 → 1)
değeri taşır:

```
x      = (t − arıza_başlangıcı) / arıza_süresi      (0 ile 1 arasına kırpılır)
şiddet = x ^ 1.6
```

Bozulma başta yavaştır, sona doğru hızlanır. (Kondenser fanı arızasında fanın etkileri `şiddet^1.5`
ile ölçeklenir.) Şiddet 1'e ulaştığında ünite artık sıcaklığı tutamaz; bu an **arıza anı** kabul edilir.
`hours_to_failure` etiketi, arıza anına kalan saattir.

### Etiketleme

Bir saat, o saatteki en yüksek şiddet **≥ 0,1** ise arıza türü etiketini alır; aksi halde `normal`
etiketi verilir (`hourly_features`, `label_threshold=0.1`). Şiddet `x^1.6` olduğundan eşik, arıza süresinin
yaklaşık ilk %24'ü geçildiğinde aşılır. Yani arızalı ünitelerin erken dönem (şiddet < 0,1) saatleri
`normal` olarak etiketlenir. "Gerçek" etiketler simülatörün tanımıdır; gerçek sahada bu etiketler servis
kayıtlarından türetilecektir ve çok daha belirsiz olacaktır.

### Eğitim ve test filoları

| | Eğitim | Test |
|---|---|---|
| Ünite sayısı | 120 | 40 |
| Süre | 30 gün | 30 gün |
| Rastgele tohum | 1 | 2 |
| Arıza oranı | Üniteleri %75 olasılıkla arızalı (`fault_ratio=0.75`) | Aynı süreç |
| Test filosunun gerçekleşen bileşimi | — | 10 sağlıklı + 30 arızalı ünite (gaz kaçağı 7, kondenser 3, buzlanma 4, kompresör 5, fan 11); 30 arızalı ünitenin 25'inde arıza anı 30 günlük pencerenin içindedir |

Rastgele filoda ünite parametreleri: hedef sıcaklık 0–5 °C, kapasite 0,9–1,15, dış ortam sapması
N(0, 3 K), kapı trafiği 0,5–1,6; arıza süresi 4–14 gün; arıza başlangıcı 3. günden itibaren
(`3·24` saat ile `gün·24 − 0,5·süre` saat arasında düzgün dağılım). Test filosu, eğitimde hiç kullanılmayan
farklı **üniteler**dir; ancak aynı simülatörden ve aynı parametre dağılımından üretilmiştir.

Her ünitenin ilk 12 saati (ısınma/pencere dolumu) öznitelik tablosundan çıkarılır.

## 4. Öznitelikler

Modele giren 15 öznitelik (`sogutma/features.py`). Ham 5 dakikalık veri önce **saatlik** olarak
toplanır, sonra **12 saatlik kayan ortalama** (`WINDOW_H = 12`, `min_periods=2`) ile yumuşatılır.
Kompresör durduğu (veya defrosttaki) anlarda anlamsız olan sinyaller yalnızca kompresör
çalışırken hesaba katılır.

| Öznitelik | Anlamı | Hesaplama |
|---|---|---|
| `p_suc` | Emme basıncı (bar) | Çalışma anlarının ortalaması |
| `p_dis` | Basma basıncı (bar) | Çalışma anlarının ortalaması |
| `sh` | Kızgınlık / superheat (K) | Çalışma anlarının ortalaması |
| `sc` | Aşırı soğutma / subcooling (K) | Çalışma anlarının ortalaması |
| `i_comp` | Kompresör akımı (A) | Çalışma anlarının ortalaması |
| `i_fan` | Kondenser fan akımı (A) | Çalışma anlarının ortalaması |
| `vib` | Kompresör titreşimi (mm/s) | Çalışma anlarının ortalaması |
| `t_dis` | Basma hattı sıcaklığı (°C) | Çalışma anlarının ortalaması |
| `cond_approach` | Yoğuşma − dış ortam farkı (K) | `T_sat(p_dis) − t_amb`; çalışma anlarında |
| `evap_delta` | Oda − evaporatör farkı (K) | `t_room − T_sat(p_suc)`; çalışma anlarında |
| `duty` | Kompresör çalışma oranı | Saatte kompresörün çalıştığı (defrost dışı) zaman oranı |
| `starts` | Saatlik kompresör kalkışı | Saatteki kalkış sayısı |
| `t_room_dev` | Oda sıcaklığı sapması (K) | `t_room − hedef sıcaklık` |
| `coil_defrost_peak` | Defrostta batarya tepe sıcaklığı (°C) | Defrost anlarındaki en yüksek batarya sıcaklığı; son 12 saatin maksimumu (defrost 6 saatte bir olduğundan pencerede en az bir defrost bulunur) |
| `t_amb` | Dış ortam sıcaklığı (°C) | Saatlik ortalama |

Notlar:

- `T_sat(p)`, simülatördeki R404A yaklaşık eğrisinin tersidir; başka gazlar için değiştirilmelidir.
- Eksik saatler (ör. hiç çalışmama) önceki/sonraki değerle doldurulur (`ffill`/`bfill`).
- Kapı açılış bilgisi simülatörde üretilir ancak **modele öznitelik olarak verilmez**.
- Öznitelikler 12 saatlik pencerenin ortalaması olduğundan model ani olayları değil, **saatler-günler mertebesinde ilerleyen** bozulmaları yakalar.

## 5. Modeller ve gerekçeleri

| Model | Görev | Yapılandırma | Eğitim verisi | Neden? |
|---|---|---|---|---|
| **Isolation Forest** | Anomali tespiti | 300 ağaç, `random_state=0`; girdi `StandardScaler` ile ölçeklenir | Yalnızca şiddeti 0 olan saatler | Arıza etiketi gerektirmez; gerçek sahada ilk günden çalışabilir ve eğitilmemiş arıza türlerine karşı da bir güvenlik ağı sağlar |
| **HistGradientBoostingClassifier** | Arıza türü (6 sınıf: normal + 5 arıza) | `max_iter=200`, `learning_rate=0.05`, `max_leaf_nodes=15`, `min_samples_leaf=80`, `l2_regularization=1.0`, `random_state=0` | Tüm eğitim saatleri | Tablo verisinde güçlü ve hızlıdır; olasılık çıktısı verir; küçük veriyle iyi çalışır |
| **HistGradientBoostingRegressor** | Arızaya kalan süre (saat) | `max_iter=300`, `random_state=0`; hedef `log1p(min(süre, 336 saat))` | Arıza etiketli ve arıza anına kalan süresi > 0 olan saatler | Bakım planlaması için tahmini süre; log ölçeği yakın arızalarda göreli doğruluğu öne çıkarır |

Ek ayrıntılar:

- Ham anomali skoru, sağlıklı eğitim verisinin dağılımına göre normalleştirilir:
  `anomali = clip((s50 − s) / (s50 − s1) / 2, 0, 1)`; burada `s` Isolation Forest skoru, `s50` ve `s1`
  sağlıklı verideki skorun medyanı ve %1'lik dilimidir. Sağlıklı medyan seviyesinde skor 0, sağlıklı verinin
  %1'lik kuyruğunda yaklaşık 0,5 olur.
- Kalan süre tahmini en fazla 14 günlük (336 saat) ufukla sınırlıdır ve yalnızca durum "Normal" değilken gösterilir.
- Hiperparametreler elle seçilmiştir; ayrı bir doğrulama kümesiyle ayarlanmamıştır.
- `FaultPredictor.explain` açıklama sağlar: sağlıklı verinin ortalama ve standart sapmasına göre z-skoru
  hesaplar, mutlak değeri ≥ 2 olan en çok sapan en fazla 4 sinyali listeler (`t_amb` hariç). Bu, modelin
  iç işleyişinin tam açıklaması değil, **sağlıklı davranıştan sapmanın** özetidir.

## 6. Sağlık skoru

Her saat için:

```
ham_sağlık = 100 × ( 0.6 × P(normal) + 0.4 × (1 − anomali) )
sağlık     = ham_sağlık'ın ünite bazında üstel ağırlıklı ortalaması (EWM, span = 12 saat), 1 ondalığa yuvarlanır
```

`P(normal)` sınıflandırıcının "normal" olasılığı, `anomali` yukarıda tanımlanan 0–1 aralığındaki skordur.

| Sağlık skoru | Durum |
|---|---|
| ≥ 75 | 🟢 Normal |
| 50 – 75 | 🟠 İzlemede |
| < 50 | 🔴 Kritik |

Davranış üzerine notlar:

- Durum "Normal" ise gösterilen arıza türü `normal` olur; "Normal" değilse, arıza olasılıkları arasında (normal hariç) en yüksek olan tür gösterilir. Güven değeri o türün olasılığıdır.
- Ağırlıklar (0,6 / 0,4) ve eşikler (75 / 50) kodda sabit değerlerdir; veriden kalibre edilmemiştir.
- Ağırlıklar nedeniyle yalnızca anomali skoru yüksek, sınıflandırıcı ise "normal" diyorsa ham sağlık en düşük **60** olur (İzlemede); yalnızca sınıflandırıcı arıza diyor, anomali skoru 0 ise **40** olur (Kritik). Yani anomali tek başına "Kritik" üretemez; Kritik için sınıflandırıcının arıza olasılığı gerekir.
- EWM yumuşatma, anlık gürültüye bağlı dalgalanmayı azaltır, ancak durum değişiminde birkaç saatlik gecikme yaratır.

## 7. Değerlendirme sonuçları

**Yöntem:** Model, 120 üniteli eğitim filosunda eğitildi; **eğitimde hiç görülmeyen** 40 üniteli test
filosunda (tohum 2) değerlendirildi (28 320 ünite-saat). Sınıflandırma metrikleri saatlik örnekler üzerindedir
ve `clf.predict` çıktısıyla hesaplanır; erken uyarı metrikleri ise tam hat (sağlık skoru → durum → arıza türü)
üzerinden hesaplanır. Kaynak: `models/metrics.json`.

> ⚠️ **Tüm sayılar sentetik veri üzerindedir.** Test filosu, eğitimle aynı simülatörden ve aynı parametre
> dağılımından üretildiği için dağılım kayması yoktur; bu nedenle sonuçlar gerçek sahadaki beklentiyi
> **olduğundan çok iyimser** gösterir.

### Genel sınıflandırma (saatlik)

| Metrik | Değer |
|---|---|
| Doğruluk (accuracy) | 0,9948 (%99,5) |
| Makro F1 | 0,9916 |
| Hatalı sınıflanan saat | 148 / 28 320 |

### Sınıf bazında sonuçlar

| Sınıf | Kesinlik (precision) | Duyarlılık (recall) | F1 | Örnek (saat) |
|---|---|---|---|---|
| Normal | 0,995 | 0,997 | 0,996 | 18 630 |
| Gaz kaçağı | 0,997 | 0,996 | 0,997 | 2 574 |
| Kondenser kirlenmesi | 0,991 | 0,991 | 0,991 | 1 135 |
| Evaporatör buzlanması | 0,983 | 0,994 | 0,988 | 625 |
| Kompresör aşınması | 0,995 | 0,974 | 0,984 | 1 587 |
| Kondenser fanı arızası | 0,994 | 0,993 | 0,993 | 3 769 |

### Karışıklık matrisi (satır: gerçek, sütun: tahmin; saat sayısı)

| Gerçek \ Tahmin | Normal | Gaz kaçağı | Kondenser | Buzlanma | Kompresör | Fan |
|---|---|---|---|---|---|---|
| **Normal** | 18 576 | 8 | 10 | 11 | 3 | 22 |
| **Gaz kaçağı** | 10 | 2 564 | 0 | 0 | 0 | 0 |
| **Kondenser** | 5 | 0 | 1 125 | 0 | 5 | 0 |
| **Buzlanma** | 4 | 0 | 0 | 621 | 0 | 0 |
| **Kompresör** | 42 | 0 | 0 | 0 | 1 545 | 0 |
| **Fan** | 28 | 0 | 0 | 0 | 0 | 3 741 |

148 hatanın 143'ü normal sınıfıyla karışıklıktan (arıza saatinin "normal" sayılması veya tersi) gelir;
arıza türleri arasındaki karışıklık yalnızca 5 saattir (kondenser kirlenmesi → kompresör aşınması). Doğruluk, çoğunluk sınıfı
(normal, saatlerin yaklaşık %66'sı) tarafından yukarı çekildiği için tek başına yanıltıcı olabilir; sınıf
bazında değerlere ve erken uyarı metriklerine birlikte bakılmalıdır.

### Erken uyarı (ünite bazında)

Tanım (`train.py`, `early_warning`):

- **Alarm:** Durumun **ardışık 6 saat** boyunca "Normal" dışında olması (`ALARM_H = 6`); alarm zamanı bu 6 saatlik dizinin ilk saatidir.
- **Yakalama:** Arızanın gerçek türü ile aynı türü tahmin eden ilk kalıcı alarm, arıza anından önce gelmişse "yakalandı" sayılır.
- **Erken uyarı süresi:** Arıza anı − ilk doğru kalıcı alarm anı.
- **Yanlış alarm:** Sağlıklı ünitede herhangi bir kalıcı alarm; arızalı ünitede arıza başlangıcından **önce** gelen kalıcı alarm.
- Yakalama oranı ve medyan süre yalnızca arıza anı 30 günlük pencerenin **içinde** olan arızalı ünitelerden (25 ünite) hesaplanır.

| Metrik | Değer |
|---|---|
| Arızadan önce yakalama oranı | 25 / 25 (%100) |
| Medyan erken uyarı süresi | 159 saat ≈ **6,6 gün** |
| En kısa / en uzun erken uyarı | ≈ 2,8 gün / ≈ 10,7 gün |
| Çeyrekler arası aralık (Q1–Q3) | ≈ 4,5 – 8,7 gün |
| Yanlış alarm veren ünite | **0 / 40** (10 sağlıklı ünitede de yanlış alarm yok) |

Arıza türüne göre medyan erken uyarı (ünite sayısı küçüktür, yalnızca yön göstericidir): gaz kaçağı
≈ 6,6 gün (7 ünite), kondenser kirlenmesi ≈ 8,0 (3), fan arızası ≈ 6,7 (9), buzlanma ≈ 6,8 (2),
kompresör aşınması ≈ 4,8 (4).

Küçük örneklem uyarısı: test filosunda 10 sağlıklı ünite vardır; "0 yanlış alarm" bu küçük sayıya dayanır.
**Kalan süre tahmininin hata miktarı (ör. MAE) bu çalışmada raporlanmamıştır.**

## 8. Sınırlılıklar ve riskler

### Veri ve doğrulama

| Sınırlılık | Etki / risk |
|---|---|
| **Yalnızca sentetik veri** | Etiketler (arıza türü, şiddet, arıza anı) simülatörün kendi tanımıdır. Model, gerçek ekipmanın değil simülatörün davranışını öğrenmiştir. |
| **Simülasyondan gerçeğe fark (sim-to-real gap)** | Gerçek sensör sapmaları, montaj farklılıkları, ürün yükü, mevsimsellik, farklı ekipman markaları ve bakım geçmişi modellenmemiştir. Gerçek sahada başarımın düşmesi beklenmelidir. |
| **Test filosu aynı dağılımdan** | Test ünitelerinin parametreleri aynı rastgele dağılımdan çekilmiştir; dağılım kayması test edilmemiştir. Ayrıca test örnekleri (saatlik, 12 saat pencereli) zamansal olarak yüksek korelasyonludur; fiilen bağımsız örnek sayısı saat sayısından çok daha düşüktür (ünite sayısı olan 40 mertebesindedir). |
| **Küçük test örneklemi** | 40 ünite (10 sağlıklı, 30 arızalı; bazı arıza türlerinde 2–4 ünite). Yüzdelerin belirsizlik aralığı geniştir; hata payı raporlanmamıştır. |
| **Hiperparametre ayarı yok** | Parametreler elle seçilmiştir; ayrı bir doğrulama kümesi kullanılmamıştır. |
| **Saat zamanı** | Saatlik öznitelik etiketi saat başını gösterir ancak o saatin verisini içerir; erken uyarı süreleri en fazla yaklaşık 1 saat iyimser olabilir (panelde de benzer şekilde). |

### Kapsam

| Sınırlılık | Etki / risk |
|---|---|
| **Tek arıza** | Her ünitede en fazla bir arıza vardır. Aynı anda birden fazla arıza, arıza etkileşimleri ve belirtilerin birbirini maskelemesi modellenmemiştir; sınıflandırıcı tek arıza varsayar. |
| **Sensör arızası yok** | Takılı kalan, kayan, kopuk veya yanlış kalibre sensörler simüle edilmemiştir. Sensör hatası model tarafından yanlışlıkla ekipman arızası (veya tersi) olarak yorumlanabilir. Veri kalite kontrolü ayrı bir katman olarak gereklidir. |
| **Yalnızca kademeli, monoton arızalar** | Arızalar düzgün biçimde ilerleyen bir şiddet eğrisine sahiptir. Ani arızalar (kompresör kilitlenmesi, kontaktör/elektrik arızası, enerji kesintisi), aralıklı arızalar ve iyileşen durumlar yoktur. |
| **Yalnızca R404A** | Doyma eğrisi ve `cond_approach`/`evap_delta` öznitelikleri R404A'ya göre kurulmuştur. Başka gazlar (R134a, R449A vb.) için eğri değiştirilmeli ve model yeniden eğitilmelidir. |
| **Yalnızca soğuk oda** | Hedef sıcaklık 0–5 °C aralığındadır. Dondurucu (−18 °C), market dolabı, chiller ve çok kompresörlü veya kaskad sistemler kapsam dışıdır. |
| **Tek tip ekipman** | Tek kompresör, tek evaporatör, sabit defrost takvimi (6 saatte 20 dk) varsayılmıştır; talebe bağlı defrost, değişken hızlı kompresör ve elektronik genleşme valfi davranışları yoktur. |
| **Kısıtlı ortam koşulları** | Dış ortam günlük sinüs döngüsü ve rastgele günlük sapma ile modellenmiştir; uzun vadeli mevsimsel değişim ve aşırı sıcak dalgaları yoktur. |
| **Ürün yükü ve operasyon** | Depolanan ürün miktarı, yeni ürün girişi, operatör müdahalesi ve ayar değişiklikleri modellenmemiştir. Kapı açılışı simüle edilir ama modele girmez. |

### Tahmin belirsizliği

| Sınırlılık | Etki / risk |
|---|---|
| **Arızaya kalan süre (ETA) belirsizliği** | Tahmin tek bir nokta değeridir; güven aralığı yoktur. Simülatördeki `x^1.6` bozulma eğrisini öğrenmiştir; gerçek bozulma hızları arızadan arızaya ve ekipmandan ekipmana çok değişir. ETA, kesin bir tarih olarak değil, "kabaca aciliyet derecesi" olarak okunmalıdır. Hata miktarı bu çalışmada ölçülmemiştir. |
| **ETA ufku** | Eğitimde 14 günle sınırlandırılmıştır; daha uzak arızalar için tahmin güvenilir değildir. |
| **Sağlık skoru ağırlıkları** | Ağırlıklar ve eşikler elle belirlenmiştir; kalibre edilmiş bir olasılık veya risk ölçüsü değildir. |
| **Güven değeri** | Sınıflandırıcı olasılıkları kalibre edilmemiştir; "%90 güven", gerçekte %90 doğru anlamına gelmeyebilir. |

### Operasyonel riskler

- **Yanlış alarm yorgunluğu:** Gerçek sahada yanlış alarm oranı yükselirse kullanıcı güveni düşer.
- **Yanlış güven:** Sistemin "Normal" demesi, ünitenin arızasız olduğu anlamına gelmez; model yalnızca öğrendiği arıza türleri ve sinyaller için uyarı verebilir.
- **Model dosyası güvenliği:** Model `joblib` (pickle) ile saklanır; güvenilmeyen kaynaklardan gelen model dosyaları yüklenmemelidir.
- **Veri gizliliği:** Gerçek müşteri verisi toplandığında KVKK kapsamında değerlendirme yapılmalıdır.

## 9. Pilotta doğrulanması gerekenler

Aşağıdaki tablodaki kabul ölçütleri **önerilen hedeflerdir**; pilot başlamadan önce ilgili taraflarca
netleştirilmelidir.

| # | Doğrulanacak konu | Nasıl ölçülür? | Önerilen kabul ölçütü |
|---|---|---|---|
| 1 | Veri kalitesi ve sensör güvenilirliği | Eksik veri oranı, takılı/aralık dışı değer sayısı, kalibrasyon kontrolü | [pilotta belirlenecek] |
| 2 | Gerçek veride **yanlış alarm oranı** | Ünite başına ay başına yanlış alarm; sağlıklı üniteler üzerinde gölge modunda (uyarı göndermeden) en az [N] hafta | [hedef belirlenecek] |
| 3 | Gerçek arızalarda **yakalama oranı** ve erken uyarı süresi | Servis kayıtlarıyla eşleştirilmiş arıza olayları; alarm anı ile arıza/servis anının karşılaştırılması | [hedef belirlenecek] |
| 4 | Arıza türü doğruluğu | Teknisyenin sahada teyit ettiği arıza nedeni ile modelin tahmininin karşılaştırılması | [hedef belirlenecek] |
| 5 | **ETA doğruluğu** | Gerçekleşen kalan süre ile tahminin hatası (MAE, tahmin aralığı kapsama oranı) | [hedef belirlenecek] |
| 6 | Sim-to-real farkı | Simülatör dağılımı ile gerçek veri dağılımının öznitelik bazında karşılaştırılması; sağlıklı gerçek veride anomali skoru dağılımı | Kalibrasyon sonrası sağlıklı ünitelerin çoğunluğu "Normal" |
| 7 | Eşiklerin kalibrasyonu | Sağlık skoru eşikleri (75/50) ve alarm kuralı (6 saat) için ROC/PR analizi | Hedeflenen yanlış alarm/yakalama dengesi |
| 8 | Çoklu arıza ve sensör arızası davranışı | Sensör kopması/kayması senaryolarının sahada veya kontrollü olarak denenmesi | Sensör hatasının ayrı bir uyarı olarak işaretlenmesi |
| 9 | Ekipman çeşitliliği | Farklı marka/kapasite/gaz/hedef sıcaklıkta ünitelerde başarımın karşılaştırılması | [pilotta belirlenecek] |
| 10 | Kullanıcı kabulü ve iş akışı | Servis ekibinin uyarıyı anlaması, aksiyon alması, uyarıdan arıza önlemeye kadar geçen süre | [pilotta belirlenecek] |
| 11 | Modelin zamanla sapması (drift) | Mevsimsel değişim, bakım sonrası davranış; yeniden eğitim ihtiyacının izlenmesi | Düzenli izleme planı |

**Önerilen pilot yaklaşımı:** Önce 1–3 ay **gölge modu** (uyarı göndermeden veri toplama ve model çıktılarını
kaydetme), ardından servis kayıtlarıyla eşleştirme, simülatör eğitimli modelin gerçek veriyle ince ayarı
(transfer öğrenme) ve ancak sonrasında canlı uyarılar. Saha mimarisi için bkz. [saha-mimarisi.md](saha-mimarisi.md),
proje çerçevesi için [proje-dokumani.md](proje-dokumani.md).
