import "./__styles__/InsightCard.css"
import type { Insight } from "../types/Insight";


function InsightCard({ insight }: { insight: Insight }) {
  return (
    <div className="insight-card glass">
      <h3>{insight.title}</h3>
      <p>{insight.description}</p>
    </div>
  );
}


export default InsightCard;