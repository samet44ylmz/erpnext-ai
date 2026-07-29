/**
 * ERPNext AI — arayuz
 *
 * Sag altta yuzen buton; tiklayinca sohbet paneli acilir.
 * Kullanicinin bulundugu ekrani (doctype) algilar ve backend'e iletir.
 *
 * GUVENLIK: Form doldurma yalnizca TARAYICIDA yapilir.
 * Hicbir kayit kaydedilmez veya submit edilmez.
 */

(function () {
	"use strict";

	const PANEL_ID = "erpnext-ai-panel";
	const BTN_ID = "erpnext-ai-btn";

	// ----------------------------------------------------------
	// Metin guvenligi ve basit bicimlendirme
	// ----------------------------------------------------------
	function kacisla(s) {
		return String(s)
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;");
	}

	/** **kalin**, madde isaretleri ve satir sonlarini HTML'e cevirir. */
	function bicimle(metin) {
		let s = kacisla(metin);
		// **kalin** -> <b>
		s = s.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
		// satir basindaki "- " veya "* " -> madde isareti
		s = s.replace(/^[ \t]*[-*][ \t]+/gm, "&bull;&nbsp;");
		s = s.replace(/\n/g, "<br>");
		return s;
	}

	// ----------------------------------------------------------
	// Baglam: kullanici hangi ekranda?
	// ----------------------------------------------------------
	function mevcut_baglam() {
		const ctx = {};
		try {
			if (window.cur_frm && cur_frm.doctype) {
				ctx.doctype = cur_frm.doctype;
				if (cur_frm.doc && !cur_frm.doc.__islocal) {
					ctx.docname = cur_frm.doc.name;
				}
			} else if (window.cur_list && cur_list.doctype) {
				ctx.doctype = cur_list.doctype;
			}
			ctx.route = (frappe.get_route() || []).join("/");
		} catch (e) {
			// baglam alinamadi, sorun degil
		}
		return ctx;
	}

	// ----------------------------------------------------------
	// Form taslagi: formu ac ve doldur — KAYDETME
	// ----------------------------------------------------------
	function form_taslagi_ac(taslak) {
		if (!taslak || !taslak.doctype) return;

		frappe.new_doc(taslak.doctype).then(() => {
			setTimeout(() => {
				if (!window.cur_frm) return;
				const alanlar = taslak.alanlar || {};
				let dolan = 0;
				const eklenenSatirlar = []; // gec fiyat duzeltmesi icin

				Object.keys(alanlar).forEach((k) => {
					try {
						if (k === "items" && Array.isArray(alanlar[k])) {
							// satir tablosu (urun kalemleri) -- add_child ile eklenir.
							// ERPNext item_code yazilinca kendi fiyatini otomatik
							// getirir; bizim onerdigimiz fiyati asagida GEC ama
							// KESIN olarak tekrar yazacagiz.
							alanlar[k].forEach((satir) => {
								const eklenecek = {
									item_code: satir.item_code,
									qty: satir.qty,
								};
								// ERPNext'in bilmedigi ozel alanlar (tarih vb.)
								// direkt eklenir, sonradan ezilme riski yok.
								if (satir.hizmet_baslangic) {
									eklenecek.custom_hizmet_baslangic = satir.hizmet_baslangic;
								}
								if (satir.hizmet_bitis) {
									eklenecek.custom_hizmet_bitis = satir.hizmet_bitis;
								}
								const row = cur_frm.add_child("items", eklenecek);
								if (satir.rate != null || satir.aciklama != null) {
									eklenenSatirlar.push({
										cdt: row.doctype,
										cdn: row.name,
										rate: satir.rate,
										aciklama: satir.aciklama,
									});
								}
							});
							cur_frm.refresh_field("items");
							dolan++;
						} else if (cur_frm.get_field(k)) {
							cur_frm.set_value(k, alanlar[k]);
							dolan++;
						}
					} catch (e) {
						// bu alan doldurulamadi, digerlerine devam
					}
				});

				// ERPNext'in otomatik fiyat getirme islemi bitsin, sonra
				// bizim onerdigimiz fiyati kesin olarak uygula.
				if (eklenenSatirlar.length) {
					setTimeout(() => {
						eklenenSatirlar.forEach((s) => {
							if (s.rate != null) {
								frappe.model.set_value(s.cdt, s.cdn, "rate", s.rate);
							}
							if (s.aciklama != null) {
								frappe.model.set_value(s.cdt, s.cdn, "description", s.aciklama);
							}
						});
						cur_frm.refresh_field("items");
					}, 900);
				}
				frappe.show_alert(
					{
						message: __(
							"Taslak dolduruldu ({0} alan). Gozden gecirin ve kaydetmek icin <b>Save</b> tusuna basin.",
							[dolan]
						),
						indicator: "orange",
					},
					10
				);
			}, 700);
		});
	}

	// ----------------------------------------------------------
	// Panel
	// ----------------------------------------------------------
	function panel_olustur() {
		if (document.getElementById(PANEL_ID)) return;

		const panel = document.createElement("div");
		panel.id = PANEL_ID;

		const header = document.createElement("div");
		header.className = "eai-header";
		header.innerHTML =
			'<span class="eai-title">ERPNext AI</span>' +
			'<span class="eai-ctx" id="eai-ctx"></span>' +
			'<button class="eai-close" title="Kapat" aria-label="Kapat">&times;</button>';

		const log = document.createElement("div");
		log.className = "eai-log";
		log.id = "eai-log";

		const inputRow = document.createElement("div");
		inputRow.className = "eai-input-row";
		inputRow.innerHTML =
			'<input id="eai-input" type="text" placeholder="Verilerinizle ilgili soru sorun" autocomplete="off">' +
			'<button id="eai-send">Gonder</button>';

		panel.appendChild(header);
		panel.appendChild(log);
		panel.appendChild(inputRow);
		document.body.appendChild(panel);

		// karsilama
		const hos = document.createElement("div");
		hos.className = "eai-msg eai-bot";
		hos.innerHTML =
			"Sorabilecekleriniz:<br>" +
			"&bull;&nbsp;<b>bu ayki toplam satis</b> &mdash; veri sorgulari<br>" +
			"&bull;&nbsp;<b>ne siparis etmeliyim</b> &mdash; stok analizi<br>" +
			"&bull;&nbsp;<b>satis muduru icin is tanimi yaz</b> &mdash; IK metinleri<br>" +
			"&bull;&nbsp;<b>fatura kes</b> &mdash; form taslagi" +
			'<div class="eai-note">Uretilen her sey taslaktir; ' +
			"kaydetmeyi siz yaparsiniz.</div>";
		log.appendChild(hos);

		header.querySelector(".eai-close").onclick = panel_kapat;
		inputRow.querySelector("#eai-send").onclick = gonder;
		inputRow.querySelector("#eai-input").addEventListener("keydown", (e) => {
			if (e.key === "Enter") gonder();
		});
		document.addEventListener("keydown", (e) => {
			if (e.key === "Escape") panel_kapat();
		});
	}

	function panel_ac() {
		panel_olustur();
		const p = document.getElementById(PANEL_ID);
		p.classList.add("eai-open");

		const ctx = mevcut_baglam();
		const el = document.getElementById("eai-ctx");
		if (el) el.textContent = ctx.doctype || "";

		const inp = document.getElementById("eai-input");
		if (inp) inp.focus();
	}

	function panel_kapat() {
		const p = document.getElementById(PANEL_ID);
		if (p) p.classList.remove("eai-open");
	}

	function mesaj_ekle(metin, sinif, html) {
		const log = document.getElementById("eai-log");
		const d = document.createElement("div");
		d.className = "eai-msg " + sinif;
		if (html) d.innerHTML = metin;
		else d.textContent = metin;
		log.appendChild(d);
		log.scrollTop = log.scrollHeight;
		return d;
	}

	// ----------------------------------------------------------
	// Gonderme
	// ----------------------------------------------------------
	// Sohbet gecmisi -- coklu adim gereken sorular (KDV/tevkifat gibi)
	// icin AI'in onceki baglami hatirlamasi gerekir.
	const eai_sohbet_gecmisi = [];

	function gonder() {
		const inp = document.getElementById("eai-input");
		const soru = (inp.value || "").trim();
		if (!soru) return;

		mesaj_ekle(soru, "eai-user");
		inp.value = "";

		const bekle = mesaj_ekle("Veriler getiriliyor", "eai-bot eai-wait");
		const btn = document.getElementById("eai-send");
		btn.disabled = true;

		const gecmis_gonderilecek = eai_sohbet_gecmisi.slice(-12);

		frappe.call({
			method: "erpnext_ai.erpnext_ai.api.ask",
			args: {
				question: soru,
				context: JSON.stringify(mevcut_baglam()),
				gecmis: JSON.stringify(gecmis_gonderilecek),
			},
			callback: function (r) {
				btn.disabled = false;
				bekle.remove();
				const res = (r && r.message) || {};
				const cevap_metni = res.cevap || "Cevap alinamadi.";
				mesaj_ekle(bicimle(cevap_metni), "eai-bot", true);
				if (res.form_taslak) taslak_karti_ekle(res.form_taslak);

				eai_sohbet_gecmisi.push({ role: "user", content: soru });
				eai_sohbet_gecmisi.push({ role: "assistant", content: cevap_metni });
			},
			error: function () {
				btn.disabled = false;
				bekle.remove();
				mesaj_ekle(
					"Baglanti kurulamadi. Lutfen tekrar deneyin.",
					"eai-bot eai-err"
				);
			},
		});
	}

	function taslak_karti_ekle(taslak) {
		const log = document.getElementById("eai-log");
		const box = document.createElement("div");
		box.className = "eai-msg eai-bot eai-draft";
		box.innerHTML =
			'<div class="eai-draft-head">' +
			kacisla(taslak.doctype) +
			" taslagi hazir</div>" +
			'<div class="eai-note">Form acilip alanlar doldurulacak. ' +
			"Kaydetme islemini siz yaparsiniz.</div>";

		const b = document.createElement("button");
		b.className = "eai-draft-btn";
		b.textContent = "Taslagi ac";
		b.onclick = () => {
			panel_kapat();
			form_taslagi_ac(taslak);
		};
		box.appendChild(b);
		log.appendChild(box);
		log.scrollTop = log.scrollHeight;
	}

	// ----------------------------------------------------------
	// Yuzen buton
	// ----------------------------------------------------------
	function buton_olustur() {
		if (document.getElementById(BTN_ID)) return;
		const b = document.createElement("button");
		b.id = BTN_ID;
		b.title = "ERPNext AI";
		b.setAttribute("aria-label", "ERPNext AI asistanini ac");
		b.textContent = "AI";
		b.onclick = () => {
			const p = document.getElementById(PANEL_ID);
			if (p && p.classList.contains("eai-open")) panel_kapat();
			else panel_ac();
		};
		document.body.appendChild(b);
	}

	// ----------------------------------------------------------
	// Kritik stok pop-up'i (her giriste kontrol)
	// ----------------------------------------------------------
	function kritik_stok_kontrol() {
		if (!window.frappe || !frappe.call) return;
		frappe.call({
			method: "erpnext_ai.erpnext_ai.api.kritik_stok_kontrol",
			callback: function (r) {
				const res = (r && r.message) || {};
				if (res.var) popup_goster(res);
			},
			error: function () {},
		});
	}

	// durum -> okunabilir etiket + renk sinifi
	const DURUM_BILGI = {
		kritik:          { etiket: "Kritik",          sinif: "eai-d-kritik" },
		acil:            { etiket: "Acil",            sinif: "eai-d-acil" },
		siparis_zamani:  { etiket: "Siparis zamani",  sinif: "eai-d-siparis" },
		esik_altinda:    { etiket: "Esik altinda",    sinif: "eai-d-acil" },
		esik_alti_satis_yok: { etiket: "Esik altinda", sinif: "eai-d-acil" },
		fazla_stok:      { etiket: "Fazla stok",      sinif: "eai-d-normal" },
		olu_stok:        { etiket: "Hareketsiz",      sinif: "eai-d-normal" },
	};

	function urun_karti(u) {
		const bilgi = DURUM_BILGI[u.durum] || { etiket: u.durum || "", sinif: "eai-d-normal" };

		// olcu satirlari
		const olculer = [];
		olculer.push({ etiket: "Mevcut stok", deger: (u.stok != null ? u.stok + " adet" : "-") });
		if (u.aylik_satis) {
			olculer.push({ etiket: "Aylik satis", deger: "~" + u.aylik_satis + " adet" });
		}
		if (u.kalan_gun != null) {
			olculer.push({ etiket: "Yeterlilik", deger: u.kalan_gun + " gun", vurgu: u.kalan_gun < 15 });
		}


		let html =
			'<div class="eai-urun">' +
			'<div class="eai-urun-ust">' +
			'<span class="eai-urun-ad">' + kacisla(u.urun || "") + "</span>" +
			'<span class="eai-rozet ' + bilgi.sinif + '">' + bilgi.etiket + "</span>" +
			"</div>" +
			'<div class="eai-olcu-satir">';

		olculer.forEach(function (o) {
			html +=
				'<div class="eai-olcu">' +
				'<div class="eai-olcu-etiket">' + o.etiket + "</div>" +
				'<div class="eai-olcu-deger' + (o.vurgu ? " eai-vurgu" : "") + '">' +
				kacisla(String(o.deger)) +
				"</div></div>";
		});

		html += "</div>";

		if (u.guven === "dusuk" || u.guven === "veri_yok") {
			const uyari =
				u.guven === "veri_yok"
					? "Satis verisi yok &mdash; oneri sabit esik miktarina dayaniyor"
					: "Dusuk guven &mdash; tahmin " +
					  (u.fatura_sayisi || 0) +
					  " faturaya dayaniyor";
			html += '<div class="eai-guven">' + uyari + "</div>";
		}

		if (u.gerekce) {
			html += '<div class="eai-gerekce">' + kacisla(u.gerekce) + "</div>";
		}

		if (u.onerilen_siparis) {
			html +=
				'<div class="eai-oneri">' +
				'<span class="eai-oneri-etiket">Onerilen siparis</span>' +
				'<span class="eai-oneri-deger">' + u.onerilen_siparis + " adet</span>" +
				(u.tedarikci
					? '<span class="eai-oneri-ted">' + kacisla(u.tedarikci) + "</span>"
					: "") +
				"</div>";
		} else if (u.durum === "fazla_stok" || u.durum === "olu_stok") {
			html +=
				'<div class="eai-oneri eai-oneri-yok">' +
				'<span class="eai-oneri-etiket">Siparis onerilmiyor</span>' +
				"</div>";
		}

		html += "</div>";
		return html;
	}

	function popup_goster(res) {
		if (document.getElementById("eai-popup")) return;

		const urunler = res.urunler || [];
		const sayi = res.urun_sayisi || urunler.length;

		const overlay = document.createElement("div");
		overlay.id = "eai-popup-overlay";

		const box = document.createElement("div");
		box.id = "eai-popup";

		let govde = "";
		if (res.mesaj) {
			govde += '<div class="eai-popup-ozet">' + bicimle(res.mesaj) + "</div>";
		}
		if (urunler.length) {
			govde += '<div class="eai-popup-liste">';
			urunler.forEach(function (u) {
				govde += urun_karti(u);
			});
			govde += "</div>";
		}

		box.innerHTML =
			'<div class="eai-popup-head">' +
			'<div class="eai-popup-head-sol">' +
			'<span class="eai-popup-badge">Stok Uyarisi</span>' +
			'<span class="eai-popup-alt">Dikkat gerektiren ' + sayi + " urun</span>" +
			"</div>" +
			'<button class="eai-popup-close" title="Kapat" aria-label="Kapat">&times;</button>' +
			"</div>" +
			'<div class="eai-popup-body">' + govde + "</div>" +
			'<div class="eai-popup-foot">' +
			'<button class="eai-popup-btn-ghost" id="eai-popup-dismiss">Anladim</button>' +
			'<button class="eai-popup-btn-ghost" id="eai-popup-stok">Stok raporu</button>' +
			'<button class="eai-popup-btn" id="eai-popup-open">Asistani ac</button>' +
			"</div>";

		overlay.appendChild(box);
		document.body.appendChild(overlay);

		function kapat() {
			overlay.remove();
		}
		box.querySelector(".eai-popup-close").onclick = kapat;
		box.querySelector("#eai-popup-dismiss").onclick = kapat;
		box.querySelector("#eai-popup-stok").onclick = function () {
			kapat();
			try {
				frappe.set_route("query-report", "Stock Balance");
			} catch (e) {
				window.location.href = "/app/query-report/Stock Balance";
			}
		};
		box.querySelector("#eai-popup-open").onclick = function () {
			kapat();
			panel_ac();
		};
		overlay.onclick = function (e) {
			if (e.target === overlay) kapat();
		};
		document.addEventListener("keydown", function esc(e) {
			if (e.key === "Escape") {
				kapat();
				document.removeEventListener("keydown", esc);
			}
		});
	}

	// Cana dusen "Stok Uyarisi" bildirimine tiklaninca sayfaya gitmek
	// yerine pop-up'i tekrar acar. Etiket/sinif adina degil, gercek metne
	// bakar; boylece Frappe surumundeki DOM yapisi ne olursa olsun calisir.
	function stok_uyarisi_elementi_mi(baslangic) {
		let node = baslangic;
		let derinlik = 0;
		while (node && node.nodeType === 1 && derinlik < 8) {
			const txt = (node.textContent || "").trim();
			if (txt.indexOf("Stok Uyarisi") !== -1 && txt.length < 250) {
				return node;
			}
			node = node.parentElement;
			derinlik++;
		}
		return null;
	}

	function sozlesme_uyarisi_elementi_mi(baslangic) {
		let node = baslangic;
		let derinlik = 0;
		while (node && node.nodeType === 1 && derinlik < 8) {
			const txt = (node.textContent || "").trim();
			if (txt.indexOf("Sozlesme Uyarisi") !== -1 && txt.length < 250) {
				return node;
			}
			node = node.parentElement;
			derinlik++;
		}
		return null;
	}

	function bildirim_tiklama_yakala() {
		document.addEventListener(
			"click",
			function (e) {
				// kendi panelimizin/pop-up'imizin icindeki tiklamalara karisma
				if (
					e.target.closest(
						"#eai-popup-overlay, #erpnext-ai-panel, #erpnext-ai-btn"
					)
				) {
					return;
				}
				const eslesenStok = stok_uyarisi_elementi_mi(e.target);
				if (eslesenStok) {
					e.preventDefault();
					e.stopPropagation();
					e.stopImmediatePropagation();
					kritik_stok_kontrol();
					return;
				}
				const eslesenSozlesme = sozlesme_uyarisi_elementi_mi(e.target);
				if (eslesenSozlesme) {
					e.preventDefault();
					e.stopPropagation();
					e.stopImmediatePropagation();
					sozlesme_kontrolu(true); // zorla goster, oturum limiti gecersiz
				}
			},
			true
		);
	}

	// ----------------------------------------------------------
	// Sozlesme/lisans yenileme uyarisi (her giriste kontrol)
	// ----------------------------------------------------------
	const SOZLESME_DURUM_BILGI = {
		kritik: { etiket: "Kritik", sinif: "eai-d-kritik" },
		yakin: { etiket: "Yakin", sinif: "eai-d-acil" },
		planla: { etiket: "Planlanmali", sinif: "eai-d-normal" },
	};

	// Basit bellek bayragi -- F5/yeni sekme (script sifirdan yuklenir) ->
	// tekrar cikar. Uygulama ici gezinme (SPA route degisimi) -> script
	// yeniden yuklenmedigi icin bu deger zaten kalir, tekrar cikmaz.
	let eaiSozlesmePopupGosterildi = false;

	function sozlesme_kontrolu(zorla) {
		if (!window.frappe || !frappe.call) return;
		if (!zorla && eaiSozlesmePopupGosterildi) return;

		frappe.call({
			method: "erpnext_ai.erpnext_ai.api.sozlesme_kontrolu",
			args: { sadece_acil: 1 }, // pop-up'ta sadece kritik+yakin, planla haric
			callback: function (r) {
				const res = (r && r.message) || {};
				if (res.var && res.mesaj) {
					sozlesme_popup_goster(res);
					eaiSozlesmePopupGosterildi = true;
				}
			},
			error: function () {},
		});
	}

	/** Kompakt satir: cok sayida sozlesme oldugunda kartlar yerine bu kullanilir. */
	function sozlesme_satiri(s, idx) {
		const bilgi = SOZLESME_DURUM_BILGI[s.durum] || { etiket: s.durum || "", sinif: "eai-d-normal" };
		const fiyatHtml = s.yenileme_fiyat
			? '<span class="eai-ss-fiyat">' + Number(s.yenileme_fiyat).toLocaleString("tr-TR") + " TL</span>"
			: '<span class="eai-ss-fiyat eai-ss-fiyat-yok">—</span>';
		const btnHtml = s.yenileme_fiyat
			? '<button class="eai-ss-btn" data-sozlesme-idx="' + idx + '">Teklif</button>'
			: "";

		return (
			'<div class="eai-ss-satir">' +
			'<span class="eai-rozet ' + bilgi.sinif + ' eai-ss-rozet">' + bilgi.etiket + "</span>" +
			'<div class="eai-ss-orta">' +
			'<div class="eai-ss-musteri">' + kacisla(s.taraf || "") + "</div>" +
			'<div class="eai-ss-detay">' +
			kacisla(s.yenileme_urun_adi || "") + " &middot; " +
			s.kalan_gun + " gun (" + kacisla(s.bitis_tarihi || "") + ")" +
			"</div>" +
			"</div>" +
			fiyatHtml +
			btnHtml +
			"</div>"
		);
	}

	function sozlesme_popup_goster(res) {
		if (document.getElementById("eai-sozlesme-popup")) return;

		const liste = res.sozlesmeler || [];
		const overlay = document.createElement("div");
		overlay.id = "eai-sozlesme-popup-overlay";
		overlay.className = "eai-popup-overlay";

		const box = document.createElement("div");
		box.id = "eai-sozlesme-popup";
		box.className = "eai-popup";

		let govde = '<div class="eai-popup-ozet">' + bicimle(res.mesaj) + "</div>";
		if (liste.length) {
			govde += '<div class="eai-ss-liste">';
			liste.forEach(function (s, idx) {
				govde += sozlesme_satiri(s, idx);
			});
			govde += "</div>";
		}

		box.innerHTML =
			'<div class="eai-popup-head">' +
			'<div class="eai-popup-head-sol">' +
			'<span class="eai-popup-badge">Sozlesme Uyarisi</span>' +
			'<span class="eai-popup-alt">Yaklasan ' + liste.length + " yenileme</span>" +
			"</div>" +
			'<button class="eai-popup-close" title="Kapat" aria-label="Kapat">&times;</button>' +
			"</div>" +
			'<div class="eai-popup-body">' + govde + "</div>" +
			'<div class="eai-popup-foot">' +
			'<button class="eai-popup-btn-ghost" id="eai-sozlesme-dismiss">Anladim</button>' +
			'<button class="eai-popup-btn" id="eai-sozlesme-goster">Tum Uyarilari Gor</button>' +
			"</div>";

		overlay.appendChild(box);
		document.body.appendChild(overlay);

		function kapat() {
			overlay.remove();
		}
		box.querySelector(".eai-popup-close").onclick = kapat;
		box.querySelector("#eai-sozlesme-dismiss").onclick = kapat;

		// "Teklifi goster" butonlari -- hesaplanmis yenileme fiyatiyla
		// dogrudan Quotation taslagi acar.
		box.querySelectorAll("[data-sozlesme-idx]").forEach(function (btn) {
			btn.onclick = function () {
				const idx = parseInt(btn.getAttribute("data-sozlesme-idx"), 10);
				const s = liste[idx];
				if (!s || !s.yenileme_urun) return;
				kapat();
				const bugun_iso = new Date().toISOString().slice(0, 10);
				const bitis_dt = new Date();
				bitis_dt.setMonth(bitis_dt.getMonth() + (s.yenileme_ay || 12));
				const bitis_iso = bitis_dt.toISOString().slice(0, 10);
				form_taslagi_ac({
					doctype: "Quotation",
					alanlar: {
						quotation_to: "Customer",
						party_name: s.taraf,
						items: [{
							item_code: s.yenileme_urun,
							qty: 1,
							rate: s.yenileme_fiyat,
							aciklama: (s.yenileme_urun_adi || "") + " — Yenileme (" + s.yenileme_ay + " Ay)",
							hizmet_baslangic: bugun_iso,
							hizmet_bitis: bitis_iso,
						}],
					},
				});
			};
		});
		box.querySelector("#eai-sozlesme-goster").onclick = function () {
			kapat();
			try {
				frappe.set_route("teklif-uyarilari");
			} catch (e) {
				window.location.href = "/app/teklif-uyarilari";
			}
		};
		overlay.onclick = function (e) {
			if (e.target === overlay) kapat();
		};
	}

	// ----------------------------------------------------------
	// ERPNext'in yerlesik "Fetch Timesheet" penceresinden "Proje"
	// alanini gizler (native JS override degil, DOM izleme).
	// ----------------------------------------------------------
	function fetch_timesheet_proje_gizle() {
		const observer = new MutationObserver(function () {
			document.querySelectorAll(".modal-dialog").forEach(function (modal) {
				const baslik = modal.querySelector(".modal-title");
				if (!baslik) return;
				if ((baslik.textContent || "").indexOf("Fetch Timesheet") === -1) return;

				modal.querySelectorAll(".frappe-control").forEach(function (ctrl) {
					const etiket = ctrl.querySelector(".control-label");
					if (etiket && (etiket.textContent || "").trim() === "Proje") {
						const satir = ctrl.closest(".form-group") || ctrl;
						satir.style.display = "none";
					}
				});
			});
		});
		observer.observe(document.body, { childList: true, subtree: true });
	}

	function baslat() {
		if (!window.frappe || !frappe.call) return;
		buton_olustur();
		bildirim_tiklama_yakala();
		// her giriste kritik stok kontrolu -> pop-up
		// stok artik nadiren ilgili (hizmet agirlikli), sozlesme oncelikli
		setTimeout(sozlesme_kontrolu, 400);
		setTimeout(kritik_stok_kontrol, 1200);
		fetch_timesheet_proje_gizle();
	}

	// ----------------------------------------------------------
	// Hizmet suresi (tarih) girilince fiyati OTOMATIK hesaplar.
	// Sadece SRV- ile baslayan (hizmet) urunlerde calisir.
	// AI/Groq cagirmaz, dogrudan fiyat motorunu calistirir (hizli).
	// ----------------------------------------------------------
	function ay_farki_hesapla(baslangic_str, bitis_str) {
		try {
			const b1 = frappe.datetime.str_to_obj(baslangic_str);
			const b2 = frappe.datetime.str_to_obj(bitis_str);
			const ay =
				(b2.getFullYear() - b1.getFullYear()) * 12 +
				(b2.getMonth() - b1.getMonth());
			return Math.max(ay, 1);
		} catch (e) {
			return 1;
		}
	}

	function hizmet_fiyat_guncelle(frm, cdt, cdn) {
		const row = locals[cdt] && locals[cdt][cdn];
		if (!row || !row.item_code) return;
		// artik urun/hizmet ayrimi yok -- tarih girilen HER satirda calisir
		if (!row.custom_hizmet_baslangic || !row.custom_hizmet_bitis) return;

		const musteri = frm.doc.customer || frm.doc.party_name;
		if (!musteri) {
			frappe.show_alert({
				message: __("Fiyat hesaplamak icin once musteriyi secin."),
				indicator: "orange",
			});
			return;
		}

		const ay = ay_farki_hesapla(row.custom_hizmet_baslangic, row.custom_hizmet_bitis);

		frappe.call({
			method: "erpnext_ai.erpnext_ai.api.hizmet_fiyat_hesapla",
			args: { musteri: musteri, urun: row.item_code, ay_sayisi: ay },
			callback: function (r) {
				const sonuc = (r && r.message) || {};
				if (sonuc.toplam != null) {
					frappe.model.set_value(cdt, cdn, "qty", 1);
					frappe.model.set_value(cdt, cdn, "rate", sonuc.toplam);
					frappe.show_alert({
						message: __(
							"{0} ay icin fiyat guncellendi: {1} TL",
							[ay, sonuc.toplam]
						),
						indicator: "green",
					});
				}
			},
			error: function () {
				// sessiz gec, kullanici elle fiyat girebilir
			},
		});
	}

	["Quotation Item", "Sales Order Item", "Sales Invoice Item"].forEach(function (dt) {
		frappe.ui.form.on(dt, {
			custom_hizmet_baslangic: function (frm, cdt, cdn) {
				hizmet_fiyat_guncelle(frm, cdt, cdn);
			},
			custom_hizmet_bitis: function (frm, cdt, cdn) {
				hizmet_fiyat_guncelle(frm, cdt, cdn);
			},
		});
	});

	// Bu fonksiyonlari disariya aciyoruz ki "Teklif Uyarilari" ozel sayfasi
	// (ayri bir JS dosyasi) ayni mantigi tekrar yazmadan kullanabilsin.
	window.eaiYardimcilar = {
		kacisla: kacisla,
		bicimle: bicimle,
		formTaslagiAc: form_taslagi_ac,
	};

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", () => setTimeout(baslat, 1500));
	} else {
		setTimeout(baslat, 1500);
	}
})();
