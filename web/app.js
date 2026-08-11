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
      `פלטה ${cfg.plate.width_mm}×${cfg.plate.height_mm} מ"מ · שוליים ${cfg.plate.edge_margin_mm} · שטח עבודה ${cfg.plate.usable_width_mm}×${cfg.plate.usable_height_mm}`;
  }
  $('#tariff-source').textContent = 'טבלה: ' + (cfg.tariff_source || '—');

  if (cfg.tariff_error) {
    showBanner('bad', 'טבלת התמחור אינה תקינה: ' + cfg.tariff_error);
  } else if (!cfg.tariff_ready) {
    showBanner('warn',
      'טבלת התמחור עדיין לא הוזנה — נטענה התבנית שכל המחירים בה 0. ' +
      'החישוב הגיאומטרי, אילוץ הפלטה ואחוזי הבזבוז אמיתיים לחלוטין, אבל כל סכום כספי יצא 0 עד שינון ימלא את הטבלה בלשונית "טבלת התמחור".');
  }

  renderParts();
  loadTariffEditor();
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
  });
});

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
    const blocked = state.parts.some((p) => !p.data.manufacturable);
    $('#calc').disabled = false;
    $('#calc-note').textContent = blocked ? 'יש חלק שאינו ניתן לייצור — הוא לא ייכלל בהצעה.' : '';
  }

  host.innerHTML = state.parts.map((p) => {
    const d = p.data;
    const mat = materials.find((m) => m.key === p.material_key) || materials[0];
    const thicknesses = mat ? mat.thicknesses : [];
    return `
      <div class="part ${d.manufacturable ? '' : 'bad'}" data-uid="${p.uid}">
        ${thumbSvg(d)}
        <div>
          <div class="name">${esc(d.name)}
            <span class="pill ${d.manufacturable ? 'ok' : 'bad'}">${d.manufacturable ? (d.rotated_to_fit ? 'נכנס בסיבוב' : 'ניתן לייצור') : 'לא ניתן לייצור'}</span>
            <span class="pill">${d.source === 'dxf' ? 'DXF' : 'ידני'}</span>
          </div>
          <div class="meta">
            ${fmt(d.bbox.width_mm, 1)} × ${fmt(d.bbox.height_mm, 1)} מ"מ ·
            שטח נטו ${fmt(d.net_area_mm2 / 100, 1)} סמ"ר ·
            חיתוך ${fmt(d.cut_length_mm / 1000, 2)} מ' ·
            ${d.pierces} ניקובים · ${d.holes} חורים
            ${d.units_detected && d.units_detected !== 'unknown' ? ` · יחידות: ${esc(d.units_detected)}` : ''}
          </div>
          ${d.manufacturable ? '' : `<div class="small" style="color:var(--bad)">${esc(d.manufacturability_reason)}</div>`}
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

function thumbSvg(d, size = 92) {
  const w = d.bbox.width_mm || 1;
  const h = d.bbox.height_mm || 1;
  const scale = (size - 10) / Math.max(w, h);
  const paths = (d.contours || []).map((c) => {
    const pts = c.points.map(([x, y]) => `${((x - d.bbox.min_x) * scale + 5).toFixed(2)},${((h - (y - d.bbox.min_y)) * scale + 5).toFixed(2)}`).join(' ');
    const color = c.hole ? '#ff8a3d' : '#4a90d9';
    return c.closed
      ? `<polygon points="${pts}" fill="none" stroke="${color}" stroke-width="1.2"/>`
      : `<polyline points="${pts}" fill="none" stroke="#97a1ae" stroke-width="1" stroke-dasharray="3 2"/>`;
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
            fill="#2b4c73" stroke="#4a90d9" stroke-width="3"><title>${esc(p.item_id)}${p.rotated ? ' (מסובב)' : ''}</title></rect>`;
  }).join('');
  return `
    <svg class="plate" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
      <rect x="0" y="0" width="${W}" height="${H}" fill="#12161b" stroke="#3a434f" stroke-width="4"/>
      <rect x="${margin}" y="${margin}" width="${W - 2 * margin}" height="${H - 2 * margin}"
            fill="none" stroke="#2a323c" stroke-width="2" stroke-dasharray="14 10"/>
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
  const cur = q.currency === 'ILS' ? '₪' : q.currency;
  const weight = q.lines.reduce((s, l) => s + l.weight_kg, 0);

  const stats = `
    <div class="stats">
      <div class="stat"><div class="k">סה"כ חלקים</div><div class="v">${int(q.lines.reduce((s, l) => s + l.quantity, 0))}</div></div>
      <div class="stat"><div class="k">משקל</div><div class="v">${fmt(weight, 1)} ק"ג</div></div>
      <div class="stat"><div class="k">פלטות</div><div class="v">${int(q.groups.reduce((s, g) => s + g.plates_used, 0))}</div></div>
      <div class="stat"><div class="k">לפני מע"מ</div><div class="v">${cur}${fmt(q.total_before_vat)}</div></div>
      <div class="stat"><div class="k">מע"מ</div><div class="v">${cur}${fmt(q.vat_amount)}</div></div>
      <div class="stat total"><div class="k">סה"כ לתשלום</div><div class="v">${cur}${fmt(q.total)}</div></div>
    </div>`;

  const rejected = q.rejected.length ? `
    <div class="card">
      <h2>לא ניתן לייצור</h2>
      <p class="small muted" style="margin-top:0">זו אי-אפשרות טכנית, לא שאלת מחיר. אי אפשר לאשר את ההזמנה לפני שהחלקים האלה מתוקנים.</p>
      <ul class="rejects">${q.rejected.map((r) => `<li><b>${esc(r.part_name)}</b> — ${esc(r.reason)}</li>`).join('')}</ul>
    </div>` : '';

  const warnings = q.warnings.length ? `
    <div class="card"><h2>שים לב</h2><ul class="warnings">${q.warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul></div>` : '';

  const lines = `
    <div class="card">
      <h2>פירוט ההצעה</h2>
      <div class="scroll-x"><table>
        <thead><tr>
          <th>חלק</th><th>חומר</th><th class="num">עובי</th><th class="num">מידות</th>
          <th class="num">כמות</th><th class="num">שטח נטו</th><th class="num">שטח מחויב</th>
          <th class="num">אורך חיתוך</th><th class="num">ניקובים</th><th class="num">משקל</th>
          <th class="num">בזבוז</th><th>מדרגה</th>
          <th class="num">חומר</th><th class="num">חיתוך</th><th class="num">ניקוב</th>
          <th class="num">ליחידה</th><th class="num">סה"כ שורה</th>
        </tr></thead>
        <tbody>${q.lines.map((l) => `
          <tr>
            <td>${esc(l.part_name)}</td>
            <td>${esc(l.material_name)}</td>
            <td class="num">${fmt(l.thickness_mm, 1)}</td>
            <td class="num">${fmt(l.width_mm, 0)}×${fmt(l.height_mm, 0)}</td>
            <td class="num">${int(l.quantity)}</td>
            <td class="num">${fmt(l.net_area_mm2 / 100, 1)} סמ"ר</td>
            <td class="num">${fmt(l.billed_area_mm2 / 100, 1)} סמ"ר</td>
            <td class="num">${fmt(l.cut_length_mm / 1000, 2)} מ'</td>
            <td class="num">${int(l.pierces)}</td>
            <td class="num">${fmt(l.weight_kg, 2)}</td>
            <td class="num">${fmt(l.waste_pct, 1)}%</td>
            <td>${esc(l.waste_tier_label)}</td>
            <td class="num">${cur}${fmt(l.material_cost)}</td>
            <td class="num">${cur}${fmt(l.cutting_cost)}</td>
            <td class="num">${cur}${fmt(l.piercing_cost)}</td>
            <td class="num">${cur}${fmt(l.unit_price)}${l.min_charge_applied ? ' <span class="pill warn">מינימום</span>' : ''}</td>
            <td class="num"><b>${cur}${fmt(l.line_total)}</b></td>
          </tr>`).join('')}
        </tbody>
        <tfoot><tr>
          <td colspan="16">סיכום חלקים${q.order_setup_fee ? ` + הקמה ${cur}${fmt(q.order_setup_fee)}` : ''}${q.margin_amount ? ` + מרווח ${cur}${fmt(q.margin_amount)}` : ''}${q.min_order_applied ? ' (הופעל מינימום הזמנה)' : ''}</td>
          <td class="num"><b>${cur}${fmt(q.total_before_vat)}</b></td>
        </tr></tfoot>
      </table></div>
    </div>`;

  const groups = q.groups.map((g) => `
    <div class="card">
      <h2>${esc(g.material_name)} ${fmt(g.thickness_mm, 1)} מ"מ</h2>
      <div class="stats">
        <div class="stat"><div class="k">פלטות</div><div class="v">${int(g.plates_used)}</div></div>
        <div class="stat"><div class="k">ניצולת</div><div class="v">${fmt(g.utilization_pct, 1)}%</div></div>
        <div class="stat"><div class="k">בזבוז מחויב</div><div class="v">${fmt(g.effective_waste_pct, 1)}%</div></div>
        <div class="stat"><div class="k">מכפיל מדרגה</div><div class="v">×${fmt(g.tier.multiplier, 2)}</div></div>
        <div class="stat"><div class="k">שארית שמישה</div><div class="v">${fmt(g.reusable_area_mm2 / 1e6, 2)} מ"ר</div></div>
        <div class="stat"><div class="k">פלטות נטו</div><div class="v">${fmt(g.effective_plates, 2)}</div></div>
      </div>
      <p class="small muted" style="margin:6px 0 0">
        מדרגה: ${esc(g.tier.label || 'עד ' + g.tier.max_waste_pct + '%')} ·
        בזבוז גולמי מול הפלטה המלאה ${fmt(g.raw_waste_pct, 1)}% ·
        הבזבוז המחויב נמדד מול החומר שנצרך בפועל, אחרי זיכוי שאריות שחוזרות למלאי.
      </p>
      ${g.layouts.map((layout) => `
        <div class="layout-wrap">
          <div class="cap">פלטה ${layout.index + 1} · ${int(layout.part_count)} חלקים · ניצולת ${fmt(layout.utilization_pct, 1)}%${layout.remnants.length ? ` · ${layout.remnants.length} שאריות שמישות (הגדולה: ${fmt(layout.remnants[0].width_mm, 0)}×${fmt(layout.remnants[0].height_mm, 0)})` : ''}</div>
          ${plateSvg(layout)}
        </div>`).join('')}
      ${g.unplaced.length ? `<p class="small" style="color:var(--bad)">${g.unplaced.length} מופעים לא נכנסו לפריסה.</p>` : ''}
    </div>`).join('');

  const note = state.config.tariff_ready ? '' :
    `<p class="banner warn" style="border-radius:8px; margin:0 0 18px">הסכומים כאן הם 0 כי טבלת התמחור עדיין ריקה. המידות, הפריסה ואחוזי הבזבוז אמיתיים.</p>`;

  $('#quote-out').innerHTML = note + `<div class="card">${stats}<p class="small muted" style="margin:6px 0 0">מקור הטבלה: ${esc(q.tariff_source)}</p></div>` + rejected + warnings + lines + groups;
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
