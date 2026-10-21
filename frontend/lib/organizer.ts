// Pure state helpers for the page organizer (drag-and-drop reordering, rotation, deletion).

export interface PageItem {
  key: string; // stable React key
  page: number; // source page index (0-based)
  rotate: number; // additional rotation, multiple of 90
}

export function initialPages(count: number): PageItem[] {
  return Array.from({ length: count }, (_, i) => ({ key: `p${i}`, page: i, rotate: 0 }));
}

export function move(items: PageItem[], from: number, to: number): PageItem[] {
  if (from === to || from < 0 || from >= items.length || to < 0 || to >= items.length) return items;
  const next = items.slice();
  const [it] = next.splice(from, 1);
  if (it) next.splice(to, 0, it);
  return next;
}

export function rotate(items: PageItem[], index: number, delta: 90 | -90 | 180): PageItem[] {
  return items.map((it, i) => (i === index ? { ...it, rotate: (((it.rotate + delta) % 360) + 360) % 360 } : it));
}

export function rotateAll(items: PageItem[], delta: 90 | -90 | 180): PageItem[] {
  return items.map((it) => ({ ...it, rotate: (((it.rotate + delta) % 360) + 360) % 360 }));
}

export function remove(items: PageItem[], index: number): PageItem[] {
  return items.filter((_, i) => i !== index);
}

export function duplicate(items: PageItem[], index: number, seq: number): PageItem[] {
  const it = items[index];
  if (!it) return items;
  const next = items.slice();
  next.splice(index + 1, 0, { ...it, key: `${it.key}-d${seq}` });
  return next;
}

export function toOrderPayload(items: PageItem[]): { page: number; rotate: number }[] {
  return items.map((it) => ({ page: it.page, rotate: it.rotate }));
}

export function isUnchanged(items: PageItem[], count: number): boolean {
  return items.length === count && items.every((it, i) => it.page === i && it.rotate === 0);
}
