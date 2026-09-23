import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
import { HandoffBuilder, HandoffPreview } from "./handoff.jsx";

const isOwner = location.pathname === "/owner";
const isInvite = location.pathname === "/invite";
const isIdentify = location.pathname.startsWith("/identify");
let csrf = "";
async function api(path, body) {
  const response = await fetch("/api" + path, {
    method: body === undefined ? "GET" : "POST",
    headers:
      body === undefined
        ? {}
        : { "Content-Type": "application/json", "X-Prism-CSRF": csrf },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(data.detail || "This operation could not be completed.");
  if (data.csrf) csrf = data.csrf;
  return data;
}
const short = (value) => value?.slice(0, 12);
const when = (value) =>
  new Date(value * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
const money = (value) => "$" + (Math.max(0, value) / 100).toFixed(2);
const cleanProject = (value) =>
  value?.replace(" · synthetic", "") || "Shared work";
const actionId = (action) =>
  typeof action === "string" ? action : action?.id || null;
const actionWithRuntimeProfile = (action, runtimeProfile) =>
  action === "json-check" && runtimeProfile
    ? { id: action, profile: runtimeProfile }
    : action;
const isReferenceLinuxAction = (action) =>
  typeof action === "object" && action?.profile === "reference-linux";
const actionLabel = (action) => {
  const id = actionId(action);
  if (typeof action === "object" && action?.label) return action.label;
  if (id === "json-check") return "Check JSON syntax";
  if (id === "bootstrap") return "Rerun synthetic bootstrap";
  return id ? id.replaceAll("-", " ") : "No execution action";
};
const actionDescription = (action) => {
  const id = actionId(action);
  if (id === "json-check")
    return isReferenceLinuxAction(action)
      ? "Checks the syntax of the selected JSON files with a fixed parser in a Kata VM under the reference Linux runtime. It does not run project code. This profile is not a private pilot."
      : "Checks the syntax of the selected JSON files with a fixed parser in a constrained development container. It does not run project code.";
  if (id === "bootstrap")
    return "Runs the fixed synthetic bootstrap evaluator with one integer seed from 0–1,000. It does not run selected project code.";
  return id
    ? "Runs only this fixed, reviewed action with server-validated inputs."
    : "This project has no configured execution action.";
};
const actionInputs = (action) =>
  typeof action === "object" && Array.isArray(action?.required_inputs)
    ? action.required_inputs
    : [];
const missingCatalogActionInputs = (action, selected) => {
  const id = actionId(action);
  if (
    id === "json-check" &&
    !selected.some((name) => name.toLowerCase().endsWith(".json"))
  )
    return ["at least one selected .json file"];
  if (id === "bootstrap")
    return ["observations.csv", "baseline.json", "limitations.md"].filter(
      (name) => !selected.includes(name),
    );
  return [];
};
function ActionSummary({ action, detailed = false }) {
  if (!action) return null;
  const limits = [
    action.timeout_seconds && `${action.timeout_seconds} seconds`,
    action.memory_mib && `${action.memory_mib} MiB memory`,
    action.cpu && `${action.cpu} CPU`,
    action.processes && `${action.processes} processes`,
    action.scratch_mib && `${action.scratch_mib} MiB scratch`,
    action.network === "none" && "no task network",
  ].filter(Boolean);
  return (
    <>
      <strong>{actionLabel(action)}</strong>
      <p>{actionDescription(action)}</p>
      {!!actionInputs(action).length && (
        <p className="muted fine">
          Required selected inputs: {actionInputs(action).join(", ")}.
        </p>
      )}
      {detailed && !!limits.length && (
        <p className="muted fine">Runtime limits: {limits.join(" · ")}.</p>
      )}
    </>
  );
}
function Icon({ name, size = 18 }) {
  const paths = {
    plus: "M12 5v14M5 12h14",
    arrow: "M5 12h14m-6-6 6 6-6 6",
    up: "M12 19V5m-6 6 6-6 6 6",
    chevron: "m9 5 7 7-7 7",
    back: "m14 6-6 6 6 6",
    close: "m6 6 12 12M6 18 18 6",
    folder: "M3 7V5h6l2 2h10v13H3Z",
    file: "M6 3h8l4 4v14H6Zm8 0v5h4M9 12h6m-6 4h6",
    chat: "M4 4h16v12H9l-5 4ZM8 8h8m-8 4h5",
    play: "m8 5 11 7-11 7Z",
    shield: "m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Zm-4 9 3 3 5-6",
    activity: "M3 12h4l3-8 4 16 3-8h4",
    inbox: "M5 4h14l3 11v5H2v-5Zm-3 11h6l2 3h4l2-3h6",
    settings: "M4 7h16M4 17h16M8 4v6m8 4v6",
    refresh: "M20 10a8 8 0 1 0-1 7M20 4v6h-6",
    search: "M10 17a7 7 0 1 0 0-14 7 7 0 0 0 0 14Zm5-2 6 6",
    menu: "M4 6h16M4 12h16M4 18h16",
    check: "m5 12 4 4L19 6",
    external: "M14 3h7v7m0-7L10 14M10 4H4v16h16v-6",
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.65"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name] || paths.file} />
    </svg>
  );
}
function Mark({ large = false }) {
  return (
    <span className={"prism-mark" + (large ? " large" : "")} aria-hidden="true">
      <svg viewBox="0 0 32 32" fill="none">
        <path
          d="m16 3 13 24H3L16 3Z"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinejoin="round"
        />
        <path
          d="m16 3 1 17 12 7M3 27l14-7"
          stroke="currentColor"
          strokeWidth="1.3"
        />
      </svg>
    </span>
  );
}
function Badge({ children, tone = "" }) {
  return <span className={"badge " + tone}>{children}</span>;
}
function Notice({ children }) {
  return (
    <div className="notice">
      <Icon name="shield" size={15} />
      <div>{children}</div>
    </div>
  );
}
function Lines({ text, start = 1 }) {
  return (
    <div className="lines">
      {text.split("\n").map((line, i) => (
        <div className="line" key={i}>
          <span>{start + i}</span>
          <code>{line || " "}</code>
        </div>
      ))}
    </div>
  );
}
function Empty({ icon = "folder", title, children }) {
  return (
    <div className="empty-state">
      <span className="empty-icon">
        <Icon name={icon} size={25} />
      </span>
      <h2>{title}</h2>
      <p>{children}</p>
    </div>
  );
}
function Heading({ eyebrow, title, description, children }) {
  return (
    <div className="page-heading">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {children}
    </div>
  );
}
function Sheet({ title, children, onClose }) {
  const ref = useRef(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className="sheet"
      aria-labelledby="sheet-title"
      onCancel={onClose}
    >
      <div className="sheet-header">
        <h2 id="sheet-title">{title}</h2>
        <button
          autoFocus
          className="icon-button"
          aria-label="Close panel"
          onClick={onClose}
        >
          <Icon name="close" />
        </button>
      </div>
      <div className="sheet-body">{children}</div>
    </dialog>
  );
}
function FileSheet({ file, onClose }) {
  return (
    <Sheet title={file.name} onClose={onClose}>
      <div className="source-caption">
        <Badge>Source evidence</Badge>
        {file.version && <span>Version {short(file.version)}</span>}
      </div>
      <Lines text={file.text} start={file.start || 1} />
      <details className="disclosure">
        <summary>Source identity</summary>
        <p className="wrap">SHA-256 {file.sha256}</p>
        {file.end && (
          <p>
            Lines {file.start}–{file.end}
          </p>
        )}
        <p className="muted">
          The reference identifies these bytes. It does not establish that a
          claim is correct.
        </p>
      </details>
    </Sheet>
  );
}
function Shell({ actor, title, nav, children, primary, bottom, identityMode }) {
  const workspace = useRef(null);
  useEffect(() => {
    workspace.current?.scrollTo({ top: 0 });
  }, [title]);
  const [menu, setMenu] = useState(false);
  const [narrow, setNarrow] = useState(
    () => matchMedia("(max-width: 760px)").matches,
  );
  useEffect(() => {
    const media = matchMedia("(max-width: 760px)");
    const update = () => {
      setNarrow(media.matches);
      setMenu(false);
    };
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return (
    <div
      className={"app-shell" + (menu ? " menu-open" : "")}
      onKeyDown={(event) => {
        if (event.key === "Escape") setMenu(false);
      }}
    >
      <aside
        className="sidebar"
        aria-label="Workspace navigation"
        inert={narrow && !menu}
      >
        <a className="brand" href={isOwner ? "/owner" : "/review"}>
          <Mark />
          <span>prism</span>
          <Badge>Preview</Badge>
        </a>
        <div className="workspace-label">
          <span className="workspace-avatar">{isOwner ? "O" : "R"}</span>
          <span>
            {isOwner ? "Owner workspace" : "Review workspace"}
            <small>{actor}</small>
          </span>
        </div>
        <div onClick={() => setMenu(false)}>{primary}</div>
        <nav>
          {nav.map((item) => (
            <button
              key={item.id}
              className={"nav-item" + (item.active ? " active" : "")}
              aria-current={item.active ? "page" : undefined}
              onClick={() => {
                item.onClick();
                setMenu(false);
              }}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
              {item.count > 0 && (
                <span className="nav-count">{item.count}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom" onClick={() => setMenu(false)}>
          {bottom}
          <div className="local-label">
            <span />
            {identityMode ? (
              <>
                Private server<small>Externally hosted app</small>
              </>
            ) : (
              <>
                Local demo<small>Configured projects · This computer</small>
              </>
            )}
          </div>
        </div>
      </aside>
      {menu && (
        <button
          className="menu-scrim"
          aria-label="Close navigation"
          onClick={() => setMenu(false)}
        />
      )}
      <div className="workspace" ref={workspace}>
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              aria-label="Toggle navigation"
              aria-expanded={menu}
              onClick={() => setMenu(!menu)}
            >
              <Icon name="menu" />
            </button>
            <span>Workspace</span>
            <Icon name="chevron" size={12} />
            <strong>{title}</strong>
          </div>
          <span className="topbar-context">
            <Icon name="shield" size={14} />
            Limited sharing
          </span>
        </header>
        {children}
      </div>
    </div>
  );
}
function ModelDetails({ model, act, refresh, busy, owner = false }) {
  const configured = model.status === "configured";
  return (
    <>
      <h3>
        {configured
          ? model.model
          : model.status === "blocked"
            ? "Model use blocked"
            : "Model not configured"}
      </h3>
      <p>
        {!configured
          ? model.blocked_reason ||
            "Evidence browsing is available. The owner must configure a model before conversation is available. No reply is simulated."
          : owner
            ? "Owner questions, this conversation’s history, the explicitly selected project evidence and permitted tool results are sent to OpenAI when model use is enabled for the conversation. Collaborators receive only their separately reviewed share."
            : "Questions, approved evidence, this session’s history and authorized tool results are sent to the configured provider."}
      </p>
      {model.endpoint && (
        <>
          <p className="wrap">{model.endpoint}</p>
          <div className="info-grid">
            <span>Configured allowance</span>
            <strong>{money(model.budget_cents)}</strong>
            <span>Remaining reservation</span>
            <strong>{money(model.budget_cents - model.reserved_cents)}</strong>
          </div>
          <button
            className="secondary"
            disabled={busy}
            onClick={() => act(refresh)}
          >
            <Icon name="refresh" size={15} />
            Refresh allowance
          </button>
          <p className="muted">
            This is a local allowance, not the provider bill. Reservations
            persist across restarts. Provider retention may apply even with
            store=false.
          </p>
        </>
      )}
      <hr />
      <h3>Conversation visibility</h3>
      <p>
        {owner
          ? "Owner project chats are private from collaborators. Only explicitly selected and approved conversation context is shared. Both roles share one persistent model allowance."
          : "The owner can review local activity and conversations."}{" "}
        Hidden reasoning is not stored. No remote analytics are enabled.
      </p>
      <Notice>Local execution does not mean local model inference.</Notice>
    </>
  );
}
function App() {
  const [state, setState] = useState(null),
    [authMode, setAuthMode] = useState(null),
    [authRequired, setAuthRequired] = useState(false),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const pending = useRef(0);
  async function refresh() {
    setState(await api(isOwner ? "/owner/state" : "/review/state"));
  }
  async function act(fn) {
    pending.current++;
    setBusy(true);
    setError("");
    try {
      return await fn();
    } catch (e) {
      setError(e.message);
    } finally {
      pending.current--;
      setBusy(pending.current > 0);
    }
  }
  useEffect(() => {
    const token = new URLSearchParams(location.hash.slice(1)).get("token");
    if (location.hash) history.replaceState(null, "", location.pathname);
    act(async () => {
      if (isIdentify && location.pathname === "/identify/completed") return;
      if (isInvite || isIdentify) {
        if (!token)
          throw new Error(
            "This identity link is incomplete or no longer available.",
          );
        const started = await api(
          isIdentify ? "/auth/oidc/discovery" : "/auth/oidc/invitation",
          { token },
        );
        location.assign(started.authorization_url);
        return;
      }
      const mode = await api("/auth/mode");
      setAuthMode(mode);
      if (token && !mode.identity_mode) await api("/login", { token });
      try {
        await refresh();
      } catch (loginError) {
        if (mode.identity_mode) setAuthRequired(true);
        else throw loginError;
      }
    });
  }, []);
  return (
    <>
      {error && (
        <div role="alert" className="global-error">
          <span>
            {isInvite || isIdentify
              ? "This link could not be verified. Ask the owner for a new link."
              : error}
          </span>
          <button
            className="icon-button"
            aria-label="Dismiss error"
            onClick={() => setError("")}
          >
            <Icon name="close" size={16} />
          </button>
        </div>
      )}
      {!state ? (
        <div className="login">
          <Mark large />
          <h1>
            {isIdentify
              ? location.pathname === "/identify/completed"
                ? "Account verified."
                : "Verifying the account."
              : isInvite
                ? "Verifying your invitation."
                : "Your work, thoughtfully shared."}
          </h1>
          {isIdentify ? (
            <p>
              {location.pathname === "/identify/completed"
                ? "You can close this page. The owner can see which provider account opened the link. This step did not grant project access."
                : error
                  ? "This identity check is unavailable."
                  : "Continue with the configured identity provider…"}
            </p>
          ) : isInvite ? (
            <p>
              {error
                ? "This invitation could not be verified."
                : "Continue with your identity provider…"}
            </p>
          ) : authMode?.identity_mode ? (
            <>
              <p>
                {isOwner
                  ? "Sign in with the configured identity provider to manage sharing."
                  : "Open your current invitation link to enter its review workspace."}
              </p>
              {isOwner && authRequired && (
                <a className="button" href={authMode.owner_login}>
                  Continue with identity provider
                  <Icon name="arrow" size={16} />
                </a>
              )}
              <p className="muted">
                Identity is verified by the configured provider.
              </p>
            </>
          ) : (
            <>
              <p>
                {busy
                  ? "Opening your workspace…"
                  : "Open the owner or reviewer link printed by the local demo."}
              </p>
              <Badge>Local workspace</Badge>
              <p className="muted">
                External collaborator invitations require named identity mode.
              </p>
            </>
          )}
        </div>
      ) : isOwner ? (
        <Owner state={state} refresh={refresh} act={act} busy={busy} />
      ) : (
        <Reviewer state={state} refresh={refresh} act={act} busy={busy} />
      )}
    </>
  );
}
function Owner({ state, refresh, act, busy }) {
  const initialProject =
    state.projects?.find(
      (project) => project.id === sessionStorage.getItem("prism-owner-project"),
    ) || state.projects?.[0];
  const [handoffSetup, setHandoffSetup] = useState(null);
  const [reviewed, setReviewed] = useState(false);
  const [view, setView] = useState("chat"),
    [workspaceSelection, setWorkspaceSelection] = useState(() => ({
      projectId: initialProject?.id || null,
      chatId: initialProject
        ? sessionStorage.getItem(`prism-owner-chat:${initialProject.id}`)
        : null,
    })),
    [catalog, setCatalog] = useState(null),
    [selected, setSelected] = useState([]),
    [shareSelected, setShareSelected] = useState([]),
    [purpose, setPurpose] = useState(
      "Help the collaborator understand the selected work, its evidence, limitations, and outstanding tasks.",
    ),
    [mode, setMode] = useState("verify"),
    [candidate, setCandidate] = useState(null),
    [file, setFile] = useState(null),
    [revoke, setRevoke] = useState(null);
  const { projectId, chatId } = workspaceSelection;
  const project = state.projects?.find((item) => item.id === projectId) || null;
  const missingActionInputs = missingCatalogActionInputs(
    catalog?.action,
    shareSelected,
  );
  useEffect(() => setReviewed(false), [candidate?.id]);
  useEffect(() => {
    let current = true;
    setCatalog(null);
    setSelected([]);
    setShareSelected([]);
    setFile(null);
    setCandidate(null);
    if (!projectId) return () => {};
    act(async () => {
      try {
        const next = await api(`/owner/projects/${projectId}/catalog`);
        if (!current) return;
        setCatalog({
          ...next,
          action: actionWithRuntimeProfile(next.action, state.runtime_profile),
        });
        setSelected(
          next.source_kind === "synthetic"
            ? next.files.map((item) => item.name)
            : [],
        );
        setShareSelected(
          next.source_kind === "synthetic"
            ? next.files
                .map((item) => item.name)
                .filter((name) => name !== "private-notes.txt")
            : [],
        );
        setMode(next.action ? "verify" : "inspect");
      } catch (error) {
        if (current) throw error;
      }
    });
    return () => {
      current = false;
    };
  }, [projectId]);
  const labels = {
    chat: "Project workspace",
    resources: "Project resources",
    activityResults: "Activity & results",
    shares: "Shared projects",
    prepare: candidate ? "Shared version" : "New share",
    handoff: "Prepare handoff",
    requests: "Access requests",
    activity: "Sharing activity",
    conversations: "Collaborator conversations",
    settings: "Model & privacy",
  };
  function prepare() {
    setCandidate(null);
    setFile(null);
    setView("prepare");
  }
  async function freeze() {
    setCandidate(
      await api(`/owner/projects/${projectId}/candidates`, {
        files: shareSelected,
        purpose,
        mode,
      }),
    );
    setFile(null);
    await refresh();
  }
  async function approve() {
    setCandidate(
      await api(`/owner/versions/${candidate.id}/approve`, {
        digest: candidate.digest,
      }),
    );
    await refresh();
  }
  async function openVersion(id) {
    setCandidate(await api("/owner/versions/" + id));
    setView("prepare");
    setFile(null);
  }
  async function editHandoff() {
    const draft = await api(`/owner/versions/${candidate.id}/handoff-draft`);
    setHandoffSetup({
      path: `/owner/projects/${draft.project}/conversations/${draft.conversation}`,
      initial: draft.selection,
      key: crypto.randomUUID(),
    });
    setView("handoff");
  }
  function selectProject(id) {
    sessionStorage.setItem("prism-owner-project", id);
    setWorkspaceSelection({
      projectId: id,
      chatId: sessionStorage.getItem(`prism-owner-chat:${id}`),
    });
    setHandoffSetup(null);
    if (view === "handoff") setView("chat");
  }
  function selectChat(id) {
    setWorkspaceSelection((current) => {
      const key = `prism-owner-chat:${current.projectId}`;
      if (id) sessionStorage.setItem(key, id);
      else sessionStorage.removeItem(key);
      return { ...current, chatId: id };
    });
  }
  const nav = [
    { id: "chat", icon: "chat", label: "Project chat" },
    { id: "resources", icon: "file", label: "Project resources" },
    { id: "activityResults", icon: "activity", label: "Activity & results" },
    { id: "shares", icon: "folder", label: "Shared projects" },
    {
      id: "requests",
      icon: "inbox",
      label: "Access requests",
      count: state.requests?.length,
    },
    { id: "conversations", icon: "chat", label: "Collaborator chats" },
    { id: "activity", icon: "shield", label: "Sharing activity" },
  ].map((n) => ({ ...n, active: view === n.id, onClick: () => setView(n.id) }));
  return (
    <Shell
      actor={state.actor}
      title={labels[view]}
      nav={nav}
      identityMode={state.identity_mode}
      primary={
        <button
          className="new-share"
          disabled={busy}
          onClick={() => {
            selectChat(null);
            setView("chat");
          }}
        >
          <Icon name="plus" size={17} />
          New chat
        </button>
      }
      bottom={
        <button
          className={"nav-item" + (view === "settings" ? " active" : "")}
          onClick={() => setView("settings")}
        >
          <Icon name="settings" />
          <span>Model & privacy</span>
        </button>
      }
    >
      <div
        className="owner-project"
        hidden={!["chat", "resources", "activityResults"].includes(view)}
      >
        <OwnerProject
          onPrepare={(path) => {
            setHandoffSetup({ path, initial: null, key: crypto.randomUUID() });
            setView("handoff");
          }}
          state={state}
          project={project}
          projects={state.projects || []}
          selectProject={selectProject}
          catalog={catalog}
          selected={selected}
          setSelected={setSelected}
          view={view}
          setView={setView}
          chatId={chatId}
          selectChat={selectChat}
          refresh={refresh}
          act={act}
          busy={busy}
        />
      </div>
      <main
        hidden={["chat", "resources", "activityResults"].includes(view)}
        className={"page-content " + (view === "prepare" ? "prepare-page" : "")}
      >
        {handoffSetup && (
          <div hidden={view !== "handoff"}>
            <HandoffBuilder
              key={handoffSetup.key}
              {...handoffSetup}
              api={api}
              act={act}
              busy={busy}
              onCancel={() => setView("chat")}
              onFrozen={async (version) => {
                setCandidate(version);
                setFile(null);
                setView("prepare");
                await refresh();
              }}
            />
          </div>
        )}
        {view === "shares" && (
          <>
            <Heading
              eyebrow="YOUR WORKSPACE"
              title="Shared projects"
              description="A focused space for someone else to understand your work."
            >
              <button disabled={busy} onClick={prepare}>
                <Icon name="plus" size={17} />
                New share
              </button>
              <button
                className="icon-button outlined"
                aria-label="Refresh shared projects"
                disabled={busy}
                onClick={() => act(refresh)}
              >
                <Icon name="refresh" />
              </button>
            </Heading>
            <div className="section-label">
              <span>ALL VERSIONS</span>
              <span>
                {state.versions.filter((v) => v.approved && !v.revoked).length}{" "}
                active
              </span>
            </div>
            <div className="project-list">
              {!state.versions.length ? (
                <Empty title="Your first handoff starts here.">
                  Create a share, choose the evidence and review exactly what
                  others can access.
                </Empty>
              ) : (
                state.versions.map((v) => (
                  <article className="project-row" key={v.id}>
                    <span className="project-icon">
                      <Icon name="folder" size={21} />
                    </span>
                    <button
                      className="project-title"
                      disabled={busy}
                      onClick={() => act(() => openVersion(v.id))}
                    >
                      <strong>{cleanProject(v.project)}</strong>
                      <span>
                        Version {short(v.id)} · {when(v.created)}
                      </span>
                    </button>
                    <Badge
                      tone={v.revoked ? "" : v.approved ? "green" : "amber"}
                    >
                      {v.revoked
                        ? "Revoked"
                        : v.approved
                          ? "Active"
                          : "Needs review"}
                    </Badge>
                    <button
                      className="icon-button"
                      aria-label={`Open version ${short(v.id)}`}
                      disabled={busy}
                      onClick={() => act(() => openVersion(v.id))}
                    >
                      <Icon name="chevron" />
                    </button>
                  </article>
                ))
              )}
            </div>
            <div className="quiet-note">
              <Icon name="shield" size={16} />
              <p>
                You choose what is shared. Each collaborator starts with a
                fresh, limited session.
              </p>
            </div>
          </>
        )}
        {view === "prepare" && (
          <>
            <button
              className="text-button back-link"
              onClick={() => setView("shares")}
            >
              <Icon name="back" size={15} />
              Shared projects
            </button>
            <Heading
              title={
                candidate
                  ? candidate.revoked
                    ? "Shared version revoked"
                    : candidate.approved
                      ? candidate.manifest.schema === 2
                        ? "Your contextual handoff is ready."
                        : "Your handoff is ready."
                      : "Review your share"
                  : "Create a focused handoff"
              }
              description={
                candidate?.approved
                  ? candidate.manifest.schema === 2
                    ? "A fresh collaborator agent can use this approved background, selected files, and bounded capability."
                    : "Review the shared resources and manage future access."
                  : "Choose the context. Set the scope. Review before sharing."
              }
            />
            <ol className="stepper">
              {[
                "Select resources",
                "Review version",
                candidate?.manifest.schema === 2 ? "Save handoff" : "Share",
              ].map((s, i) => (
                <li
                  key={s}
                  className={
                    (candidate?.approved ? 2 : candidate ? 1 : 0) === i
                      ? "current"
                      : ""
                  }
                >
                  <span>{i + 1}</span>
                  {s}
                </li>
              ))}
            </ol>
            {!candidate ? (
              <section className="surface">
                <label className="field">
                  Project
                  <select
                    aria-label="Project to share"
                    value={projectId || ""}
                    disabled={busy}
                    onChange={(event) => selectProject(event.target.value)}
                  >
                    {(state.projects || []).map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.title}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="section-heading">
                  <div>
                    <h2>Project resources</h2>
                    <p className="muted">
                      Select the files your collaborator may read.
                    </p>
                  </div>
                  <Badge>{shareSelected.length} selected</Badge>
                </div>
                {!catalog ? (
                  <p role="status">Loading this project’s configured files…</p>
                ) : (
                  <div className="file-list">
                    {catalog.files.map((item) => (
                      <div
                        className={
                          "file-row" +
                          (shareSelected.includes(item.name) ? " selected" : "")
                        }
                        key={item.id}
                      >
                        <input
                          type="checkbox"
                          aria-label={`Share ${item.name}`}
                          checked={shareSelected.includes(item.name)}
                          onChange={(e) =>
                            setShareSelected(
                              e.target.checked
                                ? [...shareSelected, item.name].slice(0, 8)
                                : shareSelected.filter((n) => n !== item.name),
                            )
                          }
                          disabled={
                            !shareSelected.includes(item.name) &&
                            shareSelected.length >= 8
                          }
                        />
                        <button
                          className="file-name"
                          onClick={() => setFile(item)}
                        >
                          <Icon name="file" />
                          <span>
                            {item.name}
                            <small>
                              {item.bytes} bytes · {item.lines} lines
                            </small>
                          </span>
                        </button>
                        <span className="file-selection">
                          {shareSelected.includes(item.name)
                            ? "Included"
                            : "Excluded"}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                <label className="field">
                  What should your collaborator review?
                  <textarea
                    value={purpose}
                    onChange={(e) => setPurpose(e.target.value)}
                    maxLength={1000}
                  />
                </label>
                <label className="field">
                  Allowed work
                  <select
                    value={mode}
                    onChange={(e) => setMode(e.target.value)}
                  >
                    <option value="inspect">Read selected material</option>
                    {catalog?.action && (
                      <option value="verify">
                        Read material + {actionLabel(catalog.action)}
                      </option>
                    )}
                  </select>
                </label>
                {catalog?.action && mode === "verify" && (
                  <div className="notice action-notice">
                    <Icon name="shield" size={15} />
                    <div>
                      <ActionSummary action={catalog.action} />
                      <p className="muted fine">
                        The exact selected files, hashes, fixed tool and runtime
                        limits are shown before approval. No paid model call or
                        execution starts while preparing this share.
                      </p>
                      {!!missingActionInputs.length && (
                        <p className="handoff-warning">
                          Select {missingActionInputs.join(", ")} to include
                          this action.
                        </p>
                      )}
                    </div>
                  </div>
                )}
                <p className="muted fine">
                  Local Reviewer receives this access. Observer always has
                  Inspect access.
                </p>
                <div className="panel-actions">
                  <span className="muted">Original files stay unchanged.</span>
                  <button
                    disabled={
                      busy ||
                      !catalog ||
                      shareSelected.length < 1 ||
                      shareSelected.length > 8 ||
                      (mode === "verify" && !!missingActionInputs.length) ||
                      !purpose.trim()
                    }
                    onClick={() => act(freeze)}
                  >
                    Review selected content
                    <Icon name="arrow" size={16} />
                  </button>
                </div>
              </section>
            ) : (
              <section className="surface">
                <div className="section-heading">
                  <h2>{cleanProject(candidate.manifest.project)}</h2>
                  <Badge
                    tone={
                      candidate.revoked
                        ? ""
                        : candidate.approved
                          ? "green"
                          : "amber"
                    }
                  >
                    {candidate.revoked
                      ? "Revoked"
                      : candidate.approved
                        ? "Approved"
                        : "Awaiting approval"}
                  </Badge>
                </div>
                <p className="share-purpose">{candidate.manifest.purpose}</p>
                {candidate.manifest.context && (
                  <HandoffPreview
                    manifest={candidate.manifest}
                    onFile={setFile}
                  />
                )}
                <div className="section-label">
                  <span>SHARED RESOURCES</span>
                  <span>{candidate.manifest.files.length} files</span>
                </div>
                <div className="file-list">
                  {candidate.manifest.files.map((f) => (
                    <button
                      className="review-file"
                      key={f.id}
                      onClick={() => setFile(f)}
                    >
                      <Icon name="file" />
                      <span>
                        {f.name}
                        <small>{f.bytes} bytes</small>
                      </span>
                      <Icon name="chevron" size={15} />
                    </button>
                  ))}
                </div>
                {candidate.manifest.action && (
                  <div className="action-card">
                    <div className="section-heading">
                      <div>
                        <h3>Approved execution capability</h3>
                        <ActionSummary
                          action={candidate.manifest.action}
                          detailed
                        />
                      </div>
                      <Icon name="play" />
                    </div>
                    <p className="muted">
                      Requests originate in the conversation. The server checks
                      the approved file selection, action identity, parameters,
                      session limits and current grant before execution.
                    </p>
                    {!!actionInputs(candidate.manifest.action).length && (
                      <div className="action-inputs">
                        <strong>Exact tool inputs</strong>
                        {actionInputs(candidate.manifest.action).map((name) => {
                          const input = candidate.manifest.files.find(
                            (item) => item.name === name,
                          );
                          return (
                            <p className="wrap" key={name}>
                              {name}
                              {input ? ` · SHA-256 ${input.sha256}` : ""}
                            </p>
                          );
                        })}
                      </div>
                    )}
                    {candidate.manifest.action.program && (
                      <button
                        className="secondary"
                        onClick={() =>
                          setFile({
                            name: actionLabel(candidate.manifest.action),
                            text: candidate.manifest.action.program,
                            sha256: candidate.manifest.action.program_sha256,
                          })
                        }
                      >
                        Inspect fixed tool
                      </button>
                    )}
                    <details className="disclosure">
                      <summary>Runtime limits and identity</summary>
                      <p>
                        The fixed tool receives only the approved inputs. It
                        cannot select another project path, run arbitrary
                        commands, edit the project, or use task network access.
                      </p>
                      {candidate.manifest.action.image && (
                        <p className="wrap">
                          Image {candidate.manifest.action.image}
                        </p>
                      )}
                      {candidate.manifest.action.program_sha256 && (
                        <p className="wrap">
                          Program SHA-256{" "}
                          {candidate.manifest.action.program_sha256}
                        </p>
                      )}
                    </details>
                  </div>
                )}
                <details className="disclosure">
                  <summary>Version and approval fingerprint</summary>
                  <p className="wrap">Version {candidate.id}</p>
                  <p className="wrap">SHA-256 {candidate.digest}</p>
                </details>
                {!candidate.approved ? (
                  <>
                    <Notice>
                      All selected files and any displayed fixed tool inputs may
                      be disclosed. This is a file snapshot, not a copy of
                      active process memory. Approval starts no model call or
                      execution.
                    </Notice>
                    {candidate.manifest.schema === 2 && (
                      <label className="handoff-check review-consent">
                        <input
                          type="checkbox"
                          checked={reviewed}
                          onChange={(e) => setReviewed(e.target.checked)}
                        />
                        <span>
                          I reviewed the exact excerpts, summary, files, results
                          and permitted action. Excluding a file does not remove
                          its information from selected text.
                        </span>
                      </label>
                    )}
                    <div className="panel-actions">
                      <button
                        className="secondary"
                        disabled={busy}
                        onClick={() => {
                          if (candidate.manifest.schema === 2) act(editHandoff);
                          else {
                            setCandidate(null);
                            setFile(null);
                          }
                        }}
                      >
                        Change selection
                      </button>
                      <button
                        disabled={
                          busy || (candidate.manifest.schema === 2 && !reviewed)
                        }
                        onClick={() => act(approve)}
                      >
                        Approve this exact version
                        <Icon name="check" size={17} />
                      </button>
                    </div>
                  </>
                ) : candidate.revoked ? (
                  <Notice>
                    Future access to this version is blocked. Content already
                    seen or saved cannot be recalled.
                  </Notice>
                ) : (
                  <>
                    {state.identity_mode ? (
                      <InvitationManager
                        key={candidate.id}
                        version={candidate}
                        invitations={state.invitations || []}
                        refresh={refresh}
                        act={act}
                        busy={busy}
                      />
                    ) : (
                      <div className="handoff">
                        <h3>Invite a fresh perspective</h3>
                        {candidate.manifest.schema === 2 && (
                          <button
                            className="secondary"
                            disabled={busy}
                            onClick={() => act(editHandoff)}
                          >
                            Prepare a revised version
                          </button>
                        )}
                        <p>
                          Open the local reviewer workspace to try this handoff.
                        </p>
                        <a
                          className="button"
                          href={state.demo_links.reviewer}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Open reviewer
                          <Icon name="external" size={15} />
                        </a>
                        <details className="disclosure">
                          <summary>
                            Inspect-only observer and demo limitations
                          </summary>
                          <a
                            className="text-link"
                            href={state.demo_links.observer}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Open inspect-only observer ↗
                          </a>
                          <p className="muted">
                            These are local demo credentials, not named external
                            invitations. Use a separate browser profile for the
                            observer. Development isolation only; the private
                            pilot is not enabled.
                          </p>
                        </details>
                      </div>
                    )}
                    <div className="danger-zone">
                      <span>Stop future access to this version</span>
                      <button
                        className="text-button danger"
                        disabled={busy}
                        onClick={() => setRevoke(candidate.id)}
                      >
                        Revoke access
                      </button>
                    </div>
                  </>
                )}
              </section>
            )}
          </>
        )}
        {view === "requests" && (
          <>
            <Heading
              title="Access requests"
              description="See what collaborators need beyond their current scope."
            >
              <button
                className="icon-button outlined"
                aria-label="Refresh requests"
                onClick={() => act(refresh)}
                disabled={busy}
              >
                <Icon name="refresh" />
              </button>
            </Heading>
            <Notice>
              Requests do not grant access. To include more evidence in this
              demo, create a newly reviewed version. Approval and denial
              controls are planned for M2.
            </Notice>
            {!state.requests?.length ? (
              <Empty icon="inbox" title="Nothing waiting for you.">
                Additional access requests will appear here.
              </Empty>
            ) : (
              state.requests.map((r) => (
                <article className="surface request-card" key={r.id}>
                  <div className="section-heading">
                    <span className="eyebrow">COLLABORATOR REQUEST</span>
                    <Badge tone="amber">{r.status}</Badge>
                  </div>
                  <p>{r.description}</p>
                  <span className="muted">Session {short(r.session)}</span>
                </article>
              ))
            )}
          </>
        )}
        {view === "activity" && (
          <>
            <Heading
              title="Sharing activity"
              description="A record of approvals, conversations and access decisions."
            >
              <button
                className="icon-button outlined"
                aria-label="Refresh activity"
                onClick={() => act(refresh)}
                disabled={busy}
              >
                <Icon name="refresh" />
              </button>
            </Heading>
            <div className="surface activity-list">
              {state.activity.length ? (
                state.activity.map((e) => (
                  <div className="activity" key={e.id}>
                    <span className="activity-icon">
                      <Icon
                        name={e.kind.includes("denied") ? "shield" : "activity"}
                        size={16}
                      />
                    </span>
                    <div>
                      <strong>{e.kind.replaceAll("_", " ")}</strong>
                      <small>
                        {e.actor} · {short(e.resource)}
                      </small>
                    </div>
                    <time>{when(e.at)}</time>
                  </div>
                ))
              ) : (
                <Empty icon="activity" title="No activity yet.">
                  Events appear here as the workspace is used.
                </Empty>
              )}
            </div>
            <p className="muted">
              Local records only. Optional research measurements:{" "}
              {state.measurements ? "on" : "off"}.
            </p>
          </>
        )}
        {view === "conversations" && (
          <>
            <Heading
              title="Collaborator conversations"
              description="Review the questions and outcomes in each independent session."
            />
            <OwnerConversations
              sessions={state.sessions || []}
              act={act}
              busy={busy}
            />
          </>
        )}
        {view === "settings" && (
          <>
            <Heading
              title="Model & privacy"
              description={
                state.identity_mode
                  ? "Understand where information goes and how this private service runs."
                  : "Understand where information goes and how this demo runs."
              }
            />
            <section className="surface settings-content">
              <ModelDetails
                owner
                model={state.model}
                act={act}
                refresh={refresh}
                busy={busy}
              />
              <hr />
              {state.identity_mode ? (
                <>
                  <h3>Private server</h3>
                  <p>
                    This externally hosted app uses named identity sign-in and
                    recipient-bound invitations. Runtime isolation and durable
                    host leases still require separate verification before any
                    private-pilot readiness claim.
                  </p>
                </>
              ) : isReferenceLinuxAction(catalog?.action) ? (
                <>
                  <h3>Local demonstration</h3>
                  <p>
                    Explicitly configured project files, local demo actors and
                    the reference Linux runtime using a Kata VM. This profile is
                    not a private pilot; external identity and durable host
                    leases remain planned.
                  </p>
                </>
              ) : (
                <>
                  <h3>Local demonstration</h3>
                  <p>
                    Explicitly configured project files, local demo actors and
                    development containers. External identity, integrated Linux
                    isolation and durable host leases remain planned.
                  </p>
                </>
              )}
            </section>
          </>
        )}
      </main>
      {file && <FileSheet file={file} onClose={() => setFile(null)} />}
      {revoke && (
        <Sheet title="Revoke this share?" onClose={() => setRevoke(null)}>
          <p>
            Collaborators will lose future access to version {short(revoke)}.
          </p>
          <p className="muted">
            Previously viewed or saved content cannot be recalled.
          </p>
          <div className="panel-actions">
            <button className="secondary" onClick={() => setRevoke(null)}>
              Keep access
            </button>
            <button
              className="danger-button"
              disabled={busy}
              onClick={() =>
                act(async () => {
                  await api(`/owner/versions/${revoke}/revoke`, {});
                  await refresh();
                  if (candidate?.id === revoke)
                    setCandidate(await api("/owner/versions/" + revoke));
                  setRevoke(null);
                })
              }
            >
              Revoke access
            </button>
          </div>
        </Sheet>
      )}
    </Shell>
  );
}

function InvitationManager({ version, invitations, refresh, act, busy }) {
  const [recipientIssuer, setRecipientIssuer] = useState("");
  const [recipientSubject, setRecipientSubject] = useState("");
  const [inviteMode, setInviteMode] = useState(
    version.manifest.action ? "verify" : "inspect",
  );
  const [expiresIn, setExpiresIn] = useState(3600);
  const [created, setCreated] = useState(null);
  const [copyStatus, setCopyStatus] = useState("");
  const [discovery, setDiscovery] = useState(null);
  const [discoveryCopy, setDiscoveryCopy] = useState("");
  const records = invitations.filter((item) => item.version === version.id);
  const status = (item) => {
    if (item.revoked) return "Revoked";
    if (item.expires <= Date.now() / 1000) return "Expired";
    if (item.redeemed) return "Active grant";
    return "Awaiting recipient";
  };
  return (
    <div className="handoff invitation-manager">
      <h3>Invite a named collaborator</h3>
      <p>
        Enter the exact issuer and subject from the collaborator’s identity
        provider. Prism verifies both during sign-in; these fields do not verify
        identity by themselves.
      </p>
      <div className="one-time-invitation">
        <strong>Verify the account that opens this link</strong>
        <p>
          This verifies the provider account that opens the link; it does not
          prove who controls that account. Share the link privately and confirm
          the recipient through a separate trusted channel before inviting.
        </p>
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() =>
            act(async () => {
              setDiscovery(await api("/owner/identity-discoveries", {}));
              setDiscoveryCopy("");
            })
          }
        >
          Create identity link
        </button>
        {discovery && (
          <div role="status">
            <input
              aria-label="One-time identity link"
              readOnly
              value={discovery.url}
              onFocus={(event) => event.target.select()}
            />
            <div className="panel-actions">
              <button
                type="button"
                className="secondary"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(discovery.url);
                    setDiscoveryCopy("Copied");
                  } catch {
                    setDiscoveryCopy("Copy failed; select the link above.");
                  }
                }}
              >
                Copy identity link
              </button>
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    const result = await api(
                      `/owner/identity-discoveries/${discovery.id}`,
                    );
                    setDiscovery((previous) => ({ ...previous, ...result }));
                    if (result.status === "complete") {
                      setRecipientIssuer(result.issuer);
                      setRecipientSubject(result.subject);
                    }
                  })
                }
              >
                Check account
              </button>
            </div>
            <p className="muted">
              {discovery.status === "complete"
                ? "Verified identity filled below. Review it before creating an invitation."
                : `Waiting for the collaborator. Link expires ${new Date(discovery.expires * 1000).toLocaleString()}.`}
              {discoveryCopy && ` ${discoveryCopy}`}
            </p>
          </div>
        )}
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          act(async () => {
            const result = await api(
              `/owner/versions/${version.id}/invitations`,
              {
                recipient_issuer: recipientIssuer,
                recipient_subject: recipientSubject,
                mode: inviteMode,
                expires_in: Number(expiresIn),
              },
            );
            setCreated(result);
            setCopyStatus("");
            setRecipientSubject("");
            await refresh();
          });
        }}
      >
        <label className="field">
          Recipient issuer
          <input
            type="url"
            required
            autoComplete="off"
            value={recipientIssuer}
            onChange={(event) => setRecipientIssuer(event.target.value)}
            placeholder="https://identity.example"
          />
        </label>
        <label className="field">
          Recipient subject
          <input
            required
            autoComplete="off"
            value={recipientSubject}
            onChange={(event) => setRecipientSubject(event.target.value)}
            placeholder="Exact subject identifier"
          />
        </label>
        <div className="invitation-fields">
          <label className="field">
            Allowed work
            <select
              value={inviteMode}
              onChange={(event) => setInviteMode(event.target.value)}
            >
              <option value="inspect">Read approved material</option>
              {version.manifest.action && (
                <option value="verify">
                  Read + {actionLabel(version.manifest.action)}
                </option>
              )}
            </select>
          </label>
          <label className="field">
            Invitation expires
            <select
              value={expiresIn}
              onChange={(event) => setExpiresIn(Number(event.target.value))}
            >
              <option value={300}>In 5 minutes</option>
              <option value={1800}>In 30 minutes</option>
              <option value={3600}>In 1 hour</option>
              <option value={86400}>In 24 hours</option>
            </select>
          </label>
        </div>
        <button disabled={busy}>Create invitation</button>
      </form>
      {created && (
        <div className="one-time-invitation" role="status">
          <div>
            <strong>Copy this invitation now</strong>
            <p>
              It is shown once and expires{" "}
              {new Date(created.expires * 1000).toLocaleString()}.
            </p>
          </div>
          <input
            aria-label="One-time invitation URL"
            readOnly
            value={created.url}
            onFocus={(event) => event.target.select()}
          />
          <div className="panel-actions">
            <button
              type="button"
              className="secondary"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(created.url);
                  setCopyStatus("copied");
                } catch {
                  setCopyStatus("failed");
                }
              }}
            >
              {copyStatus === "copied" ? "Copied" : "Copy invitation"}
            </button>
            <button
              type="button"
              className="text-button"
              onClick={() => setCreated(null)}
            >
              Dismiss
            </button>
          </div>
          {copyStatus === "failed" && (
            <p className="copy-error" role="alert">
              Copy failed. Select the invitation URL above and copy it manually.
            </p>
          )}
        </div>
      )}
      <div className="section-label invitation-list-heading">
        <span>INVITATIONS & GRANTS</span>
        <span>{records.length}</span>
      </div>
      {!records.length ? (
        <p className="muted">
          No invitations have been created for this version.
        </p>
      ) : (
        <div className="invitation-list">
          {records.map((item) => (
            <article className="invitation-row" key={item.id}>
              <div>
                <strong>{item.recipient_subject}</strong>
                <span className="wrap">{item.recipient_issuer}</span>
                <small>
                  {item.mode === "verify"
                    ? "Read + approved action"
                    : "Read only"}{" "}
                  · expires {new Date(item.expires * 1000).toLocaleString()}
                </small>
              </div>
              <Badge tone={!item.revoked && item.redeemed ? "green" : ""}>
                {status(item)}
              </Badge>
              {item.grant_id && !item.revoked && (
                <button
                  type="button"
                  className="text-button danger"
                  disabled={busy}
                  onClick={() =>
                    act(async () => {
                      await api(`/owner/grants/${item.grant_id}/revoke`, {});
                      await refresh();
                    })
                  }
                >
                  Revoke grant
                </button>
              )}
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
function OwnerProject({
  onPrepare,
  state,
  project,
  projects,
  selectProject,
  catalog,
  selected,
  setSelected,
  view,
  setView,
  chatId,
  selectChat,
  refresh,
  act,
  busy,
}) {
  const [session, setSession] = useState(null),
    [chats, setChats] = useState([]),
    [consent, setConsent] = useState(false),
    [file, setFile] = useState(null),
    [details, setDetails] = useState(false),
    [historical, setHistorical] = useState(null),
    [query, setQuery] = useState(""),
    [hits, setHits] = useState(null);
  const base = project ? `/owner/projects/${project.id}/conversations` : null;
  const path = session ? `${base}/${session.id}` : null;
  const missingActionInputs = missingCatalogActionInputs(
    catalog?.action,
    selected,
  );
  useEffect(() => {
    let current = true;
    setChats([]);
    if (base)
      act(async () => {
        try {
          const next = await api(base);
          if (current) setChats(next);
        } catch (error) {
          if (current) throw error;
        }
      });
    return () => {
      current = false;
    };
  }, [base]);
  useEffect(() => {
    let current = true;
    setSession(null);
    setFile(null);
    setQuery("");
    setHits(null);
    setConsent(false);
    if (chatId && base)
      act(async () => {
        try {
          const result = await api(`${base}/${chatId}`);
          if (current) setSession(result);
        } catch (e) {
          if (!current) return;
          selectChat(null);
          throw e;
        }
      });
    return () => {
      current = false;
    };
  }, [base, chatId]);
  async function update() {
    await refresh();
    setChats(await api(base));
  }
  async function start() {
    const next = await api(base, {
      files: selected,
      model_policy: consent ? state.owner_model_policy : null,
    });
    selectChat(next.id);
    setView("chat");
    setChats(await api(base));
  }
  async function read(id, start = 1, end = 120) {
    setFile(await api(`${path}/evidence/${id}?start=${start}&end=${end}`));
  }
  if (!project)
    return (
      <main className="page-content">
        <Empty title="No configured project.">
          The local operator must configure a supported project.
        </Empty>
      </main>
    );
  return (
    <>
      <div className="session-bar owner-session-bar">
        <label className="session-select">
          <Icon name="folder" size={14} />
          <span className="sr-only">Project</span>
          <select
            aria-label="Owner project"
            value={project.id}
            disabled={busy}
            onChange={(event) => selectProject(event.target.value)}
          >
            {projects.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title}
              </option>
            ))}
          </select>
        </label>
        <select
          aria-label="Owner conversation"
          value={chatId || ""}
          disabled={busy}
          onChange={(e) => {
            selectChat(e.target.value || null);
            setView("chat");
          }}
        >
          <option value="">New conversation</option>
          {chats.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title ? c.title.slice(0, 55) : `Conversation ${short(c.id)}`} ·{" "}
              {when(c.created)}
            </option>
          ))}
        </select>
        {session && (
          <button
            className="secondary"
            disabled={busy}
            onClick={() => onPrepare(path)}
          >
            Prepare handoff
          </button>
        )}
      </div>
      {!session ? (
        <main className="page-content owner-start">
          <Mark large />
          <Heading
            eyebrow="YOUR PRIVATE PROJECT"
            title="Start with your own work."
            description="Use the conversation to understand the work, organize next steps, and request permitted actions. Your conversation stays separate from collaborator sessions."
          />
          <div className="owner-capabilities">
            <span>
              <Icon name="file" />
              {catalog ? catalog.files.length : 0} configured files
            </span>
            <span>
              <Icon name="activity" />
              Activity and result history
            </span>
            <span>
              <Icon name="chat" />
              Saved project conversations
            </span>
          </div>
          <section className="surface owner-disclosure">
            <div className="section-heading">
              <div>
                <h3>Choose this conversation’s project context</h3>
                <p className="muted">
                  Select 1–8 configured files and inspect their contents before
                  starting. This selection is pinned to the conversation.
                </p>
              </div>
              <Badge>{selected.length} selected</Badge>
            </div>
            {!catalog ? (
              <p role="status">Loading this project’s configured files…</p>
            ) : (
              <div className="file-list compact-file-list">
                {catalog.files.map((item) => (
                  <div
                    className={
                      "file-row" +
                      (selected.includes(item.name) ? " selected" : "")
                    }
                    key={item.id}
                  >
                    <input
                      type="checkbox"
                      aria-label={`Use ${item.name} in this conversation`}
                      checked={selected.includes(item.name)}
                      disabled={
                        !selected.includes(item.name) && selected.length >= 8
                      }
                      onChange={(event) =>
                        setSelected(
                          event.target.checked
                            ? [...selected, item.name].slice(0, 8)
                            : selected.filter((name) => name !== item.name),
                        )
                      }
                    />
                    <button
                      className="file-name"
                      type="button"
                      onClick={() => setFile(item)}
                    >
                      <Icon name="file" />
                      <span>
                        {item.name}
                        <small>
                          {item.bytes} bytes · {item.lines} lines · inspect
                        </small>
                      </span>
                    </button>
                    <span className="file-selection">
                      {selected.includes(item.name) ? "Included" : "Excluded"}
                    </span>
                  </div>
                ))}
              </div>
            )}
            <p>
              This chat can use only the selected configured files. The browser
              cannot add arbitrary paths, and setup text inside a selected file
              is treated as quoted project data rather than permission to run
              it.
            </p>
            {catalog?.action ? (
              <div className="action-inline">
                <ActionSummary action={catalog.action} />
                <p className="muted fine">
                  The action is available only when its required inputs are in
                  this selection. It runs only after an explicit conversation
                  request and server authorization.
                </p>
                {!!missingActionInputs.length && (
                  <p className="muted fine">
                    With the current selection, this conversation will be
                    inspect-only. Select {missingActionInputs.join(", ")} to
                    make the fixed action available.
                  </p>
                )}
              </div>
            ) : (
              <p className="muted fine">
                This project has no execution action. The conversation is for
                reading and discussing the selected files.
              </p>
            )}
            {state.model.status === "configured" ? (
              <>
                <label className="owner-consent">
                  <input
                    type="checkbox"
                    checked={consent}
                    onChange={(e) => setConsent(e.target.checked)}
                  />
                  <span>
                    Enable OpenAI for this conversation. My questions, this
                    chat’s history, retrieved contents from the{" "}
                    {selected.length || "selected"} selected file
                    {selected.length === 1 ? "" : "s"}, and permitted tool
                    results may be sent to OpenAI.
                  </span>
                </label>
                <p className="muted fine">
                  Uses the same {money(state.model.budget_cents)} total
                  allowance as collaborator chats;{" "}
                  {money(state.model.budget_cents - state.model.reserved_cents)}{" "}
                  remaining. Provider retention may apply. Starting the
                  conversation makes no model call; a call happens only after I
                  send a message.
                </p>
              </>
            ) : (
              <Notice>
                {state.model.blocked_reason ||
                  "No model is configured. You can still start a workspace to inspect files and review activity. Answers will not be simulated."}
              </Notice>
            )}
            <button
              disabled={
                busy ||
                !!chatId ||
                !catalog ||
                selected.length < 1 ||
                selected.length > 8 ||
                (state.model.status === "configured" && !consent)
              }
              onClick={() => act(start)}
            >
              {chatId ? "Opening conversation…" : "Start private conversation"}
              <Icon name="arrow" size={16} />
            </button>
          </section>
          <p className="muted fine">
            12 turns per conversation · No arbitrary shell or project editing.
            Only selected, approved context is shared; this chat does not share
            itself.
          </p>
        </main>
      ) : (
        <>
          <div hidden={view !== "chat"} className="chat-page">
            <Conversation
              key={session.id}
              owner
              apiBase={path}
              session={session}
              model={state.model}
              refresh={update}
              act={act}
              busy={busy}
              read={read}
              showRuns={() => setView("activityResults")}
              showDetails={() => setDetails(true)}
            />
          </div>
          {view === "resources" && (
            <main className="page-content">
              <Heading
                title="Project resources"
                description="The exact project context pinned when this conversation began. These files are not automatically shared."
              />
              <form
                className="search"
                onSubmit={(e) => {
                  e.preventDefault();
                  act(async () =>
                    setHits(
                      await api(
                        `${path}/search?query=${encodeURIComponent(query)}`,
                      ),
                    ),
                  );
                }}
              >
                <Icon name="search" />
                <input
                  aria-label="Search project evidence"
                  placeholder="Search this project's files…"
                  value={query}
                  maxLength={160}
                  required
                  onChange={(e) => setQuery(e.target.value)}
                />
                <button disabled={busy || !query.trim()}>Search</button>
              </form>
              {hits !== null && (
                <div className="search-hits">
                  <div className="section-label">
                    <span>{hits.length} results</span>
                    <button
                      className="text-button"
                      onClick={() => {
                        setHits(null);
                        setQuery("");
                      }}
                    >
                      Clear search
                    </button>
                  </div>
                  {hits.map((hit, i) => (
                    <button
                      key={i}
                      className="search-hit"
                      onClick={() =>
                        act(() => read(hit.id, hit.line, hit.line))
                      }
                    >
                      <strong>
                        {hit.name}:{hit.line}
                      </strong>
                      <span>{hit.text}</span>
                    </button>
                  ))}
                </div>
              )}
              <div className="file-list source-list">
                {session.manifest.files.map((f) => (
                  <button
                    className="review-file"
                    key={f.id}
                    onClick={() => act(() => read(f.id))}
                  >
                    <Icon name="file" />
                    <span>
                      <strong>{f.name}</strong>
                      <small>{f.bytes} bytes · project context</small>
                    </span>
                    <Icon name="chevron" size={15} />
                  </button>
                ))}
              </div>
              <p className="muted fine">
                Project revision {short(session.version)} · New chats capture
                the current configured files. This conversation keeps its
                original resource revision.
              </p>
            </main>
          )}
          {view === "activityResults" && (
            <main className="page-content">
              <Runs
                key={session.id}
                owner
                apiBase={path}
                session={session}
                act={act}
                busy={busy}
              />
            </main>
          )}
        </>
      )}
      {file && <FileSheet file={file} onClose={() => setFile(null)} />}
      {details && (
        <Sheet
          title="Model & project privacy"
          onClose={() => setDetails(false)}
        >
          <ModelDetails
            owner
            model={state.model}
            act={act}
            refresh={refresh}
            busy={busy}
          />
        </Sheet>
      )}
    </>
  );
}

function Reviewer({ state, refresh, act, busy }) {
  const [session, setSession] = useState(null),
    [tab, setTab] = useState("conversation"),
    [evidence, setEvidence] = useState(null),
    [details, setDetails] = useState(false),
    [historical, setHistorical] = useState(null),
    [query, setQuery] = useState(""),
    [hits, setHits] = useState(null);
  async function start(version) {
    const next = await api("/review/sessions", { version });
    sessionStorage.setItem("prism-session", next.id);
    setSession(next);
    setEvidence(null);
    setHits(null);
    setQuery("");
    setHistorical(null);
    setTab(next.manifest.context ? "background" : "conversation");
  }
  async function openSession(id) {
    const next = await api("/review/sessions/" + id);
    setSession(next);
    setEvidence(null);
    setHits(null);
    setQuery("");
    setHistorical(null);
    setTab(next.manifest.context ? "background" : "conversation");
  }
  async function showBackground() {
    const current = await api(`/review/sessions/${session.id}`);
    setSession(current);
    setTab("background");
  }
  async function readHistorical(id) {
    setHistorical(
      await api(`/review/sessions/${session.id}/historical-runs/${id}`),
    );
  }
  useEffect(() => {
    if (state.identity_mode && state.session) {
      act(() => openSession(state.session));
      return;
    }
    const previous = sessionStorage.getItem("prism-session");
    if (previous)
      act(async () => {
        try {
          setSession(await api("/review/sessions/" + previous));
        } catch (e) {
          sessionStorage.removeItem("prism-session");
          throw e;
        }
      });
  }, [state.identity_mode, state.session]);
  async function read(id, start = 1, end = 120) {
    setEvidence(
      await api(
        `/review/sessions/${session.id}/evidence/${id}?start=${start}&end=${end}`,
      ),
    );
  }
  const nav = session
    ? [
        ...(session.manifest.context
          ? [{ id: "background", icon: "file", label: "Shared background" }]
          : []),
        { id: "conversation", icon: "chat", label: "Conversation" },
        {
          id: "evidence",
          icon: "file",
          label: "Sources",
          count: session.manifest.files.length,
        },
        { id: "runs", icon: "activity", label: "Activity & results" },
        {
          id: "access",
          icon: "shield",
          label: "Capabilities & permissions",
        },
      ].map((n) => ({
        ...n,
        active: tab === n.id,
        onClick: () =>
          n.id === "background" ? act(showBackground) : setTab(n.id),
      }))
    : [];
  return (
    <Shell
      actor={state.actor}
      identityMode={state.identity_mode}
      title={
        session ? cleanProject(session.manifest.project) : "Shared with you"
      }
      nav={nav}
      primary={
        session && !state.identity_mode ? (
          <>
            <button
              className="new-share"
              disabled={busy}
              onClick={() => act(() => start(session.version))}
            >
              <Icon name="plus" size={17} />
              New session
            </button>
            <button
              className="text-button"
              disabled={busy}
              onClick={() =>
                act(async () => {
                  await refresh();
                  sessionStorage.removeItem("prism-session");
                  setSession(null);
                  setEvidence(null);
                  setHistorical(null);
                })
              }
            >
              Choose another share
            </button>
          </>
        ) : null
      }
      bottom={
        <>
          <button className="model-mini" onClick={() => setDetails(true)}>
            <span>
              <span
                className={
                  "status-dot " +
                  (state.model.status === "configured" ? "" : "offline")
                }
              />
              {state.model.status === "configured"
                ? "Model configured"
                : state.model.status === "blocked"
                  ? "Model blocked"
                  : "Model unavailable"}
            </span>
            <small>
              {state.model.status === "blocked"
                ? state.model.blocked_reason
                : state.model.endpoint
                  ? money(
                      state.model.budget_cents - state.model.reserved_cents,
                    ) + " allowance remaining"
                  : "Browse sources without a model"}
            </small>
          </button>
        </>
      }
    >
      {!session ? (
        <main className="page-content">
          {state.identity_mode ? (
            <Empty icon="shield" title="Opening your invited workspace…">
              Prism is loading the single session authorized by your invitation.
            </Empty>
          ) : (
            <>
              <Heading
                eyebrow="SHARED WITH YOU"
                title="A new perspective starts here."
                description="Choose a reviewed version to start an independent session."
              />
              <div className="project-list">
                {!state.versions.length ? (
                  <Empty title="No available shares yet.">
                    Ask the owner to approve a version before you start.
                  </Empty>
                ) : (
                  state.versions.map((v) => (
                    <article className="share-card" key={v.id}>
                      <div className="project-icon">
                        <Icon name="folder" size={22} />
                      </div>
                      <div>
                        <h2>{cleanProject(v.project)}</h2>
                        <p>{v.purpose}</p>
                        <small>Version {short(v.id)} · approved evidence</small>
                      </div>
                      <button
                        disabled={busy}
                        onClick={() => act(() => start(v.id))}
                      >
                        Start session
                        <Icon name="arrow" size={16} />
                      </button>
                    </article>
                  ))
                )}
              </div>
            </>
          )}
        </main>
      ) : (
        <>
          <div className="session-bar">
            <span>
              <span className="status-dot" />
              {session.mode === "verify" && session.manifest.action
                ? `Approved files + ${actionLabel(session.manifest.action)}`
                : "Approved files only"}
              <span className="separator">/</span>
              {session.manifest.files.length} approved files
            </span>
            <button className="text-button" onClick={() => setTab("access")}>
              View capabilities
              <Icon name="chevron" size={12} />
            </button>
          </div>
          {tab === "background" && session.manifest.context && (
            <main className="page-content">
              <Heading
                eyebrow="APPROVED HANDOFF"
                title="Pick up where the owner left off."
                description={`Version ${short(session.version)} · Selected background for a fresh, independent agent.`}
              />
              <Notice>
                These excerpts and historical results are prior work, not new
                messages or executions in your session. Only the files and tools
                shown under Capabilities & permissions are available. Asking the
                agent sends this approved background to the configured model
                provider.
              </Notice>
              <button disabled={busy} onClick={() => setTab("conversation")}>
                Continue in your conversation <Icon name="arrow" size={16} />
              </button>
              <HandoffPreview
                manifest={session.manifest}
                onFile={(file) => act(() => read(file.id))}
              />
            </main>
          )}
          <div hidden={tab !== "conversation"} className="chat-page">
            <Conversation
              key={session.id}
              session={session}
              model={state.model}
              refresh={refresh}
              act={act}
              busy={busy}
              read={read}
              showRuns={() => setTab("runs")}
              showBackground={() => act(showBackground)}
              readHistorical={(id) => act(() => readHistorical(id))}
              showDetails={() => setDetails(true)}
            />
          </div>
          {tab === "evidence" && (
            <main className="page-content">
              <Heading
                title="Sources"
                description="The exact evidence approved for this shared version."
              />
              <form
                className="search"
                onSubmit={(e) => {
                  e.preventDefault();
                  act(async () =>
                    setHits(
                      await api(
                        `/review/sessions/${session.id}/search?query=${encodeURIComponent(query)}`,
                      ),
                    ),
                  );
                }}
              >
                <Icon name="search" />
                <input
                  aria-label="Search approved evidence"
                  placeholder="Search within approved files…"
                  maxLength={160}
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
                <button className="secondary" disabled={busy || !query.trim()}>
                  Search
                </button>
              </form>
              {hits !== null && (
                <div className="search-hits">
                  <div className="section-label">
                    <span>{hits.length} RESULTS</span>
                    <button
                      className="text-button"
                      onClick={() => {
                        setHits(null);
                        setQuery("");
                      }}
                    >
                      Clear search
                    </button>
                  </div>
                  {hits.map((h, i) => (
                    <button
                      key={i}
                      onClick={() =>
                        act(() =>
                          read(h.id, Math.max(1, h.line - 2), h.line + 5),
                        )
                      }
                    >
                      <strong>
                        {h.name}:{h.line}
                      </strong>
                      <span>{h.text}</span>
                    </button>
                  ))}
                  {!hits.length && (
                    <p className="muted">
                      No matches in the approved evidence.
                    </p>
                  )}
                </div>
              )}
              <div className="file-list source-list">
                {session.manifest.files.map((f) => (
                  <button
                    className="review-file"
                    key={f.id}
                    onClick={() => act(() => read(f.id))}
                  >
                    <Icon name="file" />
                    <span>
                      {f.name}
                      <small>{f.bytes} bytes · approved source</small>
                    </span>
                    <Icon name="chevron" size={16} />
                  </button>
                ))}
              </div>
              <p className="muted">
                Version {short(session.version)} · New source changes are not
                included automatically.
              </p>
            </main>
          )}
          {tab === "runs" && (
            <main className="page-content">
              <Runs key={session.id} session={session} act={act} busy={busy} />
            </main>
          )}
          {tab === "access" && (
            <main className="page-content narrow">
              <Heading
                title="Capabilities & permissions"
                description="A clear boundary around the work you can do."
              />
              <section className="surface">
                <div className="section-heading">
                  <h2>{cleanProject(session.manifest.project)}</h2>
                  <Badge tone="green">
                    {session.mode === "verify" && session.manifest.action
                      ? "Approved files + bounded action"
                      : "Approved files only"}
                  </Badge>
                </div>
                <p>{session.manifest.purpose}</p>
                <h3 className="space-top">You can</h3>
                <ul className="checks">
                  <li>
                    Read and search {session.manifest.files.length} approved
                    files.
                  </li>
                  <li>
                    {state.model.status === "configured"
                      ? "Ask the configured model about this evidence."
                      : state.model.blocked_reason ||
                        "Ask about this evidence once the owner configures a model."}
                  </li>
                  {session.mode === "verify" && session.manifest.action && (
                    <li>
                      Request {actionLabel(session.manifest.action)}.{" "}
                      {actionDescription(session.manifest.action)} Each request
                      is checked against the approved files, action, inputs and
                      current grant before execution.
                    </li>
                  )}
                </ul>
                <h3>Outside this share</h3>
                <p className="muted">
                  Other project files, original agent memory, arbitrary commands
                  and task network access.
                </p>
                <AccessRequest
                  key={session.id}
                  session={session}
                  act={act}
                  busy={busy}
                />
                <details className="disclosure">
                  <summary>Session and version identity</summary>
                  <p className="wrap">Version {session.version}</p>
                  <p className="wrap">Session {session.id}</p>
                </details>
              </section>
              <Notice>
                The owner can review this conversation and revoke future access.
                Already delivered content cannot be recalled.
              </Notice>
            </main>
          )}
        </>
      )}
      {evidence && (
        <FileSheet file={evidence} onClose={() => setEvidence(null)} />
      )}
      {historical && (
        <Sheet
          title="Historical owner result"
          onClose={() => setHistorical(null)}
        >
          <p>
            Imported from the approved handoff. This is not a new execution in
            your session.
          </p>
          <pre className="handoff-json">
            {JSON.stringify(historical, null, 2)}
          </pre>
        </Sheet>
      )}
      {details && (
        <Sheet title="Model & privacy" onClose={() => setDetails(false)}>
          <ModelDetails
            model={state.model}
            act={act}
            refresh={refresh}
            busy={busy}
          />
          <p className="muted">
            {isReferenceLinuxAction(session?.manifest?.action)
              ? "This session uses the reference Linux runtime with a Kata VM path. It is not a private pilot."
              : "This is a local demo with development isolation. It is not a private pilot."}
          </p>
        </Sheet>
      )}
    </Shell>
  );
}
function OwnerConversations({ sessions, act, busy }) {
  const [opened, setOpened] = useState(null),
    [turns, setTurns] = useState([]);
  return (
    <>
      <div className="session-picker">
        <label className="field">
          Review session
          <select
            value={opened || ""}
            disabled={busy || !sessions.length}
            onChange={(e) => {
              const id = e.target.value;
              if (id)
                act(async () => {
                  setTurns(await api(`/owner/sessions/${id}/turns`));
                  setOpened(id);
                });
            }}
          >
            <option value="">Select a collaborator session</option>
            {sessions.map((s) => (
              <option key={s.id} value={s.id}>
                {s.recipient} · {short(s.id)}
              </option>
            ))}
          </select>
        </label>
      </div>
      {!opened ? (
        <Empty icon="chat" title="Every session has its own story.">
          Choose a session to review its questions, answers and failures.
        </Empty>
      ) : !turns.length ? (
        <Empty icon="chat" title="No conversation yet.">
          This session has not asked the model a question.
        </Empty>
      ) : (
        turns.map((t) => (
          <article className="surface transcript" key={t.id}>
            <div className="section-heading">
              <span className="eyebrow">REVIEWER</span>
              <Badge tone={t.status === "completed" ? "green" : "amber"}>
                {t.status}
              </Badge>
            </div>
            <h3>{t.question}</h3>
            <p className="answer-text">
              {t.answer?.answer || t.error || "Still running."}
            </p>
          </article>
        ))
      )}
      <p className="muted fine">
        Collaborators are informed of owner visibility. Hidden reasoning is not
        stored.
      </p>
    </>
  );
}
function AccessRequest({ session, act, busy }) {
  const [description, setDescription] = useState(""),
    [sent, setSent] = useState(false),
    [open, setOpen] = useState(false);
  return (
    <div className="access-form">
      <button
        className="secondary"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <Icon name="plus" size={15} />
        Request additional access
      </button>
      {open && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            act(async () => {
              await api(`/review/sessions/${session.id}/requests`, {
                description,
              });
              setSent(true);
              setDescription("");
              setOpen(false);
            });
          }}
        >
          <label className="field">
            What do you need?
            <textarea
              maxLength={500}
              minLength={5}
              required
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Describe the evidence or task you need and why."
            />
          </label>
          <button disabled={busy || description.trim().length < 5}>
            Send request to owner
          </button>
          <p className="muted fine">
            A request grants nothing and starts no work. Additional evidence
            requires a newly reviewed version.
          </p>
        </form>
      )}
      {sent && (
        <div role="status">
          <Notice>Request recorded. Your permissions are unchanged.</Notice>
        </div>
      )}
    </div>
  );
}
function Conversation({
  session,
  model,
  refresh,
  act,
  busy,
  read,
  showRuns,
  showBackground,
  readHistorical,
  showDetails,
  apiBase,
  owner = false,
}) {
  const [turns, setTurns] = useState([]),
    [question, setQuestion] = useState(""),
    [sending, setSending] = useState(false);
  const path = `${apiBase || `/review/sessions/${session.id}`}/turns`;
  const modelReady =
    model.status === "configured" && (!owner || session.model_policy);
  const availableAction =
    session.mode === "verify" ? session.manifest.action : null;
  const scroll = useRef(null);
  useEffect(() => {
    act(async () => setTurns(await api(path)));
  }, [session.id]);
  useEffect(() => {
    scroll.current?.scrollTo({
      top: scroll.current.scrollHeight,
      behavior: "smooth",
    });
  }, [turns, sending]);
  async function ask() {
    if (!modelReady) return;
    setSending(true);
    try {
      await api(path, { question, request_key: crypto.randomUUID() });
      setQuestion("");
    } finally {
      try {
        setTurns(await api(path));
        await refresh();
      } finally {
        setSending(false);
      }
    }
  }
  const suggestions = owner
    ? [
        {
          icon: "file",
          title: "Understand the work",
          question:
            "Explain the current work, its supporting evidence, and its limitations. Cite the available sources and do not run anything.",
        },
        {
          icon: "shield",
          title: "Review project context",
          question:
            "Summarize the selected project context and explain how it affects the work. Cite the available sources without running anything.",
        },
        {
          icon: "activity",
          title: "Plan the next task",
          question:
            "Based on the available project context, identify the most useful outstanding task and explain which currently permitted capabilities it would need. Do not run anything yet.",
        },
      ]
    : [
        ...(session.manifest.context
          ? [
              {
                icon: "chat",
                title: "Continue the handoff",
                question:
                  "Summarize the approved handoff background and identify the outstanding work. Reference the background and any historical results without starting a new execution.",
              },
            ]
          : []),
        {
          icon: "file",
          title: "Understand the work",
          question:
            "Using only the shared evidence, explain the work, its current result, and what remains uncertain. Cite the sources and do not run anything.",
        },
        {
          icon: "search",
          title: "Review limitations",
          question:
            "What limitations and evidence gaps should I know about? Use the shared evidence without running anything.",
        },
        {
          icon: "play",
          title: "Review permitted actions",
          question:
            "Explain what action is currently permitted, its exact parameter limits, and what approval checks apply. Do not execute it.",
        },
      ];
  return (
    <div className="conversation">
      <div ref={scroll} className="chat-scroll">
        {!turns.length ? (
          <div className="chat-welcome">
            <Mark large />
            <div className="eyebrow">
              {owner ? "YOUR PRIVATE PROJECT" : "YOUR SHARED WORKSPACE"}
            </div>
            <h1>
              {!modelReady ? (
                <>
                  Browse the available
                  <br />
                  workspace.
                </>
              ) : owner ? (
                <>
                  What are you
                  <br />
                  working on?
                </>
              ) : (
                <>
                  What would you like
                  <br />
                  to understand?
                </>
              )}
            </h1>
            <p>
              {!modelReady ? (
                <>
                  Sources, activity, and permitted capabilities remain
                  <br />
                  available without a model conversation.
                </>
              ) : owner ? (
                <>
                  Understand your work, organize what remains,
                  <br />
                  and request only the available capabilities.
                </>
              ) : (
                <>
                  Ask about the shared work, review its limits,
                  <br />
                  or clarify the next permitted task.
                </>
              )}
            </p>
            {modelReady && (
              <div className="suggestions">
                {suggestions
                  .filter((s) => s.icon !== "play" || availableAction)
                  .map((s) => (
                    <button
                      className="suggestion"
                      key={s.title}
                      onClick={() => setQuestion(s.question)}
                    >
                      <Icon name={s.icon} />
                      <span>{s.title}</span>
                      <Icon name="chevron" size={13} />
                    </button>
                  ))}
              </div>
            )}
          </div>
        ) : (
          <div className="chat-transcript">
            {turns.map((turn) => (
              <article className="turn" key={turn.id}>
                <div className="user-message">
                  <p>{turn.question}</p>
                </div>
                <div className="agent-message">
                  <div className="agent-label">
                    <Mark />
                    <strong>Prism</strong>
                    {turn.status !== "completed" && (
                      <Badge tone="amber">{turn.status}</Badge>
                    )}
                  </div>
                  {turn.answer ? (
                    <>
                      <p className="answer-text">{turn.answer.answer}</p>
                      <div className="citation-list">
                        {turn.answer.citations.map((c, i) => (
                          <button
                            className="citation"
                            key={i}
                            onClick={() =>
                              act(() => read(c.id, c.start, c.end))
                            }
                          >
                            <Icon name="file" size={13} />
                            {c.name}
                            <span>
                              {c.start}–{c.end}
                            </span>
                          </button>
                        ))}
                        {(turn.answer.context_references || []).map((id) => (
                          <button
                            className="citation"
                            key={id}
                            onClick={showBackground}
                          >
                            <Icon name="file" size={13} />
                            Shared background ·{" "}
                            {id === "summary"
                              ? "summary"
                              : id === "open_questions"
                                ? "next steps"
                                : "excerpt"}
                          </button>
                        ))}
                        {(turn.answer.historical_run_references || []).map(
                          (id) => (
                            <button
                              className="citation"
                              key={id}
                              onClick={() => readHistorical(id)}
                            >
                              <Icon name="play" size={13} />
                              Historical owner result {short(id)}
                            </button>
                          ),
                        )}
                        {turn.answer.run_references.map((id) => (
                          <button
                            className="citation"
                            key={id}
                            onClick={showRuns}
                          >
                            <Icon name="play" size={13} />
                            Run {short(id)}
                          </button>
                        ))}
                      </div>
                      {turn.answer.limitations.length > 0 && (
                        <Notice>{turn.answer.limitations.join(" ")}</Notice>
                      )}
                      {turn.answer.claims.length > 0 && (
                        <details className="disclosure claims">
                          <summary>Claims & provenance</summary>
                          {turn.answer.claims.map((c, i) => (
                            <div className="claim" key={i}>
                              <Badge>{c.kind.replace("_", " ")}</Badge>
                              <span>{c.text}</span>
                            </div>
                          ))}
                          <p className="muted fine">
                            References are checked for identity and range.
                            Interpretations still need your judgment.
                          </p>
                        </details>
                      )}
                      {turn.answer.pending_request_id && (
                        <p className="muted">
                          Access request {short(turn.answer.pending_request_id)}{" "}
                          is pending. No new permissions were granted.
                        </p>
                      )}
                    </>
                  ) : (
                    <div className="turn-failure">
                      <p>
                        {turn.error ||
                          "Still running. Refresh this conversation to check for a result."}
                      </p>
                      <button className="text-button" onClick={showRuns}>
                        Check activity & results
                        <Icon name="arrow" size={13} />
                      </button>
                    </div>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
        {sending && (
          <div role="status" className="working">
            <span className="status-dot" />
            Prism is working with your {owner ? "project" : "approved context"}…
          </div>
        )}
      </div>
      <div className="composer-wrap">
        {owner && (
          <span className="turn-allowance">
            {turns.length} / 12 turns · Shared model allowance{" "}
            {money(model.budget_cents - model.reserved_cents)}
          </span>
        )}
        {!modelReady && (
          <Notice>
            {model.status !== "configured"
              ? model.blocked_reason ||
                "Model not configured. You can still browse sources and review available capabilities."
              : "Model use was not enabled for this conversation. Start a new chat to review the disclosure."}
          </Notice>
        )}
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            if (modelReady) act(ask);
          }}
        >
          <textarea
            aria-label="Your question"
            disabled={!modelReady}
            placeholder={
              !modelReady
                ? "Conversation unavailable"
                : owner
                  ? "Work with your project agent…"
                  : "Ask about this shared project…"
            }
            maxLength={1500}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (
                e.key === "Enter" &&
                !e.shiftKey &&
                !e.nativeEvent.isComposing
              ) {
                e.preventDefault();
                if (!busy && question.trim() && modelReady) act(ask);
              }
            }}
          />
          <div className="composer-toolbar">
            <button type="button" className="text-button" onClick={showDetails}>
              <span
                className={"status-dot " + (!modelReady ? "offline" : "")}
              />
              {!modelReady ? "Model unavailable" : "OpenAI"}
              <Icon name="chevron" size={12} />
            </button>
            <button
              className="send-button"
              aria-label={sending ? "Working" : "Send message"}
              disabled={busy || !question.trim() || !modelReady}
            >
              <Icon name="up" size={19} />
            </button>
          </div>
        </form>
        <div className="composer-note">
          <span>
            {!modelReady
              ? "Model unavailable · Sources remain accessible"
              : owner
                ? "Project context is sent to OpenAI · Not shared with collaborators"
                : "Approved context is sent to OpenAI · Owner can review this conversation"}
          </span>
          <button
            className="text-button"
            disabled={busy}
            onClick={() =>
              act(async () => {
                setTurns(await api(path));
                await refresh();
              })
            }
          >
            Refresh
          </button>
        </div>
      </div>
    </div>
  );
}

function Runs({ session, act, busy, apiBase, owner = false }) {
  const [runs, setRuns] = useState([]),
    [turns, setTurns] = useState([]),
    [error, setError] = useState("");
  const path = `${apiBase || `/review/sessions/${session.id}`}/runs`;
  const turnsPath = `${apiBase || `/review/sessions/${session.id}`}/turns`;
  async function refresh() {
    const [nextRuns, nextTurns] = await Promise.all([
      api(path),
      api(turnsPath),
    ]);
    setRuns(nextRuns);
    setTurns(nextTurns);
  }
  useEffect(() => {
    act(refresh);
  }, [session.id]);
  const active = runs.some((r) => ["queued", "running"].includes(r.status));
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(
      () =>
        refresh().catch((e) => {
          setError(e.message);
          clearInterval(timer);
        }),
      1000,
    );
    return () => clearInterval(timer);
  }, [active, session.id]);
  return (
    <div className="runs">
      <div className="tab-heading">
        <h2>Activity & results</h2>
        <button className="text-button" onClick={() => act(refresh)}>
          Refresh activity
        </button>
      </div>
      <div className="run-content">
        <p className="muted">
          Requests begin in the conversation. This history shows persisted
          conversation statuses and execution records returned by the workspace.
          It is not a complete trace of every internal model or tool event.
        </p>
        {error && (
          <div role="alert" className="error">
            {error}
          </div>
        )}
        <details className="disclosure capability-details">
          <summary>Available capability and authorization details</summary>
          {session.mode === "verify" && session.manifest.action ? (
            <div>
              <ActionSummary action={session.manifest.action} detailed />
              <p>
                Each conversation request is reviewed against the approved
                files, fixed tool, inputs, session limits and current grant. The
                task has no network or source-workspace access.
              </p>
            </div>
          ) : (
            <p>
              This session permits reading approved material only. Executions
              are denied, including direct API requests.
            </p>
          )}
        </details>
        <div className="section-label">
          <span>CONVERSATION ACTIVITY</span>
          <span>{turns.length} records</span>
        </div>
        {!turns.length && (
          <p className="muted">No conversation activity yet.</p>
        )}
        {turns.map((turn) => (
          <article className="run-card" key={turn.id}>
            <div className="panel-heading">
              <h3>{turn.question}</h3>
              <Badge tone={turn.status === "completed" ? "green" : "amber"}>
                {turn.status}
              </Badge>
            </div>
            <small className="muted">
              Conversation record {short(turn.id)}
              {turn.created ? ` · ${when(turn.created)}` : ""}
            </small>
            {turn.error && <p className="muted">{turn.error}</p>}
            <details>
              <summary>Stored conversation record</summary>
              <pre>{JSON.stringify(turn, null, 2)}</pre>
            </details>
          </article>
        ))}
        <div className="section-label">
          <span>EXECUTION RESULTS</span>
          <span>{runs.length} records</span>
        </div>
        {!runs.length && (
          <Empty icon="activity" title="No task activity yet.">
            Ask in the conversation when you want the agent to use an available
            capability. Any authorized execution and its result will appear here
            as an execution result.
          </Empty>
        )}
        {runs.map((r) => (
          <article className="run-card" key={r.id}>
            <div className="panel-heading">
              <h3>Task result</h3>
              <Badge tone={r.status === "completed" ? "green" : "amber"}>
                {r.status}
              </Badge>
            </div>
            <small className="muted">
              Task {short(r.id)} · {owner ? "project revision" : "version"}{" "}
              {short(r.version)} · {when(r.created)}
            </small>
            {r.result ? (
              <>
                <p className="muted">
                  Completed in {r.result.elapsed_seconds}s · stored result ·
                  exit {r.result.exit_code}
                </p>
                <details>
                  <summary>Task details, parameters, and exact output</summary>
                  <pre>{JSON.stringify(r, null, 2)}</pre>
                </details>
              </>
            ) : (
              <div>
                <p className="muted">
                  {r.error ||
                    (r.status === "queued"
                      ? "Queued for an approved capability."
                      : "Working within the approved limits…")}
                </p>
                <details>
                  <summary>Task details and parameters</summary>
                  <pre>{JSON.stringify(r, null, 2)}</pre>
                </details>
              </div>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
