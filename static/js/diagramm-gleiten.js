/*
 * Gleitender Werte-Kasten und Marker für alle Chart.js-Graphen (Orbita-Graphen-Standard, 09.11.2026).
 *
 * Verhalten wie im Orbita-Dashboard: Kasten, senkrechte Marker-Linie und aktiver Punkt wandern gemeinsam per
 * ``transform: translate3d()`` mit ``transition: transform 400ms ease``, blenden in 150 ms weich ein und aus und
 * behalten beim Verlassen ihren letzten Inhalt. Touch: Ziehen mit dem Daumen bewegt Marker und Kasten, senkrecht
 * scrollt die Seite (``touch-action: pan-y``), kein Kopieren-Menü beim langen Drücken.
 *
 * Achse: ``BbDiagramm.zeitachse(...)`` liefert eine lineare X-Achse, deren Daten immer rechts neben der
 * Y-Beschriftung beginnen (schmal: Y-Werte innen über den Gitterlinien, damit die Plotfläche fast die ganze
 * Kartenbreite nutzt; breit: Achse außen, Linie um den Punktradius eingerückt).
 *
 * Nutzung:
 *   new Chart(ctx, { …, options: { …, plugins: { tooltip: BbDiagramm.tooltip({ titel, wert }) } },
 *                    plugins: [BbDiagramm.plugin] });
 */
(function () {
    'use strict';

    var GLEITEN = 'transform 400ms ease, opacity 150ms ease';
    var ABSTAND = 12;           // Kasten ↔ Zeiger (wie Recharts ``offset``)
    var ABSTAND_DAUMEN = 28;    // Kasten ↔ Daumen, damit der Finger ihn nicht verdeckt
    var PUNKT = 14;             // r = 5 + 2 px Rand
    var EINZUG_PUNKT = 6;       // Linie um den Punktradius einrücken
    var SCHMAL = 640;           // darunter stehen die Y-Werte innen

    var ZAHL = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 });
    var GANZ = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 });

    function stil(el, css) { for (var k in css) el.style[k] = css[k]; return el; }
    function neu(tag, cls, css, eltern) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        stil(e, css || {});
        if (eltern) eltern.appendChild(e);
        return e;
    }

    /** Kompakte Achsenwerte: „850“, „1,2k“, „12k“, „1,4 Mio.“ */
    function kompakt(v) {
        var a = Math.abs(v);
        if (a >= 1e6) return ZAHL.format(v / 1e6) + ' Mio.';
        if (a >= 1e4) return GANZ.format(v / 1e3) + 'k';
        if (a >= 1e3) return ZAHL.format(v / 1e3) + 'k';
        return ZAHL.format(v);
    }

    /** Wert im Kasten nach Art der Kennzahl. */
    function wertNachArt(v, art) {
        if (v == null || !isFinite(v)) return '–';
        if (art === 'percent') return ZAHL.format(v) + ' %';
        if (art === 'minutes') return v >= 60 ? ZAHL.format(v / 60) + ' h' : GANZ.format(v) + ' min';
        return GANZ.format(v);
    }
    function achseNachArt(v, art) {
        if (art === 'percent') return ZAHL.format(v) + ' %';
        if (art === 'minutes') return v >= 60 ? ZAHL.format(v / 60) + ' h' : GANZ.format(v) + ' m';
        return kompakt(v);
    }

    var TAG_LANG = new Intl.DateTimeFormat('de-DE', { weekday: 'short', day: 'numeric', month: 'long', year: 'numeric' });
    var TAG_KURZ = new Intl.DateTimeFormat('de-DE', { day: 'numeric', month: 'short' });
    function datum(iso) {
        var d = new Date(String(iso).slice(0, 10) + 'T12:00:00');
        return isNaN(d) ? null : d;
    }
    function tagLang(iso) { var d = datum(iso); return d ? TAG_LANG.format(d) : String(iso); }
    function tagKurz(iso) { var d = datum(iso); return d ? TAG_KURZ.format(d) : String(iso); }

    function istSchmal(chart) { return (chart.width || chart.canvas.clientWidth) < SCHMAL; }

    /* ---------- Ebene mit Marker, Punkt und Kasten je Diagramm ---------- */

    function ebene(chart) {
        if (chart.$bbEbene) return chart.$bbEbene;
        var box = chart.canvas.parentNode;
        if (getComputedStyle(box).position === 'static') box.style.position = 'relative';
        var weich = { touchAction: 'pan-y', webkitTouchCallout: 'none', userSelect: 'none', webkitUserSelect: 'none', webkitTapHighlightColor: 'transparent' };
        stil(box, weich);
        stil(chart.canvas, weich);
        var basis = { position: 'absolute', left: '0', top: '0', pointerEvents: 'none', opacity: '0', transition: GLEITEN, willChange: 'transform, opacity', zIndex: '2' };
        var linie = neu('div', 'bb-marker-linie', Object.assign({ width: '1px', background: 'rgba(120, 113, 108, 0.5)' }, basis), box);
        var punkt = neu('div', 'bb-marker-punkt', Object.assign({ width: PUNKT + 'px', height: PUNKT + 'px', borderRadius: '50%', border: '2px solid #fff', boxSizing: 'border-box' }, basis), box);
        var kasten = neu('div', 'bb-kasten', Object.assign({}, basis, {
            zIndex: '30', background: '#ffffff', border: '1px solid #e5e7eb', borderRadius: '12px',
            boxShadow: '0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1)',
            padding: '10px 12px', fontSize: '14px', lineHeight: '1.375', color: 'var(--text-primary, #1A1F36)',
            minWidth: '9rem', maxWidth: 'min(18rem, 80vw)', boxSizing: 'border-box', whiteSpace: 'nowrap'
        }), box);
        kasten.setAttribute('aria-hidden', 'true');

        var e = { chart: chart, box: box, linie: linie, punkt: punkt, kasten: kasten, sichtbar: false, gezeigt: false, zeiger: null };
        e.merke = function (ev) {
            var r = box.getBoundingClientRect();
            var p = ev.touches && ev.touches[0] ? ev.touches[0] : ev;
            e.zeiger = { x: p.clientX - r.left, y: p.clientY - r.top, daumen: !!ev.touches || ev.pointerType === 'touch' };
        };
        e.draussen = function (ev) { if (e.sichtbar && !box.contains(ev.target)) verstecke(e, true); };
        e.menue = function (ev) { if (e.zeiger && e.zeiger.daumen) ev.preventDefault(); };
        box.addEventListener('pointermove', e.merke, { passive: true });
        box.addEventListener('pointerdown', e.merke, { passive: true });
        box.addEventListener('touchstart', e.merke, { passive: true });
        box.addEventListener('touchmove', e.merke, { passive: true });
        box.addEventListener('contextmenu', e.menue);
        document.addEventListener('touchstart', e.draussen, { passive: true });
        e.weg = function () {
            box.removeEventListener('pointermove', e.merke);
            box.removeEventListener('pointerdown', e.merke);
            box.removeEventListener('touchstart', e.merke);
            box.removeEventListener('touchmove', e.merke);
            box.removeEventListener('contextmenu', e.menue);
            document.removeEventListener('touchstart', e.draussen);
            [linie, punkt, kasten].forEach(function (n) { n.remove(); });
        };
        chart.$bbEbene = e;
        return e;
    }

    /** Beim allerersten Zeigen springen statt von (0, 0) herein zu gleiten. */
    function schiebe(n, x, y, springen) {
        var t = 'translate3d(' + Math.round(x * 10) / 10 + 'px, ' + Math.round(y * 10) / 10 + 'px, 0)';
        if (springen) {
            n.style.transition = 'opacity 150ms ease';
            n.style.transform = t;
            void n.offsetWidth;
            n.style.transition = GLEITEN;
        } else {
            n.style.transform = t;
        }
    }

    function verstecke(e, auchChart) {
        e.sichtbar = false;
        [e.linie, e.punkt, e.kasten].forEach(function (n) { n.style.opacity = '0'; });
        if (auchChart) {
            e.chart.setActiveElements([]);
            e.chart.tooltip.setActiveElements([], { x: 0, y: 0 });
            e.chart.update('none');
        }
    }

    function fuelle(kasten, titel, zeilen) {
        kasten.textContent = '';
        if (titel) neu('p', '', { margin: '0 0 6px', fontWeight: '600' }, kasten).textContent = titel;
        zeilen.forEach(function (z) {
            var reihe = neu('div', '', { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '16px' }, kasten);
            var links = neu('span', '', { display: 'flex', alignItems: 'center', gap: '8px', minWidth: '0', fontSize: '12px', color: 'var(--text-tertiary, #6B7280)' }, reihe);
            if (z.farbe) neu('span', '', { width: '10px', height: '10px', borderRadius: '50%', flexShrink: '0', background: z.farbe }, links);
            neu('span', '', { overflow: 'hidden', textOverflow: 'ellipsis' }, links).textContent = z.name;
            neu('span', '', { fontWeight: '600', fontVariantNumeric: 'tabular-nums' }, reihe).textContent = z.wert;
        });
    }

    /**
     * Tooltip-Optionen für Chart.js: eigener DOM-Kasten statt Canvas-Tooltip.
     * o.titel(dataIndex) → Text, o.wert(wert, datensatz) → Text, o.name(datensatz) → Text.
     */
    function tooltip(o) {
        o = o || {};
        return {
            enabled: false,
            animation: false,
            external: function (ctx) {
                var chart = ctx.chart, tt = ctx.tooltip, e = ebene(chart);
                if (!tt || tt.opacity === 0 || !tt.dataPoints || !tt.dataPoints.length) { verstecke(e, false); return; }
                var a = chart.chartArea;
                var erster = tt.dataPoints[0];
                var px = erster.element.x, py = erster.element.y;
                var zeilen = tt.dataPoints.map(function (dp) {
                    var ds = dp.dataset;
                    var farbe = typeof ds.borderColor === 'string' ? ds.borderColor : (typeof ds.backgroundColor === 'string' ? ds.backgroundColor : null);
                    var roh = dp.parsed && dp.parsed.y != null ? dp.parsed.y : dp.raw;
                    return { farbe: farbe, name: o.name ? o.name(ds) : (ds.label || ''), wert: o.wert ? o.wert(roh, ds) : GANZ.format(roh) };
                });
                fuelle(e.kasten, o.titel ? o.titel(erster.dataIndex) : (tt.title || []).join(' '), zeilen);

                var springen = !e.gezeigt;
                e.gezeigt = true;
                // Marker-Linie über die Plotfläche, Punkt auf dem Wert
                stil(e.linie, { top: a.top + 'px', height: (a.bottom - a.top) + 'px' });
                e.punkt.style.background = zeilen[0].farbe || '#F97316';
                schiebe(e.linie, px - 0.5, 0, springen);
                schiebe(e.punkt, px - PUNKT / 2, py - PUNKT / 2, springen);

                // Kasten neben Zeiger bzw. Daumen, innerhalb der Diagrammfläche
                var bw = e.box.clientWidth, bh = e.box.clientHeight;
                var kw = e.kasten.offsetWidth, kh = e.kasten.offsetHeight;
                var z = e.zeiger || { x: px, y: py, daumen: false };
                var ab = z.daumen ? ABSTAND_DAUMEN : ABSTAND;
                var kx = px + ab, verdeckt = false;
                if (kx + kw > bw - 4) kx = px - ab - kw;
                if (kx < 4) { verdeckt = true; kx = Math.max(4, Math.min(px - kw / 2, bw - kw - 4)); }
                var ky = z.daumen ? z.y - kh - ab : z.y + ab;
                if (!z.daumen && ky + kh > bh - 4) ky = z.y - ab - kh;
                // Passt der Kasten seitlich nicht neben die Linie: über (sonst unter) den Punkt, nie auf ihn
                if (verdeckt) ky = py - kh - 14 >= 4 ? py - kh - 14 : py + 14;
                ky = Math.max(4, Math.min(ky, bh - kh - 4));
                schiebe(e.kasten, kx, ky, springen);

                e.sichtbar = true;
                [e.linie, e.punkt, e.kasten].forEach(function (n) { n.style.opacity = '1'; });
            }
        };
    }

    /**
     * Lineare X-Achse über Tagesindizes 0 … n-1, deren Daten rechts neben der Y-Beschriftung beginnen.
     * o.labels (ISO-Tage), o.kurz (Index → Tick-Text).
     */
    function zeitachse(o) {
        var n = o.labels.length;
        return {
            type: 'linear',
            bounds: 'data',
            grid: { display: false },
            border: { display: false },
            ticks: {
                color: o.farbe || '#78716C',
                font: function (c) { return { size: istSchmal(c.chart) ? 11 : 12 }; },
                maxRotation: 0,
                autoSkip: false,
                align: 'inner',
                padding: 6,
                callback: function (v) { return o.kurz ? o.kurz(v) : tagKurz(o.labels[v]); }
            },
            afterDataLimits: function (s) {
                var chart = s.chart, y = chart.scales.y;
                var spanne = Math.max(1, n - 1);
                var schmal = istSchmal(chart);
                var lp = chart.options.layout && chart.options.layout.padding || {};
                var breite = chart.width - (lp.left || 0) - (lp.right || 0) - (schmal || !y ? 0 : y.width);
                var links = EINZUG_PUNKT, rechts = EINZUG_PUNKT;
                if (schmal && y && y.ticks && y.ticks.length) {
                    var c2 = chart.ctx;
                    c2.save();
                    c2.font = Chart.helpers.toFont(y.options.ticks.font).string;
                    var breitest = 0;
                    y.ticks.forEach(function (t) { breitest = Math.max(breitest, c2.measureText(String(t.label)).width); });
                    c2.restore();
                    // breiteste Y-Beschriftung + 2 px + 4 px Luft, mindestens der Punktradius
                    links = Math.max(EINZUG_PUNKT, (y.options.ticks.padding || 0) + breitest + 6);
                }
                var nutz = Math.max(40, breite - links - rechts);
                s.min = -links * spanne / nutz;
                s.max = spanne + rechts * spanne / nutz;
            },
            afterBuildTicks: function (s) {
                var anzahl = istSchmal(s.chart) ? 4 : 6;
                if (n <= 1) { s.ticks = [{ value: 0 }]; return; }
                var schritte = Math.min(anzahl, n) - 1, gesehen = {}, ticks = [];
                for (var i = 0; i <= schritte; i++) {
                    var v = Math.round(i * (n - 1) / schritte);
                    if (!gesehen[v]) { gesehen[v] = 1; ticks.push({ value: v }); }
                }
                s.ticks = ticks;
            }
        };
    }

    /** Y-Achse: ab 0, runde Obergrenze über dem Maximum; schmal innen über den Gitterlinien. */
    function wertachse(o) {
        o = o || {};
        return {
            beginAtZero: true,
            grace: '5%',
            border: { display: false },
            grid: { color: o.gitter || '#F5F5F4', drawTicks: false },
            ticks: {
                color: o.farbe || '#78716C',
                precision: o.art === 'percent' ? 1 : 0,
                maxTicksLimit: 5,
                font: function (c) { return { size: istSchmal(c.chart) ? 11 : 12 }; },
                mirror: function (c) { return istSchmal(c.chart); },
                padding: function (c) { return istSchmal(c.chart) ? 2 : 10; },
                labelOffset: function (c) { return istSchmal(c.chart) ? -8 : 0; },
                z: 1,
                callback: function (v) { return achseNachArt(v, o.art); }
            }
        };
    }

    var plugin = {
        id: 'bbGleiten',
        afterInit: function (chart) { ebene(chart); },
        resize: function (chart) {
            // schmal ↔ breit: Spiegelung der Y-Werte umschalten (Optionen sind scriptable, Layout neu)
            var e = chart.$bbEbene;
            if (e && e.sichtbar) verstecke(e, false);
        },
        afterDestroy: function (chart) { if (chart.$bbEbene) { chart.$bbEbene.weg(); chart.$bbEbene = null; } }
    };

    window.BbDiagramm = {
        plugin: plugin,
        tooltip: tooltip,
        zeitachse: zeitachse,
        wertachse: wertachse,
        wertNachArt: wertNachArt,
        tagLang: tagLang,
        tagKurz: tagKurz,
        kompakt: kompakt
    };
})();
