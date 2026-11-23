import type { Metadata } from "next";
import { Hahmlet } from "next/font/google";
import "./globals.css";

// 리포트 제목과 인용문에 쓰는 명조. 인터넷이 없으면 설치된 명조 글꼴로 대신 보인다.
const serif = Hahmlet({
  weight: ["400", "500", "600", "700"],
  subsets: ["latin"],
  display: "swap",
  variable: "--font-serif",
  preload: false,
});

export const metadata: Metadata = {
  title: "매수에 이의 있습니다!",
  description: "기업분석 리포트를 올리면 AI 리스크 검토자와 배심원이 투자 논리를 따져 묻는 검증 작업대",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko" className={serif.variable}>
      <body>{children}</body>
    </html>
  );
}
