/* סרגל הצד — **רכיב אחד שמוזרק, ולא סימון שמועתק לאחד-עשר מסכים.**
 *
 * ניווט שמועתק מתפצל: מסך אחד מקבל קישור חדש והשאר לא, ואז "איפה
 * ההיסטוריה" תלוי באיזה מסך אתה עומד. הרשימה כאן היא **מקור אחד**,
 * והמסכים מקבלים אותה בזמן טעינה.
 *
 * **מה שמוצג נגזר מהיכולות של המשתמש ולא מהכתובת.** מי שיש לו
 * `quote:total` בלבד לא יראה את טבלת המחירים ולא את הדשבורד —
 * לא כי הם מוסתרים ב-CSS, אלא כי `/api/me` לא החזיר את היכולת.
 * קישור שמוביל ל-403 הוא מסך שמציע בחירה שנועדה להיכשל.
 */

const LINKS = [
  { href: '/', label: 'תמחור', cap: null, icon: 'M4 13h6V4H4v9Zm0 7h6v-5H4v5Zm9 0h7v-9h-7v9Zm0-16v5h7V4h-7Z' },
  { href: '/catalog', label: 'קטלוג', cap: null, icon: 'M4 6h16M4 12h16M4 18h10' },
  { href: '/editor', label: 'עורך', cap: null, badge: 'חדש', icon: 'M4 20h4L19 9l-4-4L4 16v4Zm10-13 4 4' },
  { href: '/cart', label: 'עגלה', cap: null, cart: true, icon: 'M6 6h15l-1.6 8.4a2 2 0 0 1-2 1.6H9.2a2 2 0 0 1-2-1.6L5.4 4H3m6 16a1 1 0 1 0 0-2 1 1 0 0 0 0 2Zm9 0a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z' },
  { href: '/history', label: 'ההצעות שלי', cap: null, icon: 'M12 8v5l3 2m6-2a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z' },
  { href: '/orders', label: 'ההזמנות שלי', cap: null, icon: 'M6 3h9l4 4v14H6V3Zm9 0v4h4M9 12h7M9 16h5' },
  { href: '/shop', label: 'הזמנות שהתקבלו', cap: 'quote:use', icon: 'M4 7h16l-1.2 11.2a2 2 0 0 1-2 1.8H7.2a2 2 0 0 1-2-1.8L4 7Zm4 0V5a4 4 0 0 1 8 0v2' },
  { href: '/dashboard', label: 'דשבורד', cap: 'quote:use', icon: 'M5 19V9m7 10V5m7 14v-6' },
  { href: '/tariff', label: 'טבלת מחירים', cap: 'prices:edit', icon: 'M4 7h16M4 12h16M4 17h7M17 15l3 3-3 3' },
  { href: '/account', label: 'החשבון שלי', cap: null, icon: 'M5 20a7 7 0 0 1 14 0M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z' },
];

const MARK =
  '<svg viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M5 8.5A3.5 3.5 0 0 1 8.5 5h9.9L27 13.6v9.9A3.5 3.5 0 0 1 23.5 27h-15A3.5 3.5 0 0 1 5 23.5v-15Z" stroke="currentColor" stroke-width="2.2" stroke-linejoin="round"/><path d="M18.4 5v5.1a3.5 3.5 0 0 0 3.5 3.5H27" stroke="currentColor" stroke-width="2.2" stroke-linejoin="round"/><circle cx="12.4" cy="19.6" r="3" fill="currentColor"/></svg>';

function icon(path) {
  return `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="${path}" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
}

export async function mountSidebar(active = location.pathname) {
  let me = null;
  try {
    const response = await fetch('/api/me');
    if (response.ok) me = await response.json();
  } catch {
    /* מסך שנטען בלי רשת עדיין צריך ניווט; פשוט בלי הפריטים המוגבלים. */
  }
  const caps = new Set((me && (me.capabilities || me.capability_keys)) || []);

  const items = LINKS.filter((link) => !link.cap || caps.has(link.cap))
    .map((link) => {
      /* התאמה מדויקת לשורש, ותחילית לשאר: `/catalog` צריך להיות
         מסומן גם כשעומדים על `/product/tray-4-sides`? לא — זה מסך
         אחר. אבל `/history/12` כן. */
      const on =
        link.href === '/'
          ? active === '/'
          : active === link.href || active.startsWith(link.href + '/');
      /* תג העגלה מקבל מזהה כדי שאפשר יהיה לעדכן אותו בלי לבנות
         מחדש את הניווט: מי שמוסיף פריט רואה את המספר משתנה, ולא
         מגלה אותו בטעינת המסך הבא. */
      const badge = link.cart
        ? '<em class="sb-count" id="sb-cart-count" hidden>0</em>'
        : link.badge ? `<em>${link.badge}</em>` : '';
      return `<a class="sb-link${on ? ' on' : ''}" href="${link.href}"${on ? ' aria-current="page"' : ''}>
        ${icon(link.icon)}<span>${link.label}</span>${badge}</a>`;
    })
    .join('');

  const who = me
    ? `<div class="sb-who"><b>${me.display_name || me.username || ''}</b><span>${
        (me.capability_labels || []).join(' · ') || 'משתמש'
      }</span></div>`
    : '';

  const nav = document.createElement('nav');
  nav.className = 'sidebar';
  nav.setAttribute('aria-label', 'ניווט ראשי');
  nav.innerHTML = `
    <a class="sb-brand" href="/">${MARK}<span><b>ימיש כדורי</b><small>חיתוך לייזר</small></span></a>
    <div class="sb-links">${items}</div>
    <div class="sb-foot">${who}</div>`;
  document.body.prepend(nav);

  const toggle = document.createElement('button');
  toggle.className = 'sb-toggle ghost';
  toggle.type = 'button';
  toggle.setAttribute('aria-label', 'תפריט');
  toggle.innerHTML = icon('M4 7h16M4 12h16M4 17h16');
  toggle.addEventListener('click', () => document.body.classList.toggle('sb-open'));
  document.body.prepend(toggle);
  /* לחיצה על התוכן סוגרת את התפריט בטלפון. בלי זה הוא נשאר פתוח
     מעל המסך שלחצת עליו, וזה נראה כמו קפיאה. */
  document.addEventListener('click', (event) => {
    if (!document.body.classList.contains('sb-open')) return;
    if (nav.contains(event.target) || toggle.contains(event.target)) return;
    document.body.classList.remove('sb-open');
  });
  if (me) refreshCartBadge();
  return me;
}

/* **המספר מגיע מנקודת קצה שאינה מתמחרת.** תמחור של כל העגלה על
   כל טעינת מסך היה נסטינג מלא בשביל תג בן ספרה אחת, על קופסה
   שהיא שתי ליבות לחמישה פרויקטים. */
export async function refreshCartBadge() {
  const tag = document.getElementById('sb-cart-count');
  if (!tag) return 0;
  try {
    const response = await fetch('/api/cart/count');
    if (!response.ok) return 0;
    const { count } = await response.json();
    tag.textContent = String(count);
    tag.hidden = !count;
    return count;
  } catch {
    return 0;
  }
}
