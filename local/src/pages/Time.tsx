import { useEffect, useState } from "react";
import { useAuth } from '@clerk/clerk-react';
import "./__styles__/time.css"
import { SimpleCalendar } from "../components/calendar/Calendar";
import "../components/__styles__/Calendar.css";
import sonicVShadow from '../assets/sonicvsshadow.jpg';

type TimeEntry = {
    id: string;
    activity: string;
    duration_hours: number;
    duration_minutes: number;
    date: string;
    created_at: string;
    notes?: string | null;
    type: "log" | "event";
    start_time?: string;
};

/* ---------------------------------------------------------
   Modal Component
--------------------------------------------------------- */
function Modal({ open, onClose, children }: any) {
    if (!open) return null;

    return (
        <div className="modal-backdrop">
            <div className="modal">
                {children}
                <button className="sa-btn danger" onClick={onClose}>Close</button>
            </div>
        </div>
    );
}

/* ---------------------------------------------------------
   Calendar Pane — with toolbar
--------------------------------------------------------- */
/* ---------------------------------------------------------
   Calendar Pane — with toggle logs button
--------------------------------------------------------- */
function CalendarPane({
    entries,
    onNewEvent,
    onNewNote,
    onDelete,
    selectedId,
    setSelectedId,
    onCellClick,
    logsOpen,
    setLogsOpen
}: {
    entries: TimeEntry[];
    onNewEvent: () => void;
    onNewNote: () => void;
    onDelete: () => void;
    selectedId: string | null;
    setSelectedId: (id: string | null) => void;
    onCellClick: (date: string) => void;
    logsOpen: boolean;
    setLogsOpen: (open: boolean) => void;
}) {
    return (
        <div className="calendar-pane">
            <div className="calendar-toolbar">
                <button className="sa-btn" onClick={onNewNote}>+ New Log Entry</button>
                <button className="sa-btn" onClick={onNewEvent}>+ New Calendar Event</button>
                <button className="sa-btn danger" onClick={onDelete}>Delete</button>
                <button className="sa-btn" onClick={() => setLogsOpen(!logsOpen)} style={{ background: 'var(--bg-surface-hover)' }}>
                    {logsOpen ? "Hide Logs" : "Show Logs"}
                </button>
            </div>
            <SimpleCalendar
                events={entries.map(e => ({
                    id: e.id,
                    title: e.activity,
                    date: e.date,
                    type: e.type,
                    startTime: e.start_time
                }))}
                selectedId={selectedId}
                setSelectedId={setSelectedId}
                onCellClick={onCellClick}
            />
        </div>
    );
}

/* ---------------------------------------------------------
   Time Log Table
--------------------------------------------------------- */

function TimeLogTable({
    entries,
    selectedId,
    setSelectedId
}: {
    entries: TimeEntry[];
    selectedId: string | null;
    setSelectedId: (id: string | null) => void;
}) {
    return (
        <div className="sa-card glass">
            {/* ⚡ Injected Style Block to guarantee override regardless of file bundle state */}
            <style>{`
                table.sa-table tbody tr.selected-row td {
                    background-color: var(--accent-primary) !important;
                    color: #ffffff !important;
                    font-weight: 600;
                }
            `}</style>

            <table className="sa-table">
                <thead>
                    <tr>
                        <th>Activity</th>
                        <th>Hours</th>
                        <th>Minutes</th>
                        <th>Date</th>
                        <th>Created</th>
                        <th>Notes</th>
                    </tr>
                </thead>
                <tbody>
                    {Array.isArray(entries) && entries.map((e) => (
                        <tr
                            key={e.id}
                            className={selectedId === e.id ? "selected-row" : ""}
                            onClick={() => {
                                if (selectedId === e.id) {
                                    setSelectedId(null);   // deselect
                                } else {
                                    setSelectedId(e.id);   // select
                                }
                            }}
                        >
                            <td>{e.activity}</td>
                            <td>{e.duration_hours}</td>
                            <td>{e.duration_minutes}</td>
                            <td>{e.date}</td>
                            <td>{new Date(e.created_at).toLocaleString()}</td>
                            <td>{e.notes || "—"}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}
/* ---------------------------------------------------------
   Tabs Component
--------------------------------------------------------- */
// function Tabs({
//     active,
//     setActive,
// }: {
//     active: string;
//     setActive: (tab: string) => void;
// }) {
//     return (
//         <div className="tabs">
//             <button
//                 className={active === "log" ? "active" : ""}
//                 onClick={() => setActive("log")}
//             >
//                 Time Log
//             </button>

//             <button
//                 className={active === "summary" ? "active" : ""}
//                 onClick={() => setActive("summary")}
//             >
//                 Summary
//             </button>
//         </div>
//     );
// }

/* ---------------------------------------------------------
   MAIN PAGE — Combined Calendar + Time Workspace
--------------------------------------------------------- */
export function TimeWorkspace() {
    const { getToken } = useAuth();
    const [entries, setEntries] = useState<TimeEntry[]>([]);
    const [events, setEvents] = useState<TimeEntry[]>([]);
    const [loading, setLoading] = useState(true);
    const [activeTab, setActiveTab] = useState("log");
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [eventTitle, setEventTitle] = useState("");
    const [eventDate, setEventDate] = useState("");
    const [eventHours, setEventHours] = useState(0);
    const [eventMinutes, setEventMinutes] = useState(0);
    const [eventNotes, setEventNotes] = useState("");
    const [noteTitle, setNoteTitle] = useState("");
    const [noteBody, setNoteBody] = useState("");
    const [noteMinutes, setNoteMinutes] = useState(0);
    const [noteDate, setNoteDate] = useState("");
    const [selectedEntry, setSelectedEntry] = useState<any>(null);
    const [summaryModalOpen, setSummaryModalOpen] = useState(false);
    const [eventStartTime, setEventStartTime] = useState("");
    const [modalOpen, setModalOpen] = useState(false);
    const [modalType, setModalType] = useState<"event" | "log" | null>(null);
    
    // Toggle state for logs side panel
    const [logsOpen, setLogsOpen] = useState(true);

    const PAAPP_BASE_URL = import.meta.env.VITE_PAAPP_BASE || "https://paapp-u2l9.onrender.com";

    const authenticatedFetch = async (url: string, options: RequestInit = {}) => {
        const token = await getToken();
        return fetch(url, {
            ...options,
            headers: {
                ...options.headers,
                "Authorization": `Bearer ${token}`,
                "Content-Type": "application/json"
            }
        });
    };

    async function handleCreateNote() {
        await authenticatedFetch("https://saapp.onrender.com/api/time/log", {
            method: "POST",
            body: JSON.stringify({
                activity: noteTitle,
                duration_hours: Math.floor(noteMinutes / 60),
                duration_minutes: noteMinutes % 60,
                date: noteDate,
                notes: noteBody,
                type: "log"
            })
        });
        setModalOpen(false);
        fetchEntries();
    }

    async function handleDelete() {
        if (!selectedId) return;

        const isLogEntry = entries.some(e => e.id === selectedId);
        if (!isLogEntry) {
            await authenticatedFetch("https://paapp-u2l9.onrender.com/api/saapp/delete", {
                method: "POST",
                body: JSON.stringify({ id: selectedId })
            });
        }
        
        const endpoint = isLogEntry
            ? `https://saapp.onrender.com/api/time/delete?id=${selectedId}`
            : `https://saapp.onrender.com/api/events/delete?id=${selectedId}`;

        await authenticatedFetch(endpoint, { method: "DELETE" });

        setSelectedId(null);
        fetchEntries();
    }

    async function handleCreateEvent() {
        await authenticatedFetch("https://saapp.onrender.com/api/events/create", {
            method: "POST",
            body: JSON.stringify({
                activity: eventTitle,
                start_time: eventStartTime,
                date: eventDate,
                notes: eventNotes,
                type: "event"
            })
        });

        await authenticatedFetch("https://paapp-u2l9.onrender.com/api/saapp/event", {
            method: "POST",
            body: JSON.stringify({
                username: localStorage.getItem('principal') || 'guest',
                activity: eventTitle,
                start_time: eventStartTime,
                date: eventDate,
                notes: eventNotes,
                type: "event"
            })
        });

        setModalOpen(false);
        fetchEntries();
    }

    async function fetchEntries() {
        setLoading(true);
        try {
            const logRes = await authenticatedFetch(`https://saapp.onrender.com/api/time/list`);
            const logs = await logRes.json();
            setEntries(Array.isArray(logs) ? logs : []);
            
            const eventRes = await authenticatedFetch(`https://saapp.onrender.com/api/events/list`);
            const eventsData = await eventRes.json();
            setEvents(Array.isArray(eventsData) ? eventsData : []); 
        } catch (error) {
            console.error("Failed to fetch entries:", error);
            setEntries([]);
            setEvents([]);
        }
        setLoading(false);
    }

    useEffect(() => {
        fetchEntries();
    }, []);

    if (loading) return <div className="self-service-loading">Loading time entries...</div>;

    return (
        <div
            className="time-workspace"
            style={{
                backgroundImage: `linear-gradient(rgba(18, 24, 36, 0.7), rgba(18, 24, 36, 0.95)), url(${sonicVShadow})`,
                backgroundSize: "cover",
                backgroundRepeat: "no-repeat",
                backgroundPosition: "center",
                minHeight: "100vh",
                display: "grid",
                gridTemplateColumns: logsOpen ? "2fr 1fr" : "1fr",
                gap: "24px",
                padding: "24px",
                transition: "grid-template-columns 0.3s ease"
            }}
        >
            {/* LEFT: Calendar */}
            <CalendarPane
                entries={events}
                onNewEvent={() => { setModalType("event"); setModalOpen(true); }}
                onNewNote={() => { setModalType("log"); setModalOpen(true); }}
                onDelete={handleDelete}
                selectedId={selectedId}
                setSelectedId={setSelectedId}
                onCellClick={(date) => {
                    setModalType("event");
                    setEventDate(date);
                    setModalOpen(true);
                }}
                logsOpen={logsOpen}
                setLogsOpen={setLogsOpen}
            />

            {/* RIGHT: Tabs + Content (Conditionally rendered) */}
            {logsOpen && (
                <div className="details-pane">
                    <div className="tab-content">
                        {activeTab === "log" && (
                            <div className="table-scroll-wrapper">
                                <TimeLogTable
                                    entries={entries}
                                    selectedId={selectedId}
                                    setSelectedId={setSelectedId}
                                />
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* MODAL */}
            <Modal open={modalOpen} onClose={() => setModalOpen(false)}>
                {modalType === "event" && (
                    <div className="modal-form-content">
                        <h3>Create Event</h3>
                        <input className="sa-input" placeholder="Event title" value={eventTitle} onChange={(e) => setEventTitle(e.target.value)} />
                        <input className="sa-input" type="date" value={eventDate} onChange={(e) => setEventDate(e.target.value)} />
                        <input className="sa-input" type="time" value={eventStartTime} onChange={(e) => setEventStartTime(e.target.value)} />
                        <input className="sa-input" type="number" placeholder="Hours" value={Math.floor(noteMinutes / 60)} onChange={(e) => setNoteMinutes(Number(e.target.value) * 60 + (noteMinutes % 60))} />
                        <textarea className="sa-input" placeholder="Notes" value={eventNotes} onChange={(e) => setEventNotes(e.target.value)} />
                        <button className="sa-btn primary-action-btn" onClick={handleCreateEvent}>Create Event</button>
                    </div>
                )}

                {modalType === "log" && (
                    <div className="modal-form-content">
                        <h3>Create Note</h3>
                        <input className="sa-input" placeholder="Note title" value={noteTitle} onChange={(e) => setNoteTitle(e.target.value)} />
                        <input className="sa-input" type="date" value={noteDate} onChange={(e) => setNoteDate(e.target.value)} />
                        <input className="sa-input" type="number" placeholder="Hours" value={Math.floor(noteMinutes / 60)} onChange={(e) => setNoteMinutes(Number(e.target.value) * 60 + (noteMinutes % 60))} />
                        <input className="sa-input" type="number" placeholder="Minutes" value={noteMinutes} onChange={(e) => setNoteMinutes(Number(e.target.value))} />
                        <textarea className="sa-input" placeholder="Notes" value={noteBody} onChange={(e) => setNoteBody(e.target.value)} />
                        <button className="sa-btn primary-action-btn" onClick={handleCreateNote}>Create Note</button>
                    </div>
                )}
            </Modal>
        </div>
    );
}