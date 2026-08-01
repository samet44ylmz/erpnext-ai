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
    except Exception as e:
        # ONCEDEN sessizce yutuluyordu -- EVDS bir ay bozuk olsa kimse
        # fark etmezdi. Artik Frappe Error Log'a duser, Admin gorebilir.
        try:
            frappe.log_error(
                title="EVDS baglanti hatasi",
                message=f"seri={seri}, tarih={hedef_tarih}, hata={str(e)[:300]}",
            )
        except Exception:
            pass
        return None


def _evds_yuzde_degisim(seri, eski_tarih):
    """Iki tarih arasindaki yuzde degisimi dondurur (None: veri yok)."""
    eski = _evds_deger(seri, eski_tarih)
    bugun = _evds_deger(seri, date.today())
    if eski is None or bugun is None or eski == 0:
        return None
    return (bugun - eski) / eski * 100.0


# ------------------------------------------------------------------
# IS KURALLARI (sektor ayari, sadakat kademeleri, marj/iskonto sinirlari)
#
# Bu degerler ARTIK KODDA SABIT DEGIL -- ERPNext arayuzunden duzenlenebilen
# DocType kayitlarindan okunur. Satis ekibi bir yuzdeyi degistirmek icin
# kod degisikligi + deploy beklemek zorunda degil.
#
# Asagidaki VARSAYILAN_* tablolari YALNIZCA yedektir: DocType'lar henuz
# kurulmadiysa (setup script'i calistirilmadan once) ya da tablo bosaltildiysa
# sistem eski davranisiyla calismaya devam eder, sessizce sifirlanmaz.
# ------------------------------------------------------------------
VARSAYILAN_MIN_MARJ = 0.15          # maliyetin en az %15 uzerinde olmali
VARSAYILAN_ISKONTO_ONAY_ESIGI = 15  # toplam indirim bu yuzdeyi gecerse onay

VARSAYILAN_SEKTOR_FIYAT_AYARI = {
    # yuzde: pozitif = zam, negatif = indirim (taban fiyata uygulanir)
    "Finans": 8,
    "Sigorta": 5,
    "Yatirim": 5,
    "Enerji": 3,
    "Saglik": 0,
    "Teknoloji": 0,
    "Kamu": -8,
    "Lojistik": -3,
    "FMCG": -5,
}

VARSAYILAN_SEKTOR_GEREKCE = {
    "Finans": "regule sektor, yuksek uyum/guvenlik gereksinimleri ve buyuk butceler",
    "Sigorta": "regule sektor, orta duzey uyum maliyeti",
    "Yatirim": "regule sektor, benzer uyum gereksinimleri",
    "Enerji": "kritik altyapi, yuksek guvenilirlik beklentisi",
    "Saglik": "standart uygulama, ek bir sektorel ayar yok",
    "Teknoloji": "standart uygulama, ek bir sektorel ayar yok",
    "Kamu": "ihale/kamu alim mevzuati, rekabetci ve dusuk marjli surec",
    "Lojistik": "rekabetci, fiyata duyarli sektor",
    "FMCG": "buyuk olcekli firmalar, guclu pazarlik gucu",
}

# (minimum_ciro, indirim_yuzdesi) -- BUYUKTEN KUCUGE sirali olmali
VARSAYILAN_SADAKAT_KADEMELERI = [
    (300000.0, 12.0),
    (150000.0, 8.0),
    (75000.0, 5.0),
    (25000.0, 3.0),
]

AYAR_CACHE_ANAHTARI = "erpnext_ai_fiyat_ayarlari"
AYAR_CACHE_SANIYE = 300  # 5 dk; kayit degisince hook zaten aninda temizler


def fiyat_ayarlari_onbellegi_temizle(doc=None, method=None):
    """
    Ayar DocType'larindan biri degisince onbellegi dusurur. hooks.py'daki
    doc_events uzerinden otomatik cagrilir; elle de cagrilabilir.
    """
    try:
        frappe.cache().delete_value(AYAR_CACHE_ANAHTARI)
    except Exception:
        pass


def _fiyat_ayarlari():
    """
    Is kurallarini DocType'lardan okur ve onbellege alir.

    Her tablo BAGIMSIZ degerlendirilir: sektor tablosu doluysa oradan,
    bos/eksikse varsayilandan okunur. Boylece yarim kurulumda sistem
    kismen degil, tutarli sekilde calisir.

    Donus: {"sektor", "gerekce", "kademeler", "min_marj",
            "iskonto_esigi", "kaynak"}
    """
    try:
        onbellek = frappe.cache().get_value(AYAR_CACHE_ANAHTARI)
        if onbellek:
            return onbellek
    except Exception:
        pass

    ayar = {
        "sektor": dict(VARSAYILAN_SEKTOR_FIYAT_AYARI),
        "gerekce": dict(VARSAYILAN_SEKTOR_GEREKCE),
        "kademeler": list(VARSAYILAN_SADAKAT_KADEMELERI),
        "min_marj": VARSAYILAN_MIN_MARJ,
        "iskonto_esigi": VARSAYILAN_ISKONTO_ONAY_ESIGI,
        "kaynak": [],
    }

    # --- 1) Sektor fiyat ayarlari ---
    try:
        if frappe.db.exists("DocType", "Sektor Fiyat Ayari"):
            satirlar = frappe.get_all(
                "Sektor Fiyat Ayari",
                filters={"aktif": 1},
                fields=["sektor", "yuzde", "gerekce"],
                limit_page_length=0,
            )
            if satirlar:
                ayar["sektor"] = {
                    s["sektor"]: float(s.get("yuzde") or 0) for s in satirlar
                }
                ayar["gerekce"] = {
                    s["sektor"]: (s.get("gerekce") or "") for s in satirlar
                }
                ayar["kaynak"].append("sektor:doctype")
    except Exception as e:
        frappe.log_error(
            f"Sektor Fiyat Ayari okunamadi, varsayilan kullaniliyor: {str(e)[:300]}",
            "erpnext_ai fiyat ayarlari",
        )

    # --- 2) Sadakat kademeleri ---
    try:
        if frappe.db.exists("DocType", "Sadakat Kademesi"):
            satirlar = frappe.get_all(
                "Sadakat Kademesi",
                filters={"aktif": 1},
                fields=["minimum_ciro", "indirim_yuzde"],
                limit_page_length=0,
            )
            if satirlar:
                kademeler = [
                    (float(s.get("minimum_ciro") or 0), float(s.get("indirim_yuzde") or 0))
                    for s in satirlar
                ]
                # Buyukten kucuge -- ilk eslesen kademe kazanir.
                ayar["kademeler"] = sorted(kademeler, key=lambda k: k[0], reverse=True)
                ayar["kaynak"].append("sadakat:doctype")
    except Exception as e:
        frappe.log_error(
            f"Sadakat Kademesi okunamadi, varsayilan kullaniliyor: {str(e)[:300]}",
            "erpnext_ai fiyat ayarlari",
        )

    # --- 3) Marj / iskonto sinirlari (tekil ayar kaydi) ---
    try:
        if frappe.db.exists("DocType", "Fiyat Motoru Ayari"):
            tekil = frappe.get_single("Fiyat Motoru Ayari")
            marj = tekil.get("minimum_marj_yuzde")
            esik = tekil.get("iskonto_onay_esigi")
            if marj not in (None, ""):
                marj = float(marj)
                # Alan YUZDE bekler (15 = %15). Biri oran olarak (0.15)
                # girerse marj %0.15'e duser ve zarar kontrolu pratikte
                # hicbir seyi yakalamaz -- sessiz gecmeyip yedege doneriz.
                if 0 < marj < 1:
                    frappe.log_error(
                        f"Fiyat Motoru Ayari: minimum marj '{marj}' olarak girilmis. "
                        f"Bu alan YUZDE bekler (orn. 15 = %15). Oran girildigi "
                        f"varsayilarak yok sayildi, varsayilan "
                        f"%{VARSAYILAN_MIN_MARJ * 100:.0f} kullaniliyor.",
                        "erpnext_ai fiyat ayarlari",
                    )
                else:
                    ayar["min_marj"] = marj / 100.0
            if esik not in (None, ""):
                ayar["iskonto_esigi"] = float(esik)
            ayar["kaynak"].append("sinirlar:doctype")
    except Exception as e:
        frappe.log_error(
            f"Fiyat Motoru Ayari okunamadi, varsayilan kullaniliyor: {str(e)[:300]}",
            "erpnext_ai fiyat ayarlari",
        )

    if not ayar["kaynak"]:
        ayar["kaynak"] = ["varsayilan"]

    try:
        frappe.cache().set_value(AYAR_CACHE_ANAHTARI, ayar, expires_in_sec=AYAR_CACHE_SANIYE)
    except Exception:
        pass
    return ayar


def _sektor_ayari(musteri):
    """
    Musterinin Customer Group'una (sektorune) gore taban fiyat ayari.
    Donus: (yuzde, sektor_adi, gerekce_metni)
    """
    try:
        grup = frappe.db.get_value("Customer", musteri, "customer_group")
    except Exception:
        grup = None
    ayar = _fiyat_ayarlari()
    yuzde = ayar["sektor"].get(grup, 0)
    gerekce = ayar["gerekce"].get(grup, "")
    return yuzde, grup, gerekce


def _sadakat_indirim_yuzdesi(ciro):
    """Son 12 aylik ciroya gore kademeli sadakat indirimi (%)."""
    for minimum_ciro, yuzde in _fiyat_ayarlari()["kademeler"]:
        if ciro >= minimum_ciro:
            return yuzde
    return 0


def _musteri_son_12ay_cirosu(musteri):
    """Musterinin son 12 aydaki onayli (docstatus=1) toplam cirosu."""
    baslangic = (date.today() - timedelta(days=365)).isoformat()
    try:
        rows = frappe.get_list(
            "Sales Invoice",
            filters={"customer": musteri, "docstatus": 1, "posting_date": [">=", baslangic]},
            fields=["sum(grand_total) as toplam"],
        )
        return float(rows[0]["toplam"] or 0) if rows and rows[0].get("toplam") else 0.0
    except Exception:
        return 0.0


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
    Musteri (VEYA potansiyel musteri/Lead) + urun (+ opsiyonel miktar) icin
    fiyat onerisi hazirlar.

    Hesaplama sirasi:
      1) Taban fiyat: kayitli musterinin gecmis alim fiyati (enflasyon/kur ile
         guncellenmis) VEYA guncel standart fiyat -- HANGISI YUKSEKSE (Lead
         icin dogrudan standart fiyat, gecmis olamayacagi icin).
      2) TOPLAM tutar:
         - HIZMET (SRV-*): taban fiyat YILLIKTIR. Miktar = AY SAYISI.
           toplam = taban_fiyat * (miktar / 12).
         - URUN (PRM-*): taban fiyat BIRIM fiyattir. Miktar = ADET/LISANS.
           toplam = taban_fiyat * miktar.
      3) Bu toplam uzerinden sadakat indirimi (Customer'da, son 12 ay cirosuna
         gore; Lead'de yok).
      4) Nihai toplam / miktar = onerilen birim fiyat.

    Indirimler URUN BASI degil, SIPARISIN TOPLAM BAKIYESI uzerinden hesaplanir.
    SALT-OKUNUR: hicbir kayit olusturmaz/degistirmez.
    """
    musteri_girilen = args.get("musteri")
    urun = args.get("urun")
    miktar = args.get("miktar") or 1
    if not musteri_girilen or not urun:
        return "Musteri ve urun kodu gerekli."

    coz = _musteri_coz(musteri_girilen)
    tur = coz["tur"]
    musteri = coz["kayit"]
    urun = _urun_coz(urun)

    if tur is None:
        return (f"'{musteri_girilen}' adinda kayitli bir musteri veya "
                "potansiyel musteri (Lead) bulunamadi.")

    # --- 1) TABAN FIYAT belirle ---
    if tur == "Lead":
        taban_fiyat = _standart_fiyat(urun)
        gecmis_var = False
        detay_gecmis = {
            "musteri_turu": "Lead",
            "standart_fiyat": taban_fiyat,
            "not": "Bu bir potansiyel musteri (Lead); tanim geregi gecmis "
                   "alimi olamaz, standart fiyat esas alinir.",
        }
    else:
        err = _guard("Sales Invoice")
        if err:
            return err
        # ONCE musterinin faturalarini bul (kucuk, sinirli kume), SONRA o
        # faturalardaki urun satirina bak. Eskiden TERSI yapiliyordu (once
        # urunun TUM satislarini 200 satirla sinirlayip sonra musteriye gore
        # filtreleme) -- cok satan bir urunde musterinin kaydi o 200 satirin
        # disinda kalip "gecmis yok" YANLIS sonucu verebiliyordu.
        try:
            musteri_faturalari = frappe.get_all(
                "Sales Invoice",
                filters={"customer": musteri, "docstatus": 1},
                fields=["name", "posting_date"],
                order_by="posting_date desc",
                limit_page_length=200,
            )
        except Exception as e:
            return f"Fatura sorgusu hatasi: {str(e)[:200]}"

        faturalar = []
        satirlar = []
        if musteri_faturalari:
            fatura_adlari = [f["name"] for f in musteri_faturalari]
            try:
                kalemler = frappe.get_all(
                    "Sales Invoice Item",
                    filters={"item_code": urun, "parent": ["in", fatura_adlari]},
                    fields=["parent", "rate"],
                    limit_page_length=0,
                )
            except Exception as e:
                return f"Sorgu hatasi: {str(e)[:200]}"

            if kalemler:
                kalem_parent_adlari = {k["parent"] for k in kalemler}
                faturalar = [f for f in musteri_faturalari if f["name"] in kalem_parent_adlari][:1]
                satirlar = kalemler

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
                    "not": "Enflasyon/kur verisi su an alinamiyor (EVDS baglantisi "
                           "olmayabilir). Eski fiyati referans olarak kullanin.",
                }, ensure_ascii=False, default=str)

            if enf_yuzde is not None and kur_yuzde is not None:
                secilen, yuzde = ("enflasyon", enf_yuzde) if enf_yuzde >= kur_yuzde else ("kur", kur_yuzde)
            elif enf_yuzde is not None:
                secilen, yuzde = "enflasyon", enf_yuzde
            else:
                secilen, yuzde = "kur", kur_yuzde

            tahmini_fiyat = round(eski_fiyat * (1 + yuzde / 100.0), 2)
            guncel_standart = _standart_fiyat(urun)
            if guncel_standart and guncel_standart > tahmini_fiyat:
                taban_fiyat, nihai_faktor = guncel_standart, "standart_fiyat_guncellemesi"
            else:
                taban_fiyat, nihai_faktor = tahmini_fiyat, secilen

            gecmis_var = True
            detay_gecmis = {
                "musteri_turu": "Customer",
                "eski_fiyat": eski_fiyat,
                "eski_tarih": str(eski_tarih),
                "enflasyon_yuzde": round(enf_yuzde, 2) if enf_yuzde is not None else None,
                "kur_yuzde": round(kur_yuzde, 2) if kur_yuzde is not None else None,
                "guncel_standart_fiyat": guncel_standart,
                "enflasyon_kur_tahmini": tahmini_fiyat,
                "kullanilan_faktor": nihai_faktor,
            }
        else:
            taban_fiyat = _standart_fiyat(urun)
            gecmis_var = False
            detay_gecmis = {
                "musteri_turu": "Customer",
                "standart_fiyat": taban_fiyat,
                "not": "Bu musterinin bu urun icin gecmis alimi yok, standart fiyat esas alinir.",
            }

    if not taban_fiyat:
        return json.dumps({
            "_action": "fiyat_bulunamadi",
            "not": "Ne gecmis fiyat ne standart fiyat tanimli. Birim fiyati siz belirtmelisiniz.",
        }, ensure_ascii=False, default=str)

    # --- 1.5) SEKTOR AYARI: musterinin sektorune (Customer Group) gore
    # taban fiyat yukari/asagi ayarlanir -- indirimlerden ONCE uygulanir.
    sektor_yuzde, sektor_grubu, sektor_gerekce = _sektor_ayari(musteri)
    taban_fiyat_sektor_oncesi = taban_fiyat
    if sektor_yuzde:
        taban_fiyat = round(taban_fiyat * (1 + sektor_yuzde / 100.0), 2)

    # --- 2) TOPLAM tutar: SURE BAZLI ise urunin GERCEK 'Fiyat Periyodu'
    # alanina bakilir (Yillik/Aylik) -- artik SRV-/PRM- onekinden VARSAYIM
    # yapilmiyor. Alan bos/tanimsizsa Yillik varsayilir (eski davranisla
    # ayni, geriye donuk uyumlu).
    if "sure_bazli" in args:
        hizmet_mi_hesap = bool(args.get("sure_bazli"))
    else:
        hizmet_mi_hesap = urun.startswith("SRV-")

    if hizmet_mi_hesap:
        try:
            periyot = frappe.db.get_value("Item", urun, "custom_fiyat_periyodu")
        except Exception:
            periyot = None
        if periyot == "Aylik":
            # standart_rate ZATEN aylik -- oranlamaya (12'ye bolmeye) GEREK YOK.
            toplam_oncesi = round(taban_fiyat * miktar, 2)
        else:
            # "Yillik" veya alan hic isaretlenmemis -- eski/varsayilan davranis.
            toplam_oncesi = round(taban_fiyat * (miktar / 12.0), 2)
    else:
        toplam_oncesi = round(taban_fiyat * miktar, 2)

    # --- 3) INDIRIMLER: TOPLAM tutar uzerinden ---
    # KURAL: sektor zaten INDIRIM veriyorsa (negatif ayar -- Kamu, Lojistik,
    # FMCG gibi), sadakat indirimi UYGULANMAZ. Boylece iki indirim ust uste
    # binip fiyati gereksiz/riskli sekilde dusurmez. Sektor notr/prim ise
    # (0 veya pozitif) sadakat indirimi normal calisir.
    if tur == "Customer" and sektor_yuzde >= 0:
        ciro_12ay = _musteri_son_12ay_cirosu(musteri)
        sadakat_yuzde = _sadakat_indirim_yuzdesi(ciro_12ay)
    else:
        ciro_12ay = 0.0
        sadakat_yuzde = 0

    ara_toplam = round(toplam_oncesi * (1 - sadakat_yuzde / 100.0), 2) if sadakat_yuzde else toplam_oncesi

    toplam_nihai = ara_toplam  # toptan/buyuk siparis indirimi KALDIRILDI

    onerilen_birim_fiyat = round(toplam_nihai / miktar, 2) if miktar else toplam_nihai

    # --- MALIYET KONTROLU: fiyat, maliyet + MINIMUM MARJ'in altina dustu mu? ---
    # Sadece "maliyetin ustunde mi" yetmez -- makul bir kar marji da sart.
    # Sektor/sadakat indirimleri ust uste binince fiyat sessizce bu sinirin
    # altina duşebilir; bunu ASLA sessiz gecmeyiz.
    zarar_riski = False
    maliyet = None
    try:
        maliyet = frappe.db.get_value("Item", urun, "custom_maliyet")
        if maliyet:
            maliyet = float(maliyet)
            # ONEMLI: bu da ayni "Fiyat Periyodu" alanina gore hesaplanir --
            # onceden burada da SRV- onekinden varsayim yapiliyordu, ayni
            # risk (12 kat sapma) maliyet tarafinda da vardi.
            if hizmet_mi_hesap and periyot != "Aylik":
                karsilastirma_maliyet = round(maliyet * (miktar / 12.0), 2)
            elif hizmet_mi_hesap and periyot == "Aylik":
                karsilastirma_maliyet = round(maliyet * miktar, 2)
            else:
                karsilastirma_maliyet = maliyet * miktar
            gerekli_minimum = round(karsilastirma_maliyet * (1 + _fiyat_ayarlari()["min_marj"]), 2)
            if toplam_nihai < gerekli_minimum:
                zarar_riski = True
    except Exception:
        pass

    # --- ISKONTO TAVANI: toplam indirim cok yuksekse onay istensin ---
    toplam_indirim_yuzde = 0.0
    if sektor_yuzde < 0:
        toplam_indirim_yuzde += abs(sektor_yuzde)
    if sadakat_yuzde:
        toplam_indirim_yuzde += sadakat_yuzde
    onay_gerekli = toplam_indirim_yuzde > _fiyat_ayarlari()["iskonto_esigi"]

    sonuc = {
        "gecmis_var": gecmis_var,
        "miktar": miktar,
        "taban_birim_fiyat_sektor_oncesi": taban_fiyat_sektor_oncesi,
        "sektor_ayari_yuzde": sektor_yuzde,
        "sektor_grubu": sektor_grubu,
        "sektor_gerekce": sektor_gerekce,
        "taban_birim_fiyat": taban_fiyat,
        "toplam_oncesi_indirim": toplam_oncesi,
        "sadakat_cirosu_12ay": round(ciro_12ay, 2),
        "sadakat_indirim_yuzde": sadakat_yuzde,
        "toplam_nihai": toplam_nihai,
        "onerilen_fiyat": onerilen_birim_fiyat,
        "zarar_riski": zarar_riski,
        "maliyet": maliyet,
        "toplam_indirim_yuzde": round(toplam_indirim_yuzde, 1),
        "onay_gerekli": onay_gerekli,
    }
    sonuc.update(detay_gecmis)
    return json.dumps(sonuc, ensure_ascii=False, default=str)


def _tevkifat_kodu_bul(amac_metni):
    """
    Kullanicinin 'ne icin tevkifat' cevabina gore Tevkifat Kodlari
    listesinden en uygun kaydi bulur (anahtar kelime ortusmesine gore).
    Bulamazsa None doner.
    """
    if not amac_metni:
        return None
    try:
        kayitlar = frappe.get_all(
            "Tevkifat Kodlari", fields=["kod", "aciklama", "oran"], limit_page_length=0
        )
    except Exception:
        return None
    if not kayitlar:
        return None

    hedef = _normalize_tr(amac_metni)
    hedef_kelimeler = set(hedef.split())

    en_iyi, en_iyi_skor = None, 0
    for k in kayitlar:
        aciklama_norm = _normalize_tr(k.get("aciklama") or "")
        aciklama_kelimeler = set(aciklama_norm.split())
        skor = len(hedef_kelimeler & aciklama_kelimeler)
        if hedef in aciklama_norm or aciklama_norm in hedef:
            skor += 5
        if skor > en_iyi_skor:
            en_iyi_skor = skor
            en_iyi = k
    return en_iyi if en_iyi_skor > 0 else None


def _turkey_tax_sablonu():
    """Sistemde tanimli 'Turkey Tax' ile baslayan vergi sablonunu bulur."""
    try:
        return frappe.db.get_value(
            "Sales Taxes and Charges Template",
            {"name": ["like", "Turkey Tax%"]},
            "name",
        )
    except Exception:
        return None


def _kdv_orani_al(sablon_adi):
    """
    Vergi sablonunun GERCEK oranini okur (sabit %18 YANLISTI -- Temmuz 2023'ten
    beri Turkiye'de standart oran %20, ayrica urune gore %1/%10/%20 da olabilir).
    Sablon/oran bulunamazsa None doner -- boyle durumda tevkifat hesaplanmaz,
    kullaniciya "oran bulunamadi, elle kontrol edin" denir; YANLIS oran ASLA
    varsayilmaz.
    """
    if not sablon_adi:
        return None
    try:
        satirlar = frappe.get_all(
            "Sales Taxes and Charges Template Detail",
            filters={"parent": sablon_adi},
            fields=["rate"],
            limit_page_length=1,
        )
        if satirlar and satirlar[0].get("rate") is not None:
            return float(satirlar[0]["rate"])
    except Exception:
        pass
    return None


def _tool_fatura_taslagi(args):
    """
    Musteri + urun + miktar icin Satis Faturasi (Sales Invoice) taslagi
    hazirlar. teklif_taslagi ile AYNI fiyat motorunu kullanir, farkli olarak:
      - KDV dahil edilsin mi (kdv_dahil)
      - Tevkifatli mi (tevkifatli), oyleyse hangi kod (tevkifat_amaci'na
        gore Tevkifat Kodlari listesinden otomatik bulunur)
    SALT-OKUNUR: hicbir kayit olusturmaz/kaydetmez, sadece taslak dondurur.
    """
    musteri_girilen = args.get("musteri")
    urun = args.get("urun")
    miktar = args.get("miktar") or 1
    sure_bazli = bool(args.get("sure_bazli"))
    kdv_dahil = bool(args.get("kdv_dahil"))
    tevkifatli = bool(args.get("tevkifatli"))
    tevkifat_amaci = args.get("tevkifat_amaci")

    if not musteri_girilen or not urun:
        return "Musteri ve urun bilgisi gerekli."

    coz = _musteri_coz(musteri_girilen)
    tur = coz["tur"]
    musteri_cozulmus = coz["kayit"]
    urun_cozulmus = _urun_coz(urun)

    if tur != "Customer":
        return ("Satis faturasi yalnizca kayitli musterilere (Customer) "
                "kesilebilir; Lead'e fatura kesilmez, once teklif/siparis "
                "surecinden gecmesi gerekir.")
    if not frappe.db.exists("Item", urun_cozulmus):
        return f"'{urun}' adinda kayitli bir urun bulunamadi."

    try:
        fiyat_ham = _tool_fiyat_onerisi({
            "musteri": musteri_girilen, "urun": urun_cozulmus,
            "miktar": miktar, "sure_bazli": sure_bazli,
        })
        fiyat_data = json.loads(fiyat_ham) if isinstance(fiyat_ham, str) and fiyat_ham.startswith("{") else {}
    except Exception:
        fiyat_data = {}

    birim_fiyat = fiyat_data.get("onerilen_fiyat")
    toplam_fiyat = fiyat_data.get("toplam_nihai")

    if not birim_fiyat:
        return json.dumps({
            "_action": "fiyat_bulunamadi",
            "not": "Bu urun icin ne gecmis fiyat ne standart fiyat tanimli. "
                   "Birim fiyati siz belirtmelisiniz.",
        }, ensure_ascii=False, default=str)

    if sure_bazli:
        bugun = frappe.utils.getdate()
        bitis = frappe.utils.add_months(bugun, miktar)
        urun_adi = frappe.db.get_value("Item", urun_cozulmus, "item_name") or urun_cozulmus
        satir = {
            "item_code": urun_cozulmus,
            "qty": 1,
            "rate": toplam_fiyat,
            "aciklama": f"{urun_adi} — {miktar} Ay",
            "hizmet_baslangic": bugun.isoformat(),
            "hizmet_bitis": bitis.isoformat(),
        }
    else:
        satir = {"item_code": urun_cozulmus, "qty": miktar, "rate": birim_fiyat}

    alanlar = {
        "customer": musteri_cozulmus,
        "items": [satir],
    }

    # --- KDV ---
    if kdv_dahil:
        sablon = _turkey_tax_sablonu()
        if sablon:
            alanlar["taxes_and_charges"] = sablon

    # --- Tevkifat ---
    tevkifat_bilgi = None
    if tevkifatli and not kdv_dahil:
        tevkifatli = False  # KDV yoksa tevkifat da olamaz -- kod seviyesinde garanti

    if tevkifatli:
        bulunan = _tevkifat_kodu_bul(tevkifat_amaci)
        if bulunan:
            # SADECE kodu yaziyoruz. Tevkifat TUTARI'ni ve vergi tablosundaki
            # eksi satiri, sistemde zaten kurulu olan "Fatura Tevkifat
            # Hesaplama" Client Script'i (kod alani degisince tetiklenir)
            # kendisi hesaplayip ekliyor. Biz de yazsaydik cift hesap ve
            # cakisma olurdu; ayrica eksi vergi satirini biz eklemiyorduk.
            alanlar["custom_tevkifat_kodu"] = bulunan["kod"]
            # bilgi amacli tahmini tutar (AI aciklamasi icin, forma yazilmaz).
            # KDV orani ARTIK SABIT DEGIL -- gercek sablondan okunur (onceden
            # yanlislikla %18 sabitlenmisti; 2023'ten beri standart %20).
            sablon_adi = _turkey_tax_sablonu()
            kdv_orani = _kdv_orani_al(sablon_adi)
            if kdv_orani is not None:
                kdv_tutari = round(toplam_fiyat * kdv_orani / 100.0, 2)
                tahmini_tevkifat = round(kdv_tutari * bulunan["oran"] / 100.0, 2)
                tevkifat_bilgi = {
                    "kod": bulunan["kod"],
                    "aciklama": bulunan["aciklama"],
                    "oran": bulunan["oran"],
                    "kdv_orani_kullanilan": kdv_orani,
                    "tahmini_tevkifat_tutari": tahmini_tevkifat,
                    "not": "Tutar ve vergi satiri formda otomatik hesaplanir.",
                }
            else:
                tevkifat_bilgi = {
                    "kod": bulunan["kod"],
                    "aciklama": bulunan["aciklama"],
                    "oran": bulunan["oran"],
                    "not": "KDV orani sablondan okunamadi, tahmini tutar "
                           "hesaplanamadi. Formdaki gercek tutari kontrol edin.",
                }
        else:
            tevkifat_bilgi = {
                "not": f"'{tevkifat_amaci}' icin uygun bir tevkifat kodu bulunamadi. "
                       "Lutfen kodu kendiniz secin.",
            }

    return json.dumps({
        "_action": "form_taslak",
        "doctype": "Sales Invoice",
        "alanlar": alanlar,
        "ozet": {
            "musteri": musteri_cozulmus,
            "urun": urun_cozulmus,
            "sure_bazli": sure_bazli,
            "miktar": miktar,
            "birim_fiyat": birim_fiyat,
            "toplam_fiyat": toplam_fiyat,
            "kdv_dahil": kdv_dahil,
            "tevkifatli": tevkifatli,
            "tevkifat_bilgi": tevkifat_bilgi,
            "fiyat_kaynagi": _fiyat_ozet_sadelestir(fiyat_data),
        },
    }, ensure_ascii=False, default=str)


def _tool_teklif_taslagi(args):
    """
    Musteri (VEYA potansiyel musteri/Lead) + urun + miktar icin DOGRU YAPIDA
    bir Quotation taslagi hazirlar.
    - Fiyati _tool_fiyat_onerisi'ye (miktar dahil) devreder -- taban fiyat +
      sadakat indirimi + buyuk siparis indirimi zinciri orada hesaplanir.
    - Quotation'in GERCEK alanlariyla doner: quotation_to (Customer/Lead),
      party_name, items[].
    SALT-OKUNUR: hicbir kayit olusturmaz/kaydetmez.
    """
    musteri_girilen = args.get("musteri")
    urun = args.get("urun")
    miktar = args.get("miktar") or 1
    sure_bazli = bool(args.get("sure_bazli"))

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

    # Orijinal (kullanicinin yazdigi) musteri metnini yolluyoruz; fiyat_onerisi
    # kendi icinde tekrar cozer -- Lead'in sistem kimligini degil, yazilan
    # ismi kullanmak gerekiyor (aksi halde Lead ismi ikinci kez cozulemez).
    try:
        fiyat_ham = _tool_fiyat_onerisi({
            "musteri": musteri_girilen,
            "urun": urun_cozulmus,
            "miktar": miktar,
            "sure_bazli": sure_bazli,
        })
        fiyat_data = json.loads(fiyat_ham) if isinstance(fiyat_ham, str) and fiyat_ham.startswith("{") else {}
    except Exception:
        fiyat_data = {}

    birim_fiyat = fiyat_data.get("onerilen_fiyat")
    toplam_fiyat = fiyat_data.get("toplam_nihai")

    if not birim_fiyat:
        return json.dumps({
            "_action": "fiyat_bulunamadi",
            "not": "Bu urun icin ne gecmis fiyat ne standart fiyat tanimli. "
                   "Birim fiyati siz belirtmelisiniz.",
        }, ensure_ascii=False, default=str)

    if sure_bazli:
        # SURE BAZLI: adet HER ZAMAN 1; sure GERCEK TARIH ALANLARINA yazilir
        # (custom_hizmet_baslangic / custom_hizmet_bitis), fiyat TOPLAM'dir.
        # Urun kodu (SRV-/PRM-) ONEMLI DEGIL -- karar kullanicinin sozune gore verildi.
        bugun = frappe.utils.getdate()
        bitis = frappe.utils.add_months(bugun, miktar)
        urun_adi = frappe.db.get_value("Item", urun_cozulmus, "item_name") or urun_cozulmus
        aciklama = f"{urun_adi} — {miktar} Ay"
        satir = {
            "item_code": urun_cozulmus,
            "qty": 1,
            "rate": toplam_fiyat,
            "aciklama": aciklama,
            "hizmet_baslangic": bugun.isoformat(),
            "hizmet_bitis": bitis.isoformat(),
        }
    else:
        # ADET/LISANS BAZLI: direkt carpim, sure/tarih yok.
        satir = {"item_code": urun_cozulmus, "qty": miktar, "rate": birim_fiyat}

    return json.dumps({
        "_action": "form_taslak",
        "doctype": "Quotation",
        "alanlar": {
            "quotation_to": tur,  # "Customer" veya "Lead"
            "party_name": musteri_cozulmus,
            "items": [satir],
        },
        "ozet": {
            "musteri": musteri_cozulmus,
            "musteri_turu": tur,
            "urun": urun_cozulmus,
            "sure_bazli": sure_bazli,
            "miktar": miktar,
            "birim_fiyat": birim_fiyat,
            "toplam_fiyat": toplam_fiyat,
            "fiyat_kaynagi": _fiyat_ozet_sadelestir(fiyat_data),
        },
    }, ensure_ascii=False, default=str)


TOOL_FNS = {
    "bugun": _tool_bugun,
    "toplam": _tool_toplam,
    "say": _tool_say,
    "liste": _tool_liste,
    "alanlari_getir": _tool_alanlari_getir,
    "form_doldur": _tool_form_doldur,
    "fiyat_onerisi": _tool_fiyat_onerisi,
    "teklif_taslagi": _tool_teklif_taslagi,
    "fatura_taslagi": _tool_fatura_taslagi,
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
            "Bir alanin toplamini ve adedini dondurur. Orn: KDV icin "
            "alan='total_taxes_and_charges', filtreler posting_date+docstatus:1."
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
            "Kayitlari listeler. 'siralama' ile en cok/yuksek sorulari. "
            "AZ alan iste."
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
            "SADECE fiyat sorulduysa (teklif/fatura taslagi istenmiyorsa) "
            "kullan. Gecmis alim + enflasyon/kur veya standart fiyat, "
            "sektor ayari ve sadakat indirimiyle fiyat hesaplar."
        ),
        "parameters": {"type": "object", "properties": {
            "musteri": {"type": "string", "description": "Musteri adi"},
            "urun": {"type": "string", "description": "Urun adi veya kodu, orn: Takim veya 1234"},
            "miktar": {"type": "integer", "description": (
                "Siparis miktari (varsayilan 1). Buyuk siparis indirimi bu "
                "miktara gore hesaplanir, o yuzden bilinen miktari MUTLAKA gonder."
            )},
        }, "required": ["musteri", "urun"]},
    }},
    {"type": "function", "function": {
        "name": "teklif_taslagi",
        "description": (
            "Quotation (teklif) taslagi hazirlar. Musteri/Lead ve urun "
            "isimlerini otomatik cozer, fiyati hesaplar. Teklif isteklerinde "
            "form_doldur YERINE bunu kullan. KDV/tevkifat SORMA, bunu cagirmak "
            "icin hicbir on-soru gerekmez, direkt cagir."
        ),
        "parameters": {"type": "object", "properties": {
            "musteri": {"type": "string"},
            "urun": {"type": "string"},
            "miktar": {"type": "integer", "description": (
                "Kullanicinin belirttigi sayidir. 'sure_bazli'=true ise bu AY "
                "SAYISIDIR ('6 aylik' -> 6, '1 yillik' -> 12). 'sure_bazli'=false "
                "ise bu ADET/LISANS sayisidir ('50 lisans' -> 50)."
            )},
            "sure_bazli": {"type": "boolean", "description": (
                "'ay/yil' gecerse true (yillik fiyattan oranli, adet=1). "
                "'adet/lisans' gecerse false (birim x adet). Urun koduna DEGIL, "
                "kullanicinin sozune bak."
            )},
        }, "required": ["musteri", "urun", "sure_bazli"]},
    }},
    {"type": "function", "function": {
        "name": "fatura_taslagi",
        "description": (
            "Sales Invoice (fatura) taslagi hazirlar. 'fatura kes' "
            "isteklerinde kullan. Cagirmadan ONCE KDV/tevkifat sor."
        ),
        "parameters": {"type": "object", "properties": {
            "musteri": {"type": "string"},
            "urun": {"type": "string"},
            "miktar": {"type": "integer"},
            "sure_bazli": {"type": "boolean", "description": (
                "true: 'ay/yil' ifadesi -> miktar ay sayisi, yillik fiyattan "
                "oranli hesap. false: 'adet/lisans' ifadesi -> direkt carpim."
            )},
            "kdv_dahil": {"type": "boolean", "description": (
                "Kullanici KDV ekleme sorusuna 'evet' dediyse true, "
                "'hayir' dediyse false."
            )},
            "tevkifatli": {"type": "boolean", "description": (
                "Kullanici tevkifat sorusuna 'evet' dediyse true, "
                "'hayir' dediyse false. KDV=false ise bu SORULMAZ, "
                "otomatik false gonderilir (KDV yoksa tevkifat olmaz)."
            )},
            "tevkifat_amaci": {"type": "string", "description": (
                "Tevkifatli ise: kullanicinin 'ne icin' cevabi (orn: 'reklam "
                "hizmeti'). Kod otomatik bulunur."
            )},
        }, "required": ["musteri", "urun", "sure_bazli", "kdv_dahil", "tevkifatli"]},
    }},
    {"type": "function", "function": {
        "name": "form_doldur",
        "description": (
            "Basit form taslagi (Job Opening gibi, satir tablosu olmayan). "
            "Teklif/fatura icin BUNU KULLANMA."
        ),
        "parameters": {"type": "object", "properties": {
            "doctype": {"type": "string", "description": "orn: Sales Invoice, Quotation"},
            "alanlar": {"type": "object", "description": "orn: {'customer': 'Ahmet Yilmaz', 'grand_total': 5000}"},
        }, "required": ["doctype", "alanlar"]},
    }},
]


def _system_prompt(context=None):
    ctx = ""
    if context:
        dt = context.get("doctype")
        docname = context.get("docname")
        parts = []
        if dt:
            parts.append(f"Ekran: {dt}.")
        if docname:
            parts.append(f"Kayit: {docname}.")
        if parts:
            ctx = "\nBAGLAM: " + " ".join(parts)

    return (
        "ERPNext asistanisin. Araclarla veri cekip Turkce cevap verirsin.\n"
        "TEMEL: Tarih gerekirse once 'bugun' cagir. Kesinlesmis belge = "
        "docstatus:1. Tutarlar TL formatinda (45.200,00 TL). Veri UYDURMA. "
        "JSON/ham cikti GOSTERME, dogal cumle kur. Listelerde az alan iste.\n"
        "\n"
        "TEKLIF/FATURA:\n"
        "- 'teklif' -> teklif_taslagi. 'fatura' -> fatura_taslagi.\n"
        "- TEKLIF'te (teklif_taslagi) KDV/tevkifat HICBIR ZAMAN SORULMAZ, "
        "bu konu teklifle ilgisizdir -- direkt arac cagrilir.\n"
        "- SADECE fatura_taslagi'ndan ONCE sor: KDV dahil mi? Tevkifat var mi? "
        "Tevkifat evetse: ne icin? (cevabi tevkifat_amaci'na yaz, kodu arac bulur). "
        "Cevaplar onceki mesajlardaysa TEKRAR SORMA.\n"
        "- KDV'ye 'hayir' denirse tevkifat SORULMAZ (tevkifat KDV'nin bir "
        "kismini kesmek demektir, KDV yoksa tevkifat da olamaz). Direkt "
        "kdv_dahil=false, tevkifatli=false ile fatura_taslagi'ni cagir.\n"
        "- 'zarar_riski': true donerse (fiyat maliyetin altinda) COK ONEMLI: "
        "taslak sunmadan ONCE YUKSEK SESLE uyar -- 'DIKKAT: Bu fiyat (X TL) "
        "urunun maliyetinin (Y TL) altinda, zararina satis riski var!' de ve "
        "'Yine de devam edeyim mi?' diye SOR. Onay gelmeden taslak acma.\n"
        "- sure_bazli karari URUN KODUNA DEGIL kullanicinin sozune bakar: "
        "'ay/yil' gecerse true (miktar=ay sayisi). 'adet/lisans' gecerse false "
        "(birim x adet). Belirsizse sor. Yillik/aylik oranlama Item'in KENDI "
        "'Fiyat Periyodu' alanindan okunur (varsayilan Yillik) -- kod tahmin "
        "etmez, veriye bakar.\n"
        "- Sayiyi ('6 aylik', '50 lisans') MUTLAKA miktar'a koy. Yil -> ay (1 yil=12).\n"
        "- Aciklarken belirt: gecmis fiyat + enflasyon/kur farki, sektor ayari "
        "(sektor_gerekce'yi temel al ama her seferinde FARKLI kelimelerle anlat), "
        "sadakat indirimi, nihai tutar. Veri yoksa durustce soyle.\n"
        "- Lead'de gecmis/sadakat olmaz, standart fiyat kullanilir.\n"
        "- Sektor NEGATIF (indirim) ise sadakat indirimi HIC UYGULANMAZ, "
        "cirosu yuksek olsa bile 0 cikar -- bu bir hata degil, kural boyle "
        "(iki indirim ust uste binmesin diye). Sorulursa boyle acikla.\n"
        "\n"
        "IK METINLERI:\n"
        "- Is tanimi: Pozisyon Ozeti, Temel Sorumluluklar (5-7), Aranan "
        "Nitelikler (4-6), Tercih Sebebi. SMART hedef: 3-5 olculebilir hedef, "
        "her biri sayi+sure icersin. Bunlarda detayli yaz. Sonunda 'taslaktir' de. "
        "Gercek calisan/maas bilgisi ISTEME, sadece pozisyon adiyla calis.\n"
        "- Kaydetmek isterse form_doldur ile Job Opening taslagi ac.\n"
        "\n"
        "UYARI ALANI: Bir arac sonucunda 'uyari' alani varsa (kirpilmis veri) "
        "MUTLAKA kullaniciya aktar, gizleme.\n"
        "\n"
        "DOGRULUK (KRITIK):\n"
        "- Sadece GERCEKTEN cagirdigin araclarin sonucuna dayan.\n"
        "- Ilgili araci CAGIRMADIYSAN 'taslak hazirladim/olusturdum' DEME.\n"
        "- Yeterli bilgi varsa sadece anlatma, araci DA cagir. Eksikse sor.\n"
        "- Erisim reddedilirse kibarca soyle. Kisa ve net cevap ver."
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
    "bu bir taslaktır", "bu bir taslaktir",
    "taslaktır, gözden", "taslaktir, gozden",
    "taslaktır, kaydedebilir", "taslaktir, kaydedebilir",
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
    sonuc = last or {"error": {"message": "Bilinmeyen hata"}}
    # Kullanici sohbette bir hata mesaji goruyor ama bu ADMIN icin de
    # gorunur olmali -- onceden sadece kullaniciya gidiyordu, log yoktu.
    try:
        frappe.log_error(
            title="Groq API hatasi (tum denemeler basarisiz)",
            message=str(sonuc.get("error"))[:500],
        )
    except Exception:
        pass
    return sonuc


@frappe.whitelist()
def ask(question, context=None, gecmis=None):
    """
    gecmis: onceki mesajlarin JSON listesi ([{"role":"user"/"assistant",
    "content":"..."}]) -- cok adimli sohbetlerde (orn. 'KDV dahil mi?' diye
    sorup cevap beklemek) onceki baglamin hatirlanmasi icin gerekli.
    """
    if isinstance(context, str):
        try:
            context = json.loads(context)
        except Exception:
            context = None

    messages = [
        {"role": "system", "content": _system_prompt(context)},
    ]

    if gecmis:
        try:
            onceki = json.loads(gecmis) if isinstance(gecmis, str) else gecmis
            if isinstance(onceki, list):
                for m in onceki[-6:]:  # son 6 mesaj yeterli (token tasarrufu)
                    if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content"):
                        messages.append({"role": m["role"], "content": str(m["content"])[:800]})
        except Exception:
            pass

    messages.append({"role": "user", "content": question})

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
            if name in ("form_doldur", "teklif_taslagi", "fatura_taslagi"):
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


def _fiyat_ozet_sadelestir(fiyat_data):
    """
    fiyat_onerisi'nin TAM ciktisi cok kalabalik (tekrarli alanlar icerir)
    ve Groq'a geri gonderilince token siniri asilmasina yol aciyordu.
    Bu fonksiyon sadece AI'in aciklama yapmak icin ihtiyaci olan alanlari
    (tekrarsiz) dondurur -- token kullanimini ciddi dusurur.
    """
    if not fiyat_data or not isinstance(fiyat_data, dict):
        return {}

    sade = {
        "gecmis_var": fiyat_data.get("gecmis_var"),
        "musteri_turu": fiyat_data.get("musteri_turu"),
        "sektor_grubu": fiyat_data.get("sektor_grubu"),
        "sektor_ayari_yuzde": fiyat_data.get("sektor_ayari_yuzde"),
        "sadakat_indirim_yuzde": fiyat_data.get("sadakat_indirim_yuzde"),
        "kullanilan_faktor": fiyat_data.get("kullanilan_faktor"),
        "toplam_nihai": fiyat_data.get("toplam_nihai"),
        "zarar_riski": fiyat_data.get("zarar_riski"),
        "onay_gerekli": fiyat_data.get("onay_gerekli"),
    }
    if fiyat_data.get("onay_gerekli"):
        sade["toplam_indirim_yuzde"] = fiyat_data.get("toplam_indirim_yuzde")
    if fiyat_data.get("zarar_riski"):
        sade["maliyet"] = fiyat_data.get("maliyet")
    if fiyat_data.get("gecmis_var"):
        sade["eski_fiyat"] = fiyat_data.get("eski_fiyat")
        sade["eski_tarih"] = fiyat_data.get("eski_tarih")
        if fiyat_data.get("enflasyon_yuzde") is not None:
            sade["enflasyon_yuzde"] = fiyat_data.get("enflasyon_yuzde")
        if fiyat_data.get("kur_yuzde") is not None:
            sade["kur_yuzde"] = fiyat_data.get("kur_yuzde")
    else:
        sade["standart_fiyat"] = fiyat_data.get("standart_fiyat")

    # None olan alanlari at, daha da kucultsun
    return {k: v for k, v in sade.items() if v is not None}


def _yenileme_gerekce_metni(fiyat_data):
    """
    Fiyat hesaplama zincirini (taban fiyat + sektor + sadakat) okunakli,
    maddeler halinde bir aciklamaya cevirir. Groq/AI KULLANMAZ -- sadece
    zaten hesaplanmis sayilardan cumle kurar, bu yuzden hizlidir.
    """
    if not fiyat_data:
        return ""

    satirlar = []

    if fiyat_data.get("gecmis_var"):
        eski_fiyat = fiyat_data.get("eski_fiyat")
        eski_tarih = fiyat_data.get("eski_tarih")
        satirlar.append(f"Gecmis alim: {eski_tarih} tarihinde {eski_fiyat:,.0f} TL.".replace(",", "."))
        enf = fiyat_data.get("enflasyon_yuzde")
        kur = fiyat_data.get("kur_yuzde")
        if enf is not None or kur is not None:
            parcalar = []
            if enf is not None:
                parcalar.append(f"enflasyon %{enf:.1f}")
            if kur is not None:
                parcalar.append(f"dolar kuru %{kur:.1f}")
            satirlar.append("Bu tarihten bugune " + " ve ".join(parcalar) + " degisti.")
        guncel_standart = fiyat_data.get("guncel_standart_fiyat")
        kullanilan = fiyat_data.get("kullanilan_faktor")
        if kullanilan == "standart_fiyat_guncellemesi" and guncel_standart:
            satirlar.append(
                f"Guncel standart fiyat ({guncel_standart:,.0f} TL) enflasyon/kur "
                "tahmininden yuksek oldugu icin o esas alindi.".replace(",", ".")
            )
    else:
        standart = fiyat_data.get("standart_fiyat")
        if standart:
            satirlar.append(f"Gecmis alim yok, standart fiyat ({standart:,.0f} TL/yil) esas alindi.".replace(",", "."))

    sektor_yuzde = fiyat_data.get("sektor_ayari_yuzde")
    sektor_grubu = fiyat_data.get("sektor_grubu")
    if sektor_yuzde:
        yon = "prim" if sektor_yuzde > 0 else "indirim"
        satirlar.append(f"{sektor_grubu} sektorunde oldugu icin %{abs(sektor_yuzde):.0f} {yon} uygulandi.")

    sadakat_yuzde = fiyat_data.get("sadakat_indirim_yuzde")
    ciro = fiyat_data.get("sadakat_cirosu_12ay")
    if sadakat_yuzde:
        satirlar.append(
            f"Son 12 ayda {ciro:,.0f} TL ciro yapildigi icin ".replace(",", ".") +
            f"%{sadakat_yuzde:.0f} sadakat indirimi uygulandi."
        )
    elif ciro is not None and ciro > 0:
        satirlar.append(f"Son 12 ay ciro ({ciro:,.0f} TL) sadakat indirimi esigine ulasmadi.".replace(",", "."))

    toplam = fiyat_data.get("toplam_nihai")
    if toplam:
        satirlar.append(f"Nihai toplam: {toplam:,.0f} TL.".replace(",", "."))

    return " ".join(satirlar)


@frappe.whitelist()
def sozlesme_kontrolu(sadece_acil=None, hafif=None):
    """
    Bitis tarihi yaklasan (60 gun icindeki) sozlesmeleri bulur, kademeli
    aciliyet (kritik <=7 gun, yakin <=30 gun, planla <=60 gun) belirler,
    AI'dan kisa uyari metni uretir. ERPNext'in hazir 'Contract' DocType'ini
    kullanir -- yeni bir yapi eklemez, sadece mevcut sozlesme kayitlarini okur.

    ONEMLI -- TAM SALT-OKUNUR DEGIL: Musteri/hizmet/fatura/sozlesme
    verilerine dokunmaz, ama sistem bildirimi olarak 'Notification Log'a
    (sadece 'sadece_acil' cagrisinda, tek kayit, tekrar eklemez) yazar.
    Bu yazma ignore_permissions=True ile yapilir -- gorunur, zararsiz bir
    sistem bildirimi oldugu icin bilincli tercih, ama "hicbir kayit
    olusturmaz" iddiasi eksikti; iste tam durum budur.
    Donus: {"var": bool, "mesaj": str, "sozlesmeler": [...]}
    """
    try:
        if not frappe.db.exists("DocType", "Contract"):
            return {"var": False, "mesaj": "", "sozlesmeler": []}
    except Exception:
        return {"var": False, "mesaj": "", "sozlesmeler": []}

    bugun = date.today()
    ust_sinir = (bugun + timedelta(days=60)).isoformat()

    try:
        meta = frappe.get_meta("Contract")
        alan_adlari = {df.fieldname for df in meta.fields}
    except Exception:
        return {"var": False, "mesaj": "", "sozlesmeler": []}

    if "end_date" not in alan_adlari:
        return {"var": False, "mesaj": "", "sozlesmeler": []}

    secilecek_alanlar = ["name", "end_date"]
    if "party_name" in alan_adlari:
        secilecek_alanlar.append("party_name")
    if "party_type" in alan_adlari:
        secilecek_alanlar.append("party_type")
    if "custom_ilgili_urun" in alan_adlari:
        secilecek_alanlar.append("custom_ilgili_urun")

    filtreler = {"end_date": ["between", [bugun.isoformat(), ust_sinir]]}
    if "status" in alan_adlari:
        filtreler["status"] = ["not in", ["Inactive", "Cancelled"]]

    try:
        sozlesmeler_ham = frappe.get_all(
            "Contract", filters=filtreler, fields=secilecek_alanlar,
            order_by="end_date asc", limit_page_length=50,
        )
    except Exception:
        try:
            sozlesmeler_ham = frappe.get_all(
                "Contract",
                filters={"end_date": ["between", [bugun.isoformat(), ust_sinir]]},
                fields=secilecek_alanlar, order_by="end_date asc", limit_page_length=50,
            )
        except Exception:
            return {"var": False, "mesaj": "", "sozlesmeler": []}

    if not sozlesmeler_ham:
        return {"var": False, "mesaj": "", "sozlesmeler": []}

    liste = []
    for s in sozlesmeler_ham:
        try:
            bitis = frappe.utils.getdate(s.get("end_date"))
        except Exception:
            continue
        kalan_gun = (bitis - bugun).days
        if kalan_gun <= 7:
            durum = "kritik"
        elif kalan_gun <= 30:
            durum = "yakin"
        else:
            durum = "planla"
        madde = {
            "sozlesme": s.get("name"),
            "taraf": s.get("party_name") or s.get("name"),
            "bitis_tarihi": str(bitis),
            "kalan_gun": kalan_gun,
            "durum": durum,
        }

        # Urun/hizmet biliniyorsa, YENILEME TEKLIFINI de hesapla
        urun_kodu = s.get("custom_ilgili_urun")
        taraf_turu = s.get("party_type")
        if urun_kodu and madde["taraf"] and taraf_turu in ("Customer", None, ""):
            try:
                ham = _tool_fiyat_onerisi({
                    "musteri": madde["taraf"], "urun": urun_kodu,
                    "miktar": 12, "sure_bazli": True,  # varsayilan: 1 yillik yenileme onerisi
                })
                fiyat_data = json.loads(ham) if isinstance(ham, str) and ham.startswith("{") else {}
                madde["yenileme_urun"] = urun_kodu
                madde["yenileme_urun_adi"] = frappe.db.get_value("Item", urun_kodu, "item_name") or urun_kodu
                madde["yenileme_fiyat"] = fiyat_data.get("toplam_nihai")
                madde["yenileme_ay"] = 12
                madde["yenileme_gerekce"] = _yenileme_gerekce_metni(fiyat_data)
            except Exception:
                pass

        liste.append(madde)

    # sadece_acil verilmisse "planla" (60 gune kadar, henuz acil olmayan)
    # kategorisini cikar -- bu, pop-up'ta sadece kritik+yakin gostermek icin.
    # Kalici "Teklif Uyarilari" sayfasi bu parametreyi VERMEZ, hepsini gorur.
    if sadece_acil:
        liste = [l for l in liste if l["durum"] in ("kritik", "yakin")]

    if not liste:
        return {"var": False, "mesaj": "", "sozlesmeler": []}

    ozet_satirlari = []
    for l in liste:
        satir = f"{l['taraf']}: {l['kalan_gun']} gun sonra ({l['bitis_tarihi']}) bitiyor"
        if l.get("yenileme_fiyat"):
            satir += (f" — yenileme onerisi: {l['yenileme_urun_adi']}, "
                      f"1 yillik {l['yenileme_fiyat']} TL")
        ozet_satirlari.append(satir)
    veri = "\n".join(ozet_satirlari)
    mesaj = None
    if not hafif:  # 'hafif' (kalici sayfa) icin Groq'a hic gidilmez, hizli olsun
        try:
            messages = [
                {"role": "system", "content": (
                    "Sen bir sozlesme/lisans takip asistanisin. Bitis tarihi "
                    "yaklasan sozlesmeler icin kisa, net bir Turkce uyari metni "
                    "yaz. MUTLAKA taraf adini belirt. Yenileme onerisi verilmisse "
                    "(urun + fiyat), bunu da 'en uygun teklif' olarak belirt. "
                    "En yakin bitecekten baslayarak anlat. 2-4 cumleyi gecme. "
                    "JSON veya teknik detay yazma, sadece dogal uyari metni."
                )},
                {"role": "user", "content": f"Yaklasan sozlesmeler:\n{veri}"},
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

    if not mesaj:
        adlar = ", ".join(f"{l['taraf']} ({l['kalan_gun']} gun)" for l in liste)
        mesaj = f"Yaklasan sozlesme yenilemeleri: {adlar}."

    # Cana kayit SADECE pop-up tetiklendiginde (sadece_acil=True) dusurulur.
    # Kalici sayfa (sadece_acil verilmeden cagrilir) cana ayrica yazmaz,
    # yoksa her ziyarette farkli basliklarla tekrarlanan kayit olusurdu.
    if sadece_acil:
        try:
            kullanici = frappe.session.user
            ilk_isimler = ", ".join(l["taraf"] for l in liste[:3])
            subject = f"Sozlesme Uyarisi: {ilk_isimler} — yenileme yaklasiyor"
            mevcut = frappe.get_all(
                "Notification Log",
                filters={"for_user": kullanici, "subject": subject, "read": 0},
                limit_page_length=1,
            )
            if not mevcut:
                log = frappe.new_doc("Notification Log")
                log.subject = subject
                log.email_content = mesaj
                log.for_user = kullanici
                log.type = "Alert"
                try:
                    meta2 = frappe.get_meta("Notification Log")
                    if meta2.get_field("link"):
                        log.link = "/app/contract"
                    else:
                        log.document_type = "Contract"
                except Exception:
                    pass
                log.insert(ignore_permissions=True)
                frappe.db.commit()
        except Exception:
            pass

    return {"var": True, "mesaj": mesaj, "sozlesmeler": liste}


@frappe.whitelist()
def hizmet_fiyat_hesapla(musteri, urun, ay_sayisi):
    """
    Manuel olarak fatura/teklif ekranindan cagirilir (AI/Groq'suz, anlik).
    Musteri + hizmet urunu + ay sayisi verilince, tum fiyat mantigini
    (yillik standart/gecmis fiyat + sektor + sadakat + oranli hesap)
    calistirip TOPLAM tutari dondurur. Kullanicinin tarih girince satirin
    fiyatinin otomatik guncellenmesini saglar.
    SALT-OKUNUR: hicbir kayit olusturmaz/degistirmez.
    """
    try:
        ay_sayisi = int(float(ay_sayisi))
    except Exception:
        ay_sayisi = 1
    ay_sayisi = max(ay_sayisi, 1)

    try:
        # tarih girisiyle cagrildigi icin HER ZAMAN sure bazli (yillik/12*ay) hesapla
        ham = _tool_fiyat_onerisi({
            "musteri": musteri, "urun": urun, "miktar": ay_sayisi, "sure_bazli": True,
        })
        veri = json.loads(ham) if isinstance(ham, str) and ham.startswith("{") else {}
    except Exception:
        veri = {}

    return {
        "toplam": veri.get("toplam_nihai"),
        "detay": veri,
    }


@frappe.whitelist()
def durum():
    return {
        "hazir": bool(_groq_key()),
        "model": _groq_model(),
        "izinli_doctype_sayisi": len(ALLOWED_DOCTYPES),
    }
