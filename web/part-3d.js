/* הצגת הגוף — מהפריסה שתומחרה, בלי ספרייה חיצונית.
 *
 * **למה נכתב ולא הובא.** three.js הוא ~600KB שנטענים אצל כל מבקר,
 * והמסכים כאן עצמאיים בכוונה — בלי פנייה לשרת חיצוני, כדי שמי
 * שפותח מסך עם מחירים לא ישאיר עקבות אצל צד שלישי. מה שנדרש כאן
 * הוא מצומצם: לוחות **מישוריים** עם עובי, מצלמה אורתוגונלית, והצללה
 * אחת. זה כל הקובץ.
 *
 * **וזה לא מודל שני.** ה-`faces` מגיעים מהשרת, מקופלים מאותה פריסה
 * שממנה חושבו השטח ואורך החיתוך. אין כאן גיאומטריה שנוצרת בדפדפן.
 *
 * **הפינות כאן מעוגלות מאז 27.8.2026, וזה לא קוסמטיקה.** פח אמיתי
 * מתקפל בקשת ולא בקו, והשרת מקפל היום סביב רדיוס — אותו רדיוס שממנו
 * נגזר ניכוי הכיפוף בפריסה. פינה חדה על המסך הייתה מבטיחה מוצר
 * שהמכבש אינו יודע לייצר, ומידה חיצונית שאינה זו שהוזמנה.
 */

const LIGHT = normalize([-0.35, -0.75, 0.55]);

function normalize(v) {
  const n = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / n, v[1] / n, v[2] / n];
}

function shade(normal, base) {
  /* למברט עם תאורת סביבה, **בסימן ולא בערך מוחלט.**

     עד 27.8.2026 עמד כאן `Math.abs`, וזה נראה כמו פרט טכני: הוא
     נתן לפאה ולהפוכה שלה **בדיוק אותו גוון**. התוצאה היא שקופסה
     מסובבת לזווית נמוכה מאבדת את ההבדל בין פנים לחוץ — כל הדפנות
     יוצאות באותו אפור, והגוף נקרא שטוח. פח אמיתי מואר מבחוץ
     ומוצל מבפנים, וזה מה שאומר לעין איפה החלל.

     החצי האחורי אינו שחור אלא מואר באור מילוי חלש: פאה פנימית
     בצל מוחלט נראית כמו חור בחלק. */
  const d = normal[0] * LIGHT[0] + normal[1] * LIGHT[1] + normal[2] * LIGHT[2];
  const k = d >= 0 ? 0.52 + 0.48 * d : 0.34 + 0.16 * (1 + d);
  return `rgb(${Math.round(base[0] * k)},${Math.round(base[1] * k)},${Math.round(base[2] * k)})`;
}

/* מצלמה אורתוגונלית: yaw סביב ציר z של העולם, ואז pitch.
   אורתוגונלית ולא פרספקטיבה — חלק מכונה נמדד, וקו מקביל שנפגש
   באופק נראה יפה ומטעה. */
function makeCamera(yaw, pitch) {
  const cy = Math.cos(yaw);
  const sy = Math.sin(yaw);
  const cp = Math.cos(pitch);
  const sp = Math.sin(pitch);
  return (p) => {
    const x = p[0] * cy - p[1] * sy;
    const y = p[0] * sy + p[1] * cy;
    return [x, y * cp - p[2] * sp, y * sp + p[2] * cp];
    //       ↑ מסך-x     ↑ עומק        ↑ מסך-y (למעלה חיובי)
  };
}

function polygonFromFace(face, thickness) {
  /* לוח מישורי + עובי = מנסרה. שלושה סוגי מצולעים יוצאים מכאן:
     הפנים העליונות והתחתונות (עם החורים, במילוי evenodd), דפנות
     ההיקף, ודפנות החורים. */
  const n = face.normal;
  const lift = (p) => [
    p[0] + n[0] * thickness,
    p[1] + n[1] * thickness,
    p[2] + n[2] * thickness,
  ];
  return { top: face.outline.map(lift), bottom: face.outline, holes: face.holes, normal: n };
}

function quads(loopA, loopB, normalHint) {
  /* דופן בין שתי לולאות מקבילות. הנורמל מחושב ממכפלה וקטורית של שתי
     צלעות הפאה עצמה, ולא נלקח מהלוח — דופן היקף פונה **הצידה**,
     ולהאיר אותה כמו הפָּן העליון היה משטח את החלק לגמרי. */
  const out = [];
  for (let i = 0; i < loopA.length; i++) {
    const j = (i + 1) % loopA.length;
    const a = loopA[i];
    const b = loopA[j];
    const c = loopB[j];
    const d = loopB[i];
    const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
    const v = [d[0] - a[0], d[1] - a[1], d[2] - a[2]];
    let nrm = normalize([
      u[1] * v[2] - u[2] * v[1],
      u[2] * v[0] - u[0] * v[2],
      u[0] * v[1] - u[1] * v[0],
    ]);
    if (!Number.isFinite(nrm[0])) nrm = normalHint;
    out.push({ loops: [[a, b, c, d]], normal: nrm });
  }
  return out;
}

function collect(model, wallsOnHoles) {
  const t = model.thickness_mm || 1;
  const pieces = [];

  for (const face of model.faces) {
    const prism = polygonFromFace(face, t);
    const liftedHoles = face.holes.map((h) =>
      h.map((p) => [
        p[0] + face.normal[0] * t,
        p[1] + face.normal[1] * t,
        p[2] + face.normal[2] * t,
      ])
    );
    // פָּן עליון ותחתון — החורים הם לולאות נוספות באותו מצולע,
    // וה-canvas חותך אותם עם evenodd בלי שנצטרך לשלש משולשים.
    pieces.push({ loops: [prism.top, ...liftedHoles], normal: face.normal });
    pieces.push({
      loops: [prism.bottom, ...face.holes],
      normal: [-face.normal[0], -face.normal[1], -face.normal[2]],
    });
    pieces.push(...quads(prism.bottom, prism.top, face.normal));
    if (wallsOnHoles) {
      for (let i = 0; i < face.holes.length; i++) {
        pieces.push(...quads(face.holes[i], liftedHoles[i], face.normal));
      }
    }
  }
  return pieces;
}

export function createViewer(canvas, options = {}) {
  const base = options.color || [176, 188, 201];
  const edge = options.edge || 'rgba(20,26,34,.55)';
  let model = null;
  let pieces = [];
  let coarse = [];
  let yaw = -0.62;
  let pitch = 1.02;
  let frame = 0;
  let dragging = false;

  /* **שתי רמות פירוט, ולא תקציב חורים.**

     עד 27.8.2026 היה כאן `HOLE_WALL_BUDGET = 420`: מעליו דפנות
     החורים פשוט לא צוירו. במשרבייה עם שבעים פתחים זה בדיוק מה
     שקרה — החורים יצאו עיגולים לבנים שטוחים, כאילו הודבקו על
     הפח ולא נחתכו דרכו, וזה המוצר שהכי חשוב בו שרואים שהוא מנוקב.

     מי שמסובב אינו בוחן פרט, ומי שבוחן פרט אינו מסובב. לכן הגרסה
     המלאה מצוירת תמיד כשהיד עומדת, והמדוללת רק בזמן הגרירה. */

  function draw() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (canvas.width !== Math.round(w * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const active = dragging && coarse.length ? coarse : pieces;
    if (!model || !active.length) return;

    const camera = makeCamera(yaw, pitch);
    const centre = model.size_mm.map((s) => s / 2);
    const shift = (p) => [p[0] - centre[0], p[1] - centre[1], p[2] - centre[2]];

    /* קנה המידה נגזר מהמצולעים המוקרנים בפועל ולא מהתיבה החוסמת:
       גוף מסובב תופס על המסך משהו אחר מהתיבה שלו, וסקאלה מהתיבה
       הייתה משאירה שוליים משתנים בזמן סיבוב. */
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    const flat = active.map((piece) => {
      const loops = piece.loops.map((loop) =>
        loop.map((p) => {
          const q = camera(shift(p));
          if (q[0] < minX) minX = q[0];
          if (q[0] > maxX) maxX = q[0];
          if (q[2] < minY) minY = q[2];
          if (q[2] > maxY) maxY = q[2];
          return q;
        })
      );
      let depth = 0;
      let count = 0;
      for (const loop of loops) for (const q of loop) { depth += q[1]; count++; }
      return { loops, depth: depth / count, normal: camera(piece.normal) };
    });

    const pad = 14;
    const scale = Math.min((w - pad * 2) / (maxX - minX || 1), (h - pad * 2) / (maxY - minY || 1));
    const ox = w / 2 - ((minX + maxX) / 2) * scale;
    const oy = h / 2 + ((minY + maxY) / 2) * scale;

    // אלגוריתם הצייר: הרחוק נצבע ראשון. מספיק לגוף פח שאין בו
    // פאות שחודרות זו את זו.
    flat.sort((a, b) => b.depth - a.depth);

    ctx.lineJoin = 'round';
    for (const piece of flat) {
      ctx.beginPath();
      for (const loop of piece.loops) {
        loop.forEach((q, i) => {
          const x = ox + q[0] * scale;
          const y = oy - q[2] * scale;
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
        ctx.closePath();
      }
      ctx.fillStyle = shade(normalize(piece.normal), base);
      ctx.fill('evenodd');
      ctx.strokeStyle = edge;
      ctx.lineWidth = 0.6;
      ctx.stroke();
    }
  }

  function schedule() {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      draw();
    });
  }

  let lastX = 0;
  let lastY = 0;
  const start = (event) => {
    dragging = true;
    lastX = event.clientX;
    lastY = event.clientY;
    canvas.setPointerCapture(event.pointerId);
    canvas.style.cursor = 'grabbing';
  };
  const move = (event) => {
    if (!dragging) return;
    yaw += (event.clientX - lastX) * 0.011;
    // גבול הנטייה: מעבר לו הגוף מתהפך, והמשתמש מאבד את הכיוון.
    pitch = Math.max(0.06, Math.min(Math.PI - 0.06, pitch - (event.clientY - lastY) * 0.011));
    lastX = event.clientX;
    lastY = event.clientY;
    schedule();
  };
  const end = (event) => {
    dragging = false;
    schedule();
    canvas.style.cursor = 'grab';
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
  };
  canvas.addEventListener('pointerdown', start);
  canvas.addEventListener('pointermove', move);
  canvas.addEventListener('pointerup', end);
  canvas.addEventListener('pointercancel', end);
  canvas.style.cursor = 'grab';
  canvas.style.touchAction = 'none';

  const observer = new ResizeObserver(schedule);
  observer.observe(canvas);

  return {
    show(next) {
      model = next;
      pieces = next && next.faces ? collect(next, true) : [];
      coarse = next && next.faces ? collect(next, false) : [];
      schedule();
    },
    reset() {
      yaw = -0.62;
      pitch = 1.02;
      schedule();
    },
    redraw: schedule,
  };
}
