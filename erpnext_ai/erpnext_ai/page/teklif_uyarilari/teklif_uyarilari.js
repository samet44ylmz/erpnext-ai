/**
 * Teklif Uyarilari -- kalici sayfa.
 *
 * Yaklasan sozlesme yenilemelerinin TAMAMINI listeler (pop-up'in aksine
 * kapanmaz, istendigi zaman tekrar ziyaret edilebilir). Her satirda
 * hesaplanmis yenileme fiyati + "Teklif" butonu (Quotation taslagi acar).
 *
 * GUVENLIK: Bu sayfa hicbir kayit olusturmaz/kaydetmez. "Teklif" butonu
 * sadece bir TASLAK form acar; kaydetmek kullaniciya aittir.
 */

frappe.pages["teklif-uyarilari"].on_page_load = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Teklif Uyarıları",
		single_column: true,
	});

	page.set_primary_action("Yenile", function () {
		yukle();
	}, "refresh");

	var govde = $(
		'<div style="padding: 22px; max-width: 920px;"></div>'
	).appendTo(page.body);

	var ozetEl = $(
		'<div style="margin-bottom:16px; font-size:13px; color:#556070;"></div>'
	).appendTo(govde);
	var listeEl = $("<div></div>").appendTo(govde);

	function yukle() {
		listeEl.html(
			'<div style="color:#8A93A3; font-size:13px;">Yükleniyor…</div>'
		);
		frappe.call({
			method: "erpnext_ai.erpnext_ai.api.sozlesme_kontrolu",
			callback: function (r) {
				cizVeGoster((r && r.message) || {});
			},
			error: function () {
				listeEl.html(
					'<div style="color:#A93A26; font-size:13px;">Veri alinamadi. Lutfen sayfayi yenileyin.</div>'
				);
			},
		});
	}

	function cizVeGoster(res) {
		var liste = res.sozlesmeler || [];

		if (!liste.length) {
			ozetEl.text("");
			listeEl.html(
				'<div style="color:#8A93A3; font-size:13.5px; padding: 24px 0;">' +
					"Su an yaklasan bir sozlesme yenilemesi yok." +
					"</div>"
			);
			return;
		}

		ozetEl.text(liste.length + " yaklasan sozlesme yenilemesi bulundu.");

		var yardim = window.eaiYardimcilar || {};
		var kacisla = yardim.kacisla || function (s) { return s; };

		var html = '<div class="eai-ss-liste">';
		liste.forEach(function (s, idx) {
			var rozetSinif =
				s.durum === "kritik" ? "eai-d-kritik" :
				s.durum === "yakin" ? "eai-d-acil" : "eai-d-normal";
			var rozetMetin =
				s.durum === "kritik" ? "Kritik" :
				s.durum === "yakin" ? "Yakın" : "Planlanmalı";
			var fiyatHtml = s.yenileme_fiyat
				? '<span class="eai-ss-fiyat">' +
				  Number(s.yenileme_fiyat).toLocaleString("tr-TR") +
				  " TL</span>"
				: '<span class="eai-ss-fiyat eai-ss-fiyat-yok">—</span>';

			html +=
				'<div class="eai-ss-satir">' +
				'<span class="eai-rozet ' + rozetSinif + ' eai-ss-rozet">' + rozetMetin + "</span>" +
				'<div class="eai-ss-orta">' +
				'<div class="eai-ss-musteri">' + kacisla(s.taraf || "") + "</div>" +
				'<div class="eai-ss-detay">' +
				kacisla(s.yenileme_urun_adi || "") + " &middot; " +
				s.kalan_gun + " gun (" + kacisla(s.bitis_tarihi || "") + ")" +
				"</div></div>" +
				fiyatHtml;

			if (s.yenileme_fiyat) {
				html += '<button class="eai-ss-btn" data-idx="' + idx + '">Teklif</button>';
			}
			html += "</div>";
		});
		html += "</div>";

		listeEl.html(html);

		listeEl.find("[data-idx]").on("click", function () {
			var idx = parseInt($(this).attr("data-idx"), 10);
			var s = liste[idx];
			if (!s || !s.yenileme_urun) return;

			var bugun = new Date();
			var bugunIso = bugun.toISOString().slice(0, 10);
			var bitisDt = new Date();
			bitisDt.setMonth(bitisDt.getMonth() + (s.yenileme_ay || 12));
			var bitisIso = bitisDt.toISOString().slice(0, 10);

			var acFn = (window.eaiYardimcilar || {}).formTaslagiAc;
			if (!acFn) {
				frappe.msgprint(
					"Asistan yardimci fonksiyonlari yuklenemedi, sayfayi yenileyip tekrar deneyin."
				);
				return;
			}

			acFn({
				doctype: "Quotation",
				alanlar: {
					quotation_to: "Customer",
					party_name: s.taraf,
					items: [
						{
							item_code: s.yenileme_urun,
							qty: 1,
							rate: s.yenileme_fiyat,
							aciklama:
								(s.yenileme_urun_adi || "") +
								" — Yenileme (" + s.yenileme_ay + " Ay)",
							hizmet_baslangic: bugunIso,
							hizmet_bitis: bitisIso,
						},
					],
				},
			});
		});
	}

	yukle();
};
