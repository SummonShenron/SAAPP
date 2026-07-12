import { useState } from "react";
import "../__styles__/Calendar.css"

type CalendarEvent = {
  id: string;
  title: string;
  date: string; // YYYY-MM-DD
  type: string;
  startTime?: string;
};

export function SimpleCalendar({
  events,
  selectedId,
  setSelectedId,
  onCellClick
}: {
  events: CalendarEvent[],
  selectedId: string | null,
  setSelectedId: (id: string | null) => void,
  onCellClick: (date: string) => void;
}) {
  const today = new Date();
  const [currentMonth, setCurrentMonth] = useState(today.getMonth());
  const [currentYear, setCurrentYear] = useState(today.getFullYear());
  const firstDay = new Date(currentYear, currentMonth, 1);
  const lastDay = new Date(currentYear, currentMonth + 1, 0);

  const daysInMonth = lastDay.getDate();
  const startWeekday = firstDay.getDay(); // 0 = Sunday

  const prevMonth = () => {
    if (currentMonth === 0) {
      setCurrentMonth(11);
      setCurrentYear(currentYear - 1);
    } else {
      setCurrentMonth(currentMonth - 1);
    }
  };

  const nextMonth = () => {
    if (currentMonth === 11) {
      setCurrentMonth(0);
      setCurrentYear(currentYear + 1);
    } else {
      setCurrentMonth(currentMonth + 1);
    }
  };

  const monthName = firstDay.toLocaleString("default", { month: "long" });

  const grid: Array<{ date: string | null; events: CalendarEvent[] }> = [];

  // Fill empty days before month starts
  for (let i = 0; i < startWeekday; i++) {
    grid.push({ date: null, events: [] });
  }

  // Fill actual days
  for (let day = 1; day <= daysInMonth; day++) {
    const dateStr = `${currentYear}-${String(currentMonth + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const dayEvents = events.filter((e) => e.date === dateStr);

    grid.push({ date: dateStr, events: dayEvents });
  }

  return (
    <div className="simple-calendar glass">
      <div className="cal-header">
        <button onClick={prevMonth}>◀</button>
        <h2>{monthName} {currentYear}</h2>
        <button onClick={nextMonth}>▶</button>
      </div>
      <div className="cal-weekdays">
        {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((d) => (
          <div key={d} className="cal-weekday">{d}</div>
        ))}
      </div>

      <div className="cal-grid">
        

        {grid.map((cell, idx) => (
          <div
            key={idx}
            className="cal-cell"
            onClick={() => {
              if (!cell.date) return;
              onCellClick(cell.date);
            }}
          >
            {cell.date && (
              <>
                <div className="cal-date">
                  {cell.date.split("-")[2]}
                </div>

                {cell.events.map((ev) => (
                  <div
                    key={ev.id}
                    className={`cal-event ${selectedId === ev.id ? "selected" : ""}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (selectedId === ev.id) setSelectedId(null);
                      else setSelectedId(ev.id);
                    }}
                  >
                    {ev.startTime && (
                        <span style={{ fontWeight: "bold", marginRight: "4px" }}>
                            {ev.startTime}
                        </span>
                    )}
                    {ev.title}
                  </div>
                ))}
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );

}
