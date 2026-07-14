import { useEffect, useState } from "react";
import InsightCard from "../components/InsightCard";
import { api } from "../api";
import SvS from "../assets/sonic-vs-shadow.jpg"
import "./__styles__/InsightsPage.css"
import { CategoryBreakdownChart } from "../components/InsightsGraph"
 

export interface Insight {
  title: string;
  description: string;
  data: any;
}

export default function InsightsPage() {
  const [insights, setInsights] = useState<Insight[]>([]);
  const categoryInsight = insights.find(
    (i) => i.title === "Most Frequent Activity Category"
  );
  const username = localStorage.getItem("username") || "default_user";

  useEffect(() => {
    async function fetchInsights() {
        const res = await api.getInsights(username);
        setInsights(res || []); // Map the returned array directly
    }
    fetchInsights();
    }, [username]);

  return (
    <div className="insights-container"
    style={{
                backgroundImage: `linear-gradient(rgba(18, 24, 36, 0.7), rgba(18, 24, 36, 0.95)), url(${SvS})`,
                backgroundSize: "cover",
                backgroundRepeat: "no-repeat",
                backgroundPosition: "center",
                minHeight: "100vh",
                display: "flex",
                flexDirection: "row",
                gap: "24px",
                padding: "24px"
            }}>
      <h1>Insights</h1>

      <div className="insights-grid">
        {insights.map((insight, idx) => (
          <InsightCard key={idx} insight={insight} />
        ))}
      </div>
      {categoryInsight && (
        <div className="chart-section glass">
          <h2>Activity Category Breakdown</h2>
          <CategoryBreakdownChart data={categoryInsight.data} />
        </div>
      )}
      </div>
  );
}
