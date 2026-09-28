"""Arıza türleri: görünen adlar, belirtiler ve bakım önerileri."""

FAULTS = {
    "normal": {
        "kisa": "Normal",
        "ad": "Normal çalışma",
        "belirtiler": "Tüm değerler referans aralığında.",
        "oneri": "Rutin bakım planına devam edin.",
    },
    "gaz_kacagi": {
        "kisa": "Gaz kaçağı",
        "ad": "Soğutucu gaz kaçağı",
        "belirtiler": "Emme basıncı düşüyor, kızgınlık (superheat) yükseliyor, "
        "aşırı soğutma (subcooling) azalıyor, kompresör daha uzun çalışıyor.",
        "oneri": "Kaçak testi yapın (elektronik dedektör / köpük), kaçağı giderin, "
        "sistemi vakumlayıp etiket değerinde şarj edin.",
    },
    "kondenser_kirlenmesi": {
        "kisa": "Kondenser kirli",
        "ad": "Kondenser kirlenmesi",
        "belirtiler": "Yoğuşma sıcaklığı dış ortama göre yükseliyor, basma basıncı "
        "ve kompresör akımı artıyor.",
        "oneri": "Kondenser bataryasını temizleyin, hava giriş/çıkışını engelleyen "
        "nesneleri kaldırın.",
    },
    "evaporator_buzlanma": {
        "kisa": "Buzlanma",
        "ad": "Evaporatör buzlanması / defrost arızası",
        "belirtiler": "Oda ile evaporatör arasındaki sıcaklık farkı büyüyor, emme "
        "basıncı ve kızgınlık düşüyor, defrost sırasında batarya yeterince ısınmıyor.",
        "oneri": "Defrost rezistansını, defrost sensörünü ve zamanlamasını kontrol "
        "edin; evaporatörü eritip drenajı açın.",
    },
    "kompresor_asinmasi": {
        "kisa": "Kompresör",
        "ad": "Kompresör aşınması",
        "belirtiler": "Titreşim ve kompresör akımı artıyor, basma hattı sıcaklığı "
        "yükseliyor, soğutma kapasitesi düşüyor.",
        "oneri": "Yağ seviyesini, valfleri ve montaj takozlarını kontrol edin; "
        "kompresör değişimi için yedek parça planlayın.",
    },
    "fan_arizasi": {
        "kisa": "Fan",
        "ad": "Kondenser fanı arızası",
        "belirtiler": "Fan motor akımı yükseliyor (rulman sürtünmesi), yoğuşma "
        "basıncı artıyor.",
        "oneri": "Fan motoru rulmanlarını ve kanatlarını kontrol edin, gerekirse "
        "motoru değiştirin.",
    },
}

FAULT_TYPES = list(FAULTS)


def fault_name(key):
    return FAULTS[key]["ad"]


def short_name(key):
    return FAULTS[key]["kisa"]
