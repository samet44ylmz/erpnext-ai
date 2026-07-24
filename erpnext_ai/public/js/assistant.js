/**
 * ERPNext AI — arayuz
 *
 * Sag altta yuzen buton, tiklayinca sohbet paneli acilir.
 * GUVENLIK: Form doldurma yalnizca TARAYICIDA yapilir.
 * Hicbir kayit kaydedilmez/submit edilmez.
 */

(function () {
	"use strict";

	const PANEL_ID = "erpnext-ai-panel";
	const BTN_ID = "erpnext-ai-btn";

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
			// baglam alinamadi
		}
		return ctx;
	}

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
						// bu alan doldurulamadi
					}
				});
				frappe.show_alert(
					{
						message: __(
							"Taslak dolduruldu ({0} alan). Gozden gecirip <b>kendiniz kaydedin</b> — asistan kaydetmez.",
							[dolan]
						),
						indicator: "orange",
					},
					10
				);
			}, 700);
		});
	}

	function panel_olustur() {
		if (document.getElementById(PANEL_ID)) return;

		const panel = document.createElement("div");
		panel.id = PANEL_ID;
		panel.innerHTML = `
			<div class="eai-header">
				<span class="eai-title">ERPNext AI</span>
				<span class="eai-ctx" id="eai-ctx"></span>
				<button class="eai-close" title="Kapat">&times;</button>
			</div>
			<div class="eai-log" id="eai-log">
				<div class="eai-msg eai-bot">
					Merhaba. Verilerinizle ilgili soru sorabilir veya
					"fatura kes", "teklif hazirla" gibi taslak isteyebilirsiniz.
					<div class="eai-note">Not: Hicbir kayit onayiniz olmadan kaydedilmez.</div>
				</div>
			</div>
			<div class="eai-input-row">
				<input id="eai-input" type="text" placeholder="Sorunuzu yazin..." autocomplete="off">
				<button id="eai-send">Gonder</button>
			</div>
		`;
		document.body.appendChild(panel);

		panel.querySelector(".eai-close").onclick = panel_kapat;
		panel.querySelector("#eai-send").onclick = gonder;
		panel.querySelector("#eai-input").addEventListener("keydown", (e) => {
			if (e.key === "Enter") gonder();
		});
	}

	function panel_ac() {
		panel_olustur();
		const p = document.getElementById(PANEL_ID);
		p.classList.add("eai-open");
		const ctx = mevcut_baglam();
		const el = document.getElementById("eai-ctx");
		if (el) el.textContent = ctx.doctype ? ctx.doctype : "";
		const inp = document.getElementById("eai-input");
		if (inp) inp.focus();
	}

	function panel_kapat() {
		const p = document.getElementById(PANEL_ID);
		if (p) p.classList.remove("eai-open");
	}

	function mesaj_ekle(metin, sinif) {
		const log = document.getElementById("eai-log");
		const d = document.createElement("div");
		d.className = "eai-msg " + sinif;
		d.textContent = metin;
		log.appendChild(d);
		log.scrollTop = log.scrollHeight;
		return d;
	}

	function gonder() {
		const inp = document.getElementById("eai-input");
		const soru = (inp.value || "").trim();
		if (!soru) return;

		mesaj_ekle(soru, "eai-user");
		inp.value = "";

		const bekle = mesaj_ekle("Dusunuyorum...", "eai-bot eai-wait");
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
				mesaj_ekle(res.cevap || "(cevap alinamadi)", "eai-bot");

				if (res.form_taslak) {
					taslak_butonu_ekle(res.form_taslak);
				}
			},
			error: function () {
				btn.disabled = false;
				bekle.remove();
				mesaj_ekle("Bir hata olustu. Lutfen tekrar deneyin.", "eai-bot eai-err");
			},
		});
	}

	function taslak_butonu_ekle(taslak) {
		const log = document.getElementById("eai-log");
		const box = document.createElement("div");
		box.className = "eai-msg eai-bot eai-draft";
		box.innerHTML = `
			<div><b>${frappe.utils.escape_html(taslak.doctype)}</b> taslagi hazir.</div>
			<div class="eai-note">Form acilip doldurulacak. <b>Kaydetmeyi siz yapacaksiniz.</b></div>
		`;
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

	function buton_olustur() {
		if (document.getElementById(BTN_ID)) return;
		const b = document.createElement("button");
		b.id = BTN_ID;
		b.title = "ERPNext AI";
		b.textContent = "AI";
		b.onclick = () => {
			const p = document.getElementById(PANEL_ID);
			if (p && p.classList.contains("eai-open")) panel_kapat();
			else panel_ac();
		};
		document.body.appendChild(b);
	}

	function baslat() {
		if (!window.frappe || !frappe.call) return;
		buton_olustur();
	}

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", () => setTimeout(baslat, 1500));
	} else {
		setTimeout(baslat, 1500);
	}
})();
