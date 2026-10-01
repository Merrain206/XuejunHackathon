import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Ask the Company · 思看科技",
  description: "基于可验证证据理解一家公司。",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
