"use client";

import GlobalOverview from "@/components/GlobalOverview";
import TodayView from "@/components/Today";

export default function HomePage() {
  return (
    <div className="space-y-8">
      <GlobalOverview />
      <TodayView />
    </div>
  );
}
