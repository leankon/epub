import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Nav } from "@/components/Nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "PDF Workstation",
  description: "Private, local-first PDF, OCR and EPUB workstation",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main">
          Skip to content
        </a>
        <div className="shell">
          <aside className="sidebar" aria-label="Main navigation">
            <div className="brand">PDF Workstation</div>
            <p className="privacy" role="note">
              <span aria-hidden="true">●</span>
              <span>
                Files are processed locally on this computer. Nothing is uploaded to the internet.
              </span>
            </p>
            <Nav />
          </aside>
          <main id="main">{children}</main>
        </div>
      </body>
    </html>
  );
}
