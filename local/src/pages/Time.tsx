import { useEffect, useState } from 'react';
import "./__styles__/Time.css";

type TimeEntry = {
    id: string;
    activity: string;
    duration_hours: number;
    duration_minutes: number;
    date: string;
    created_at: string;
    notes?: string | null;
};



export function TimeTrackingSheet() {
    const [entries, setEntries] = useState<TimeEntry[]>([]);
    const [loading, setLoading] = useState(true);

    async function fetchEntries() {
        setLoading(true);
        const username = localStorage.getItem("principal");
        const res = await fetch(`http://127.0.0.1:8000/api/time/list?username=${username}`);
        const data: TimeEntry[] = await res.json();
        setEntries(data);
        setLoading(false);
    }

    useEffect(() => {
        fetchEntries();
    }, []);

    if (loading) return <div>Loading time entries...</div>;

    return (
        <div className="page-container">
            <h1>Time Tracking</h1>

            <div className="sa-card">
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
                        {entries.map(e => (
                            <tr key={e.id}>
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
        </div>
    );

}

