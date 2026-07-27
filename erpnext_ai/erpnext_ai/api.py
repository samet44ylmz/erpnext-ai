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
from datetime import date

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



TOOL_FNS = {
    "bugun": _tool_bugun,
    "toplam": _tool_toplam,
    "say": _tool_say,
    "liste": _tool_liste,
    "alanlari_getir": _tool_alanlari_getir,
    "form_doldur": _tool_form_doldur,
    "stok_durumu": _tool_stok_durumu,
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
        "name": "form_doldur",
        "description": (
            "Yeni kayit icin FORM TASLAGI hazirlar (kaydetmez!). "
            "Kullanici 'fatura kes', 'teklif hazirla' derse kullan. "
            "Kullanicinin verdigi TUM bilgileri (musteri, tutar, urun, miktar) "
            "'alanlar' sozlugune MUTLAKA yerlestir. Bos birakma."
        ),
        "parameters": {"type": "object", "properties": {
            "doctype": {"type": "string", "description": "orn: Sales Invoice, Quotation"},
            "alanlar": {"type": "object", "description": "orn: {'customer': 'Ahmet Yilmaz', 'grand_total': 5000}"},
        }, "required": ["doctype", "alanlar"]},
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
        "- Stok/siparis sorularinda 'stok_durumu' aracini kullan. Eshik altindaki "
        "urunler icin onerilen siparis miktarini ve tedarikciyi belirt. "
        "Siparisi SEN VERMEZSIN, sadece onerirsin. Normal stoktakileri kisaca gec, "
        "eshik altindakilere odaklan.\n"
        "- Erisim reddedilirse kibarca bu veriye erisimin olmadigini soyle.\n"
        "- Kisa, net ve Turkce cevap ver."
        + ctx
    )


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
            return {
                "cevap": (msg.get("content") or "").strip() or "(cevap uretilemedi)",
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

            if name == "form_doldur":
                try:
                    parsed = json.loads(result)
                    if parsed.get("_action") == "form_taslak":
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
    # 1) Esik alti urunleri bul (stok_durumu mantigi)
    ham = _tool_stok_durumu({})
    try:
        urunler = json.loads(ham)
    except Exception:
        return {"var": False, "mesaj": "", "urun_sayisi": 0}

    if not isinstance(urunler, list):
        return {"var": False, "mesaj": "", "urun_sayisi": 0}

    esik_alti = [u for u in urunler if u.get("durum") == "esik_altinda"]
    if not esik_alti:
        return {"var": False, "mesaj": "", "urun_sayisi": 0}

    # 2) AI'dan oneri metni uret
    ozet = []
    for u in esik_alti:
        ozet.append(
            f"{u.get('urun')}: stok {u.get('stok')}, esik {u.get('esik')}, "
            f"onerilen siparis {u.get('onerilen_siparis')}, "
            f"tedarikci {u.get('tedarikci') or 'belirsiz'}"
        )
    veri = "\n".join(ozet)

    mesaj = None
    try:
        messages = [
            {"role": "system", "content": (
                "Sen bir stok asistanisin. Verilen esik alti urunler icin "
                "kisa, net bir Turkce uyari metni yaz. "
                "MUTLAKA urun adini yazarak baslat (orn: 'Takim urununun stogu...'). "
                "Her urun icin: kac adet kaldi, esik kac, kac adet siparis onerilir, "
                "hangi tedarikciden. 2-3 cumleyi gecme. "
                "JSON veya teknik detay yazma, sadece dogal uyari metni."
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
            log.document_type = "Item"
            log.insert(ignore_permissions=True)
            frappe.db.commit()
    except Exception:
        # cana yazma basarisiz olsa bile pop-up calismali
        pass

    return {
        "var": True,
        "mesaj": mesaj,
        "urun_sayisi": len(esik_alti),
    }


@frappe.whitelist()
def durum():
    return {
        "hazir": bool(_groq_key()),
        "model": _groq_model(),
        "izinli_doctype_sayisi": len(ALLOWED_DOCTYPES),
    }
