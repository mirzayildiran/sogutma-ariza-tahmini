# Bildirimler: E-posta, Telegram, Webhook

Saatlik tahminlerden **uyarı olayları** üretir ve bunları konsola, e-postaya, Telegram'a ya da bir
webhook adresine gönderir. Kod: `sogutma/bildirim.py`; komut satırı: `predict.py --bildirim`.

> [!IMPORTANT]
> Bildirimler model çıktısına dayanır; model **sentetik veriyle** eğitilmiştir ve çıktısı ön göstergedir
> (bkz. [Model Kartı](model-karti.md)). Mesajlar saha kontrolünün yerine geçmez.
> **Varsayılan davranış deneme modudur: hiçbir şey gönderilmez.**

## 1. Nasıl çalışır?

```
predict.py → saatlik tahmin tablosu → olay üretimi → kanallar → durum dosyası
                                      (kurallar)     (konsol/e-posta/Telegram/webhook)
```

Her ünitenin saatlik satırları sırayla işlenir:

| Olay | Ne zaman doğar? |
|---|---|
| `uyari` | Normal → İzlemede geçişi `surekli_saat` saat sürerse |
| `kritik` | Normal → Kritik geçişi `surekli_saat` saat sürerse |
| `yukselis` | İzlemede → Kritik (kademeli yükselme: önce İzlemede, sonra Kritik bildirilir) |
| `iyilesme` | Normal'e dönüş `iyilesme_saat` saat sürerse (yalnızca daha önce uyarı gittiyse) |
| `ariza_degisti` | Alarm sürerken tahmin edilen arıza türü kalıcı olarak değişirse |
| `hatirlatma` | `hatirlatma: true` ise ve alarm `cooldown_saat` sonra hâlâ sürüyorsa |

Kurallar:

- **Süreklilik:** Anlık dalgalanma uyarı üretmez. Durum art arda `surekli_saat` saat sürmelidir
  (`train.py` içindeki 6 saatlik `ALARM_H` fikriyle uyumlu; varsayılan İzlemede için 6, Kritik için 3 saat).
  Kritik saatleri "en az İzlemede" sayılır.
- **Cooldown (tekrar engelleme):** Aynı (ünite, durum, arıza türü) için `cooldown_saat` içinde ikinci mesaj
  gitmez. Durum dalgalansa bile aynı arıza için 12 saat içinde tekrar uyarı almazsınız.
- **Asgari güven:** Yükselme ancak o saatin güveni `min_guven` ve üzerindeyse bildirilir.
- **Eski olaylar:** `en_eski_olay_saat` (varsayılan 48) saatten eski geçişler gönderilmez; örneğin geçmişe dönük
  bir CSV'yi ilk kez çalıştırdığınızda yüzlerce eski mesaj gitmez. Alarm hâlâ sürüyorsa güncel durum
  **bir kez** bildirilir.
- **Sessiz saatler:** Bu aralıkta üretilen olaylar **ertelenir** (durum dosyasında bekler) ve aralık bitince
  gönderilir. `kritik_haric: true` ise Kritik olaylar sessiz saatte de hemen gider. Sessiz saat, tablodaki
  son zaman damgasına göre değerlendirilir (canlı veride "şimdi" demektir).
- **Durum dosyası:** Son işlenen saat, ünitenin açık alarm durumu, cooldown kayıtları ve bekleyen olaylar
  JSON dosyasında saklanır. Saatlik çalışan bir cron işi aynı veriyi tekrar işlese bile eski uyarılar
  yeniden gitmez.
- **Hata yönetimi:** Ağ/sunucu hataları yakalanır, 3 kez (1 sn, 2 sn bekleyerek) denenir ve özetle raporlanır;
  hat asla çökmez. Yanlış parola ve 4xx yanıtları gibi kalıcı hatalar tekrar denenmez. Tüm kanalları
  başarısız olan olay durum dosyasında saklanır ve sonraki çalıştırmada (en fazla 3 kez) yeniden denenir.

### Mesaj örneği

```
🔴 KRİTİK · Soğuk Oda A1 · Soğutucu gaz kaçağı (güven %97) · tahmini arıza ~2 gün · Öneri: Kaçak testi yapın ...
🟢 NORMALE DÖNDÜ · Soğuk Oda A1 · Soğutucu gaz kaçağı belirtisi geçti · sağlık 91/100
```

Tek satırlık biçim konsol ve Telegram içindir; e-posta çok satırlı biçimi (zaman, sağlık skoru, belirtiler,
öneri) kullanır. Webhook hem yapılandırılmış olayı hem kısa metni gönderir.

## 2. Kullanım

```bash
# 1) Örnek ayarı kopyalayın ve düzenleyin
cp examples/bildirim_ayarlari.ornek.json bildirim_ayarlari.json

# 2) Deneme modu (varsayılan): mesajlar yazdırılır, hiçbir şey gönderilmez, durum dosyası yazılmaz
python predict.py veri.csv --bildirim bildirim_ayarlari.json

# 3) Gerçek gönderim
python predict.py veri.csv --bildirim bildirim_ayarlari.json --gonder
```

`--gonder` verilmezse ayar dosyasında `deneme_modu: false` yazsa bile **deneme modu** geçerlidir (komut
satırı güvenli tarafı seçer). Durum dosyası `--durum dosya.json` ile değiştirilebilir (varsayılan
`data/bildirim_durumu.json`). Bildirim bayrakları verilmezse `predict.py` çıktısı değişmez.

### Saatlik cron örneği

Sensör CSV'sinin her saat güncellendiğini varsayarsak (`crontab -e`):

```cron
5 * * * *  cd /opt/sogutma && . .venv/bin/activate && \
  SOGUTMA_TELEGRAM_TOKEN="..." SOGUTMA_SMTP_PAROLA="..." SOGUTMA_WEBHOOK_SIRRI="..." \
  python predict.py /var/sogutma/son_veri.csv --bildirim /etc/sogutma/bildirim_ayarlari.json --gonder \
  >> /var/log/sogutma-bildirim.log 2>&1
```

Sırları doğrudan crontab satırına yazmak yerine, yalnızca sahibinin okuyabildiği (`chmod 600`) bir dosyadan
yüklemek daha güvenlidir:

```cron
5 * * * *  cd /opt/sogutma && set -a && . /etc/sogutma/ortam && set +a && .venv/bin/python predict.py ... --gonder
```

## 3. Ayar başvurusu (JSON)

Bilinmeyen anahtarlar hata verir (yazım hatalarını yakalamak için). `_` ile başlayan anahtarlar not
olarak yok sayılır. Örnek dosya: [`examples/bildirim_ayarlari.ornek.json`](../examples/bildirim_ayarlari.ornek.json).
TOML yalnızca Python 3.11+ (ya da `tomli` kuruluysa) desteklenir; JSON önerilir.

| Anahtar | Tür / varsayılan | Açıklama |
|---|---|---|
| `deneme_modu` | bool, `true` | Doğrudan API kullanımında geçerli; `predict.py` bunu `--gonder` ile belirler |
| `bildirilen_durumlar` | liste, `["İzlemede","Kritik"]` | Hangi durumlar bildirilsin (`"İzlemede"`, `"Kritik"`) |
| `iyilesme_bildir` | bool, `true` | Normal'e dönüş bildirilsin mi |
| `cooldown_saat` | sayı, `12` | Aynı ünite+durum+arıza için tekrar engelleme süresi |
| `surekli_saat` | sayı ya da `{"İzlemede": n, "Kritik": n}`, `{6, 3}` | Durumun kalıcı sayılması için gereken ardışık saat |
| `iyilesme_saat` | sayı, `6` | Normal'in kalıcı sayılması için gereken ardışık saat |
| `min_guven` | 0–1, `0.5` | Yükselme/hatırlatma için asgari model güveni |
| `hatirlatma` | bool, `false` | Alarm sürerken cooldown dolunca hatırlatma gönder |
| `en_eski_olay_saat` | sayı ya da `null`, `48` | Bundan eski geçişler gönderilmez; `null` = sınırsız |
| `sessiz_saatler` | nesne | `aktif` (false), `baslangic` ("22:00"), `bitis` ("07:00"), `kritik_haric` (true); gece yarısını aşan aralık desteklenir |
| `birim_adlari` | nesne | `{"U1": "Soğuk Oda A1"}`; yoksa "Ünite U1" |
| `kanallar` | nesne | Aşağıda |

### Kanallar (`kanallar.<ad>`; hepsinde `aktif: true/false`)

| Kanal | Alanlar | Gizli bilgi (ortam değişkeni) |
|---|---|---|
| `konsol` | yalnızca `aktif` | yok |
| `eposta` | `sunucu`, `port` (587), `guvenlik` (`"starttls"` ya da `"ssl"`), `kullanici`, `gonderen`, `alicilar` (liste) | `SOGUTMA_SMTP_PAROLA` |
| `telegram` | `sohbet_idleri` (liste) | `SOGUTMA_TELEGRAM_TOKEN` |
| `webhook` | `url` (http/https) | `SOGUTMA_WEBHOOK_SIRRI` (imza için, isteğe bağlı) |

## 4. Ortam değişkenleri

Gizli bilgiler **yalnızca** ortam değişkenlerinden okunur; ayar dosyasında parola, token ya da sır alanı
yoktur ve olmamalıdır.

| Değişken | Zorunlu mu? | Anlamı |
|---|---|---|
| `SOGUTMA_SMTP_PAROLA` | e-posta kullanıcı adı varsa | SMTP parolası (Gmail vb. için uygulama parolası) |
| `SOGUTMA_SMTP_KULLANICI` | hayır | SMTP kullanıcı adı; verilirse ayar dosyasındaki `kullanici` yerine geçer |
| `SOGUTMA_TELEGRAM_TOKEN` | Telegram için evet | Bot token'ı |
| `SOGUTMA_WEBHOOK_URL` | hayır | Adres gizli anahtar içeriyorsa (Slack tarzı) `url` yerine bunu kullanın |
| `SOGUTMA_WEBHOOK_SIRRI` | hayır | Tanımlıysa webhook gövdesi HMAC-SHA256 ile imzalanır |

Gerçek gönderimde bir değişken eksikse o kanal hata olarak raporlanır, diğer kanallar etkilenmez.

## 5. Webhook biçimi ve imza doğrulama

`POST`, `Content-Type: application/json; charset=utf-8`:

```json
{"surum": 1,
 "olay": {"tur": "kritik", "birim": "U1", "ad": "Soğuk Oda A1", "zaman": "2026-01-10T23:00:00",
          "durum": "Kritik", "onceki": "Normal", "ariza": "gaz_kacagi", "guven": 0.97,
          "saglik": 12.0, "eta_h": 32.0, "tekrar": 0},
 "mesaj": "🔴 KRİTİK · Soğuk Oda A1 · ..."}
```

`SOGUTMA_WEBHOOK_SIRRI` tanımlıysa istekte `X-Sogutma-Imza: sha256=<hex>` başlığı bulunur; değer, ham
istek gövdesinin (UTF-8 baytları) sırla alınmış HMAC-SHA256 özetidir. Alıcı tarafta doğrulama (Python):

```python
import hashlib, hmac
beklenen = "sha256=" + hmac.new(SIR.encode(), ham_govde, hashlib.sha256).hexdigest()
gecerli = hmac.compare_digest(beklenen, istek_basligi)   # sabit zamanlı karşılaştırma
```

## 6. Telegram botu kurulumu (genel adımlar)

1. Telegram'da bot oluşturmak için resmî bot yönetim hesabıyla (BotFather) yeni bir bot oluşturun ve verilen
   **token'ı** güvenli biçimde saklayın.
2. Botu bildirim almak istediğiniz sohbete ya da gruba ekleyin; botla en az bir mesajlaşın (özel sohbette
   önce siz mesaj atmalısınız).
3. Sohbet kimliğini (chat id) öğrenin; botun aldığı güncellemelerden (Bot API `getUpdates`) okunabilir.
   Grup kimlikleri genellikle eksi işaretli sayılardır.
4. Token'ı `SOGUTMA_TELEGRAM_TOKEN` ortam değişkenine, sohbet kimliğini ayar dosyasındaki
   `kanallar.telegram.sohbet_idleri` listesine yazın.
5. Önce deneme modunda çalıştırın, sonra `--gonder` ile tek bir deneme yapın.

## 7. Güvenlik notları

- **Sırları depoya koymayın.** Gerçek ayar dosyası `bildirim_ayarlari.json` `.gitignore` içindedir; yine de
  alıcı adresleri kişisel veri sayılabilir. Parola/token'lar yalnızca ortam değişkenindedir.
- Token'lar hata mesajlarında ve günlüklerde `***` ile maskelenir; webhook adresinin yalnızca alan adı
  günlüğe yazılır.
- SMTP bağlantısı **STARTTLS** (ya da `ssl`) ve sertifika doğrulamasıyla yapılır; düz metin SMTP desteklenmez.
- Webhook alıcısında imzayı doğrulayın ve **sabit zamanlı** karşılaştırma kullanın; yinelenen istekler için
  `olay.zaman` + `olay.birim` ile tekrar koruması ekleyin.
- Durum dosyası ve günlükleri yalnızca servis hesabının yazabildiği bir dizinde tutun.
- Sunucuya `--gonder` ile çıkan sistemde çıkış (egress) yalnızca SMTP sunucusu, `api.telegram.org` ve webhook
  adresiyle sınırlandırılabilir.

## 8. Sınırlılıklar

- Aynı alarm içinde arıza türü sık değişirse (`ariza_degisti`) her tür için cooldown ayrı işler; en fazla
  tür sayısı kadar mesaj gider.
- Sessiz saatte ertelenen bir uyarı, alarm bu arada geçse bile ertelenmiş haliyle (özgün zamanıyla) gönderilir.
- Kanal başına ayrı yeniden deneme kuyruğu yoktur: olay en az bir kanaldan gittiyse başarılı sayılır.
- Zaman damgaları saat dilimsizdir; sessiz saatler verideki yerel saate göre değerlendirilir.
- Çok sayıda ünite için mesajlar tek tek gönderilir (toplu özet yoktur).
