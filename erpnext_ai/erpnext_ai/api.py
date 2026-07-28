# -*- coding: utf-8 -*-
"""
ERPNext AI - Backend

GUVENLIK:
  - AI serbest SQL yazamaz; yalnizca parametreli, tanimli araclari cagirir.
  - Yalnizca ALLOWED_DOCTYPES okunabilir (maas/personel bilincli olarak yok).
  - Kisisel veri alanlari Groq'a GONDERILMEZ.
  - Salt-okunur: hicbir kayit olusturulmaz/kaydedilmez/submit edilmez.
  - ERPNext'in kendi yetki sistemi devrededir.
"""

import json
import os
import time
from datetime import date, timedelta

import frappe
import requests
from frappe import _

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _groq_key():
    key = os.environ.get("GROQ_AI_KEY") or os.environ.get("GROQ_API_KEY") or ""
    return key.strip()


def _groq_model():
    return os.environ.get("GROQ_AI_MODEL", "openai/gpt-oss-120b").strip()


ALLOWED_DOCTYPES = {
    "Sales Invoice",
    "Sales Invoice Item",
    "Purchase Invoice",
    "Purchase Invoice Item",
    "Quotation",
    "Quotation Item",
    "Sales Order",
    "Sales Order Item",
    "Delivery Note",
    "Item",
    "Stock Ledger Entry",
    "Bin",
    "Payment Entry",
    # HCM: yalnizca pozisyon tanimlari — calisan/maas verisi DEGIL
    "Job Opening",
    "Designation",
    "Item Price",
}

BLOCKED_FIELDS = {
    "customer_name", "supplier_name", "contact_person", "contact_email",
    "contact_mobile", "contact_display", "email_id", "mobile_no", "phone",
    "address_line1", "address_line2", "tax_id", "customer_address",
    "shipping_address", "shipping_address_name", "owner", "modified_by",
    "party_name", "title", "contact_info", "company_address",
}

MAX_ROWS = 25          # tek listede en fazla satir (token korumasi)
MAX_STEPS = 10
MAX_TOOL_CHARS = 3500  # araç sonucundan modele gidecek en fazla karakter


def _guard(doctype):
    if doctype not in ALLOWED_DOCTYPES:
        return (
            f"ERISIM YOK: '{doctype}' bu asistanin erisebilecegi veriler arasinda degil. "
            "Maas, personel ve benzeri hassas veriler kapsam disidir."
        )
    return None


def _strip_personal(rows):
    return [{k: v for k, v in r.items() if k not in BLOCKED_FIELDS} for r in rows]


def _tool_bugun(args):
    return json.dumps({"bugun": date.today().isoformat()}, ensure_ascii=False)


def _tool_toplam(args):
    dt = args.get("doctype", "")
    err = _guard(dt)
    if err:
        return err
    alan = args.get("alan", "grand_total")
    filtreler = args.get("filtreler") or {}
    try:
        rows = frappe.get_list(
            dt,
            filters=filtreler,
            fields=[f"sum({alan}) as toplam", "count(name) as adet"],
        )
    except frappe.PermissionError:
        return "Bu veriye erisim yetkiniz yok."
    except Exception as e:
        return f"Sorgu hatasi: {str(e)[:200]}"
    return json.dumps(rows[0] if rows else {}, ensure_ascii=False, default=str)


def _tool_say(args):
    dt = args.get("doctype", "")
    err = _guard(dt)
    if err:
        return err
    filtreler = args.get("filtreler") or {}
    try:
        adet = frappe.db.count(dt, filters=filtreler)
    except frappe.PermissionError:
        return "Bu veriye erisim yetkiniz yok."
    except Exception as e:
        return f"Sorgu hatasi: {str(e)[:200]}"
    return json.dumps({"adet": adet}, ensure_ascii=False)


def _tool_liste(args):
    dt = args.get("doctype", "")
    err = _guard(dt)
    if err:
        return err
    alanlar = [a for a in (args.get("alanlar") or ["name"]) if a not in BLOCKED_FIELDS]
    if not alanlar:
        alanlar = ["name"]
    # token korumasi: en fazla 6 alan
    alanlar = alanlar[:6]
    filtreler = args.get("filtreler") or {}
    limit = min(int(args.get("limit") or 10), MAX_ROWS)
    siralama = args.get("siralama")
    try:
        rows = frappe.get_list(
            dt, filters=filtreler, fields=alanlar,
            limit_page_length=limit, order_by=siralama,
        )
    except frappe.PermissionError:
        return "Bu veriye erisim yetkiniz yok."
    except Exception as e:
        return f"Sorgu hatasi: {str(e)[:200]}"
    rows = _strip_personal(rows)
    out = json.dumps(rows, ensure_ascii=False, default=str)
    # token korumasi: cok uzunsa kirp
    if len(out) > MAX_TOOL_CHARS:
        kirpik = rows[:10]
        out = json.dumps(kirpik, ensure_ascii=False, default=str)
        out += f"\n(Not: sonuc kirpildi, ilk 10 kayit gosteriliyor, toplam {len(rows)} kayit var.)"
    return out


def _tool_alanlari_getir(args):
    dt = args.get("doctype", "")
    err = _guard(dt)
    if err:
        return err
    try:
        meta = frappe.get_meta(dt)
    except Exception as e:
        return f"Meta okunamadi: {str(e)[:200]}"
    fields = []
    for df in meta.fields:
        if df.fieldtype in ("Section Break", "Column Break", "HTML", "Button"):
            continue
        # sadece ise yarar alanlar; token korumasi icin ozet
        fields.append({
            "fieldname": df.fieldname,
            "label": df.label,
            "fieldtype": df.fieldtype,
            "reqd": df.reqd,
        })
    return json.dumps(fields[:40], ensure_ascii=False, default=str)


def _tool_form_doldur(args):
    """Form TASLAGI hazirlar. HICBIR SEY KAYDEDILMEZ."""
    dt = args.get("doctype", "")
    err = _guard(dt)
    if err:
        return err
    alanlar = args.get("alanlar") or {}
    return json.dumps({
        "_action": "form_taslak",
        "doctype": dt,
        "alanlar": alanlar,
    }, ensure_ascii=False, default=str)

def _tool_stok_durumu(args):
    """
    Her urunun guncel stogunu, yeniden siparis esigini, siparis miktarini
    ve varsayilan tedarikcisini birlestirip dondurur.
    Eshik altinda olanlari isaretler. SALT-OKUNUR, siparis vermez.
    """
    depo = args.get("depo")  # opsiyonel; verilmezse tum depolar
    try:
        # 1) Urunlerin yeniden siparis ayarlarini cek (Item + Item Reorder)
        urunler = frappe.get_list(
            "Item",
            filters={"disabled": 0, "is_stock_item": 1},
            fields=["name", "item_name", "item_code"],
            limit_page_length=100,
        )
    except frappe.PermissionError:
        return "Urun verisine erisim yetkiniz yok."
    except Exception as e:
        return f"Sorgu hatasi: {str(e)[:200]}"

    sonuc = []
    for u in urunler:
        kod = u.get("item_code") or u.get("name")

        # guncel stok (Bin tablosundan)
        bin_filtreler = {"item_code": kod}
        if depo:
            bin_filtreler["warehouse"] = depo
        try:
            binler = frappe.get_list(
                "Bin", filters=bin_filtreler,
                fields=["warehouse", "actual_qty"], limit_page_length=20,
            )
        except Exception:
            binler = []
        toplam_stok = sum((b.get("actual_qty") or 0) for b in binler)

        # yeniden siparis esigi (Item Reorder cocuk tablosu)
        try:
            reorder = frappe.get_all(
                "Item Reorder",
                filters={"parent": kod},
                fields=["warehouse_reorder_level", "warehouse_reorder_qty", "warehouse"],
                limit_page_length=5,
            )
        except Exception:
            reorder = []
        esik = reorder[0]["warehouse_reorder_level"] if reorder else None
        siparis_qty = reorder[0]["warehouse_reorder_qty"] if reorder else None

        # varsayilan tedarikci
        try:
            ts = frappe.get_all(
                "Item Default",
                filters={"parent": kod},
                fields=["default_supplier"],
                limit_page_length=1,
            )
            tedarikci = ts[0]["default_supplier"] if ts and ts[0].get("default_supplier") else None
        except Exception:
            tedarikci = None

        durum = "normal"
        if esik is not None and toplam_stok < esik:
            durum = "esik_altinda"

        sonuc.append({
            "urun": u.get("item_name") or kod,
            "kod": kod,
            "stok": toplam_stok,
            "esik": esik,
            "onerilen_siparis": siparis_qty,
            "tedarikci": tedarikci,
            "durum": durum,
        })

    return json.dumps(sonuc, ensure_ascii=False, default=str)



def _tool_stok_analiz(args):
    """
    Satis hizina gore akilli stok analizi ve siparis onerisi.

    Mantik:
      - Son 1 aydaki (varsayilan 30 gun) satistan gunluk satis hizi hesaplanir.
      - Mevcut stok / gunluk hiz = kac gun yeter.
      - Hedef kapsama suresi kadar stok tutulmasi onerilir.
      - Satis yoksa ama stok esigin altindaysa yine uyarilir (esik yedegi).
      - Az sayida faturaya dayanan tahminler "dusuk guven" olarak isaretlenir.
    SALT-OKUNUR: hicbir siparis olusturulmaz.
    """
    from math import ceil

    gecmis_gun = int(args.get("gecmis_gun") or 30)
    hedef_gun = int(args.get("hedef_gun") or 45)
    depo = args.get("depo")

    baslangic = (date.today() - timedelta(days=gecmis_gun)).isoformat()

    # 1) Donem icindeki kesinlesmis satis faturalari (kullanici yetkisiyle)
    try:
        faturalar = frappe.get_list(
            "Sales Invoice",
            filters={"docstatus": 1, "posting_date": [">=", baslangic]},
            fields=["name"],
            limit_page_length=0,
        )
    except frappe.PermissionError:
        return "Satis verisine erisim yetkiniz yok."
    except Exception as e:
        return f"Satis sorgusu hatasi: {str(e)[:200]}"

    fatura_adlari = [f.get("name") for f in faturalar]

    # 2) Urun bazinda toplam miktar + kac ayri faturada gectigi
    satis_map = {}
    fatura_sayi_map = {}
    if fatura_adlari:
        try:
            kalemler = frappe.get_all(
                "Sales Invoice Item",
                filters={"parent": ["in", fatura_adlari]},
                fields=["item_code", "qty", "parent"],
                limit_page_length=5000,
            )
            gecen = {}
            for k in kalemler:
                kod = k.get("item_code")
                satis_map[kod] = satis_map.get(kod, 0.0) + float(k.get("qty") or 0)
                gecen.setdefault(kod, set()).add(k.get("parent"))
            fatura_sayi_map = {k: len(v) for k, v in gecen.items()}
        except Exception:
            satis_map, fatura_sayi_map = {}, {}

    # 3) Urunler + stok + esik
    try:
        urunler = frappe.get_list(
            "Item",
            filters={"disabled": 0, "is_stock_item": 1},
            fields=["name", "item_name", "item_code"],
            limit_page_length=100,
        )
    except Exception as e:
        return f"Urun sorgusu hatasi: {str(e)[:200]}"

    def yuvarla(x):
        """Okunakli sayi: 20 ustu degerleri 10'un katina yuvarlar."""
        n = int(ceil(x))
        return int(ceil(n / 10.0) * 10) if n > 20 else n

    sonuc = []
    for u in urunler:
        kod = u.get("item_code") or u.get("name")

        bin_filtreler = {"item_code": kod}
        if depo:
            bin_filtreler["warehouse"] = depo
        try:
            binler = frappe.get_list(
                "Bin", filters=bin_filtreler,
                fields=["actual_qty"], limit_page_length=20,
            )
        except Exception:
            binler = []
        stok = sum(float(b.get("actual_qty") or 0) for b in binler)

        try:
            reorder = frappe.get_all(
                "Item Reorder", filters={"parent": kod},
                fields=["warehouse_reorder_level", "warehouse_reorder_qty"],
                limit_page_length=1,
            )
        except Exception:
            reorder = []
        esik = reorder[0].get("warehouse_reorder_level") if reorder else None
        esik_siparis = reorder[0].get("warehouse_reorder_qty") if reorder else None

        satilan = satis_map.get(kod, 0.0)
        fatura_adedi = fatura_sayi_map.get(kod, 0)
        gunluk = satilan / gecmis_gun if gecmis_gun else 0.0
        aylik = round(gunluk * 30, 1)

        esik_altinda = (esik is not None and stok < esik)

        # tahmin guveni: az faturaya dayanan hiz yaniltici olabilir
        if satilan <= 0:
            guven = "veri_yok"
        elif fatura_adedi < 3:
            guven = "dusuk"
        elif fatura_adedi < 6:
            guven = "orta"
        else:
            guven = "iyi"

        # --- Karar mantigi ---
        if gunluk <= 0:
            kalan_gun = None
            if esik_altinda:
                # satis verisi yok ama esik altinda -> klasik esik mantigi devrede
                durum = "esik_alti_satis_yok"
                oneri = int(esik_siparis or 0)
                gerekce = (f"Son {gecmis_gun} gunde satis kaydi yok, ancak stok "
                           f"({int(stok)}) siparis esiginin ({int(esik)}) altinda. "
                           "Satis hizi hesaplanamadigi icin oneri sabit esik "
                           "miktarina dayanir; karari gozden gecirin.")
            elif stok > 0:
                durum = "olu_stok"
                oneri = 0
                gerekce = (f"Son {gecmis_gun} gunde satis yok, elde {int(stok)} adet var. "
                           "Yeni siparis onerilmez.")
            else:
                durum = "hareketsiz"
                oneri = 0
                gerekce = f"Son {gecmis_gun} gunde satis yok, stok da yok."
        else:
            kalan_gun = round(stok / gunluk, 1)
            ihtiyac = gunluk * hedef_gun - stok
            oneri = yuvarla(ihtiyac) if ihtiyac > 0 else 0

            if kalan_gun < 7:
                durum = "kritik"
                gerekce = (f"Ayda ~{aylik} adet satiyor, elde {int(stok)} adet var. "
                           f"Yaklasik {kalan_gun} gun yeter.")
            elif kalan_gun < 15:
                durum = "acil"
                gerekce = f"Ayda ~{aylik} adet satiyor, {kalan_gun} gunluk stok kaldi."
            elif oneri > 0:
                durum = "siparis_zamani"
                gerekce = (f"Ayda ~{aylik} adet satiyor, {kalan_gun} gunluk stok var. "
                           f"{hedef_gun} gunluk kapsama icin takviye gerekir.")
            elif kalan_gun > hedef_gun * 2:
                durum = "fazla_stok"
                gerekce = (f"Ayda ~{aylik} adet satiyor ama {kalan_gun} gunluk stok var. "
                           "Siparis onerilmez, stok fazlasi baglaniyor.")
            else:
                durum = "normal"
                gerekce = f"Ayda ~{aylik} adet satiyor, {kalan_gun} gunluk stok yeterli."

            # guven dusukse uyari notu ve sabit esik miktariyla karsilastirma
            if guven == "dusuk" and oneri > 0:
                gerekce += (f" Not: bu tahmin yalnizca {fatura_adedi} faturaya dayaniyor, "
                            "hiz yaniltici olabilir.")
                if esik_siparis and oneri > esik_siparis * 3:
                    gerekce += (f" Tanimli sabit siparis miktari {int(esik_siparis)} adet; "
                                "aradaki fark buyuk, teyit edin.")

        sonuc.append({
            "urun": u.get("item_name") or kod,
            "kod": kod,
            "stok": int(stok),
            "esik": esik,
            "esik_altinda": esik_altinda,
            "aylik_satis": aylik,
            "kalan_gun": kalan_gun,
            "durum": durum,
            "onerilen_siparis": oneri,
            "sabit_siparis_miktari": esik_siparis,
            "fatura_sayisi": fatura_adedi,
            "guven": guven,
            "gerekce": gerekce,
        })

    oncelik = {"kritik": 0, "acil": 1, "esik_alti_satis_yok": 2, "siparis_zamani": 3,
               "normal": 4, "fazla_stok": 5, "olu_stok": 6, "hareketsiz": 7}
    sonuc.sort(key=lambda x: oncelik.get(x["durum"], 9))

    return json.dumps({
        "gecmis_gun": gecmis_gun,
        "hedef_gun": hedef_gun,
        "urunler": sonuc,
    }, ensure_ascii=False, default=str)


# ------------------------------------------------------------------
# EVDS (TCMB) — doviz kuru ve enflasyon verisi
# ------------------------------------------------------------------
EVDS_URL = "https://evds3.tcmb.gov.tr/igmevdsms-dis"
SERI_USD = "TP.DK.USD.S.YTL"    # Dolar satis kuru
SERI_TUFE = "TP.FE.OKTG01"      # TUFE (Tuketici Fiyat Endeksi, genel)


def _evds_key():
    return (os.environ.get("EVDS_API_KEY") or "").strip()


def _evds_deger(seri, hedef_tarih):
    """
    Belirli bir tarihe en yakin (o tarihten once/o tarihte) yayinlanmis
    seri degerini dondurur. Gunluk cache kullanir, EVDS'e gereksiz istek
    atmaz.
    """
    if isinstance(hedef_tarih, str):
        hedef_tarih = frappe.utils.getdate(hedef_tarih)

    cache_key = f"evds:{seri}:{hedef_tarih.isoformat()}"
    try:
        cached = frappe.cache().get_value(cache_key)
        if cached is not None:
            return float(cached)
    except Exception:
        pass

    key = _evds_key()
    if not key:
        return None

    # TUFE aylik yayinlanir, gunluk kurdan daha genis pencere gerekir
    geriye_gun = 45 if seri == SERI_TUFE else 10
    baslangic = (hedef_tarih - timedelta(days=geriye_gun)).strftime("%d-%m-%Y")
    bitis = hedef_tarih.strftime("%d-%m-%Y")
    url = f"{EVDS_URL}/series={seri}&startDate={baslangic}&endDate={bitis}&type=json"

    try:
        r = requests.get(url, headers={"key": key}, timeout=20)
        data = r.json()
        items = data.get("items") or []
        if not items:
            return None
        alan = seri.replace(".", "_")
        # sondan geriye dogru ilk DOLU degeri bul (hafta sonu/tatil = null olabilir)
        deger_str = None
        for it in reversed(items):
            v = it.get(alan)
            if v not in (None, ""):
                deger_str = v
                break
        if deger_str is None:
            return None
        deger = float(str(deger_str).replace(",", "."))
        try:
            frappe.cache().set_value(cache_key, deger, expires_in_sec=60 * 60 * 20)
        except Exception:
            pass
        return deger
    except Exception:
        return None


def _evds_yuzde_degisim(seri, eski_tarih):
    """Iki tarih arasindaki yuzde degisimi dondurur (None: veri yok)."""
    eski = _evds_deger(seri, eski_tarih)
    bugun = _evds_deger(seri, date.today())
    if eski is None or bugun is None or eski == 0:
        return None
    return (bugun - eski) / eski * 100.0


def _standart_fiyat(urun_kodu):
    """Item Price (Standard Selling) ya da Item.standard_rate."""
    try:
        fiyat = frappe.get_all(
            "Item Price",
            filters={"item_code": urun_kodu, "selling": 1},
            fields=["price_list_rate"],
            order_by="modified desc",
            limit_page_length=1,
        )
        if fiyat and fiyat[0].get("price_list_rate"):
            return float(fiyat[0]["price_list_rate"])
    except Exception:
        pass
    try:
        rate = frappe.db.get_value("Item", urun_kodu, "standard_rate")
        return float(rate) if rate else None
    except Exception:
        return None


def _normalize_tr(s):
    """Turkce karakterleri sadelestirip kucuk harfe cevirir (esnek karsilastirma icin)."""
    if not s:
        return ""
    cevrim = str.maketrans({
        "İ": "i", "I": "i", "ı": "i",  # noktali/noktasiz I farki onemli
        "Ş": "s", "ş": "s",
        "Ğ": "g", "ğ": "g",
        "Ü": "u", "ü": "u",
        "Ö": "o", "ö": "o",
        "Ç": "c", "ç": "c",
    })
    return s.translate(cevrim).lower().strip()


def _musteri_coz(girilen_ad):
    """
    Kullanicinin yazdigi adi, Turkce karakter farkina bakmaksizin once Customer
    (kayitli musteri) icinde, bulamazsa Lead (potansiyel musteri/aday) icinde arar.
    Donus: {"tur": "Customer"|"Lead"|None, "kayit": gercek_kayit_adi}
    Hicbiri bulunamazsa {"tur": None, "kayit": girilen_ad} doner.
    """
    if not girilen_ad:
        return {"tur": None, "kayit": girilen_ad}
    hedef = _normalize_tr(girilen_ad)

    # 1) Once Customer'da ara (isim = kayit adi)
    try:
        musteriler = frappe.get_all("Customer", fields=["name"], limit_page_length=0)
        for m in musteriler:
            if _normalize_tr(m["name"]) == hedef:
                return {"tur": "Customer", "kayit": m["name"]}
        for m in musteriler:
            if hedef in _normalize_tr(m["name"]) or _normalize_tr(m["name"]) in hedef:
                return {"tur": "Customer", "kayit": m["name"]}
    except Exception:
        pass

    # 2) Customer'da yoksa Lead'de ara (isim ayri bir alanda: lead_name)
    try:
        adaylar = frappe.get_all("Lead", fields=["name", "lead_name"], limit_page_length=0)
        for a in adaylar:
            if _normalize_tr(a.get("lead_name") or "") == hedef:
                return {"tur": "Lead", "kayit": a["name"]}
        for a in adaylar:
            ad = _normalize_tr(a.get("lead_name") or "")
            if ad and (hedef in ad or ad in hedef):
                return {"tur": "Lead", "kayit": a["name"]}
    except Exception:
        pass

    return {"tur": None, "kayit": girilen_ad}


def _urun_coz(girilen):
    """
    Kullanicinin yazdigi urun adini/kodunu gercek item_code'a cevirir.
    'Takim' yazilirsa '1234' gibi gercek kodu bulur.
    """
    if not girilen:
        return girilen
    try:
        urunler = frappe.get_all("Item", fields=["item_code", "item_name"], limit_page_length=0)
    except Exception:
        return girilen
    hedef = _normalize_tr(girilen)
    for u in urunler:
        if _normalize_tr(u.get("item_code") or "") == hedef:
            return u["item_code"]
    for u in urunler:
        if _normalize_tr(u.get("item_name") or "") == hedef:
            return u["item_code"]
    for u in urunler:
        ad = _normalize_tr(u.get("item_name") or "")
        if ad and (hedef in ad or ad in hedef):
            return u["item_code"]
    return girilen


def _tool_fiyat_onerisi(args):
    """
    Musteri (VEYA potansiyel musteri/Lead) + urun icin fiyat onerisi hazirlar.
      - Kayitli MUSTERI ve gecmis alimi VARSA: son alim fiyatini, enflasyon
        (TUFE) ve dolar kuru degisimiyle guncelleyip onerir. Hangisi daha
        yuksekse o oran uygulanir (maliyet riskini azaltmak icin).
      - Musteri gecmisi YOKSA veya LEAD (potansiyel musteri) ise: standart
        satis fiyati onerilir (Lead'in tanim geregi gecmis siparisi olamaz).
    SALT-OKUNUR: hicbir kayit olusturmaz/degistirmez.
    """
    musteri_girilen = args.get("musteri")
    urun = args.get("urun")
    if not musteri_girilen or not urun:
        return "Musteri ve urun kodu gerekli."

    coz = _musteri_coz(musteri_girilen)
    tur = coz["tur"]
    musteri = coz["kayit"]
    urun = _urun_coz(urun)

    if tur is None:
        return (f"'{musteri_girilen}' adinda kayitli bir musteri veya "
                "potansiyel musteri (Lead) bulunamadi.")

    if tur == "Lead":
        standart = _standart_fiyat(urun)
        return json.dumps({
            "gecmis_var": False,
            "musteri_turu": "Lead",
            "standart_fiyat": standart,
            "not": "Bu bir potansiyel musteri (Lead); tanim geregi gecmis "
                   "alimi olamaz, standart fiyat onerilir.",
        }, ensure_ascii=False, default=str)

    err = _guard("Sales Invoice")
    if err:
        return err

    try:
        satirlar = frappe.get_all(
            "Sales Invoice Item",
            filters={"item_code": urun},
            fields=["parent", "rate"],
            limit_page_length=200,
        )
    except Exception as e:
        return f"Sorgu hatasi: {str(e)[:200]}"

    faturalar = []
    if satirlar:
        parent_adlar = list({s["parent"] for s in satirlar})
        try:
            faturalar = frappe.get_all(
                "Sales Invoice",
                filters={
                    "name": ["in", parent_adlar],
                    "customer": musteri,
                    "docstatus": 1,
                },
                fields=["name", "posting_date"],
                order_by="posting_date desc",
                limit_page_length=1,
            )
        except Exception as e:
            return f"Fatura sorgusu hatasi: {str(e)[:200]}"

    if faturalar:
        son_fatura = faturalar[0]
        son_satir = next((s for s in satirlar if s["parent"] == son_fatura["name"]), None)
        eski_fiyat = float(son_satir["rate"]) if son_satir and son_satir.get("rate") else None
        eski_tarih = son_fatura["posting_date"]

        enf_yuzde = _evds_yuzde_degisim(SERI_TUFE, eski_tarih)
        kur_yuzde = _evds_yuzde_degisim(SERI_USD, eski_tarih)

        if eski_fiyat is None or (enf_yuzde is None and kur_yuzde is None):
            return json.dumps({
                "gecmis_var": True,
                "eski_fiyat": eski_fiyat,
                "eski_tarih": str(eski_tarih),
                "not": "Enflasyon/kur verisi su an alinamiyor (EVDS baglantisi olmayabilir). "
                       "Eski fiyati referans olarak kullanin, otomatik oneri yapilamiyor.",
            }, ensure_ascii=False, default=str)

        if enf_yuzde is not None and kur_yuzde is not None:
            if enf_yuzde >= kur_yuzde:
                secilen, yuzde = "enflasyon", enf_yuzde
            else:
                secilen, yuzde = "kur", kur_yuzde
        elif enf_yuzde is not None:
            secilen, yuzde = "enflasyon", enf_yuzde
        else:
            secilen, yuzde = "kur", kur_yuzde

        yeni_fiyat = round(eski_fiyat * (1 + yuzde / 100.0), 2)

        return json.dumps({
            "gecmis_var": True,
            "musteri_turu": "Customer",
            "eski_fiyat": eski_fiyat,
            "eski_tarih": str(eski_tarih),
            "enflasyon_yuzde": round(enf_yuzde, 2) if enf_yuzde is not None else None,
            "kur_yuzde": round(kur_yuzde, 2) if kur_yuzde is not None else None,
            "kullanilan_faktor": secilen,
            "onerilen_fiyat": yeni_fiyat,
        }, ensure_ascii=False, default=str)

    # gecmis yok -> standart fiyat
    standart = _standart_fiyat(urun)
    return json.dumps({
        "gecmis_var": False,
        "musteri_turu": "Customer",
        "standart_fiyat": standart,
        "not": "Bu musterinin bu urun icin gecmis alimi yok, standart fiyat onerilir.",
    }, ensure_ascii=False, default=str)


def _tool_teklif_taslagi(args):
    """
    Musteri (VEYA potansiyel musteri/Lead) + urun + miktar icin DOGRU YAPIDA
    bir Quotation taslagi hazirlar.
    - Ismi cozer: once Customer'da, bulamazsa Lead'de arar.
    - Fiyati _tool_fiyat_onerisi ile hesaplar (Lead ise dogrudan standart fiyat).
    - Quotation'in GERCEK alanlariyla doner: quotation_to (Customer/Lead),
      party_name, items[].
    SALT-OKUNUR: hicbir kayit olusturmaz/kaydetmez.
    """
    musteri_girilen = args.get("musteri")
    urun = args.get("urun")
    miktar = args.get("miktar") or 1

    if not musteri_girilen or not urun:
        return "Musteri ve urun bilgisi gerekli."

    coz = _musteri_coz(musteri_girilen)
    tur = coz["tur"]
    musteri_cozulmus = coz["kayit"]
    urun_cozulmus = _urun_coz(urun)

    if tur is None:
        return (f"'{musteri_girilen}' adinda kayitli bir musteri veya "
                "potansiyel musteri (Lead) bulunamadi.")
    if not frappe.db.exists("Item", urun_cozulmus):
        return f"'{urun}' adinda kayitli bir urun bulunamadi."

    try:
        fiyat_ham = _tool_fiyat_onerisi({"musteri": musteri_cozulmus, "urun": urun_cozulmus})
        fiyat_data = json.loads(fiyat_ham) if isinstance(fiyat_ham, str) and fiyat_ham.startswith("{") else {}
    except Exception:
        fiyat_data = {}

    if fiyat_data.get("gecmis_var"):
        birim_fiyat = fiyat_data.get("onerilen_fiyat") or fiyat_data.get("eski_fiyat")
    else:
        birim_fiyat = fiyat_data.get("standart_fiyat")

    if not birim_fiyat:
        return json.dumps({
            "_action": "fiyat_bulunamadi",
            "not": "Bu urun icin ne gecmis fiyat ne standart fiyat tanimli. "
                   "Birim fiyati siz belirtmelisiniz.",
        }, ensure_ascii=False, default=str)

    return json.dumps({
        "_action": "form_taslak",
        "doctype": "Quotation",
        "alanlar": {
            "quotation_to": tur,  # "Customer" veya "Lead"
            "party_name": musteri_cozulmus,
            "items": [
                {"item_code": urun_cozulmus, "qty": miktar, "rate": birim_fiyat},
            ],
        },
        "ozet": {
            "musteri": musteri_cozulmus,
            "musteri_turu": tur,
            "urun": urun_cozulmus,
            "miktar": miktar,
            "birim_fiyat": birim_fiyat,
            "fiyat_kaynagi": fiyat_data,
        },
    }, ensure_ascii=False, default=str)


TOOL_FNS = {
    "bugun": _tool_bugun,
    "toplam": _tool_toplam,
    "say": _tool_say,
    "liste": _tool_liste,
    "alanlari_getir": _tool_alanlari_getir,
    "form_doldur": _tool_form_doldur,
    "stok_durumu": _tool_stok_durumu,
    "stok_analiz": _tool_stok_analiz,
    "fiyat_onerisi": _tool_fiyat_onerisi,
    "teklif_taslagi": _tool_teklif_taslagi,
}

TOOLS_SPEC = [
    {"type": "function", "function": {
        "name": "bugun",
        "description": "Bugunun tarihini dondurur. Tarih araligi hesaplamadan once cagir.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "toplam",
        "description": (
            "Bir alanin toplamini ve kayit adedini dondurur. "
            "Ornek: bu ayki KDV -> doctype='Sales Invoice', "
            "alan='total_taxes_and_charges', "
            "filtreler={'posting_date': ['between', ['2026-07-01','2026-07-31']], 'docstatus': 1}"
        ),
        "parameters": {"type": "object", "properties": {
            "doctype": {"type": "string"},
            "alan": {"type": "string", "description": "grand_total, total_taxes_and_charges, net_total vb."},
            "filtreler": {"type": "object"},
        }, "required": ["doctype", "alan"]},
    }},
    {"type": "function", "function": {
        "name": "say",
        "description": "Filtreye uyan kayit sayisini dondurur.",
        "parameters": {"type": "object", "properties": {
            "doctype": {"type": "string"},
            "filtreler": {"type": "object"},
        }, "required": ["doctype"]},
    }},
    {"type": "function", "function": {
        "name": "liste",
        "description": (
            "Kayitlari listeler. 'siralama' ile en cok/en yuksek sorulari cevaplanir. "
            "Az sayida alan iste (orn: sadece isim ve miktar), fazla alan token limitini asar."
        ),
        "parameters": {"type": "object", "properties": {
            "doctype": {"type": "string"},
            "alanlar": {"type": "array", "items": {"type": "string"}},
            "filtreler": {"type": "object"},
            "siralama": {"type": "string", "description": "orn: 'grand_total desc'"},
            "limit": {"type": "integer", "description": "en fazla 25, varsayilan 10"},
        }, "required": ["doctype"]},
    }},
    {"type": "function", "function": {
        "name": "alanlari_getir",
        "description": "Bir doctype'in alan listesini dondurur. Form doldurmadan once kullan.",
        "parameters": {"type": "object", "properties": {
            "doctype": {"type": "string"},
        }, "required": ["doctype"]},
    }},
    {"type": "function", "function": {
        "name": "fiyat_onerisi",
        "description": (
            "Musteri VEYA potansiyel musteri (Lead) + urun icin fiyat "
            "onerisi hesaplar. Kayitli musterinin gecmis alimi varsa, o "
            "fiyati enflasyon (TUFE) ve dolar kuru degisimine gore gunceller "
            "(hangisi yuksekse onu kullanir). Gecmis yoksa VEYA potansiyel "
            "musteri (Lead) ise standart satis fiyatini onerir. "
            "'teklif ver', 'fiyat oner', 'ne kadara verelim' gibi isteklerde "
            "form_doldur'dan ONCE bu araci cagir."
        ),
        "parameters": {"type": "object", "properties": {
            "musteri": {"type": "string", "description": "Musteri adi"},
            "urun": {"type": "string", "description": "Urun adi veya kodu, orn: Takim veya 1234"},
        }, "required": ["musteri", "urun"]},
    }},
    {"type": "function", "function": {
        "name": "teklif_taslagi",
        "description": (
            "Musteri VEYA potansiyel musteri (Lead) + urun + miktar icin DOGRU "
            "YAPIDA Quotation (teklif) taslagi hazirlar. Isimleri otomatik cozer "
            "(once Customer'da, sonra Lead'de arar), fiyati hesaplar, satir "
            "tablosunu dogru doldurur. Teklif/fiyat/fatura-benzeri istekler icin "
            "'form_doldur' YERINE bunu kullan — form_doldur Quotation/Sales "
            "Invoice gibi satir tablolu belgeler icin DOGRU CALISMAZ."
        ),
        "parameters": {"type": "object", "properties": {
            "musteri": {"type": "string"},
            "urun": {"type": "string"},
            "miktar": {"type": "integer", "description": "varsayilan 1"},
        }, "required": ["musteri", "urun"]},
    }},
    {"type": "function", "function": {
        "name": "form_doldur",
        "description": (
            "Yeni kayit icin FORM TASLAGI hazirlar (kaydetmez!). Job Opening gibi "
            "BASIT (satir tablosu olmayan) belgeler icin kullan. "
            "Quotation/Sales Invoice/Sales Order gibi urun satiri iceren teklif/"
            "fatura istekleri icin BUNU DEGIL, 'teklif_taslagi' aracini kullan."
        ),
        "parameters": {"type": "object", "properties": {
            "doctype": {"type": "string", "description": "orn: Sales Invoice, Quotation"},
            "alanlar": {"type": "object", "description": "orn: {'customer': 'Ahmet Yilmaz', 'grand_total': 5000}"},
        }, "required": ["doctype", "alanlar"]},
    }},
    {"type": "function", "function": {
        "name": "stok_analiz",
        "description": (
            "AKILLI stok analizi: satis hizina gore siparis onerisi. "
            "Her urun icin aylik satis hizi, kac gunluk stok kaldigi ve "
            "kac adet siparis verilmesi gerektigini hesaplar. "
            "'ne siparis etmeliyim', 'ne kadar siparis vereyim', 'stok analizi', "
            "'hangi urun bitiyor' gibi sorularda BUNU kullan (stok_durumu yerine). "
            "Yavas satan urunlerde siparis onermez, fazla stok baglanmasin diye."
        ),
        "parameters": {"type": "object", "properties": {
            "gecmis_gun": {"type": "integer", "description": "kac gunluk satisa bakilsin, varsayilan 30 (son 1 ay)"},
            "hedef_gun": {"type": "integer", "description": "kac gunluk stok tutulsun, varsayilan 45"},
            "depo": {"type": "string", "description": "opsiyonel"},
        }},
    }},
    {"type": "function", "function": {
        "name": "stok_durumu",
        "description": (
            "Tum stok urunlerinin guncel miktarini, yeniden siparis esigini, "
            "onerilen siparis miktarini ve tedarikcisini dondurur. "
            "'stok durumu', 'ne siparis etmeliyim', 'stogu azalan urunler' "
            "gibi sorularda kullan. Eshik altindaki urunler 'durum: esik_altinda' olur."
        ),
        "parameters": {"type": "object", "properties": {
            "depo": {"type": "string", "description": "opsiyonel, orn: Magazalar - PT"},
        }},
    }},
]


def _system_prompt(context=None):
    ctx = ""
    if context:
        dt = context.get("doctype")
        docname = context.get("docname")
        route = context.get("route")
        parts = []
        if dt:
            parts.append(f"Kullanici su an '{dt}' ekraninda.")
        if docname:
            parts.append(f"Acik kayit: {docname}.")
        if route and not dt:
            parts.append(f"Sayfa: {route}.")
        if parts:
            ctx = "\nBAGLAM: " + " ".join(parts) + " Sorusu bu ekranla ilgiliyse buna gore cevapla."

    return (
        "Sen ERPNext icinde calisan bir asistansin. Kullanicinin sorularini "
        "verilen araclarla cevaplarsin.\n"
        "KURALLAR:\n"
        "- Tarih araligi gerekiyorsa ONCE 'bugun' aracini cagir.\n"
        "- Kesinlesmis belgeler icin filtrelere 'docstatus': 1 ekle.\n"
        "- Tutarlari Turk Lirasi formatinda sun (orn: 45.200,00 TL).\n"
        "- Veriyi ASLA uydurma; yalnizca arac sonuclarini kullan.\n"
        "- ARAC CIKTISINI (JSON, ham veri, kod blogu) KULLANICIYA GOSTERME. "
        "Sadece dogal, akici Turkce cumlelerle ozetle. Asla { } veya JSON yazma.\n"
        "- Kullanici yeni kayit olusturmak isterse 'form_doldur' aracini kullan ve "
        "verdigi bilgileri alanlara yerlestir. Kaydetme islemini SEN YAPMAZSIN; "
        "taslak kullaniciya gosterilir. Cevabinda kisaca 'taslagi hazirladim, "
        "gozden gecirip kaydedebilirsiniz' de. Teknik detay/JSON verme.\n"
        "- Liste sorularinda az alan iste (isim + gerekli olan), token limiti icin.\n"
        "- Stok/siparis sorularinda 'stok_analiz' aracini kullan (satis hizina "
        "gore akilli oneri verir). Cevapta her urun icin: kac gunluk stok kaldi, "
        "ayda ne kadar satiyor, kac adet siparis onerilir ve NEDEN. "
        "Aciliyet sirasina gore anlat (once kritik olanlar). "
        "'fazla_stok' veya 'olu_stok' durumundaki urunler icin siparis ONERME; "
        "aksine stok fazlasi oldugunu soyle. "
        "Siparisi SEN VERMEZSIN, sadece onerirsin.\n"
        "- Satis gecmisi yoksa (aylik_satis 0 ise) bunu durustce belirt: "
        "'yeterli satis verisi yok, tahmin yapilamiyor' de, uydurma.\n"
        "- 'guven' alani 'dusuk' ise oneriyi kesin bir emir gibi sunma; "
        "tahminin az sayida faturaya dayandigini ve teyit gerektigini soyle. "
        "'esik_alti_satis_yok' durumunda satis hizi hesaplanamadigini, "
        "onerinin sabit esik miktarina dayandigini belirt.\n"
        "- Erisim reddedilirse kibarca bu veriye erisimin olmadigini soyle.\n"
        "\n"
        "IK (HCM) METIN URETIMI:\n"
        "- Kullanici IS TANIMI isterse (orn: 'satis muduru icin is tanimi yaz') "
        "su basliklarla yapilandirilmis bir taslak yaz:\n"
        "  **Pozisyon Ozeti** (2-3 cumle), **Temel Sorumluluklar** (5-7 madde), "
        "**Aranan Nitelikler** (4-6 madde), **Tercih Sebebi** (2-3 madde).\n"
        "- Kullanici SMART HEDEF isterse 3-5 hedef yaz. Her hedef olculebilir "
        "bir sayi ve net bir sure icermeli (orn: 'ceyrek sonuna kadar musteri "
        "memnuniyetini %85e cikarmak'). Her hedefin altina kisa bir olcum "
        "kriteri ekle.\n"
        "- Bu metinlerde detayli ol; kisalik kurali IK metinleri icin gecerli degil.\n"
        "- Bu metinler TASLAKTIR. Sonunda kisaca 'Bu bir taslaktir, gozden "
        "gecirip duzenleyebilirsiniz' de.\n"
        "- Gercek calisan ismi, maas, kisisel bilgi ISTEME ve UYDURMA. "
        "Yalnizca pozisyon adiyla calis. Maas/personel verisine erisimin yok.\n"
        "- Kullanici bu metni sisteme kaydetmek isterse 'form_doldur' ile "
        "'Job Opening' taslagi hazirla (job_title ve description alanlarini doldur). "
        "Kaydetmeyi kullanici yapar.\n"
        "\n"
        "TEKLIF/FIYAT ONERISI:\n"
        "- Kullanici SADECE fiyat sorarsa (teklif taslagi degil), 'fiyat_onerisi' "
        "aracini kullan ve sonucu anlat.\n"
        "- Kullanici teklif/form/fatura ISTERSE (musteri+urun+miktar belliyse), "
        "DOGRUDAN 'teklif_taslagi' aracini cagir (fiyat_onerisi'ni ayrica "
        "cagirmana gerek yok, teklif_taslagi bunu kendi icinde yapar).\n"
        "- Sonucu anlatirken: eski fiyati, hangi faktorun (enflasyon/kur) "
        "kullanildigini ve yeni fiyati ACIKCA belirt. Ornek: "
        "'4 ay once 500 TL'den almisti. Bu surede enflasyon %13, dolar kuru "
        "%8 artti; enflasyon daha yuksek oldugu icin fiyata %13 yansitildi, "
        "yeni oneri 565 TL.'\n"
        "- Gecmis alim yoksa: standart fiyat kullanildigini belirt.\n"
        "- '_action': 'fiyat_bulunamadi' donerse, kullaniciya birim fiyati "
        "kendisinin belirtmesi gerektigini soyle.\n"
        "- Enflasyon/kur verisi alinamadiysa bunu durustce soyle, uydurma.\n"
        "- Miktar belirtilmediyse taslak acmadan once miktari sor.\n"
        "- 'potansiyel musteri', 'aday musteri' gibi ifadeler Lead demektir; "
        "'musteri_turu': 'Lead' donerse bunun henuz kayitli musteri olmadigini, "
        "gecmis alim olamayacagini ve standart fiyat onerildigini belirt.\n"
        "\n"
        "COK ONEMLI - DOGRULUK KURALI:\n"
        "- SADECE gercekten cagirdigin araclarin sonucuna dayanarak konus.\n"
        "- 'form_doldur' aracini CAGIRMADIYSAN, 'taslak hazirladim', "
        "'form olusturdum' gibi ifadeler KULLANMA. Bunu soylemek icin o araci "
        "GERCEKTEN cagirmis olman sart.\n"
        "- Bir islemi yaptigini iddia etmeden once, o islemi yapan araci "
        "cagirdigindan emin ol. Yapmadigin bir seyi yapmis gibi anlatma.\n"
        "- Kullanici teklif/fiyat/form istediginde ve yeterli bilgi (musteri, "
        "urun, miktar) varsa, sadece anlatma, 'form_doldur' aracini DA cagir; "
        "sonra 'taslak hazir' de. Bilgi eksikse taslak acmadan once kullaniciya sor.\n"
        "\n"
        "- Diger sorularda kisa, net ve Turkce cevap ver."
        + ctx
    )


_TASLAK_IDDIA_KALIPLARI = (
    "taslağı hazırladım", "taslagi hazirladim",
    "taslak hazırladım", "taslak hazirladim",
    "formu hazırladım", "formu hazirladim",
    "form hazırladım", "form hazirladim",
    "formu oluşturdum", "formu olusturdum",
    "form oluşturdum", "form olusturdum",
    "taslağı oluşturdum", "taslagi olusturdum",
)


def _dogruluk_kontrolu(cevap, form_taslak):
    """
    AI, gercekte form_doldur cagirmadigi halde 'taslak hazirladim' derse
    (form_taslak bos oldugu halde) bunu tespit edip duzeltme notu ekler.
    Prompt tek basina yeterli olmadigi icin kod seviyesinde guvence.
    """
    if form_taslak:
        return cevap
    dusuk = cevap.lower()
    if any(k in dusuk for k in _TASLAK_IDDIA_KALIPLARI):
        cevap += (
            "\n\n(Duzeltme: Henuz bir taslak olusturmadim. "
            "Taslak acmami isterseniz musteri, urun ve miktari belirtip "
            "onaylamaniz yeterli.)"
        )
    return cevap


def _groq_call(messages):
    """Groq'a istek; 429/503'te bekleyip tekrar dener."""
    key = _groq_key()
    if not key:
        frappe.throw(_("Groq API anahtari tanimli degil (GROQ_AI_KEY)."))
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {
        "model": _groq_model(),
        "messages": messages,
        "tools": TOOLS_SPEC,
        "temperature": 0.1,
    }
    last = None
    for attempt in range(4):
        try:
            r = requests.post(GROQ_URL, headers=headers, json=body, timeout=90)
            data = r.json()
        except Exception as e:
            last = {"error": {"message": str(e)}}
            time.sleep(2 * (attempt + 1))
            continue
        if "error" in data:
            code = r.status_code
            if code in (429, 503) and attempt < 3:
                time.sleep(3 * (attempt + 1))
                last = data
                continue
            return data
        return data
    return last or {"error": {"message": "Bilinmeyen hata"}}


@frappe.whitelist()
def ask(question, context=None):
    if isinstance(context, str):
        try:
            context = json.loads(context)
        except Exception:
            context = None

    messages = [
        {"role": "system", "content": _system_prompt(context)},
        {"role": "user", "content": question},
    ]

    adimlar = []
    form_taslak = None

    for _step in range(MAX_STEPS):
        data = _groq_call(messages)

        if "error" in data:
            err = data["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            # Kullaniciya sade mesaj
            if "rate limit" in msg.lower() or "tpm" in msg.lower():
                sade = ("Su an cok fazla veri islendi, kisa bir sure sonra tekrar deneyin. "
                        "Daha dar bir soru (orn. belirli bir tarih araligi) daha hizli sonuc verir.")
            else:
                sade = "AI servisine su an ulasilamiyor. Lutfen birazdan tekrar deneyin."
            return {"cevap": sade, "form_taslak": None, "adimlar": adimlar}

        msg = data["choices"][0]["message"]
        calls = msg.get("tool_calls")

        if not calls:
            cevap = (msg.get("content") or "").strip() or "(cevap uretilemedi)"
            cevap = _dogruluk_kontrolu(cevap, form_taslak)
            return {
                "cevap": cevap,
                "form_taslak": form_taslak,
                "adimlar": adimlar,
            }

        messages.append(msg)
        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"]["arguments"] or "{}")
            except Exception:
                args = {}

            adimlar.append({"arac": name, "doctype": args.get("doctype", "")})

            fn = TOOL_FNS.get(name)
            result = fn(args) if fn else f"Bilinmeyen arac: {name}"

            # form_doldur VEYA teklif_taslagi -- hangi arac olursa olsun,
            # sonuc bir form taslagi ise yakala
            if name in ("form_doldur", "teklif_taslagi"):
                try:
                    parsed = json.loads(result)
                    if isinstance(parsed, dict) and parsed.get("_action") == "form_taslak":
                        form_taslak = parsed
                except Exception:
                    pass

            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "name": name,
                "content": result[:MAX_TOOL_CHARS],
            })

    return {
        "cevap": "Islem uzun surdu. Lutfen sorunuzu biraz daha sadelestirin.",
        "form_taslak": form_taslak,
        "adimlar": adimlar,
    }


@frappe.whitelist()
def kritik_stok_kontrol():
    """
    Esik altindaki urunleri bulur, AI'dan oneri metni uretir.
    Cana TEK kayit dusurur (ayni okunmamis uyari varsa tekrar eklemez).
    Donus: {"var": bool, "mesaj": "...", "urun_sayisi": n}
    """
    # 1) Akilli analiz: satis hizina gore dikkat gerektiren urunler
    ham = _tool_stok_analiz({})
    try:
        veri = json.loads(ham)
        urunler = veri.get("urunler", []) if isinstance(veri, dict) else []
    except Exception:
        urunler = []

    # Uyari gerektirenler: satis hizina gore acil olanlar VEYA esik altindakiler
    uyari_durumlari = {"kritik", "acil", "siparis_zamani", "esik_alti_satis_yok"}
    esik_alti = [
        u for u in urunler
        if u.get("durum") in uyari_durumlari or u.get("esik_altinda")
    ]

    if not esik_alti and not urunler:
        # analiz hic calismadiysa klasik esik kontrolune don
        try:
            basit = json.loads(_tool_stok_durumu({}))
            if isinstance(basit, list):
                esik_alti = [u for u in basit if u.get("durum") == "esik_altinda"]
        except Exception:
            esik_alti = []

    if not esik_alti:
        return {"var": False, "mesaj": "", "urun_sayisi": 0}

    # 2) AI'dan oneri metni uret
    ozet = []
    for u in esik_alti:
        parca = f"{u.get('urun')}: stok {u.get('stok')}"
        if u.get("aylik_satis"):
            parca += f", ayda ~{u.get('aylik_satis')} satiyor"
        if u.get("kalan_gun") is not None:
            parca += f", {u.get('kalan_gun')} gunluk stok kaldi"
        if u.get("onerilen_siparis"):
            parca += f", onerilen siparis {u.get('onerilen_siparis')} adet"
        if u.get("tedarikci"):
            parca += f", tedarikci {u.get('tedarikci')}"
        if u.get("guven") == "dusuk":
            parca += " (tahmin az veriye dayaniyor, guven dusuk)"
        elif u.get("guven") == "veri_yok":
            parca += " (satis verisi yok)"
        ozet.append(parca)
    veri = "\n".join(ozet)

    mesaj = None
    try:
        messages = [
            {"role": "system", "content": (
                "Sen bir stok asistanisin. Verilen urunler icin "
                "kisa, net bir Turkce uyari metni yaz. "
                "MUTLAKA urun adini yazarak baslat (orn: 'Takim urununun stogu...'). "
                "Her urun icin: kac adet kaldi, ayda ne kadar satiyor, kac gun yeter, "
                "kac adet siparis onerilir, hangi tedarikciden. 'Esik' kelimesini KULLANMA. "
                "2-3 cumleyi gecme. JSON veya teknik detay yazma, sadece dogal uyari metni."
            )},
            {"role": "user", "content": f"Esik alti urunler:\n{veri}"},
        ]
        key = _groq_key()
        if key:
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            body = {"model": _groq_model(), "messages": messages, "temperature": 0.2}
            r = requests.post(GROQ_URL, headers=headers, json=body, timeout=30)
            data = r.json()
            if "choices" in data:
                mesaj = (data["choices"][0]["message"].get("content") or "").strip()
    except Exception:
        mesaj = None

    # urun adlari
    urun_adlari = ", ".join(u.get("urun", "") for u in esik_alti)

    if not mesaj:
        # AI cevap vermezse basit metin
        mesaj = (f"Su urunler yeniden siparis esiginin altinda: {urun_adlari}. "
                 "Lutfen siparis veriniz.")

    # 3) Cana TEK kayit dusur — urun bazli benzersiz subject, tekrar ekleme
    try:
        kullanici = frappe.session.user
        # benzersiz subject: hangi urunler etkilendigini goster
        subject = f"Stok Uyarisi: {urun_adlari} — yeniden siparis gerekli"
        mevcut = frappe.get_all(
            "Notification Log",
            filters={
                "for_user": kullanici,
                "subject": subject,
                "read": 0,
            },
            limit_page_length=1,
        )
        if not mevcut:
            log = frappe.new_doc("Notification Log")
            log.subject = subject
            log.email_content = mesaj
            log.for_user = kullanici
            log.type = "Alert"
            # tiklayinca Stok Bakiyesi raporuna gitsin
            # (alan bu ERPNext surumunde varsa)
            try:
                meta = frappe.get_meta("Notification Log")
                if meta.get_field("link"):
                    log.link = "/app/query-report/Stock Balance"
                else:
                    log.document_type = "Item"
            except Exception:
                pass
            log.insert(ignore_permissions=True)
            frappe.db.commit()
    except Exception:
        # cana yazma basarisiz olsa bile pop-up calismali
        pass

    # Frontend'in kart olarak gosterebilmesi icin yapilandirilmis detay
    detaylar = []
    for u in esik_alti:
        detaylar.append({
            "urun": u.get("urun"),
            "stok": u.get("stok"),
            "esik": u.get("esik"),
            "aylik_satis": u.get("aylik_satis"),
            "kalan_gun": u.get("kalan_gun"),
            "durum": u.get("durum"),
            "onerilen_siparis": u.get("onerilen_siparis"),
            "tedarikci": u.get("tedarikci"),
            "gerekce": u.get("gerekce"),
            "guven": u.get("guven"),
            "esik_altinda": u.get("esik_altinda"),
            "fatura_sayisi": u.get("fatura_sayisi"),
        })

    return {
        "var": True,
        "mesaj": mesaj,
        "urun_sayisi": len(esik_alti),
        "urunler": detaylar,
    }


@frappe.whitelist()
def durum():
    return {
        "hazir": bool(_groq_key()),
        "model": _groq_model(),
        "izinli_doctype_sayisi": len(ALLOWED_DOCTYPES),
    }
