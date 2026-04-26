import type { Metadata } from "next";
import { Anton, Geist, Inter, Noto_Sans_SC } from "next/font/google";
import "./globals.css";
import ToastContainer from "@/components/ui/Toast";
import WorkspaceFrame from "@/components/shell/WorkspaceFrame";

const headingFont = Anton({
  weight: "400",
  subsets: ["latin"],
  display: "swap",
  variable: "--font-heading-loaded",
});

const primaryFont = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-primary-loaded",
});

const captionFont = Geist({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-caption-loaded",
});

const cjkFont = Noto_Sans_SC({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
  variable: "--font-primary-cjk-loaded",
});

export const metadata: Metadata = {
  title: "AgentD",
  description: "Enterprise AI Agent Platform",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body
        className={`${headingFont.variable} ${primaryFont.variable} ${captionFont.variable} ${cjkFont.variable} antialiased`}
      >
        <WorkspaceFrame>{children}</WorkspaceFrame>
        <ToastContainer />
      </body>
    </html>
  );
}
