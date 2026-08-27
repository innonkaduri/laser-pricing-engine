/* ציור החלק — מהמתארים שהשרת החזיר, ולא משרטוט משלנו.
 *
 * זו הנקודה שלמענה הקובץ הזה קיים בכלל: **התמונה היא הגיאומטריה
 * שתומחרה.** אין כאן "איור של פלטת בסיס"; יש את אותם מתארים בדיוק
 * שמהם חושבו השטח, אורך החיתוך ומספר הניקובים. איור נפרד היה נפרד
 * מהמנוע בשקט ברגע שמישהו משנה בנאי — והמסך היה ממשיך להראות את
 * החלק הישן במחיר החדש.
 *
 * הצירים: המנוע עובד ב-Y-למעלה כמו כל CAD, ו-SVG עובד ב-Y-למטה.
 * ההיפוך נעשה ב-transform אחד על הקבוצה ולא על כל נקודה, כדי
 * שהנתיבים יישארו זהים למספרים שהשרת שלח.
 */

export function contourPath(points, closed) {
  if (!points || !points.length) return '';
  const head = `M ${points[0][0]} ${points[0][1]}`;
  const rest = points.slice(1).map((p) => `L ${p[0]} ${p[1]}`).join(' ');
  return `${head} ${rest}${closed ? ' Z' : ''}`;
}

/* מצייר גיאומטריה לתוך <svg> נתון.
 *
 * `geometry` הוא מה ש-/api/catalog/<id>/preview מחזיר: bbox, contours
 * ואולי fold_lines. `opts.dims` מוסיף קווי מידה — הם עולים מקום, ולכן
 * הם כבויים בתמונה ממוזערת ודלוקים בתצוגה הגדולה.
 */
export function drawPart(svg, geometry, opts = {}) {
  const { dims = false, pad = 0.08 } = opts;
  const box = geometry.bbox;
  const w = box.width_mm;
  const h = box.height_mm;
  if (!(w > 0 && h > 0)) return;

  /* השוליים יחסיים לצלע הגדולה, כדי שפס צר וארוך לא יקבל שוליים
     שמבליעים אותו לגמרי ברוחב. */
  const margin = Math.max(w, h) * pad + (dims ? Math.max(w, h) * 0.09 : 0);
  const minX = box.min_x - margin;
  const minY = box.min_y - margin;
  svg.setAttribute('viewBox', `${minX} ${minY} ${w + margin * 2} ${h + margin * 2}`);
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');

  /* קו בעובי קבוע בפיקסלים בלי קשר לקנה המידה: פלטה של 1400 מ"מ
     ולוחית של 60 מ"מ חייבות להיראות מצוירות באותו עט. */
  const stroke = Math.max(w, h) / 190;

  const outer = [];
  const holes = [];
  for (const c of geometry.contours || []) {
    (c.hole ? holes : outer).push(contourPath(c.points, c.closed));
  }

  const folds = (geometry.fold_lines || [])
    .map(([x1, y1, x2, y2]) => `M ${x1} ${y1} L ${x2} ${y2}`)
    .join(' ');

  const flipY = box.min_y * 2 + h; // היפוך סביב מרכז ה-bbox
  const parts = [
    `<g transform="translate(0 ${flipY}) scale(1 -1)" fill="none"`,
    ` stroke-linejoin="round" stroke-linecap="round">`,
    outer.length
      ? `<path d="${outer.join(' ')}" fill="var(--part-fill, #dbe3ec)"` +
        ` stroke="var(--part-line, #0a0e13)" stroke-width="${stroke}"/>`
      : '',
    holes.length
      ? `<path d="${holes.join(' ')}" fill="var(--part-hole, #ffffff)"` +
        ` stroke="var(--part-line, #0a0e13)" stroke-width="${stroke}"/>`
      : '',
    folds
      ? `<path d="${folds}" stroke="var(--part-fold, #d84315)" stroke-width="${stroke}"` +
        ` stroke-dasharray="${stroke * 5} ${stroke * 4}"/>`
      : '',
    `</g>`,
  ];

  if (dims) parts.push(dimensions(box, stroke));
  svg.innerHTML = parts.join('');
}

/* קווי מידה מתחת ומימין. הטקסט אינו מתהפך עם הצירים, ולכן הוא יושב
   מחוץ לקבוצה ההפוכה ומקבל את הקואורדינטות שלו ישירות. */
function dimensions(box, stroke) {
  const w = box.width_mm;
  const h = box.height_mm;
  const off = Math.max(w, h) * 0.055;
  const tick = off * 0.35;
  const size = Math.max(w, h) * 0.05;
  const y = box.min_y + h + off; // ב-SVG זה *מתחת* לחלק, כי Y יורד
  const x = box.min_x + w + off;
  const line = 'var(--part-dim, #7a8899)';
  return [
    `<g stroke="${line}" fill="none" stroke-width="${stroke * 0.7}">`,
    `<path d="M ${box.min_x} ${y} L ${box.min_x + w} ${y}`,
    ` M ${box.min_x} ${y - tick} L ${box.min_x} ${y + tick}`,
    ` M ${box.min_x + w} ${y - tick} L ${box.min_x + w} ${y + tick}`,
    ` M ${x} ${box.min_y} L ${x} ${box.min_y + h}`,
    ` M ${x - tick} ${box.min_y} L ${x + tick} ${box.min_y}`,
    ` M ${x - tick} ${box.min_y + h} L ${x + tick} ${box.min_y + h}"/>`,
    `</g>`,
    `<text x="${box.min_x + w / 2}" y="${y + size}" fill="${line}"`,
    ` font-size="${size}" text-anchor="middle" direction="ltr">${round(w)}</text>`,
    `<text x="${x + size * 0.35}" y="${box.min_y + h / 2}" fill="${line}"`,
    ` font-size="${size}" dominant-baseline="middle" direction="ltr">${round(h)}</text>`,
  ].join('');
}

function round(n) {
  return Number.isInteger(n) ? n : Number(n.toFixed(1));
}
