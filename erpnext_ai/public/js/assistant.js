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
				Object.keys(alanlar).forEach((k) => {
					try {
						if (cur_frm.get_field(k)) {
							cur_frm.set_value(k, alanlar[k]);
							dolan++;
						}
					} catch (e) {
						// bu alan doldurulamadi, digerlerine devam
					}
				});
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
	function gonder() {
		const inp = document.getElementById("eai-input");
		const soru = (inp.value || "").trim();
		if (!soru) return;

		mesaj_ekle(soru, "eai-user");
		inp.value = "";

		const bekle = mesaj_ekle("Veriler getiriliyor", "eai-bot eai-wait");
		const btn = document.getElementById("eai-send");
		btn.disabled = true;

		frappe.call({
			method: "erpnext_ai.erpnext_ai.api.ask",
			args: {
				question: soru,
				context: JSON.stringify(mevcut_baglam()),
			},
			callback: function (r) {
				btn.disabled = false;
				bekle.remove();
				const res = (r && r.message) || {};
				mesaj_ekle(bicimle(res.cevap || "Cevap alinamadi."), "eai-bot", true);
				if (res.form_taslak) taslak_karti_ekle(res.form_taslak);
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
	// yerine pop-up'i tekrar acar.
	function bildirim_tiklama_yakala() {
		document.addEventListener(
			"click",
			function (e) {
				if (e.target.closest("#eai-popup-overlay")) return;
				const el = e.target.closest("a, .notification-item, [data-name]");
				if (!el) return;
				const metin = (el.textContent || "").trim();
				if (metin.indexOf("Stok Uyarisi") === 0) {
					e.preventDefault();
					e.stopPropagation();
					kritik_stok_kontrol();
				}
			},
			true
		);
	}

	function baslat() {
		if (!window.frappe || !frappe.call) return;
		buton_olustur();
		bildirim_tiklama_yakala();
		// her giriste kritik stok kontrolu -> pop-up
		setTimeout(kritik_stok_kontrol, 2500);
	}

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", () => setTimeout(baslat, 1500));
	} else {
		setTimeout(baslat, 1500);
	}
})();
