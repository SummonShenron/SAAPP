import { Bar } from "react-chartjs-2";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
  Legend
} from "chart.js";

ChartJS.register(CategoryScale, LinearScale, BarElement, Title, Tooltip, Legend);

interface CategoryBreakdownChartProps {
  data: [string, number][];
}

export function CategoryBreakdownChart({ data }: CategoryBreakdownChartProps) {
  const labels = data.map(([category]) => category);
  const values = data.map(([_, count]) => count);

  return (
    <div style={{ width: "100%", height: "300px" }}>
      <Bar
        data={{
          labels,
          datasets: [
            {
              label: "Activity Count",
              data: values,
              backgroundColor: "rgba(99, 102, 241, 0.6)",
              borderColor: "rgba(99, 102, 241, 1)",
              borderWidth: 1,
            },
          ],
        }}
        options={{
          responsive: true,
          maintainAspectRatio: false,
        }}
      />
    </div>
  );
}
