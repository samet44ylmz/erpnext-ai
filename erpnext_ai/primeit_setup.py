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
            e.gender = "Erkek" if frappe.db.exists("Gender", "Erkek") else None
            e.insert(ignore_permissions=True)
            frappe.db.commit()
            olusan += 1
        except Exception as ex:
            print(f"  Calisan olusturulamadi {tam_ad}: {str(ex)[:200]}")

    print(f"\n{olusan} yeni calisan olusturuldu, {atlanan} zaten vardi.")
    return {"olusan": olusan, "atlanan": atlanan}
