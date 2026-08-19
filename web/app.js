'use strict';

/* מצב הדף. `parts` הוא מקור האמת של המסך; השרת מחזיק רק את הגיאומטריות. */
const state = { config: null, parts: [], seq: 0 };

const $ = (sel) => document.querySelector(sel);
const fmt = (n, d = 2) => Number(n).toLocaleString('he-IL', { minimumFractionDigits: d, maximumFractionDigits: d });
const int = (n) => Number(n).toLocaleString('he-IL');
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

/* ---------- קריאות שרת ---------- */

async function api(path, options = {}) {
  const res = await fetch(path, options);
  const text = await res.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = null; }
  if (!res.ok) {
    throw new Error((body && body.detail) || text || `שגיאה ${res.status}`);
  }
  return body;
}

/* ---------- אתחול ---------- */

async function boot() {
  try {
    state.config = await api('/api/config');
  } catch (err) {
    showBanner('bad', 'לא ניתן לטעון את הגדרות המערכת: ' + err.message);
    return;
  }
  const cfg = state.config;

  if (cfg.plate) {
    $('#plate-label').textContent =
      `פלטה ${cfg.plate.width_mm}×${cfg.plate.height_mm} · שטח עבודה ${cfg.plate.usable_width_mm}×${cfg.plate.usable_height_mm} מ"מ`;
  }
  $('#tariff-source').textContent = 'טבלה: ' + (cfg.tariff_source || '—');

  if (cfg.tariff_error) {
    showBanner('bad', 'טבלת התמחור אינה תקינה: ' + cfg.tariff_error);
  } else if (!cfg.tariff_ready) {
    showBanner('warn',
      'טבלת התמחור עדיין ריקה — כל סכום יצא 0. המידות, הפריסה ואחוזי הבזבוז אמיתיים.');
  }

  renderParts();
  await showWhoAmI();
}

async function showWhoAmI() {
  /* מי מחובר, מה מותר לו, ואיך יוצאים. נכשל בשקט בכוונה: בפיתוח מקומי
     השער כבוי, ואין סיבה שכותרת ריקה תפיל באנר שגיאה על המסך. */
  let me;
  try {
    me = await api('/api/me');
  } catch {
    loadTariffEditor();
    return;
  }
  state.me = me;

  /* לשונית שאי אפשר לשמור בה היא תקלה שנראית כמו הרשאה. מי שאין לו
     prices:edit פשוט לא רואה את עורך הטבלה. */
  const mayEdit = !me.authenticated || (me.capabilities || []).includes('prices:edit');
  if (mayEdit) {
    loadTariffEditor();
  } else {
    const tab = document.querySelector('nav.tabs button[data-view="tariff"]');
    if (tab) tab.remove();
  }

  if (!me.authenticated) return;
  const host = $('#whoami');
  host.textContent = `מחובר: ${me.display_name} · `;
  const out = document.createElement('a');
  out.href = '#';
  out.textContent = 'יציאה';
  out.addEventListener('click', async (event) => {
    event.preventDefault();
    await fetch('/api/logout', { method: 'POST' });
    location.href = '/login';
  });
  host.appendChild(out);
}

function showBanner(kind, text) {
  const el = $('#banner');
  el.className = 'banner ' + kind;
  el.textContent = text;
}

/* ---------- לשוניות ---------- */

document.querySelectorAll('nav.tabs button').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('nav.tabs button').forEach((b) => b.classList.toggle('active', b === btn));
    document.querySelectorAll('.view').forEach((v) => v.classList.toggle('active', v.id === 'view-' + btn.dataset.view));
    if (btn.dataset.view === 'state') loadState();
  });
});

/* ---------- מצב המערכת ---------- */

async function loadState() {
  const host = $('#state-out');
  let data;
  try {
    data = await api('/api/dashboard');
  } catch (err) {
    host.innerHTML = `<div class="card"><h2>מצב המערכת</h2><p style="color:var(--bad)">${esc(err.message)}</p></div>`;
    return;
  }

  const t = data.tariff;
  const pct = t.total_cells ? Math.round((t.filled_cells / t.total_cells) * 100) : 0;
  const labels = Object.fromEntries(t.field_labels.map((f) => [f.field, f.label]));

  /* הכותרת אומרת את האמת הלא-נעימה בגדול: כמה מהטבלה באמת מלא.
     "הטבלה ריקה" הוא מצב שאפשר לעבוד איתו — רק אם רואים מה ריק. */
  const alerts = [];
  if (!t.filled_cells) {
    alerts.push('הטבלה ריקה לגמרי — כל סכום יצא 0. המידות, הפריסה והבזבוז אמיתיים גם עכשיו.');
  } else if (!t.ready) {
    alerts.push('אין עדיין מחיר פלטה או מחיר חיתוך באף שורה — כל סכום יצא 0.');
  }
  if (t.error) alerts.push('טעינת הטבלה נכשלה: ' + t.error);
  if (!data.engine.gate_on) alerts.push('השער כבוי — הכתובת פתוחה לכל אחד.');
  if (data.engine.session_secret_ephemeral) alerts.push('אין SESSION_SECRET — כל הפעלה מחדש תנתק את כולם.');
  if (t.ready && !data.engine.disk_present) alerts.push('הטבלה קיימת בזיכרון בלבד — הפעלה מחדש תמחק אותה.');
  if (t.ready && data.engine.disk_present && !data.engine.disk_matches_memory) {
    alerts.push('הטבלה בזיכרון שונה מזו שעל הדיסק.');
  }
  if (!data.backup.reporting) alerts.push('הגיבוי אינו מדווח — ייתכן שהטיימר אינו רץ.');
  else if (data.backup.stale) alerts.push(`הגיבוי לא רץ מזה ${int(data.backup.minutes_since_run || 0)} דקות.`);
  if (!data.users.count) alerts.push('אין עדיין משתמשים — הכניסה אפשרית רק בסיסמת המערכת.');

  host.innerHTML = `
    <div class="card">
      <h2>מצב המערכת</h2>
      <div class="stats">
        <div class="stat total"><div class="k">שדות מחיר שמולאו</div><div class="v">${pct}%</div></div>
        <div class="stat"><div class="k">מתוך</div><div class="v">${int(t.filled_cells)}/${int(t.total_cells)}</div></div>
        <div class="stat"><div class="k">מקור הטבלה</div><div class="v" style="font-size:15px">${esc(t.source || '—')}</div></div>
        <div class="stat"><div class="k">משתמשים</div><div class="v">${int(data.users.count)}</div></div>
        <div class="stat"><div class="k">גיבוי אחרון</div><div class="v" style="font-size:15px">${
          data.backup.reporting && data.backup.last_run ? esc(data.backup.last_run.replace('T', ' ')) : '—'
        }</div></div>
      </div>
      ${alerts.length
        ? `<ul class="warnings" style="margin-top:14px">${alerts.map((a) => `<li>${esc(a)}</li>`).join('')}</ul>`
        : '<p class="hint" style="margin-top:14px">אין התראות פתוחות.</p>'}
    </div>

    <div class="card">
      <h2>מה עוד חסר בטבלה</h2>
      <p class="hint" style="margin-top:-8px">
        שורה עם מחיר פלטה בלבד עדיין מייצרת הצעה חלקית, ולכן הספירה היא על תאים ולא על שורות.
      </p>
      <div class="scroll-x"><table>
        <thead><tr>
          <th>חומר</th><th class="num">עוביים</th><th class="num">עם מחיר</th>
          <th class="num">תאים שמולאו</th><th>שדות שריקים בכל השורות</th>
        </tr></thead>
        <tbody>${t.materials.map((m) => {
          const allEmpty = Object.entries(m.empty_fields)
            .filter(([, count]) => count === m.rows)
            .map(([field]) => labels[field] || field);
          return `
          <tr>
            <td>${esc(m.name)}</td>
            <td class="num">${int(m.rows)}</td>
            <td class="num">${int(m.priced_rows)}</td>
            <td class="num">${int(m.filled_cells)}/${int(m.total_cells)}</td>
            <td class="small">${allEmpty.length ? esc(allEmpty.join(' · ')) : '—'}</td>
          </tr>`;
        }).join('')}
        </tbody>
      </table></div>
    </div>`;
}

/* ---------- העלאת קבצים ---------- */

const drop = $('#drop');
const fileInput = $('#file');

drop.addEventListener('click', () => fileInput.click());
drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('hot'); });
drop.addEventListener('dragleave', () => drop.classList.remove('hot'));
drop.addEventListener('drop', (e) => {
  e.preventDefault();
  drop.classList.remove('hot');
  handleFiles(e.dataTransfer.files);
});
fileInput.addEventListener('change', () => { handleFiles(fileInput.files); fileInput.value = ''; });

async function handleFiles(files) {
  for (const file of files) {
    const form = new FormData();
    form.append('file', file);
    try {
      const data = await api('/api/upload', { method: 'POST', body: form });
      addPart(data);
    } catch (err) {
      showBanner('bad', `${file.name}: ${err.message}`);
    }
  }
}

/* ---------- קלט ידני ---------- */

$('#m-shape').addEventListener('change', () => {
  const isCircle = $('#m-shape').value === 'circle';
  $('#m-rect-fields').style.display = isCircle ? 'none' : 'flex';
  $('#m-circle-fields').style.display = isCircle ? 'block' : 'none';
});

$('#m-add').addEventListener('click', async () => {
  const shape = $('#m-shape').value;
  const count = Math.max(0, parseInt($('#m-holes').value || '0', 10));
  const holeD = parseFloat($('#m-hole-d').value || '0');
  const spec = {
    name: $('#m-name').value.trim() || (shape === 'circle' ? 'דיסקית' : 'לוח'),
    shape,
    holes: Array.from({ length: count }, () => ({ diameter_mm: holeD })),
  };
  if (shape === 'circle') {
    spec.diameter_mm = parseFloat($('#m-d').value);
  } else {
    spec.width_mm = parseFloat($('#m-w').value);
    spec.height_mm = parseFloat($('#m-h').value);
  }
  try {
    const data = await api('/api/manual', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(spec),
    });
    addPart(data);
  } catch (err) {
    showBanner('bad', err.message);
  }
});

/* ---------- ניהול החלקים ---------- */

function addPart(data) {
  const materials = state.config.materials || [];
  const first = materials[0];
  state.parts.push({
    uid: ++state.seq,
    data,
    material_key: first ? first.key : '',
    thickness_mm: first ? first.thicknesses[0] : 0,
    quantity: 1,
    bend_count: 0,
    bend_length_mm: 0,
  });
  if (data.warnings && data.warnings.length) {
    showBanner('warn', `${data.name}: ${data.warnings.join(' · ')}`);
  }
  renderParts();
}

$('#clear').addEventListener('click', () => { state.parts = []; $('#quote-out').innerHTML = ''; renderParts(); });

function renderParts() {
  const host = $('#parts');
  $('#part-count').textContent = state.parts.length;
  const materials = (state.config && state.config.materials) || [];

  if (!state.parts.length) {
    host.innerHTML = '<p class="empty">עדיין לא נוספו חלקים.</p>';
    $('#calc').disabled = true;
    $('#calc-note').textContent = '';
    return;
  }

  if (!materials.length) {
    $('#calc').disabled = true;
    $('#calc-note').textContent = 'אין חומרים בטבלת התמחור.';
  } else {
    const split = state.parts.filter((p) => (p.data.pieces || 1) > 1).length;
    $('#calc').disabled = false;
    $('#calc-note').textContent = split
      ? `${split} חלקים גדולים מפלטה — ייוצרו בכמה חתיכות עם ריתוך.`
      : '';
  }

  host.innerHTML = state.parts.map((p) => {
    const d = p.data;
    const mat = materials.find((m) => m.key === p.material_key) || materials[0];
    const thicknesses = mat ? mat.thicknesses : [];
    const pieces = d.pieces || 1;
    const isSplit = pieces > 1;
    return `
      <div class="part" data-uid="${p.uid}">
        ${thumbSvg(d)}
        <div>
          <div class="name">${esc(d.name)}
            ${isSplit
              ? `<span class="pill warn">${pieces} חתיכות + ריתוך</span>`
              : `<span class="pill ok">${d.rotated_to_fit ? 'נכנס בסיבוב' : 'נכנס בפלטה'}</span>`}
            <span class="pill">${d.source === 'dxf' ? 'DXF' : 'ידני'}</span>
          </div>
          <div class="meta">
            ${fmt(d.bbox.width_mm, 1)} × ${fmt(d.bbox.height_mm, 1)} מ"מ ·
            שטח נטו ${fmt(d.net_area_mm2 / 100, 1)} סמ"ר ·
            חיתוך ${fmt(d.cut_length_mm / 1000, 2)} מ' ·
            ${d.pierces} ניקובים · ${d.holes} חורים
            ${d.units_detected && d.units_detected !== 'unknown' ? ` · יחידות: ${esc(d.units_detected)}` : ''}
          </div>
          ${isSplit ? `<div class="small" style="color:var(--warn)">גדול מפלטה — ייחתך ל-${d.split_columns}×${d.split_rows} חתיכות ויולחם, ${fmt(d.weld_length_mm / 1000, 2)} מ' תפר.</div>` : ''}
          <div class="controls">
            <div><label>חומר</label>
              <select data-field="material_key">
                ${materials.map((m) => `<option value="${esc(m.key)}" ${m.key === p.material_key ? 'selected' : ''}>${esc(m.name)}</option>`).join('')}
              </select>
            </div>
            <div><label>עובי (מ"מ)</label>
              <select data-field="thickness_mm">
                ${thicknesses.map((t) => `<option value="${t}" ${t === p.thickness_mm ? 'selected' : ''}>${t}</option>`).join('')}
              </select>
            </div>
            <div style="max-width:90px"><label>כמות</label>
              <input type="number" min="1" step="1" value="${p.quantity}" data-field="quantity">
            </div>
            <div style="max-width:90px"><label>כיפופים</label>
              <input type="number" min="0" step="1" value="${p.bend_count}" data-field="bend_count">
            </div>
            <div style="max-width:120px"><label>אורך כיפוף (מ"מ)</label>
              <input type="number" min="0" step="1" value="${p.bend_length_mm || ''}" placeholder="לא חובה" data-field="bend_length_mm">
            </div>
            <div style="max-width:60px"><button class="ghost" data-action="remove">הסר</button></div>
          </div>
        </div>
      </div>`;
  }).join('');

  host.querySelectorAll('.part').forEach((el) => {
    const uid = Number(el.dataset.uid);
    const part = state.parts.find((p) => p.uid === uid);
    el.querySelectorAll('[data-field]').forEach((input) => {
      input.addEventListener('change', () => {
        const field = input.dataset.field;
        if (field === 'material_key') {
          part.material_key = input.value;
          const mat = materials.find((m) => m.key === part.material_key);
          // העובי הנבחר עשוי לא להתקיים בחומר החדש — נופלים לעובי הראשון
          // שיש לו מחיר, במקום לשלוח לשרת צירוף שאין לו שורה בטבלה.
          if (mat && !mat.thicknesses.includes(part.thickness_mm)) part.thickness_mm = mat.thicknesses[0];
          renderParts();
        } else if (field === 'thickness_mm') {
          part.thickness_mm = parseFloat(input.value);
        } else if (field === 'bend_count') {
          part.bend_count = Math.max(0, parseInt(input.value || '0', 10));
        } else if (field === 'bend_length_mm') {
          part.bend_length_mm = Math.max(0, parseFloat(input.value || '0'));
        } else {
          part.quantity = Math.max(1, parseInt(input.value || '1', 10));
        }
      });
    });
    const removeBtn = el.querySelector('[data-action="remove"]');
    if (removeBtn) removeBtn.addEventListener('click', () => {
      state.parts = state.parts.filter((p) => p.uid !== uid);
      renderParts();
    });
  });
}

/* ---------- ציור ---------- */

function thumbSvg(d, size = 76) {
  const w = d.bbox.width_mm || 1;
  const h = d.bbox.height_mm || 1;
  const scale = (size - 10) / Math.max(w, h);
  const paths = (d.contours || []).map((c) => {
    const pts = c.points.map(([x, y]) => `${((x - d.bbox.min_x) * scale + 5).toFixed(2)},${((h - (y - d.bbox.min_y)) * scale + 5).toFixed(2)}`).join(' ');
    const color = c.hole ? '#d9541e' : '#3f74a8';
    return c.closed
      ? `<polygon points="${pts}" fill="none" stroke="${color}" stroke-width="1.2"/>`
      : `<polyline points="${pts}" fill="none" stroke="#9aa3ae" stroke-width="1" stroke-dasharray="3 2"/>`;
  }).join('');
  return `<svg class="thumb" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">${paths}</svg>`;
}

function plateSvg(layout) {
  const W = layout.plate.width_mm;
  const H = layout.plate.height_mm;
  const margin = layout.plate.edge_margin_mm;
  const rects = layout.placements.map((p) => {
    // מערכת הצירים של האורז מתחילה בפינת אזור העבודה, ולכן מוסיפים את השוליים.
    const x = p.x + margin;
    const y = H - margin - p.y - p.h;
    return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${p.w.toFixed(1)}" height="${p.h.toFixed(1)}"
            fill="#cfe0f0" stroke="#3f74a8" stroke-width="3"><title>${esc(p.item_id)}${p.rotated ? ' (מסובב)' : ''}</title></rect>`;
  }).join('');
  return `
    <svg class="plate" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
      <rect x="0" y="0" width="${W}" height="${H}" fill="#fafbfc" stroke="#c7cdd4" stroke-width="4"/>
      <rect x="${margin}" y="${margin}" width="${W - 2 * margin}" height="${H - 2 * margin}"
            fill="none" stroke="#dde1e6" stroke-width="2" stroke-dasharray="14 10"/>
      ${rects}
    </svg>`;
}

/* ---------- תמחור ---------- */

$('#calc').addEventListener('click', async () => {
  const btn = $('#calc');
  btn.disabled = true;
  btn.textContent = 'מחשב…';
  try {
    const payload = {
      parts: state.parts.map((p) => ({
        geometry_id: p.data.geometry_id,
        name: p.data.name,
        material_key: p.material_key,
        thickness_mm: p.thickness_mm,
        quantity: p.quantity,
        bend_count: p.bend_count || 0,
        bend_length_mm: p.bend_length_mm || 0,
      })),
    };
    const quote = await api('/api/quote', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    renderQuote(quote);
  } catch (err) {
    $('#quote-out').innerHTML = `<div class="card"><h2>התמחור לא בוצע</h2><p style="color:var(--bad)">${esc(err.message)}</p></div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'חשב הצעת מחיר';
  }
});

function renderQuote(q) {
  /* התאריך נכתב לנייר ולא למסך. הגיליון הוא פנימי — הפירוק, הבזבוז
     והפריסה — וההצעה שיוצאת ללקוח נוצרת ב-CRM. */
  const dateEl = $('#print-date');
  if (dateEl) dateEl.textContent = new Date().toLocaleString('he-IL');

  const cur = q.currency === 'ILS' ? '₪' : q.currency;
  const weight = q.lines.reduce((s, l) => s + l.weight_kg, 0);

  const stats = `
    <div class="stats">
      <div class="stat total"><div class="k">סה"כ לתשלום</div><div class="v">${cur}${fmt(q.total)}</div></div>
      <div class="stat"><div class="k">לפני מע"מ</div><div class="v">${cur}${fmt(q.total_before_vat)}</div></div>
      <div class="stat"><div class="k">חלקים</div><div class="v">${int(q.lines.reduce((s, l) => s + l.quantity, 0))}</div></div>
      <div class="stat"><div class="k">פלטות</div><div class="v">${int(q.groups.reduce((s, g) => s + g.plates_used, 0))}</div></div>
      <div class="stat"><div class="k">משקל</div><div class="v">${fmt(weight, 1)} ק"ג</div></div>
    </div>`;

  const rejected = q.rejected.length ? `
    <div class="card">
      <h2>לא ניתן לייצור</h2>
      <p class="hint" style="margin-top:-8px">אי-אפשרות טכנית, לא שאלת מחיר. אי אפשר לאשר את ההזמנה לפני תיקון.</p>
      <ul class="rejects">${q.rejected.map((r) => `<li><b>${esc(r.part_name)}</b> — ${esc(r.reason)}</li>`).join('')}</ul>
    </div>` : '';

  const warnings = q.warnings.length ? `
    <div class="card"><h2>שים לב</h2><ul class="warnings">${q.warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul></div>` : '';

  // מה שמופיע תמיד מול מה שנפתח במתג. הקריטריון: מה שהלקוח שואל עליו
  // נשאר, ומה ששימושי לבדיקה תפעולית מסומן detail.
  const lines = `
    <div class="card">
      <div class="table-head">
        <h2 style="margin:0">פירוט ההצעה</h2>
        <div style="display:flex; gap:12px; align-items:center">
          <label class="toggle"><input type="checkbox" id="full-cols"> פירוט מלא</label>
          <button class="ghost" id="print-sheet">הדפס גיליון</button>
        </div>
      </div>
      <div class="scroll-x"><table id="lines-table">
        <thead><tr>
          <th>חלק</th><th>חומר</th><th class="num detail">עובי</th><th class="num">מידות</th>
          <th class="num">כמות</th><th class="num detail">שטח נטו</th><th class="num detail">שטח מחויב</th>
          <th class="num detail">אורך חיתוך</th><th class="num detail">ניקובים</th><th class="num detail">משקל</th>
          <th class="num detail">בזבוז</th><th class="detail">מדרגה</th>
          <th class="num detail">חומר</th><th class="num detail">חיתוך</th><th class="num detail">ניקוב</th>
          <th class="num detail">ריתוך</th><th class="num detail">כיפוף</th>
          <th class="num">ליחידה</th><th class="num">סה"כ</th>
        </tr></thead>
        <tbody>${q.lines.map((l) => `
          <tr>
            <td>${esc(l.part_name)}${l.pieces > 1 ? ` <span class="pill warn">${l.pieces} חתיכות</span>` : ''}</td>
            <td>${esc(l.material_name)}</td>
            <td class="num detail">${fmt(l.thickness_mm, 1)}</td>
            <td class="num">${fmt(l.width_mm, 0)}×${fmt(l.height_mm, 0)}</td>
            <td class="num">${int(l.quantity)}</td>
            <td class="num detail">${fmt(l.net_area_mm2 / 100, 1)} סמ"ר</td>
            <td class="num detail">${fmt(l.billed_area_mm2 / 100, 1)} סמ"ר</td>
            <td class="num detail">${fmt(l.cut_length_mm / 1000, 2)} מ'</td>
            <td class="num detail">${int(l.pierces)}</td>
            <td class="num detail">${fmt(l.weight_kg, 2)}</td>
            <td class="num detail">${fmt(l.waste_pct, 1)}%</td>
            <td class="detail">${esc(l.waste_tier_label)}</td>
            <td class="num detail">${cur}${fmt(l.material_cost)}${l.plate_floor_applied ? ' <span class="pill warn">רצפת פלטה</span>' : ''}</td>
            <td class="num detail">${cur}${fmt(l.cutting_cost)}</td>
            <td class="num detail">${cur}${fmt(l.piercing_cost)}</td>
            <td class="num detail">${cur}${fmt(l.welding_cost)}${l.weld_length_mm ? ` <span class="muted">(${fmt(l.weld_length_mm / 1000, 2)} מ')</span>` : ''}</td>
            <td class="num detail">${cur}${fmt(l.bending_cost)}${l.bend_count ? ` <span class="muted">(${int(l.bend_count)} כיפופים)</span>` : ''}</td>
            <td class="num">${cur}${fmt(l.unit_price)}${l.min_charge_applied ? ' <span class="pill warn">מינימום</span>' : ''}</td>
            <td class="num"><b>${cur}${fmt(l.line_total)}</b></td>
          </tr>`).join('')}
        </tbody>
      </table></div>
      <p class="sum-line">
        <span>סיכום חלקים${q.order_setup_fee ? ` + הקמה ${cur}${fmt(q.order_setup_fee)}` : ''}${q.margin_amount ? ` + מרווח ${cur}${fmt(q.margin_amount)}` : ''}${q.min_order_applied ? ' (הופעל מינימום הזמנה)' : ''}</span>
        <b>${cur}${fmt(q.total_before_vat)}</b>
      </p>
    </div>`;

  const groups = q.groups.map((g) => `
    <div class="card">
      <h2>${esc(g.material_name)} ${fmt(g.thickness_mm, 1)} מ"מ</h2>
      <div class="stats">
        <div class="stat"><div class="k">פלטות</div><div class="v">${int(g.plates_used)}</div></div>
        <div class="stat"><div class="k">ניצולת</div><div class="v">${fmt(g.utilization_pct, 1)}%</div></div>
        <div class="stat"><div class="k">בזבוז מחויב</div><div class="v">${fmt(g.effective_waste_pct, 1)}%</div></div>
        <div class="stat"><div class="k">מכפיל</div><div class="v">×${fmt(g.tier.multiplier, 2)}</div></div>
      </div>
      ${g.plate_floor_applied ? `<p class="hint" style="color:var(--warn)">הופעלה רצפת פלטה: החיוב הועלה ל-${fmt(g.consumed_area_mm2 / 1e6, 2)} מ"ר — החומר שנצרך בפועל.</p>` : ''}
      ${g.layouts.map((layout) => `
        <div class="layout-wrap">
          <div class="cap">פלטה ${layout.index + 1} · ${int(layout.part_count)} חלקים · ניצולת ${fmt(layout.utilization_pct, 1)}%${layout.remnants.length ? ` · ${layout.remnants.length} שאריות שמישות (הגדולה: ${fmt(layout.remnants[0].width_mm, 0)}×${fmt(layout.remnants[0].height_mm, 0)})` : ''}</div>
          ${plateSvg(layout)}
        </div>`).join('')}
      ${g.unplaced.length ? `<p class="small" style="color:var(--bad)">${g.unplaced.length} מופעים לא נכנסו לפריסה.</p>` : ''}
    </div>`).join('');

  const note = state.config.tariff_ready ? '' :
    `<p class="banner warn" style="border-radius:8px; border:1px solid #ecd9a4; margin:0 0 16px">הסכומים כאן 0 — טבלת התמחור עדיין ריקה.</p>`;

  $('#quote-out').innerHTML = note + `<div class="card">${stats}</div>` + rejected + warnings + lines + groups;

  const printBtn = $('#print-sheet');
  if (printBtn) printBtn.addEventListener('click', () => window.print());

  const toggle = $('#full-cols');
  if (toggle) {
    toggle.addEventListener('change', () => {
      $('#lines-table').classList.toggle('full', toggle.checked);
    });
  }
}

/* ---------- עורך הטבלה ---------- */

async function loadTariffEditor() {
  try {
    const data = await api('/api/tariff');
    $('#tariff-json').value = JSON.stringify(data.raw, null, 2);
    $('#tariff-status').innerHTML = data.ready
      ? '<span class="pill ok">טבלה פעילה עם מחירים</span>'
      : '<span class="pill warn">תבנית בלבד — כל המחירים 0</span>';
  } catch (err) {
    $('#tariff-msg').textContent = err.message;
  }
}

$('#tariff-reload').addEventListener('click', loadTariffEditor);

$('#tariff-save').addEventListener('click', async () => {
  const msg = $('#tariff-msg');
  msg.style.color = 'var(--muted)';
  msg.textContent = 'שומר…';
  let parsed;
  try {
    parsed = JSON.parse($('#tariff-json').value);
  } catch (err) {
    msg.style.color = 'var(--bad)';
    msg.textContent = 'ה-JSON אינו תקין: ' + err.message;
    return;
  }
  try {
    const res = await api('/api/tariff', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(parsed),
    });
    msg.style.color = res.ready ? 'var(--ok)' : 'var(--warn)';
    msg.textContent = (res.ready ? 'הטבלה פעילה. ' : 'נשמר, אבל כל המחירים עדיין 0. ') + res.persist_note;
    state.config = await api('/api/config');
    $('#tariff-source').textContent = 'טבלה: ' + state.config.tariff_source;
    if (state.config.tariff_ready) $('#banner').className = 'banner hidden';
    loadTariffEditor();
    renderParts();
  } catch (err) {
    msg.style.color = 'var(--bad)';
    msg.textContent = err.message;
  }
});

boot();
