// src/components/TaskCard.tsx
import React from "react";
import "./__styles__/TaskCard.css"

interface TaskCardProps {
  task: {
    id: string;
    title: string;
    description?: string;
    lane: "backlog" | "in-progress" | "completed";
    createdAt: string;
  };
}

export default function TaskCard({ task }: TaskCardProps) {
  return (
    <div className="task-card">
      <h3 className="task-card-title">{task.title}</h3>

      {task.description && (
        <p className="task-card-desc">{task.description}</p>
      )}

      <div className="task-card-meta">
        <span>{new Date(task.createdAt).toLocaleDateString()}</span>
      </div>
    </div>
  );
}
