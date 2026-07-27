# -*- coding: utf-8 -*-
"""
Test/demo satis verisi olusturur.

GECICI ARACTIR — uygulama kodu degildir, pod icinde bir kez calistirilir.
Pod yenilenince dosya kaybolur; olusan veri veritabaninda kalir.

Kullanim:
    bench --site <site> execute erpnext_ai.erpnext_ai.demo_data.listele
    bench --site <site> execute erpnext_ai.erpnext_ai.demo_data.olustur
    bench --site <site> execute erpnext_ai.erpnext_ai.demo_data.temizle
"""

import random
from datetime import date, timedelta

import frappe

# item_code -> (birim fiyat, aylik satis hedefi)
URUNLER = {
    "4444": (200, 17),   # Gomlek
    "1234": (500, 34),   # Takim
    "0707": (150, 10),   # Tisort
}

MUSTERILER = ["Mehmet Kütük", "Zeynep Üraz", "Samet Yılmaz", "Say Tekstil"]

GUN_SAYISI = 90


def _sirket():
    s = frappe.defaults.get_user_default("Company")
    if s:
        return s
    kayit = frappe.get_all("Company", fields=["name"], limit_page_length=1)
    return kayit[0]["name"] if kayit else None


def listele():
    """Son 90 gundeki kesinlesmis satis faturalarini ozetler. SALT-OKUNUR."""
    baslangic = (date.today() - timedelta(days=GUN_SAYISI)).isoformat()
    faturalar = frappe.get_all(
        "Sales Invoice",
        filters={"docstatus": 1, "posting_date": [">=", baslangic]},
        fields=["name", "posting_date", "customer", "grand_total"],
        order_by="posting_date desc",
        limit_page_length=0,
    )
    print(f"\nSon {GUN_SAYISI} gunde {len(faturalar)} kesinlesmis fatura var.\n")

    if not faturalar:
        return {"fatura_sayisi": 0}

    adlar = [f["name"] for f in faturalar]
    kalemler = frappe.get_all(
        "Sales Invoice Item",
        filters={"parent": ["in", adlar]},
        fields=["item_code", "item_name", "qty", "parent"],
        limit_page_length=0,
    )

    ozet = {}
    for k in kalemler:
        kod = k["item_code"]
        d = ozet.setdefault(kod, {"ad": k.get("item_name") or kod, "adet": 0, "fatura": set()})
        d["adet"] += float(k.get("qty") or 0)
        d["fatura"].add(k["parent"])

    print(f"{'KOD':<8}{'URUN':<12}{'TOPLAM ADET':>13}{'FATURA':>9}")
    print("-" * 44)
    for kod, d in ozet.items():
        print(f"{kod:<8}{d['ad']:<12}{d['adet']:>13.0f}{len(d['fatura']):>9}")

    print("\nEn buyuk 10 kalem (mukerrer/hatali kayit kontrolu icin):")
    buyuk = sorted(kalemler, key=lambda x: float(x.get("qty") or 0), reverse=True)[:10]
    for k in buyuk:
        print(f"  {k['parent']}  {k.get('item_name') or k['item_code']}  {k['qty']} adet")

    return {"fatura_sayisi": len(faturalar)}


def olustur():
    """Son 90 gune yayilmis gercekci satis faturalari olusturur (stok DUSMEZ)."""
    random.seed(7)
    sirket = _sirket()
    if not sirket:
        print("Sirket bulunamadi, islem durduruldu.")
        return

    # urun ve musteri dogrulamasi
    eksik = [k for k in URUNLER if not frappe.db.exists("Item", k)]
    if eksik:
        print(f"Su urun kodlari bulunamadi: {eksik}")
        return
    yok = [m for m in MUSTERILER if not frappe.db.exists("Customer", m)]
    if yok:
        print(f"Su musteriler bulunamadi: {yok}")
        return

    bugun = date.today()
    kayitlar = []
    for kod, (fiyat, aylik) in URUNLER.items():
        kalan = int(aylik * (GUN_SAYISI / 30.0))
        while kalan > 0:
            adet = min(kalan, random.randint(1, 3))
            gun_once = random.randint(0, GUN_SAYISI - 1)
            kayitlar.append({
                "musteri": random.choice(MUSTERILER),
                "tarih": bugun - timedelta(days=gun_once),
                "kod": kod,
                "adet": adet,
                "fiyat": fiyat,
            })
            kalan -= adet

    kayitlar.sort(key=lambda x: x["tarih"])

    olusan = 0
    hatali = 0
    for k in kayitlar:
        try:
            si = frappe.new_doc("Sales Invoice")
            si.customer = k["musteri"]
            si.company = sirket
            si.set_posting_time = 1          # gecmis tarih kullanilabilsin
            si.posting_date = k["tarih"]
            si.due_date = k["tarih"]
            si.update_stock = 0              # STOK DUSMESIN
            si.append("items", {
                "item_code": k["kod"],
                "qty": k["adet"],
                "rate": k["fiyat"],
            })
            si.insert(ignore_permissions=True)
            si.submit()
            frappe.db.commit()
            olusan += 1
        except Exception as e:
            frappe.db.rollback()
            hatali += 1
            if hatali <= 3:
                print(f"HATA ({k['kod']} / {k['tarih']}): {str(e)[:200]}")

    print(f"\n{olusan} fatura olusturuldu, {hatali} hata.")
    return {"olusan": olusan, "hatali": hatali}


def temizle(gun=90, en_az_adet=20):
    """
    Belirtilen gun araligindaki, tek kalemde 'en_az_adet' ve uzeri miktar
    iceren faturalari IPTAL eder. Hatali/test kayitlarini ayiklamak icin.
    Once listele() ile ne silinecegini gorun.
    """
    baslangic = (date.today() - timedelta(days=int(gun))).isoformat()
    faturalar = frappe.get_all(
        "Sales Invoice",
        filters={"docstatus": 1, "posting_date": [">=", baslangic]},
        fields=["name"], limit_page_length=0,
    )
    if not faturalar:
        print("Fatura yok.")
        return

    adlar = [f["name"] for f in faturalar]
    kalemler = frappe.get_all(
        "Sales Invoice Item",
        filters={"parent": ["in", adlar], "qty": [">=", int(en_az_adet)]},
        fields=["parent", "item_code", "qty"], limit_page_length=0,
    )
    hedef = sorted({k["parent"] for k in kalemler})
    if not hedef:
        print(f"{en_az_adet} adet ve uzeri kalem iceren fatura bulunamadi.")
        return

    print(f"{len(hedef)} fatura iptal edilecek:")
    for ad in hedef:
        print("  ", ad)

    iptal = 0
    for ad in hedef:
        try:
            doc = frappe.get_doc("Sales Invoice", ad)
            doc.cancel()
            frappe.db.commit()
            iptal += 1
        except Exception as e:
            frappe.db.rollback()
            print(f"  iptal edilemedi {ad}: {str(e)[:150]}")

    print(f"\n{iptal} fatura iptal edildi.")
    return {"iptal": iptal}
