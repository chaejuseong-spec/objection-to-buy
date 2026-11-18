import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  devIndicators: false, // 화면 구석의 개발용 "N" 표시를 끈다
};

export default nextConfig;
