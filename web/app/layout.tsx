import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "中国行政区划 · 五级数据浏览",
  description:
    "省 / 地 / 县 / 乡 四级取自民政部国家地名信息库，村级取自国家统计局 2023 年快照（已停发）",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
