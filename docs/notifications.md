# Notifications: Email, Telegram, Webhook

Generates **alert events** from the hourly predictions and sends them to the console, email, Telegram or a
webhook URL. Code: `sogutma/bildirim.py`; command line: `predict.py --bildirim`.

> [!IMPORTANT]
> Notifications are based on the model output; the model was trained on **synthetic data** and its output is an
> early indicator (see the [Model Card](model-card.md)). Messages do not replace an on-site check.
> **The default behavior is dry-run mode: nothing is sent.**

> [!TIP]
> The **🔔 Bildirim Ayarları** (Notification Settings) page in the dashboard lets you prepare this settings file in a
> form (or upload and edit an existing file), see whether the environment variables are set, and preview the rules on
> the demo fleet in dry-run mode. This page never sends anything and never asks for passwords or tokens.

> [!NOTE]
> Settings keys, event names and message texts are in Turkish, because the messages go to Turkish-speaking service
> teams. Their meanings are given in the tables below.

## 1. How it works

```
predict.py → hourly prediction table → event generation → channels → state file
                                        (rules)            (console/email/Telegram/webhook)
```

Each unit's hourly rows are processed in order:

| Event | When is it raised? |
|---|---|
| `uyari` (warning) | A Normal → İzlemede (Watch) transition lasts `surekli_saat` hours |
| `kritik` (critical) | A Normal → Kritik (Critical) transition lasts `surekli_saat` hours |
| `yukselis` (escalation) | İzlemede → Kritik (gradual escalation: Watch is reported first, then Critical) |
| `iyilesme` (recovery) | A return to Normal lasts `iyilesme_saat` hours (only if a warning was sent before) |
| `ariza_degisti` (fault changed) | The predicted fault type changes permanently while an alarm is active |
| `hatirlatma` (reminder) | `hatirlatma: true` and the alarm is still active after `cooldown_saat` |

Rules:

- **Persistence:** a momentary fluctuation does not produce an alert. The state must last `surekli_saat` consecutive
  hours (consistent with the 6-hour `ALARM_H` idea in `train.py`; the default is 6 hours for Watch and 3 for Critical).
  Critical hours count as "at least Watch".
- **Cooldown (deduplication):** no second message is sent for the same (unit, state, fault type) within
  `cooldown_saat`. Even if the state fluctuates, you will not get a repeat alert for the same fault within 12 hours.
- **Minimum confidence:** an escalation is reported only if that hour's confidence is at or above `min_guven`.
- **Old events:** transitions older than `en_eski_olay_saat` hours (default 48) are not sent; for example, running a
  historical CSV for the first time does not send hundreds of old messages. If the alarm is still active, the current
  state is reported **once**.
- **Quiet hours:** events raised in this window are **deferred** (they wait in the state file) and sent when the window
  ends. If `kritik_haric: true` (critical excepted), Critical events are sent immediately even during quiet hours.
  Quiet hours are evaluated against the last timestamp in the table (which means "now" for live data).
- **State file:** the last processed hour, each unit's open alarm state, cooldown records and pending events are kept
  in a JSON file. Even if an hourly cron job processes the same data again, old alerts are not resent.
- **Error handling:** network/server errors are caught, retried 3 times (waiting 1 s, then 2 s) and reported in a
  summary; the pipeline never crashes. Permanent errors such as a wrong password and 4xx responses are not retried.
  An event for which all channels failed is kept in the state file and retried on the next run (at most 3 times).

### Message example

```
🔴 KRİTİK · Soğuk Oda A1 · Soğutucu gaz kaçağı (güven %97) · tahmini arıza ~2 gün · Öneri: Kaçak testi yapın ...
🟢 NORMALE DÖNDÜ · Soğuk Oda A1 · Soğutucu gaz kaçağı belirtisi geçti · sağlık 91/100
```

(English: "🔴 CRITICAL · Cold Room A1 · Refrigerant leak (confidence 97%) · estimated failure ~2 days ·
Recommendation: run a leak test ..." and "🟢 BACK TO NORMAL · Cold Room A1 · refrigerant leak symptoms cleared ·
health 91/100".)

The single-line format is for the console and Telegram; email uses a multi-line format (time, health score, symptoms,
recommendation). The webhook sends both the structured event and the short text.

## 2. Usage

```bash
# 1) Copy the sample settings and edit them
cp examples/bildirim_ayarlari.ornek.json bildirim_ayarlari.json

# 2) Dry run (default): messages are printed, nothing is sent, the state file is not written
python predict.py data.csv --bildirim bildirim_ayarlari.json

# 3) Actually send
python predict.py data.csv --bildirim bildirim_ayarlari.json --gonder
```

If `--gonder` (send) is not given, **dry-run mode** applies even if the settings file says `deneme_modu: false`
(the command line chooses the safe side). The state file can be changed with `--durum file.json` (default
`data/bildirim_durumu.json`). If no notification flags are given, the output of `predict.py` does not change.

### Hourly cron example

Assuming the sensor CSV is updated every hour (`crontab -e`):

```cron
5 * * * *  cd /opt/sogutma && . .venv/bin/activate && \
  SOGUTMA_TELEGRAM_TOKEN="..." SOGUTMA_SMTP_PAROLA="..." SOGUTMA_WEBHOOK_SIRRI="..." \
  python predict.py /var/sogutma/son_veri.csv --bildirim /etc/sogutma/bildirim_ayarlari.json --gonder \
  >> /var/log/sogutma-bildirim.log 2>&1
```

Instead of writing secrets directly into the crontab line, it is safer to load them from a file that only its owner
can read (`chmod 600`):

```cron
5 * * * *  cd /opt/sogutma && set -a && . /etc/sogutma/ortam && set +a && .venv/bin/python predict.py ... --gonder
```

## 3. Settings reference (JSON)

Unknown keys cause an error (to catch typos). Keys starting with `_` are ignored as comments.
Sample file: [`examples/bildirim_ayarlari.ornek.json`](../examples/bildirim_ayarlari.ornek.json).
TOML is supported only on Python 3.11+ (or if `tomli` is installed); JSON is recommended.

| Key | Type / default | Description |
|---|---|---|
| `deneme_modu` (dry run) | bool, `true` | Applies to direct API use; `predict.py` sets it with `--gonder` |
| `bildirilen_durumlar` (reported states) | list, `["İzlemede","Kritik"]` | Which states to report (`"İzlemede"` = Watch, `"Kritik"` = Critical) |
| `iyilesme_bildir` (report recovery) | bool, `true` | Whether to report a return to Normal |
| `cooldown_saat` | number, `12` | Deduplication period for the same unit + state + fault, in hours |
| `surekli_saat` (persistence hours) | number or `{"İzlemede": n, "Kritik": n}`, `{6, 3}` | Consecutive hours required for a state to count as persistent |
| `iyilesme_saat` (recovery hours) | number, `6` | Consecutive hours required for Normal to count as persistent |
| `min_guven` (minimum confidence) | 0–1, `0.5` | Minimum model confidence for an escalation/reminder |
| `hatirlatma` (reminder) | bool, `false` | Send a reminder when the cooldown expires while the alarm is active |
| `en_eski_olay_saat` (oldest event hours) | number or `null`, `48` | Transitions older than this are not sent; `null` = no limit |
| `sessiz_saatler` (quiet hours) | object | `aktif` (enabled, false), `baslangic` (start, "22:00"), `bitis` (end, "07:00"), `kritik_haric` (critical excepted, true); windows that cross midnight are supported |
| `birim_adlari` (unit names) | object | `{"U1": "Soğuk Oda A1"}`; otherwise "Ünite U1" |
| `kanallar` (channels) | object | See below |

### Channels (`kanallar.<name>`; all have `aktif: true/false`)

| Channel | Fields | Secret (environment variable) |
|---|---|---|
| `konsol` (console) | `aktif` only | none |
| `eposta` (email) | `sunucu` (server), `port` (587), `guvenlik` (security: `"starttls"` or `"ssl"`), `kullanici` (user), `gonderen` (sender), `alicilar` (recipients, list) | `SOGUTMA_SMTP_PAROLA` |
| `telegram` | `sohbet_idleri` (chat IDs, list) | `SOGUTMA_TELEGRAM_TOKEN` |
| `webhook` | `url` (http/https) | `SOGUTMA_WEBHOOK_SIRRI` (for signing, optional) |

## 4. Environment variables

Secrets are read **only** from environment variables; the settings file has no password, token or secret fields, and
must not have any.

| Variable | Required? | Meaning |
|---|---|---|
| `SOGUTMA_SMTP_PAROLA` | if an email user name is set | SMTP password (an app password for Gmail, etc.) |
| `SOGUTMA_SMTP_KULLANICI` | no | SMTP user name; if set, it overrides `kullanici` in the settings file |
| `SOGUTMA_TELEGRAM_TOKEN` | yes, for Telegram | Bot token |
| `SOGUTMA_WEBHOOK_URL` | no | Use this instead of `url` if the URL contains a secret key (Slack style) |
| `SOGUTMA_WEBHOOK_SIRRI` | no | If set, the webhook body is signed with HMAC-SHA256 |

If a variable is missing during real delivery, that channel is reported as an error; the other channels are not affected.

## 5. Webhook format and signature verification

`POST`, `Content-Type: application/json; charset=utf-8`:

```json
{"surum": 1,
 "olay": {"tur": "kritik", "birim": "U1", "ad": "Soğuk Oda A1", "zaman": "2026-01-10T23:00:00",
          "durum": "Kritik", "onceki": "Normal", "ariza": "gaz_kacagi", "guven": 0.97,
          "saglik": 12.0, "eta_h": 32.0, "tekrar": 0},
 "mesaj": "🔴 KRİTİK · Soğuk Oda A1 · ..."}
```

Field meanings: `surum` = schema version, `olay` = event, `tur` = event type, `birim` = unit ID, `ad` = display name,
`zaman` = time, `durum` = state, `onceki` = previous state, `ariza` = fault type, `guven` = confidence,
`saglik` = health score, `eta_h` = hours to failure, `tekrar` = repeat count, `mesaj` = message text.

If `SOGUTMA_WEBHOOK_SIRRI` is set, the request carries an `X-Sogutma-Imza: sha256=<hex>` header; the value is the
HMAC-SHA256 digest of the raw request body (its UTF-8 bytes) keyed with the secret. Verification on the receiving side
(Python):

```python
import hashlib, hmac
expected = "sha256=" + hmac.new(SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
valid = hmac.compare_digest(expected, request_header)   # constant-time comparison
```

## 6. Setting up a Telegram bot (general steps)

1. Create a new bot with Telegram's official bot management account (BotFather) and store the **token** you are given
   securely.
2. Add the bot to the chat or group where you want to receive notifications; exchange at least one message with the bot
   (in a private chat, you must message it first).
3. Find the chat ID; it can be read from the updates the bot receives (Bot API `getUpdates`).
   Group IDs are usually negative numbers.
4. Put the token in the `SOGUTMA_TELEGRAM_TOKEN` environment variable and the chat ID in the
   `kanallar.telegram.sohbet_idleri` list in the settings file.
5. Run in dry-run mode first, then do a single test with `--gonder`.

## 7. Security notes

- **Do not put secrets in the repository.** The real settings file `bildirim_ayarlari.json` is in `.gitignore`; even
  so, recipient addresses may count as personal data. Passwords/tokens live only in environment variables.
- Tokens are masked as `***` in error messages and logs; only the domain of the webhook URL is logged.
- The SMTP connection uses **STARTTLS** (or `ssl`) with certificate verification; plain-text SMTP is not supported.
- Verify the signature on the webhook receiver and use a **constant-time** comparison; add replay protection for
  duplicate requests using `olay.zaman` + `olay.birim`.
- Keep the state file and logs in a directory that only the service account can write to.
- On a system that runs with `--gonder`, egress can be restricted to the SMTP server, `api.telegram.org` and the
  webhook URL.

## 8. Limitations

- If the fault type changes often within the same alarm (`ariza_degisti`), the cooldown runs separately for each type;
  at most as many messages as there are types are sent.
- An alert deferred during quiet hours is sent in its deferred form (with its original time) even if the alarm cleared
  in the meantime.
- There is no separate retry queue per channel: an event counts as successful if it went out on at least one channel.
- Timestamps without a time zone in the analysis pipeline are UTC; quiet hours are currently evaluated in UTC.
  There is no unit/site time-zone setting yet; one must be added for a local quiet-hours policy.
- For many units, messages are sent one by one (there is no batched summary).
