# -*- coding: utf-8 -*-
"""
PrimeIT icin gercek veri kurulumu.

GECICI ARACTIR — uygulama kodu degildir, pod icinde bir kez calistirilir.

Kullanim (SIRAYLA):
    bench --site <site> execute erpnext_ai.primeit_setup.onizle
    bench --site <site> execute erpnext_ai.primeit_setup.temizle
    bench --site <site> execute erpnext_ai.primeit_setup.musterileri_olustur
    bench --site <site> execute erpnext_ai.primeit_setup.urunleri_olustur
"""

import frappe

# ------------------------------------------------------------------
# Silinecek eski test verisi (tekstil demo'su)
# ------------------------------------------------------------------
ESKI_URUN_KODLARI = ["4444", "1234", "0707"]
ESKI_MUSTERILER = ["Mehmet Kütük", "Zeynep Üraz", "Samet Yılmaz", "Say Tekstil"]

# ------------------------------------------------------------------
# PrimeIT gercek referans musterileri (primeit.com.tr/tr/referanslarimiz)
# ------------------------------------------------------------------
MUSTERI_GRUPLARI = {
    "Sigorta": [
        "AK Sigorta", "AXA Sigorta", "Allianz Sigorta", "Anadolu Sigorta",
        "Anadolu Hayat Emeklilik", "Ankara Sigorta", "Groupama Sigorta",
        "Mapfre Sigorta", "Sigorta Bilgi ve Gözetim Merkezi",
        "Türk Nippon Sigorta", "AcnTürk Sigorta",
    ],
    "Finans": [
        "ASBank", "Akfinans Bank", "Aktif Bank", "Capital Bank", "Creaditwest",
        "ICBC", "Koop Bank", "Limasol Bank", "Near East Bank", "Şeker Bank",
        "Universal Bank", "Vakıflar Bankası",
    ],
    "Yatirim": ["BMD", "Gedik Yatırım", "MKK", "Meksa Yatırım"],
    "Saglik": [
        "Anadolu Sağlık Merkezi", "Medical Park", "Selçuk Ecza Deposu", "TEKB",
    ],
    "Enerji": [
        "Aydem", "CK Enerji", "Dedaş", "Dicle Elektrik", "Enerjisa", "Esgaz",
        "GDZ", "Izmir Gaz",
    ],
    "Kamu": [
        "Bayrampaşa Belediyesi", "Bursa Büyükşehir Belediyesi",
        "Konya Büyükşehir Belediyesi", "Küçükcekmece Belediyesi",
        "Osmangazi Municipality", "Sakarya Büyükşehir Belediyesi",
        "Sivas Belediyesi",
    ],
    "Lojistik": [
        "Budo", "Burulaş", "Ets Tur", "Infotech", "Kayseri Ulaşım",
        "Kuryenet", "Pudo", "Zain",
    ],
    "FMCG": ["A101", "Sodexo", "Tchibo", "Toyzz Shop"],
    "Teknoloji": ["Etiya", "Genex"],
}

# ------------------------------------------------------------------
# PrimeIT urun/hizmetleri (fiyatlar PLASEHOLDER — gercek liste fiyatlarinizla
# degistirin: Item > Stok sekmesi ya da Item Price'tan)
# ------------------------------------------------------------------
URUNLER = [
    # (kod, ad, tur, placeholder_fiyat)
    ("PRM-PRIMEON", "PrimeON", "Urun", 150000),
    ("PRM-DBRUNNER", "DbRunner", "Urun", 90000),
    ("PRM-PBA", "Prime Banking Analytics", "Urun", 200000),
    ("PRM-PQM", "Prime Queue Matic", "Urun", 80000),
    ("SRV-DBYON", "Veritabanı Yönetimi", "Hizmet", 45000),
    ("SRV-SISYON", "Sistem Yönetimi", "Hizmet", 40000),
    ("SRV-ISZEKA", "İş Zekası Uygulamaları", "Hizmet", 60000),
    ("SRV-ELKDEVOPS", "ELK DevOps Kubernetes", "Hizmet", 55000),
    ("SRV-UYGGELIS", "Uygulama Geliştirme", "Hizmet", 70000),
    ("SRV-DISKAYNAK", "Dış Kaynak", "Hizmet", 35000),
    ("SRV-EGITIM", "Eğitim & Workshop", "Hizmet", 20000),
]


def _sirket():
    s = frappe.defaults.get_user_default("Company")
    if s:
        return s
    kayit = frappe.get_all("Company", fields=["name"], limit_page_length=1)
    return kayit[0]["name"] if kayit else None


def onizle():
    """Ne silinecek, ne eklenecek -- SALT OKUNUR, hicbir sey degistirmez."""
    print("=== SILINECEK ESKI URUNLER ===")
    for kod in ESKI_URUN_KODLARI:
        var = frappe.db.exists("Item", kod)
        print(f"  {kod}: {'mevcut' if var else 'yok (zaten silinmis)'}")

    print("\n=== SILINECEK ESKI MUSTERILER ===")
    for m in ESKI_MUSTERILER:
        var = frappe.db.exists("Customer", m)
        print(f"  {m}: {'mevcut' if var else 'yok'}")

    print("\n=== BAGLI ISLEM SAYISI (silinmeden once iptal edilecek) ===")
    for kod in ESKI_URUN_KODLARI:
        adet = frappe.db.count("Sales Invoice Item", filters={"item_code": kod})
        print(f"  {kod} icin {adet} fatura kalemi")

    toplam_yeni_musteri = sum(len(v) for v in MUSTERI_GRUPLARI.values())
    print(f"\n=== EKLENECEK YENI MUSTERI SAYISI: {toplam_yeni_musteri} ===")
    for grup, liste in MUSTERI_GRUPLARI.items():
        print(f"  {grup}: {len(liste)} firma")

    print(f"\n=== EKLENECEK YENI URUN/HIZMET SAYISI: {len(URUNLER)} ===")
    for kod, ad, tur, fiyat in URUNLER:
        print(f"  {kod} - {ad} ({tur}) - {fiyat} TL (placeholder)")


def temizle():
    """
    Eski tekstil demo verisini (Gomlek/Takim/Tisort + 4 musteri) ve onlara
    bagli TUM islemleri (fatura, stok girisi) iptal edip siler.
    """
    silinen = {"faturalar": 0, "stok_girisleri": 0, "urunler": 0, "musteriler": 0}

    # 1) Bu urunlere bagli TUM Sales Invoice'lari bul ve iptal+sil
    for kod in ESKI_URUN_KODLARI:
        kalemler = frappe.get_all(
            "Sales Invoice Item", filters={"item_code": kod}, fields=["parent"]
        )
        parent_adlar = {k["parent"] for k in kalemler}
        for ad in parent_adlar:
            try:
                doc = frappe.get_doc("Sales Invoice", ad)
                if doc.docstatus == 1:
                    doc.cancel()
                frappe.delete_doc("Sales Invoice", ad, force=True, ignore_permissions=True)
                frappe.db.commit()
                silinen["faturalar"] += 1
            except Exception as e:
                print(f"  Fatura silinemedi {ad}: {str(e)[:150]}")

    # 2) Bu urunlere bagli Stok Girisi (Stock Entry) kayitlarini iptal+sil
    for kod in ESKI_URUN_KODLARI:
        kalemler = frappe.get_all(
            "Stock Entry Detail", filters={"item_code": kod}, fields=["parent"]
        )
        parent_adlar = {k["parent"] for k in kalemler}
        for ad in parent_adlar:
            try:
                doc = frappe.get_doc("Stock Entry", ad)
                if doc.docstatus == 1:
                    doc.cancel()
                frappe.delete_doc("Stock Entry", ad, force=True, ignore_permissions=True)
                frappe.db.commit()
                silinen["stok_girisleri"] += 1
            except Exception as e:
                print(f"  Stok girisi silinemedi {ad}: {str(e)[:150]}")

    # 3) Urunleri sil (Bin/Item Price/Item Reorder otomatik gider)
    for kod in ESKI_URUN_KODLARI:
        try:
            if frappe.db.exists("Item", kod):
                frappe.delete_doc("Item", kod, force=True, ignore_permissions=True)
                frappe.db.commit()
                silinen["urunler"] += 1
        except Exception as e:
            print(f"  Urun silinemedi {kod}: {str(e)[:150]}")

    # 4) Musterileri sil
    for m in ESKI_MUSTERILER:
        try:
            if frappe.db.exists("Customer", m):
                frappe.delete_doc("Customer", m, force=True, ignore_permissions=True)
                frappe.db.commit()
                silinen["musteriler"] += 1
        except Exception as e:
            print(f"  Musteri silinemedi {m}: {str(e)[:150]}")

    print(f"\nTemizlik tamamlandi: {silinen}")
    return silinen


def musterileri_olustur():
    """PrimeIT'nin 60 gercek referans firmasini Customer olarak ekler."""
    olusan, atlanan = 0, 0
    for grup, firmalar in MUSTERI_GRUPLARI.items():
        # Customer Group yoksa olustur
        if not frappe.db.exists("Customer Group", grup):
            try:
                cg = frappe.new_doc("Customer Group")
                cg.customer_group_name = grup
                cg.parent_customer_group = "Bütün Müşteri Grupları"
                cg.insert(ignore_permissions=True)
                frappe.db.commit()
            except Exception as e:
                print(f"  Musteri grubu olusturulamadi {grup}: {str(e)[:150]}")

        for firma in firmalar:
            if frappe.db.exists("Customer", firma):
                atlanan += 1
                continue
            try:
                c = frappe.new_doc("Customer")
                c.customer_name = firma
                c.customer_group = grup if frappe.db.exists("Customer Group", grup) else "Bütün Müşteri Grupları"
                c.territory = "Turkey"
                c.customer_type = "Company"
                c.insert(ignore_permissions=True)
                frappe.db.commit()
                olusan += 1
            except Exception as e:
                print(f"  Musteri olusturulamadi {firma}: {str(e)[:150]}")

    print(f"\n{olusan} yeni musteri olusturuldu, {atlanan} zaten vardi.")
    return {"olusan": olusan, "atlanan": atlanan}


def urunleri_olustur():
    """PrimeIT urun ve ana hizmetlerini HIZMET URUNU (Non-Stock) olarak ekler."""
    olusan, atlanan = 0, 0
    for kod, ad, tur, fiyat in URUNLER:
        if frappe.db.exists("Item", kod):
            atlanan += 1
            continue
        try:
            it = frappe.new_doc("Item")
            it.item_code = kod
            it.item_name = ad
            it.item_group = "Bütün Ürün Grupları"
            it.is_stock_item = 0  # HIZMET -- stok takibi yok
            it.stock_uom = "Nos"
            it.standard_rate = fiyat
            it.insert(ignore_permissions=True)
            frappe.db.commit()
            olusan += 1
        except Exception as e:
            print(f"  Urun olusturulamadi {kod} ({ad}): {str(e)[:150]}")

    print(f"\n{olusan} yeni urun/hizmet olusturuldu, {atlanan} zaten vardi.")
    print("NOT: Fiyatlar PLASEHOLDER'dir. Gercek liste fiyatlarinizla "
          "Item > Standart Oran alanindan guncelleyin.")
    return {"olusan": olusan, "atlanan": atlanan}


# ------------------------------------------------------------------
# PrimeIT calisanlari
# ------------------------------------------------------------------
CALISANLAR = [
    "Ahmet Sancaktutan",
    "Atalay Aydın",
    "Batın Göztok",
    "Burak Özgün",
    "Burak Sağlam",
    "Erbil Can Keleş",
    "Furkan Sancaktutan",
    "Kayra Keser",
    "Kerem Albayrak",
    "Muhammed Zeyrek",
    "Mehmet Albayrak",
    "Muhammed Öcal",
    "Musa Yıldırım",
    "Ramazan Orhan",   # varsayim: orijinal satirda satir arasi kaybolmus
    "Samet Gücün",     # varsayim: yukaridakiyle ayni satirdan ayrildi
    "Hasan Aksu",
]


def calisanlari_olustur():
    """PrimeIT calisanlarini Employee olarak ekler (asgari zorunlu alanlarla)."""
    import frappe.utils

    sirket = _sirket()
    if not sirket:
        print("Sirket bulunamadi, islem durduruldu.")
        return

    olusan, atlanan = 0, 0
    for tam_ad in CALISANLAR:
        parcalar = tam_ad.split(" ", 1)
        ilk_ad = parcalar[0]
        soy_ad = parcalar[1] if len(parcalar) > 1 else ilk_ad

        # ayni isimde calisan zaten var mi (basit kontrol)
        var = frappe.get_all("Employee", filters={"employee_name": tam_ad}, limit_page_length=1)
        if var:
            atlanan += 1
            continue

        try:
            e = frappe.new_doc("Employee")
            e.first_name = ilk_ad
            e.last_name = soy_ad
            e.employee_name = tam_ad
            e.company = sirket
            e.date_of_joining = frappe.utils.today()
            e.status = "Active"
            e.gender = "Male"
            e.date_of_birth = "1990-01-01"  # placeholder, gercek tarihle guncellenebilir
            e.insert(ignore_permissions=True)
            frappe.db.commit()
            olusan += 1
        except Exception as ex:
            print(f"  Calisan olusturulamadi {tam_ad}: {str(ex)[:200]}")

    print(f"\n{olusan} yeni calisan olusturuldu, {atlanan} zaten vardi.")
    return {"olusan": olusan, "atlanan": atlanan}


def demo_gecmis_olustur():
    """
    Enerjisa'ya gecmis 'Veritabani Yonetimi' hizmeti alimlari ekler (test/demo
    icin). Boylece teklif_taslagi/fiyat_onerisi gercek bir gecmis+sadakat
    senaryosu uzerinde denenebilir. Stok dusurmez (hizmet zaten stoksuz).
    """
    import frappe.utils
    from datetime import timedelta

    sirket = _sirket()
    if not sirket:
        print("Sirket bulunamadi.")
        return

    if not frappe.db.exists("Customer", "Enerjisa"):
        print("Enerjisa musterisi bulunamadi, once musterileri_olustur calistirin.")
        return
    if not frappe.db.exists("Item", "SRV-DBYON"):
        print("SRV-DBYON urunu bulunamadi, once urunleri_olustur calistirin.")
        return

    bugun = frappe.utils.getdate()
    # 3 gecmis alim: 5 ay once, 3 ay once, 1 ay once -- artan fiyatlarla
    kayitlar = [
        (150, 38000),
        (90, 40000),
        (30, 42000),
    ]

    olusan = 0
    for gun_once, fiyat in kayitlar:
        tarih = bugun - timedelta(days=gun_once)
        try:
            si = frappe.new_doc("Sales Invoice")
            si.customer = "Enerjisa"
            si.company = sirket
            si.set_posting_time = 1
            si.posting_date = tarih
            si.due_date = tarih
            si.update_stock = 0
            si.append("items", {
                "item_code": "SRV-DBYON",
                "qty": 1,
                "rate": fiyat,
            })
            si.insert(ignore_permissions=True)
            si.submit()
            frappe.db.commit()
            olusan += 1
        except Exception as e:
            print(f"  Fatura olusturulamadi ({tarih}): {str(e)[:200]}")

    print(f"\n{olusan} gecmis fatura olusturuldu (Enerjisa / Veritabani Yonetimi).")
    return {"olusan": olusan}


def demo_gecmis_genis_olustur():
    """
    Birkaç farklı musteriye/sektore gecmis alim ekler (test/demo icin),
    boylece sadakat indirimi kademeleri de farkli senaryolarda gorulebilir.
    Stok dusurmez (hizmetler zaten stoksuz).
    """
    import frappe.utils
    from datetime import timedelta

    sirket = _sirket()
    if not sirket:
        print("Sirket bulunamadi.")
        return

    bugun = frappe.utils.getdate()

    # (musteri, urun_kodu, [(gun_once, fiyat, adet), ...])
    plan = [
        ("Vakıflar Bankası", "PRM-PBA", [
            (200, 190000, 1),
            (120, 195000, 1),
            (45, 198000, 1),
        ]),  # yuksek ciro -> yuksek sadakat kademesi
        ("AK Sigorta", "SRV-SISYON", [
            (60, 39000, 1),
        ]),  # dusuk ciro -> sadakat yok/dusuk
        ("A101", "SRV-DISKAYNAK", [
            (150, 33000, 1),
            (60, 34500, 1),
        ]),  # orta ciro -> orta sadakat kademesi
    ]

    toplam_olusan = 0
    for musteri, urun_kodu, kayitlar in plan:
        if not frappe.db.exists("Customer", musteri):
            print(f"  Musteri bulunamadi, atlaniyor: {musteri}")
            continue
        if not frappe.db.exists("Item", urun_kodu):
            print(f"  Urun bulunamadi, atlaniyor: {urun_kodu}")
            continue

        for gun_once, fiyat, adet in kayitlar:
            tarih = bugun - timedelta(days=gun_once)
            try:
                si = frappe.new_doc("Sales Invoice")
                si.customer = musteri
                si.company = sirket
                si.set_posting_time = 1
                si.posting_date = tarih
                si.due_date = tarih
                si.update_stock = 0
                si.append("items", {
                    "item_code": urun_kodu,
                    "qty": adet,
                    "rate": fiyat,
                })
                si.insert(ignore_permissions=True)
                si.submit()
                frappe.db.commit()
                toplam_olusan += 1
            except Exception as e:
                print(f"  Fatura olusturulamadi ({musteri}, {tarih}): {str(e)[:200]}")

    print(f"\n{toplam_olusan} gecmis fatura olusturuldu (genis demo seti).")
    return {"olusan": toplam_olusan}


def hizmet_olcu_birimi_guncelle():
    """
    Hizmet urunlerinin (SRV-*) olcu birimini 'Adet' yerine 'Month' (Ay)
    yapar -- boylece teklif/faturada '6 Ay' gibi anlamli gorunur.
    Urunler (PRM-*) 'Nos' (adet/lisans) olarak kalir, degismez.
    """
    hedef_birim = "Month" if frappe.db.exists("UOM", "Month") else None
    if not hedef_birim:
        print("UYARI: 'Month' UOM'u bulunamadi, birim degistirilemedi.")
        return {"guncellenen": 0}

    guncellenen = 0
    for kod, ad, tur, fiyat in URUNLER:
        if tur != "Hizmet":
            continue
        if not frappe.db.exists("Item", kod):
            continue
        try:
            frappe.db.set_value("Item", kod, "stock_uom", hedef_birim)
            frappe.db.commit()
            guncellenen += 1
        except Exception as e:
            print(f"  Guncellenemedi {kod}: {str(e)[:150]}")

    print(f"\n{guncellenen} hizmet urununun olcu birimi '{hedef_birim}' yapildi.")
    return {"guncellenen": guncellenen}


def hizmet_tarih_alanlari_olustur():
    """
    Quotation Item / Sales Order Item / Sales Invoice Item satirlarina
    'Hizmet Baslangic' ve 'Hizmet Bitis' tarih alanlarini ekler (Custom
    Field). Bir kere calistirilir, kalicidir. Liste gorunumunde varsayilan
    olarak gorunur (in_list_view=1).
    """
    hedef_doctype_lar = ["Quotation Item", "Sales Order Item", "Sales Invoice Item"]
    alanlar = [
        ("custom_hizmet_baslangic", "Hizmet Başlangıç", "item_code"),
        ("custom_hizmet_bitis", "Hizmet Bitiş", "custom_hizmet_baslangic"),
    ]

    olusan = 0
    for dt in hedef_doctype_lar:
        for fieldname, label, insert_after in alanlar:
            var = frappe.db.exists("Custom Field", {"dt": dt, "fieldname": fieldname})
            if var:
                continue
            try:
                cf = frappe.new_doc("Custom Field")
                cf.dt = dt
                cf.fieldname = fieldname
                cf.label = label
                cf.fieldtype = "Date"
                cf.insert_after = insert_after
                cf.in_list_view = 1
                cf.insert(ignore_permissions=True)
                frappe.db.commit()
                olusan += 1
            except Exception as e:
                print(f"  Alan olusturulamadi ({dt}.{fieldname}): {str(e)[:200]}")

    frappe.clear_cache()
    print(f"\n{olusan} yeni alan olusturuldu (toplam {len(hedef_doctype_lar)*len(alanlar)} beklenen).")
    return {"olusan": olusan}


def sozlesme_urun_alani_olustur():
    """
    Contract (Sozlesme) doctype'ina 'Ilgili Urun/Hizmet' (Item baglantili)
    ozel alanini ekler. Boylece sozlesme hangi urun/hizmet icin yapilmis
    bilinir, bitince dogru urunun fiyatiyla yenileme teklifi hesaplanabilir.
    """
    dt = "Contract"
    fieldname = "custom_ilgili_urun"

    if frappe.db.exists("Custom Field", {"dt": dt, "fieldname": fieldname}):
        print("Alan zaten mevcut, islem yapilmadi.")
        return {"olusan": 0}

    try:
        cf = frappe.new_doc("Custom Field")
        cf.dt = dt
        cf.fieldname = fieldname
        cf.label = "İlgili Ürün/Hizmet"
        cf.fieldtype = "Link"
        cf.options = "Item"
        cf.insert_after = "party_name"
        cf.in_list_view = 1
        cf.insert(ignore_permissions=True)
        frappe.db.commit()
        frappe.clear_cache()
        print("Alan olusturuldu: Contract.custom_ilgili_urun")
        return {"olusan": 1}
    except Exception as e:
        print(f"Alan olusturulamadi: {str(e)[:200]}")
        return {"olusan": 0}


def test_verisi_50_olustur():
    """
    50 (musteri, urun) cifti icin:
      - Gecmis bir Satis Faturasi (fiyat motoru gecmis fiyat/enflasyon/
        sadakat hesaplayabilsin diye)
      - O urune bagli bir Sozlesme (Contract), bitis tarihleri COK CESITLI
        dagitilir: bazilari kritik (<=7 gun), bazilari yakin (<=30 gun),
        bazilari planlanmali (<=60 gun), bazilari uzak (>60 gun, uyari
        vermez, sadece arka plan verisi).
    Boylece hem fiyatlandirma hem sozlesme uyarisi/yenileme sistemi genis
    bir test setiyle denenebilir.
    """
    import random
    import frappe.utils
    from datetime import timedelta
    from erpnext_ai.erpnext_ai.api import _standart_fiyat

    random.seed(42)
    sirket = _sirket()
    if not sirket:
        print("Sirket bulunamadi.")
        return

    tum_musteriler = [f for liste in MUSTERI_GRUPLARI.values() for f in liste]
    tum_urunler = [kod for (kod, ad, tur, fiyat) in URUNLER]

    if len(tum_musteriler) < 50:
        print(f"UYARI: sadece {len(tum_musteriler)} musteri var, bazilari tekrar kullanilacak.")

    # 50 benzersiz (musteri, urun) cifti — musterileri karistirip sirayla urun ata
    musteri_sirali = tum_musteriler[:]
    random.shuffle(musteri_sirali)
    ciftler = []
    for i in range(50):
        musteri = musteri_sirali[i % len(musteri_sirali)]
        urun = tum_urunler[i % len(tum_urunler)]
        ciftler.append((musteri, urun))

    # bitis tarihi dagitimi: 10 kritik, 10 yakin, 10 planla, 20 uzak
    bitis_gun_havuzu = (
        [random.randint(1, 7) for _ in range(10)] +
        [random.randint(8, 30) for _ in range(10)] +
        [random.randint(31, 60) for _ in range(10)] +
        [random.randint(61, 365) for _ in range(20)]
    )
    random.shuffle(bitis_gun_havuzu)

    bugun = frappe.utils.getdate()
    fatura_sayisi, sozlesme_sayisi = 0, 0
    fatura_hata, sozlesme_hata = 0, 0

    # bitis tarihi dagilim ozeti icin sayac
    ozet = {"kritik": 0, "yakin": 0, "planla": 0, "uzak": 0}

    for i, (musteri, urun_kodu) in enumerate(ciftler):
        # --- Gecmis Satis Faturasi ---
        try:
            standart = _standart_fiyat(urun_kodu) or 50000
            varyasyon = random.uniform(0.8, 1.15)
            eski_fiyat = round(standart * varyasyon, 2)
            gun_once = random.randint(30, 180)  # 2026 mali yili icinde kalir
            fatura_tarihi = bugun - timedelta(days=gun_once)

            si = frappe.new_doc("Sales Invoice")
            si.customer = musteri
            si.company = sirket
            si.set_posting_time = 1
            si.posting_date = fatura_tarihi
            si.due_date = fatura_tarihi
            si.update_stock = 0
            si.append("items", {"item_code": urun_kodu, "qty": 1, "rate": eski_fiyat})
            si.insert(ignore_permissions=True)
            si.submit()
            frappe.db.commit()
            fatura_sayisi += 1
        except Exception as e:
            fatura_hata += 1
            if fatura_hata <= 3:
                print(f"  Fatura olusturulamadi ({musteri}/{urun_kodu}): {str(e)[:150]}")

        # --- Sozlesme (Contract) ---
        try:
            bitis_gun = bitis_gun_havuzu[i]
            if bitis_gun <= 7:
                ozet["kritik"] += 1
            elif bitis_gun <= 30:
                ozet["yakin"] += 1
            elif bitis_gun <= 60:
                ozet["planla"] += 1
            else:
                ozet["uzak"] += 1

            bitis_tarihi = bugun + timedelta(days=bitis_gun)
            baslangic_tarihi = bugun - timedelta(days=365 - bitis_gun)

            urun_adi_c = dict((k, ad) for (k, ad, t, f) in URUNLER).get(urun_kodu, urun_kodu)
            c = frappe.new_doc("Contract")
            c.party_type = "Customer"
            c.party_name = musteri
            c.custom_ilgili_urun = urun_kodu
            c.start_date = baslangic_tarihi
            c.end_date = bitis_tarihi
            c.is_signed = 1
            c.contract_terms = f"{urun_adi_c} hizmeti/urunu icin standart sozlesme sartlari."
            c.insert(ignore_permissions=True)
            frappe.db.commit()
            sozlesme_sayisi += 1
        except Exception as e:
            sozlesme_hata += 1
            if sozlesme_hata <= 3:
                print(f"  Sozlesme olusturulamadi ({musteri}/{urun_kodu}): {str(e)[:150]}")

    print(f"\nToplam: {fatura_sayisi} fatura, {sozlesme_sayisi} sozlesme olusturuldu.")
    print(f"Hatalar: {fatura_hata} fatura, {sozlesme_hata} sozlesme.")
    print(f"Bitis tarihi dagilimi: {ozet}")
    return {
        "fatura": fatura_sayisi, "sozlesme": sozlesme_sayisi,
        "fatura_hata": fatura_hata, "sozlesme_hata": sozlesme_hata,
        "dagilim": ozet,
    }


def eksik_faturalari_tamamla():
    """
    test_verisi_50_olustur() calisirken mali yil disina tasan tarihler
    yuzunden olusturulamayan faturalari GUVENLI tarih araligiyla
    (30-180 gun once, hep 2026 icinde kalir) tamamlar. Sozlesmelere
    dokunmaz, sadece eksik faturalari ekler.
    """
    import random
    import frappe.utils
    from datetime import timedelta
    from erpnext_ai.erpnext_ai.api import _standart_fiyat

    random.seed(42)
    sirket = _sirket()
    if not sirket:
        print("Sirket bulunamadi.")
        return

    tum_musteriler = [f for liste in MUSTERI_GRUPLARI.values() for f in liste]
    tum_urunler = [kod for (kod, ad, tur, fiyat) in URUNLER]
    musteri_sirali = tum_musteriler[:]
    random.shuffle(musteri_sirali)

    ciftler = []
    for i in range(50):
        musteri = musteri_sirali[i % len(musteri_sirali)]
        urun = tum_urunler[i % len(tum_urunler)]
        ciftler.append((musteri, urun))

    bugun = frappe.utils.getdate()
    olusan, atlanan, hata = 0, 0, 0

    for musteri, urun_kodu in ciftler:
        # bu cift icin zaten bir fatura var mi kontrol et
        try:
            kalemler = frappe.get_all(
                "Sales Invoice Item", filters={"item_code": urun_kodu},
                fields=["parent"], limit_page_length=200,
            )
            parent_adlar = list({k["parent"] for k in kalemler})
            varmi = frappe.get_all(
                "Sales Invoice",
                filters={"name": ["in", parent_adlar], "customer": musteri, "docstatus": 1},
                limit_page_length=1,
            ) if parent_adlar else []
        except Exception:
            varmi = []

        if varmi:
            atlanan += 1
            continue

        try:
            standart = _standart_fiyat(urun_kodu) or 50000
            varyasyon = random.uniform(0.8, 1.15)
            eski_fiyat = round(standart * varyasyon, 2)
            gun_once = random.randint(30, 180)  # HEP 2026 icinde kalir
            fatura_tarihi = bugun - timedelta(days=gun_once)

            si = frappe.new_doc("Sales Invoice")
            si.customer = musteri
            si.company = sirket
            si.set_posting_time = 1
            si.posting_date = fatura_tarihi
            si.due_date = fatura_tarihi
            si.update_stock = 0
            si.append("items", {"item_code": urun_kodu, "qty": 1, "rate": eski_fiyat})
            si.insert(ignore_permissions=True)
            si.submit()
            frappe.db.commit()
            olusan += 1
        except Exception as e:
            hata += 1
            print(f"  Fatura olusturulamadi ({musteri}/{urun_kodu}): {str(e)[:150]}")

    print(f"\nTamamlandi: {olusan} yeni fatura, {atlanan} zaten vardi, {hata} hata.")
    return {"olusan": olusan, "atlanan": atlanan, "hata": hata}
