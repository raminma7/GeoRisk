import type { Metadata } from "next";
import "maplibre-gl/dist/maplibre-gl.css";
import "./globals.css";
import Nav from "@/components/Nav";
import { ViewProvider } from "@/lib/view";

export const metadata: Metadata = {
  title: "GeoRisk — Global Geopolitical Monitoring",
  description: "Automated, evidence-based monitoring of geopolitical developments and documented conflicts.",
};

// Applies a stored theme before paint to avoid a flash of the wrong theme.
const themeScript = `try{var t=localStorage.getItem('georisk.theme');if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t)}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-screen font-sans antialiased">
        <ViewProvider>
          <Nav />
          <main className="mx-auto max-w-[1500px] px-4 py-5">{children}</main>
        </ViewProvider>
        <footer className="mx-auto max-w-[1500px] px-4 pb-8 text-xs text-muted">
          Data: news reports collected automatically by The GDELT Project (not verified one by one) · verified conflict
          deaths from UCDP (CC BY 4.0). Severity reflects what is being reported, not a prediction of war.
        </footer>
      </body>
    </html>
  );
}
