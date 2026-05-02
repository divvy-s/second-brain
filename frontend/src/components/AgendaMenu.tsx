import { X, Check } from "lucide-react";

type AgendaMenuProps = {
  isOpen: boolean;
  onClose: () => void;
  tasks: any[];
  schedule: any[];
};

export function AgendaMenu({ isOpen, onClose, tasks, schedule }: AgendaMenuProps) {
  if (!isOpen) return null;

  return (
    <div className="sidebar-menu-overlay" onClick={onClose}>
      <div className="sidebar-menu-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="action-center-header" style={{ padding: "0 0 20px 0", marginBottom: "10px" }}>
          <h2 style={{ fontSize: "1.1rem" }}>Agenda</h2>
          <button className="btn btn-icon" onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <div className="sidebar-section" style={{ marginTop: "10px" }}>
          <h3 className="section-title">
            Pending Tasks
            {tasks.length > 0 && <span className="badge" style={{ marginLeft: 8 }}>{tasks.length}</span>}
          </h3>
          {tasks.length === 0 ? (
            <div className="approval-empty">No pending tasks.</div>
          ) : (
            <div className="compact-list">
              {tasks.map((task) => (
                <div className="compact-item" key={task.id}>
                  <div className="compact-item-icon task"><Check size={12} /></div>
                  <div className="compact-item-content">
                    <div className="compact-item-title">{task.title}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="sidebar-section" style={{ marginTop: "20px" }}>
          <h3 className="section-title">
            Upcoming Schedule
            {schedule.length > 0 && <span className="badge" style={{ marginLeft: 8 }}>{schedule.length}</span>}
          </h3>
          {schedule.length === 0 ? (
            <div className="approval-empty">No upcoming events.</div>
          ) : (
            <div className="compact-list">
              {schedule.map((event) => (
                <div className="compact-item" key={event.id}>
                  <div className="compact-item-icon event">📅</div>
                  <div className="compact-item-content">
                    <div className="compact-item-title">{event.title}</div>
                    <div className="compact-item-meta">
                      {new Date(event.occurred_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

      </div>
    </div>
  );
}
