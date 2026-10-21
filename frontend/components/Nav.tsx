"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS: [string, string][] = [
  ["/", "Home"],
  ["/tools", "PDF tools"],
  ["/ocr", "OCR & scanned books"],
  ["/epub", "PDF to EPUB"],
  ["/epub-to-pdf", "EPUB to PDF"],
  ["/office", "Office conversion"],
  ["/batch", "Batch processing"],
  ["/library", "Document library"],
  ["/history", "Processing history"],
  ["/storage", "Storage & backups"],
  ["/settings", "Settings"],
];

export function Nav() {
  const path = usePathname();
  return (
    <nav>
      <ul className="nav">
        {ITEMS.map(([href, label]) => {
          const active = href === "/" ? path === "/" : path === href || path.startsWith(href + "/");
          return (
            <li key={href}>
              <Link href={href} aria-current={active ? "page" : undefined}>
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
