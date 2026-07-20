import { useState, useEffect } from "react";
import "./__styles__/Taskboard.css";
import TaskCard from "../components/TaskCard";
import { api } from "../api";

interface Task {
  id: string;
  title: string;
  description?: string;
  lane: "backlog" | "in-progress" | "completed";
  createdAt: string;
}

export const Taskboard: React.FC = () => {
  const principal = localStorage.getItem('x-user-id') ?? "";
  const [isAdmin, setIsAdmin] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [newTask, setNewTask] = useState({ title: "", description: "" });

  const [backlog, setBacklog] = useState<Task[]>([]);
  const [inProgress, setInProgress] = useState<Task[]>([]);
  const [completed, setCompleted] = useState<Task[]>([]);

  // Mobile lane tab state
  const [activeMobileLane, setActiveMobileLane] = useState<"backlog" | "in-progress" | "completed">("backlog");

  const [draggedTaskId, setDraggedTaskId] = useState<string | null>(null);
  const [dropTargetId, setDropTargetId] = useState<string | null>(null);
  const [dragOverLane, setDragOverLane] = useState<string | null>(null);
  const [isDropping, setIsDropping] = useState<string | null>(null);
  const [editingTask, setEditingTask] = useState<Task | null>(null);

  async function handleCreateTask() {
    if (!newTask.title.trim()) return;
    const taskData = {
        id: crypto.randomUUID(),
        title: newTask.title,
        description: newTask.description,
        lane: "backlog" as const,
        createdAt: new Date().toISOString(),
    };

    try {
        await api.createTask(taskData, principal);
        setBacklog((prev) => [...prev, taskData]);
        setNewTask({ title: "", description: "" });
        setShowModal(false);
    } catch (err) {
        alert("Failed to save task to backend storage.");
    }
  }

  function getLaneArray(lane: string): Task[] {
    if (lane === "backlog") return backlog;
    if (lane === "in-progress") return inProgress;
    return completed;
  }

  function setLaneArray(lane: string, arr: Task[] | ((prev: Task[]) => Task[])) {
    if (lane === "backlog") {
      setBacklog(arr);
    } else if (lane === "in-progress") {
      setInProgress(arr);
    } else {
      setCompleted(arr);
    }
  }

  function handleDragStart(taskId: string, e?: React.DragEvent) {
    setDraggedTaskId(taskId);
    setIsDropping(null);
    if (e?.dataTransfer) e.dataTransfer.setData("text/plain", taskId);
  }

  function handleDragEnd() {
    setDraggedTaskId(null);
    setDropTargetId(null);
    setDragOverLane(null);
  }

  function handleDragOver(e: React.DragEvent<HTMLDivElement>, lane: string) {
    e.preventDefault();
    setDragOverLane(lane);
  }

  function handleSaveEdit() {
    if (!editingTask) return;
    const { id, lane } = editingTask;
    const arr = getLaneArray(lane);
    const idx = arr.findIndex((t) => t.id === id);

    if (idx !== -1) {
      const updated = [...arr];
      updated[idx] = editingTask;
      setLaneArray(lane, updated);
    }
    setEditingTask(null);
  }

  async function handleDeleteTask(taskId: string) {
    try {
      await api.deleteTask(taskId, principal);
      setBacklog((prev) => prev.filter((t) => t.id !== taskId));
      setInProgress((prev) => prev.filter((t) => t.id !== taskId));
      setCompleted((prev) => prev.filter((t) => t.id !== taskId));
    } catch (err) {
      alert("Failed to remove task from backend storage.");
    }
  }

  function handleDrop(targetLane: Task["lane"]) {
    if (!draggedTaskId) return;
    const lanes = ["backlog", "in-progress", "completed"] as const;

    let sourceLane: Task["lane"] | null = null;
    for (const lane of lanes) {
      const arr = getLaneArray(lane);
      if (arr.some((t) => t.id === draggedTaskId)) {
        sourceLane = lane;
        break;
      }
    }
    
    if (!sourceLane) {
      setDraggedTaskId(null);
      setDropTargetId(null);
      return;
    }

    if (sourceLane === targetLane && dropTargetId) {
      setLaneArray(sourceLane, (prev) => {
        const from = prev.findIndex((t) => t.id === draggedTaskId);
        const to = prev.findIndex((t) => t.id === dropTargetId);
        if (from === -1 || to === -1) return prev;
        const newArr = [...prev];
        const [moved] = newArr.splice(from, 1);
        newArr.splice(to, 0, moved);
        return newArr;
      });
    } else if (sourceLane !== targetLane) {
      let movedTask: Task | null = null;

      setLaneArray(sourceLane, (prev) => {
        const idx = prev.findIndex((t) => t.id === draggedTaskId);
        if (idx === -1) return prev;
        const newArr = [...prev];
        movedTask = { ...newArr[idx], lane: targetLane };
        newArr.splice(idx, 1);
        return newArr;
      });

      setLaneArray(targetLane, (prev) => {
        if (!movedTask) return prev;
        const newArr = [...prev];
        if (dropTargetId) {
          const insertAt = newArr.findIndex((t) => t.id === dropTargetId);
          if (insertAt === -1) newArr.push(movedTask);
          else newArr.splice(insertAt, 0, movedTask);
        } else {
          newArr.push(movedTask);
        }
        return newArr;
      });
    }

    if (principal) {
      api.updateTask(draggedTaskId, { lane: targetLane }, principal).catch((err) => {
        console.error("Failed to save lane position to backend:", err);
      });
    }

    setIsDropping(draggedTaskId);
    setDraggedTaskId(null);
    setDragOverLane(null);
    setDropTargetId(null);
    setTimeout(() => setIsDropping(null), 200);
  }
  
  useEffect(() => {
    async function loadBackendTasks() {
      try {
        const allTasks = await api.getTasks();
        setBacklog(allTasks.filter((t: any) => t.lane === "backlog"));
        setInProgress(allTasks.filter((t: any) => t.lane === "in-progress"));
        setCompleted(allTasks.filter((t: any) => t.lane === "completed"));
      } catch (err) {
        console.error("Failed to load persistent tasks from backend", err);
      }
    }
    loadBackendTasks();
  }, []);

  useEffect(() => {
    async function resolveAdmin() {
      if (!principal) {
        setIsAdmin(false);
        return;
      }
      try {
        const me = await api.getMe(principal);
        const groups: string[] = Array.isArray(me.groups) ? me.groups : [];
        setIsAdmin(groups.includes("Taskboard_Admins"));
      } catch (err) {
        setIsAdmin(false);
      }
    }
    resolveAdmin();
  }, [principal]);

  const renderTaskCardItem = (task: Task) => (
    <div
      key={task.id}
      draggable
      onDragStart={(e) => handleDragStart(task.id, e)}
      onDragEnd={handleDragEnd}
      onDragOver={(e) => {
        e.preventDefault();
        if (draggedTaskId && draggedTaskId !== task.id) {
          setDropTargetId(task.id);
          setDragOverLane(task.lane);
        }
      }}
      onDragLeave={() => {
        if (dropTargetId === task.id) setDropTargetId(null);
      }}
      className={
        (task.id === draggedTaskId
          ? "taskcard-dragging"
          : task.id === isDropping
          ? "taskcard-drop"
          : "") + (dropTargetId === task.id ? " task-drop-target" : "")
      }
    >
      <TaskCard task={task} />
      <div className="task-actions">
        <button onClick={() => setEditingTask(task)}>Edit</button>
        <button onClick={() => handleDeleteTask(task.id)}>Delete</button>
      </div>
    </div>
  );

  return (
    <>
      <div className="taskboard-header">
        <button
          className="task-add-btn"
          onClick={() => isAdmin && setShowModal(true)}
          disabled={!isAdmin}
          title={!isAdmin ? "Only Taskboard_Admins can create tasks" : "Add Task"}
        >
          Add Task
        </button>
      </div>

      {/* Mobile Tab Switcher */}
      <div className="mobile-lane-tabs">
        <button 
          className={activeMobileLane === "backlog" ? "active" : ""} 
          onClick={() => setActiveMobileLane("backlog")}
        >
          Backlog ({backlog.length})
        </button>
        <button 
          className={activeMobileLane === "in-progress" ? "active" : ""} 
          onClick={() => setActiveMobileLane("in-progress")}
        >
          In Progress ({inProgress.length})
        </button>
        <button 
          className={activeMobileLane === "completed" ? "active" : ""} 
          onClick={() => setActiveMobileLane("completed")}
        >
          Completed ({completed.length})
        </button>
      </div>

      <div className="taskboard">
        {/* Backlog */}
        <div
          className={`task-lane ${activeMobileLane !== "backlog" ? "mobile-lane-hidden" : ""} ${dragOverLane === "backlog" ? "drag-over" : ""}`}
          data-lane="backlog"
          onDragOver={(e) => handleDragOver(e, "backlog")}
          onDrop={() => handleDrop("backlog")}
        >
          <h2>Backlog</h2>
          {backlog.map(renderTaskCardItem)}
        </div>

        {/* In Progress */}
        <div
          className={`task-lane ${activeMobileLane !== "in-progress" ? "mobile-lane-hidden" : ""} ${dragOverLane === "in-progress" ? "drag-over" : ""}`}
          data-lane="in-progress"
          onDragOver={(e) => handleDragOver(e, "in-progress")}
          onDrop={() => handleDrop("in-progress")}
        >
          <h2>In Progress</h2>
          {inProgress.map(renderTaskCardItem)}
        </div>

        {/* Completed */}
        <div
          className={`task-lane ${activeMobileLane !== "completed" ? "mobile-lane-hidden" : ""} ${dragOverLane === "completed" ? "drag-over" : ""}`}
          data-lane="completed"
          onDragOver={(e) => handleDragOver(e, "completed")}
          onDrop={() => handleDrop("completed")}
        >
          <h2>Completed</h2>
          {completed.map(renderTaskCardItem)}
        </div>
      </div>

      {/* CREATE MODAL */}
      {showModal && (
        <div className="task-modal-overlay">
          <div className="task-modal">
            <h3>Create Task</h3>
            <input
              type="text"
              placeholder="Task title"
              value={newTask.title}
              onChange={(e) => setNewTask({ ...newTask, title: e.target.value })}
            />
            <textarea
              placeholder="Description (optional)"
              value={newTask.description}
              onChange={(e) => setNewTask({ ...newTask, description: e.target.value })}
            />
            <div className="task-modal-actions">
              <button onClick={handleCreateTask}>Create</button>
              <button onClick={() => setShowModal(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {/* EDIT MODAL */}
      {editingTask && (
        <div className="task-modal-overlay">
          <div className="task-modal">
            <h3>Edit Task</h3>
            <input
              type="text"
              value={editingTask.title}
              onChange={(e) => setEditingTask({ ...editingTask, title: e.target.value })}
            />
            <textarea
              value={editingTask.description || ""}
              onChange={(e) => setEditingTask({ ...editingTask, description: e.target.value })}
            />
            <div className="task-modal-actions">
              <button onClick={handleSaveEdit}>Save</button>
              <button onClick={() => setEditingTask(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};