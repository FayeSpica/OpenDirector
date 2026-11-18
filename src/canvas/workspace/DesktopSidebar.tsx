import { useEffect, useState } from "react";
import { listWorkspaceProjects, type WorkspaceProject } from "../../backend/workspace";
import { errorMessage } from "../../backend/api";
import { useT } from "../../i18n/I18nProvider";
import logo from "../../assets/open_director_logo.png";

export function DesktopSidebar({ open, currentId, disabled, onToggle, onCreate, onSelect, onSettings }: {
  open: boolean; currentId: string; disabled: boolean; onToggle: () => void;
  onCreate: () => void; onSelect: (project: WorkspaceProject) => Promise<boolean>; onSettings: () => void;
}) {
  const t = useT();
  const [projects, setProjects] = useState<WorkspaceProject[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    let active = true;
    void listWorkspaceProjects().then(result => {
      if (active) { setProjects(result.projects); setError(result.warnings.join("\n")); }
    }).catch(error => { if (active) setError(errorMessage(error)); });
    return () => { active = false; };
  }, [open, currentId]);
  return <div className="od-desktop-navigation">
    <div className="od-caption-actions">
      <button type="button" onClick={onToggle} aria-expanded={open} aria-controls="od-project-sidebar" title={t("desktop.sidebar.toggle")} aria-label={t("desktop.sidebar.toggle")}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/></svg>
      </button>
      {!open && <button type="button" disabled={disabled} onClick={onCreate} title={t("canvas.project.create")} aria-label={t("canvas.project.create")}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="12" cy="12" r="9"/><path d="M12 7v10M7 12h10"/></svg></button>}
    </div>
    <aside id="od-project-sidebar" className="od-project-sidebar" inert={!open} aria-hidden={!open}>
      <div className="od-sidebar-brand"><img src={logo} alt=""/>OpenDirector</div>
      <button className="od-sidebar-create" type="button" disabled={disabled} onClick={onCreate}><span aria-hidden="true">＋</span>{t("canvas.project.create")}</button>
      <div className="od-sidebar-label">{t("desktop.sidebar.projects")}</div>
      <nav aria-label={t("desktop.sidebar.projects")}>
        {projects.map(project => <button type="button" key={project.id} disabled={disabled} aria-current={project.id === currentId ? "page" : undefined} onClick={() => { void onSelect(project); }}><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="3" y="5" width="18" height="15" rx="2"/><path d="M3 10h18M8 5l3 5M15 5l3 5"/></svg><span>{project.name}</span></button>)}
      </nav>
      {error && <p role="alert" className="od-sidebar-error">{error}</p>}
      <button className="od-sidebar-settings" type="button" onClick={onSettings}>{t("canvas.workspace.toolbar.settings")}</button>
    </aside>
  </div>;
}
