"use client";

export interface FlatBookmark {
  title: string;
  page: number; // 1-based for display
  level: number; // 0 = top level
}

export interface BookmarkNode {
  title: string;
  page: number; // 0-based
  children: BookmarkNode[];
}

export function flatten(nodes: BookmarkNode[], level = 0): FlatBookmark[] {
  return nodes.flatMap((n) => [{ title: n.title, page: (n.page ?? 0) + 1, level }, ...flatten(n.children ?? [], level + 1)]);
}

export function nest(items: FlatBookmark[]): BookmarkNode[] {
  const root: BookmarkNode[] = [];
  const stack: { level: number; node: BookmarkNode }[] = [];
  for (const it of items) {
    const node: BookmarkNode = { title: it.title, page: Math.max(0, it.page - 1), children: [] };
    while (stack.length && stack[stack.length - 1]!.level >= it.level) stack.pop();
    if (stack.length) stack[stack.length - 1]!.node.children.push(node);
    else root.push(node);
    stack.push({ level: Math.min(it.level, stack.length), node });
  }
  return root;
}

export function BookmarkEditor({ items, onChange, pageCount }: { items: FlatBookmark[]; onChange: (i: FlatBookmark[]) => void; pageCount: number }) {
  const set = (i: number, patch: Partial<FlatBookmark>) => onChange(items.map((it, k) => (k === i ? { ...it, ...patch } : it)));
  return (
    <div>
      {items.length === 0 && <p className="muted">No bookmarks. Add one below.</p>}
      {items.map((it, i) => (
        <div key={i} className="row" style={{ marginBottom: 6, paddingLeft: it.level * 24 }}>
          <input type="text" aria-label={`Bookmark ${i + 1} title`} value={it.title} onChange={(e) => set(i, { title: e.target.value })} style={{ maxWidth: 360 }} />
          <label className="check">
            page
            <input type="number" min={1} max={pageCount} value={it.page} aria-label={`Bookmark ${i + 1} page`} onChange={(e) => set(i, { page: Number(e.target.value) })} style={{ width: 80 }} />
          </label>
          <button type="button" className="small" aria-label="Indent" disabled={i === 0} onClick={() => set(i, { level: Math.min(it.level + 1, (items[i - 1]?.level ?? 0) + 1) })}>
            →
          </button>
          <button type="button" className="small" aria-label="Outdent" disabled={it.level === 0} onClick={() => set(i, { level: it.level - 1 })}>
            ←
          </button>
          <button type="button" className="small danger" onClick={() => onChange(items.filter((_, k) => k !== i))}>
            Remove
          </button>
        </div>
      ))}
      <button type="button" onClick={() => onChange([...items, { title: "New bookmark", page: 1, level: 0 }])}>
        Add bookmark
      </button>
    </div>
  );
}
