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
    /* פָּן עליון ותחתון — החורים הם לולאות נוספות באותו מצולע,
       וה-canvas חותך אותם עם evenodd בלי שנצטרך לשלש משולשים.

       **`cull` הוא מה שמבדיל בין קופסה לבין ערימת מצולעים.** שני
       הפָּנים האלה גב אל גב, ובכל רגע נתון בדיוק אחד מהם פונה
       למצלמה. בלי לסמן אותם, זה שפונה **הצידה** נצבע לפי העומק
       הממוצע שלו — ובזווית נמוכה הפאה הפנימית של הדופן הקרובה
       יוצאת הכי קרובה, נצבעת אחרונה, **ומכסה את רצפת המארז**.
       נמדד 27.8.2026: המארז נראה כאילו אין לו תחתית, ושלושת חורי
       האטם נעלמו איתה.

       דפנות ההיקף וקשתות הכיפוף אינן מסומנות: הנורמל שלהן נגזר
       ממכפלה וקטורית שסימנה תלוי בכיוון שבו הבנאי כתב את המתאר,
       וסילוק לפיו היה קורע חורים בגוף במקום לנקות אותו. */
    /* **כל חתיכה נושאת את הלוח שהיא באה ממנו.** בלי זה אפשר
       לצייר גוף אבל אי אפשר לגעת בו: לחיצה מחזירה מצולע בלי שם,
       והעריכה נשארת דו-ממדית גם כשהמסך תלת-ממדי. */
    const of = (extra) => Object.assign({ face: face.face_index, bend: face.bend_index }, extra);
    pieces.push(of({ loops: [prism.top, ...liftedHoles], normal: face.normal, cull: true }));
    pieces.push(of({
      loops: [prism.bottom, ...face.holes],
      normal: [-face.normal[0], -face.normal[1], -face.normal[2]],
      cull: true,
    }));
    for (const q of quads(prism.bottom, prism.top, face.normal)) pieces.push(of(q));
    if (wallsOnHoles) {
      for (let i = 0; i < face.holes.length; i++) {
        for (const q of quads(face.holes[i], liftedHoles[i], face.normal)) pieces.push(of(q));
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
  let radius = 1;
  let frame = 0;
  let dragging = false;
  let panning = false;
  const HOME = { yaw: -0.62, pitch: 1.02, zoom: 1, panX: 0, panY: 0 };
  const cam = { ...HOME };
  /* בחירה והדגשה. הצבעים נגזרים מצבע הבסיס ולא נכתבים ביד, כדי
     שהצופה יישאר נכון גם כשהמסך שמארח אותו יחליף ערכת צבעים. */
  const HIGHLIGHT = [232, 152, 84];
  const HOVER = [206, 200, 194];
  let selected = -1;
  let hovered = -1;
  let projection = null;
  let handle = null;          // {x, y, dir:[dx,dy], bend}
  let bending = null;         // גרירת זווית פעילה
  const onPick = options.onPick || (() => {});
  const onAngle = options.onAngle || (() => {});

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

    const camera = makeCamera(cam.yaw, cam.pitch);
    const centre = model.size_mm.map((s) => s / 2);
    const shift = (p) => [p[0] - centre[0], p[1] - centre[1], p[2] - centre[2]];

    const flat = [];
    for (const piece of active) {
      const normal = camera(piece.normal);
      // חצי הכדור האחורי: פאה שהנורמל שלה מצביע הלאה מהמצלמה מוסתרת
      // בהכרח על ידי הגוף עצמו, וציורה יכול רק לקלקל את הסדר.
      if (piece.cull && normal[1] > 0) continue;
      const loops = piece.loops.map((loop) => loop.map((p) => camera(shift(p))));
      /* **המיון לפי הנקודה הקרובה ביותר של הפאה, ולא לפי הממוצע
         ולא לפי הרחוקה.** שלושתם נוסו, ורק זה עובד — וזה נמדד:

         - **ממוצע** נכשל כי לרצפה ולדופן הרחוקה מרכזים שונים אבל
           הן נחתכות; המארז יצא בלי תחתית.
         - **הנקודה הרחוקה** נכשלת ב**תיקו**: הקצה הרחוק של הרצפה
           והבסיס של הדופן הרחוקה יושבים באותה נקודה בדיוק, ומיון
           על ערך זהה מכריע באקראי.
         - **הנקודה הקרובה** מבדילה ביניהן תמיד: הדופן הרחוקה
           מתרוממת, ולכן הנקודה הקרובה שלה עדיין רחוקה מהקצה הקרוב
           של הרצפה. היא נצבעת ראשונה, והרצפה עליה.

         והכלל נשאר נכון גם לדופן הקרובה: היא הקרובה מכולן, נצבעת
         אחרונה, ומסתירה את שפת הרצפה — בדיוק כמו במציאות. */
      let near = Infinity;
      for (const loop of loops) for (const q of loop) if (q[1] < near) near = q[1];
      flat.push({ loops, depth: near, normal, face: piece.face, bend: piece.bend });
    }

    /* **קנה המידה נגזר מרדיוס הגוף ולא מתיבת ההיטל.**

       עד 27.8.2026 הוא חושב מהמצולעים המוקרנים, כלומר **מחדש בכל
       פריים**. הכוונה הייתה לשמור שוליים קבועים; התוצאה היא שהחלק
       גדל ומתכווץ תוך כדי שמסובבים אותו ונצמד תמיד לשולי המסגרת.
       העין מפרשת שינוי גודל כתנועה קדימה, ולכן כל גרירה הרגישה
       כאילו היא גם מקרבת — וזו הסיבה המרכזית שהסיבוב הרגיש שבור.

       רדיוס הכדור החוסם אינו תלוי בזווית, ולכן הגודל על המסך קבוע
       והסיבוב הוא סיבוב בלבד. */
    // 0.5 הוא בדיוק המצב שבו הכדור החוסם ממלא את הצלע הקצרה.
    // 0.48 משאיר שוליים דקים ומבטיח שהגוף לא ייגזר בשום זווית.
    const scale = (Math.min(w, h) * 0.48 / radius) * cam.zoom;
    const ox = w / 2 + cam.panX;
    const oy = h / 2 + cam.panY;

    // אלגוריתם הצייר: הרחוק נצבע ראשון. מספיק לגוף פח שאין בו
    // פאות שחודרות זו את זו.
    flat.sort((a, b) => b.depth - a.depth);

    /* **ההיטל נשמר כדי שאפשר יהיה ללחוץ עליו.** פגיעה שמחשבת
       הקרנה משלה הייתה יכולה להיפרד ממה שמצויר — ואז המשתמש לוחץ
       על דופן אחת ונבחרת אחרת. */
    projection = { flat, scale, ox, oy };

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
      const lit = piece.face === selected ? HIGHLIGHT : piece.face === hovered ? HOVER : base;
      ctx.fillStyle = shade(normalize(piece.normal), lit);
      ctx.fill('evenodd');
      ctx.strokeStyle = edge;
      ctx.lineWidth = 0.6;
      ctx.stroke();
    }

    drawHandle(ctx, flat, scale, ox, oy);
  }

  /* **ידית, ולא "גרירה על הגוף".**

     גרירה על הגוף כבר תפוסה — היא מסובבת. אילו אותה תנועה הייתה
     גם מכופפת, כל ניסיון להסתכל על החלק מזווית אחרת היה משנה
     אותו. הידית היא המקום היחיד שבו הגרירה מכופפת, והיא מופיעה
     רק כשנבחר לוח שיש לו כיפוף. */
  function drawHandle(ctx, flat, scale, ox, oy) {
    handle = null;
    if (selected < 0 || !model) return;
    const face = model.faces[selected];
    if (!face || face.bend_index < 0) return;
    const piece = flat.find((f) => f.face === selected);
    if (!piece) return;

    const centre = model.size_mm.map((v) => v / 2);
    const camera = makeCamera(cam.yaw, cam.pitch);
    const toScreen = (p) => {
      const q = camera([p[0] - centre[0], p[1] - centre[1], p[2] - centre[2]]);
      return [ox + q[0] * scale, oy - q[2] * scale];
    };

    let cx = 0, cy = 0, cz = 0;
    for (const v of face.outline) { cx += v[0]; cy += v[1]; cz += v[2]; }
    const n = face.outline.length || 1;
    const mid = toScreen([cx / n, cy / n, cz / n]);

    /* כיוון הגרירה: **הרדיוס מציר הכיפוף אל מרכז הלוח.** משיכה
       החוצה משטיחה, משיכה פנימה מקימה — וזו התנועה שהיד עושה
       ממילא כשמכופפים פח ביד. */
    const axis = model.fold_axes && model.fold_axes[face.bend_index];
    let dir = [0, -1];
    if (axis) {
      const a = toScreen(axis.slice(0, 3)), b = toScreen(axis.slice(3, 6));
      const ax = (a[0] + b[0]) / 2, ay = (a[1] + b[1]) / 2;
      const vx = mid[0] - ax, vy = mid[1] - ay;
      const len = Math.hypot(vx, vy) || 1;
      dir = [vx / len, vy / len];
    }
    handle = { x: mid[0], y: mid[1], dir, bend: face.bend_index };

    ctx.save();
    ctx.beginPath();
    ctx.arc(mid[0], mid[1], 11, 0, Math.PI * 2);
    ctx.fillStyle = bending ? 'rgba(194,65,12,.95)' : 'rgba(255,255,255,.92)';
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = 'rgb(194,65,12)';
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(mid[0] - dir[0] * 18, mid[1] - dir[1] * 18);
    ctx.lineTo(mid[0] + dir[0] * 18, mid[1] + dir[1] * 18);
    ctx.strokeStyle = 'rgba(194,65,12,.55)';
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.restore();
  }

  /* פגיעה על **ההיטל שצויר**, מהחתיכה העליונה כלפי מטה. חישוב
     הקרנה נפרד היה יכול להיפרד ממה שרואים, ואז לחיצה על דופן אחת
     בוחרת אחרת. */
  function pickAt(clientX, clientY) {
    if (!projection) return -1;
    const rect = canvas.getBoundingClientRect();
    const x = clientX - rect.left, y = clientY - rect.top;
    const { flat, scale, ox, oy } = projection;
    for (let i = flat.length - 1; i >= 0; i--) {
      let inside = false;
      for (const loop of flat[i].loops) {
        for (let a = 0, b = loop.length - 1; a < loop.length; b = a++) {
          const xa = ox + loop[a][0] * scale, ya = oy - loop[a][2] * scale;
          const xb = ox + loop[b][0] * scale, yb = oy - loop[b][2] * scale;
          if ((ya > y) !== (yb > y) && x < ((xb - xa) * (y - ya)) / (yb - ya) + xa) inside = !inside;
        }
      }
      if (inside) return flat[i].face;
    }
    return -1;
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
  let downAt = null;
  const start = (event) => {
    downAt = [event.clientX, event.clientY];
    /* הידית נבדקת **לפני** הסיבוב: היא קטנה, היא מעל הגוף,
       ובלי הקדימות הזאת כל ניסיון לכופף היה מסובב במקום. */
    if (handle && !event.shiftKey && event.button === 0) {
      const rect = canvas.getBoundingClientRect();
      const dx = event.clientX - rect.left - handle.x;
      const dy = event.clientY - rect.top - handle.y;
      if (Math.hypot(dx, dy) <= 16) {
        bending = { bend: handle.bend, dir: handle.dir, x: event.clientX, y: event.clientY };
        canvas.setPointerCapture(event.pointerId);
        canvas.style.cursor = 'ns-resize';
        event.preventDefault();
        schedule();
        return;
      }
    }
    dragging = true;
    // הזזה: כפתור אמצעי, ימני, או Shift — שלוש הדרכים שאנשים מנסים.
    panning = event.button === 1 || event.button === 2 || event.shiftKey;
    lastX = event.clientX;
    lastY = event.clientY;
    canvas.setPointerCapture(event.pointerId);
    canvas.style.cursor = panning ? 'move' : 'grabbing';
    event.preventDefault();
  };
  const move = (event) => {
    if (bending) {
      const dx = event.clientX - bending.x;
      const dy = event.clientY - bending.y;
      bending.x = event.clientX;
      bending.y = event.clientY;
      // משיכה החוצה מהציר משטיחה, פנימה מקימה. 0.6° לפיקסל.
      const along = dx * bending.dir[0] + dy * bending.dir[1];
      onAngle(bending.bend, -along * 0.6);
      return;
    }
    if (!dragging) {
      const face = pickAt(event.clientX, event.clientY);
      if (face !== hovered) { hovered = face; schedule(); }
      canvas.style.cursor = handleUnder(event) ? 'ns-resize' : face >= 0 ? 'pointer' : 'grab';
      return;
    }
    const dx = event.clientX - lastX;
    const dy = event.clientY - lastY;
    lastX = event.clientX;
    lastY = event.clientY;

    if (panning) {
      cam.panX += dx;
      cam.panY += dy;
      schedule();
      return;
    }

    /* **שני הסימנים היו הפוכים, ותוקנו 27.8.2026.**

       גרירה ימינה סובבה את הגוף שמאלה, וגרירה מטה הראתה **פחות**
       מלמעלה במקום יותר. המוסכמה שכל תוכנת CAD מחזיקה היא שהגוף
       הולך אחרי היד — כאילו מסובבים אותו באצבע — ולא שהמצלמה נעה
       הפוך. שני מינוסים, וזה כל התיקון.

       והרגישות נגזרת מגובה הקנבס במקום להיות 0.011 רדיאן לפיקסל:
       המספר הקבוע היה מהיר מדי על מסך גדול ואיטי מדי על טלפון.
       חצי מסך = חצי סיבוב, בכל גודל. */
    const speed = Math.PI / Math.max(240, canvas.clientHeight);
    cam.yaw -= dx * speed;
    // מעבר ל-90° רואים את התחתית והתמונה מתהפכת. זה סיבוב לגיטימי
    // ומבלבל למי שבודק מוצר, ולכן הגבול עוצר רגע לפני.
    cam.pitch = Math.max(0.05, Math.min(Math.PI / 2 - 0.02, cam.pitch + dy * speed));
    schedule();
  };
  function handleUnder(event) {
    if (!handle) return false;
    const rect = canvas.getBoundingClientRect();
    return Math.hypot(event.clientX - rect.left - handle.x, event.clientY - rect.top - handle.y) <= 16;
  }

  const end = (event) => {
    /* **לחיצה היא גרירה שלא זזה.** בלי הסף הזה כל סיבוב קטן היה
       גם בוחר לוח אחר, ומי שמסתכל על החלק היה משנה את הבחירה
       בלי לרצות. */
    if (downAt && !bending) {
      const moved = Math.hypot(event.clientX - downAt[0], event.clientY - downAt[1]);
      if (moved < 4) {
        const face = pickAt(event.clientX, event.clientY);
        selected = face;
        onPick(face, face >= 0 && model.faces[face] ? model.faces[face].bend_index : -1);
      }
    }
    downAt = null;
    bending = null;
    dragging = false;
    panning = false;
    schedule();
    canvas.style.cursor = 'grab';
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
  };
  canvas.addEventListener('pointerdown', start);
  canvas.addEventListener('pointermove', move);
  canvas.addEventListener('pointerup', end);
  canvas.addEventListener('pointercancel', end);
  // בלי זה, גרירה בכפתור ימני פותחת תפריט הקשר באמצע הסיבוב.
  canvas.addEventListener('contextmenu', (event) => event.preventDefault());
  canvas.addEventListener('dblclick', () => { Object.assign(cam, HOME); schedule(); });
  canvas.addEventListener(
    'wheel',
    (event) => {
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      // הנקודה שמתחת לסמן נשארת תחתיו, בדיוק כמו בתצוגה הדו-ממדית.
      const x = event.clientX - rect.left - rect.width / 2 - cam.panX;
      const y = event.clientY - rect.top - rect.height / 2 - cam.panY;
      const next = Math.min(12, Math.max(0.3, cam.zoom * Math.exp(-event.deltaY * 0.0016)));
      const applied = next / cam.zoom;
      cam.panX -= x * (applied - 1);
      cam.panY -= y * (applied - 1);
      cam.zoom = next;
      schedule();
    },
    { passive: false }
  );
  canvas.style.cursor = 'grab';
  canvas.style.touchAction = 'none';

  const observer = new ResizeObserver(schedule);
  observer.observe(canvas);

  return {
    show(next, opts) {
      /* **בעריכה המצלמה לא קופצת.** שינוי זווית משנה את מידות
         הגוף, ואיפוס המצלמה על כל שינוי היה מזיז את החלק מתחת
         ליד בזמן שגוררים אותו. */
      const fresh = (!opts || !opts.keepCamera)
        && (!model || String(model.size_mm) !== String(next && next.size_mm));
      model = next;
      pieces = next && next.faces ? collect(next, true) : [];
      coarse = next && next.faces ? collect(next, false) : [];

      // רדיוס הכדור החוסם, פעם אחת לכל מודל: זה מה שמייצב את הגודל.
      radius = 1;
      if (next && next.size_mm) {
        const c = next.size_mm.map((s) => s / 2);
        for (const piece of pieces) {
          for (const loop of piece.loops) {
            for (const q of loop) {
              const d = Math.hypot(q[0] - c[0], q[1] - c[1], q[2] - c[2]);
              if (d > radius) radius = d;
            }
          }
        }
      }

      // **הזווית נשמרת בין עדכונים.** מי שסובב כדי לבדוק פינה ואז
      // הזיז מחוון לא רוצה שהתצוגה תקפוץ להתחלה בכל שינוי; רק חלק
      // במידות אחרות מאפס.
      if (fresh) Object.assign(cam, HOME);
      schedule();
    },
    reset() {
      Object.assign(cam, HOME);
      schedule();
    },
    select(face) { selected = typeof face === 'number' ? face : -1; schedule(); },
    selected() { return selected; },
    redraw: schedule,
  };
}
