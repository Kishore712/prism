import React, { useEffect, useState } from "react";
import "./workspace.css";

export function WorkspacePanel({ session, api, act, busy }) {
  const [workspace, setWorkspace] = useState(null);
  const [file, setFile] = useState(null);
  const [text, setText] = useState("");
  const [diff, setDiff] = useState(null);
  const [returns, setReturns] = useState([]);
  const [check, setCheck] = useState(null);
  const [saved, setSaved] = useState(false);
  const path = `/review/sessions/${session.id}/workspace`;
  const dirty = file && text !== file.text;
  const python = workspace?.validator?.id === "python-workspace";
  useEffect(() => {
    let active = true;
    act(async () => {
      const state = await api(path);
      const items = await api(`${path}/returns`);
      if (active) {
        setWorkspace(state);
        setReturns(items);
      }
    });
    return () => {
      active = false;
    };
  }, [session.id]);

  async function open(id) {
    const next = await api(`${path}/files/${id}`);
    setFile(next);
    setText(next.text);
    setSaved(false);
  }
  async function save() {
    await api(`${path}/files/${file.id}`, {
      text,
      expected_revision: file.revision,
    });
    await open(file.id);
    setWorkspace(await api(path));
    setDiff(await api(`${path}/diff`));
    setSaved(true);
  }
  return (
    <main className="page-content">
      <div className="heading">
        <h1>Working copies</h1>
        <p>Revise approved copies in this session and inspect the changes.</p>
      </div>
      <div className="notice">
        Original sources stay unchanged. Chat can read and edit these approved
        copies. Approved Python scripts require separate explicit execution
        permission. JSON checks cover exact input bytes. An earlier check can
        apply when those bytes are unchanged; it does not validate documentation
        or establish semantic correctness or owner acceptance.
      </div>
      {workspace && (
        <>
          <p className="muted">
            Revision {workspace.revision} · {workspace.revision} /{" "}
            {workspace.edit_limit} saves · 32 KiB per file
          </p>
          {workspace.matching_check && (
            <p role="status">
              Matching JSON check: {workspace.matching_check.id.slice(0, 8)} ·
              checked revision {workspace.matching_check.checked_revision} ·
              applies to revision {workspace.revision}
              {workspace.matching_check.reused &&
                " · reused because JSON input hashes are unchanged"}
              {" · "}
              {workspace.matching_check.validated
                ? "syntax passed"
                : "syntax failed"}
            </p>
          )}
          <div className="workspace-grid">
            <div className="file-list">
              {workspace.files.map((item) => (
                <button
                  key={item.id}
                  className="review-file"
                  disabled={busy || !!dirty}
                  onClick={() => act(() => open(item.id))}
                >
                  <span>
                    {item.name}
                    <small>
                      {item.editable ? "Editable copy" : "Read only"}
                      {item.changed ? " · modified" : ""}
                    </small>
                  </span>
                </button>
              ))}
            </div>
            <div className="workspace-editor">
              {file ? (
                <>
                  <h2>{file.name}</h2>
                  <label className="field">
                    {file.editable
                      ? "Edit this session’s copy"
                      : "Read-only working copy"}
                    <textarea
                      aria-label="Working copy text"
                      value={text}
                      readOnly={!file.editable}
                      disabled={busy}
                      maxLength={32768}
                      onChange={(event) => {
                        setText(event.target.value);
                        setSaved(false);
                      }}
                    />
                  </label>
                  <div className="workspace-actions">
                    <button
                      disabled={busy || !file.editable || !dirty}
                      onClick={() => act(save)}
                    >
                      Save copy
                    </button>
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() => act(() => open(file.id))}
                    >
                      {dirty ? "Discard unsaved edits / reload" : "Reload copy"}
                    </button>
                  </div>
                  {dirty && (
                    <p className="muted">
                      Unsaved changes. Save or discard before opening another
                      file.
                    </p>
                  )}
                  {saved && (
                    <p role="status">Saved to this session’s workspace.</p>
                  )}
                </>
              ) : (
                <p className="muted">
                  Choose a file to inspect its working copy.
                </p>
              )}
            </div>
          </div>
          <div className="workspace-actions">
            <button
              className="secondary"
              disabled={busy || !!dirty}
              onClick={() =>
                act(async () => {
                  const current = await api(path);
                  setWorkspace(current);
                  await api(`${path}/returns`, {
                    expected_revision: current.revision,
                  });
                  setReturns(await api(`${path}/returns`));
                })
              }
            >
              Prepare downloadable return
            </button>
            {workspace.validator?.id === "json-check" && (
              <button
                disabled={busy || !!dirty}
                onClick={() =>
                  act(async () => {
                    const current = await api(path);
                    setWorkspace(current);
                    setCheck(
                      await api(`${path}/check`, {
                        expected_revision: current.revision,
                        request_key: crypto.randomUUID(),
                      }),
                    );
                  })
                }
              >
                Run new JSON syntax check
              </button>
            )}
          </div>
          {python && (
            <div className="notice computation-result">
              <strong>
                Approved computation: {workspace.validator.entrypoint_name}
              </strong>
              <p>
                Read-only staged inputs; isolated writes; declared results:{" "}
                {workspace.validator.outputs.map((f) => f.name).join(", ")}.
                Chat can perform requested computation within this same
                approval. Execution inputs:{" "}
                {workspace.validator.required_inputs.join(", ")}.
              </p>
              <button
                disabled={busy || !!dirty}
                onClick={() =>
                  act(async () => {
                    const current = await api(path);
                    setWorkspace(current);
                    setCheck(
                      await api(`${path}/python`, {
                        entrypoint: current.validator.entrypoint,
                        expected_revision: current.revision,
                        request_key: crypto.randomUUID(),
                      }),
                    );
                  })
                }
              >
                Run approved Python copy
              </button>
              {workspace.matching_computation && (
                <p>
                  Imported computation {workspace.matching_computation.id} ·
                  computed revision{" "}
                  {workspace.matching_computation.computed_revision} · current
                  revision {workspace.revision}. Numerical correctness awaits
                  review.
                </p>
              )}
            </div>
          )}
          {check && (
            <div className="notice computation-result">
              Execution {check.id} · revision{" "}
              {check.parameters.workspace_revision} · {check.status}
              {check.parameters.workspace_revision !== workspace.revision &&
                (workspace.matching_check?.id === check.id ||
                workspace.matching_computation?.id === check.id
                  ? " · matching copied inputs and imported result"
                  : " · not attached to the current copied files")}
              {check.error && <p>{check.error}</p>}
              {check.action === "json-check" &&
                check.result?.output?.files?.map((item) => (
                  <p key={item.id}>
                    JSON {item.id.slice(0, 8)}:{" "}
                    {item.valid ? "syntax passed" : "syntax failed"}
                  </p>
                ))}
              {check.action === "python-workspace" && check.result && (
                <>
                  <details>
                    <summary>Actual computation output and provenance</summary>
                    <pre>{JSON.stringify(check.result, null, 2)}</pre>
                  </details>
                  <button
                    disabled={
                      busy ||
                      !!dirty ||
                      check.parameters.workspace_revision !== workspace.revision
                    }
                    onClick={() =>
                      act(async () => {
                        await api(`${path}/computation-results`, {
                          run_id: check.id,
                          expected_revision: workspace.revision,
                        });
                        setWorkspace(await api(path));
                        setDiff(await api(`${path}/diff`));
                        if (file) await open(file.id);
                      })
                    }
                  >
                    Import declared results into copies
                  </button>
                </>
              )}
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    setCheck(
                      await api(
                        `/review/sessions/${session.id}/runs/${check.id}`,
                      ),
                    );
                    setWorkspace(await api(path));
                  })
                }
              >
                Refresh execution result
              </button>
            </div>
          )}
          {!!returns.length && (
            <section>
              <h2>Immutable returns</h2>
              {returns.map((item) => (
                <p key={item.id}>
                  <a href={`/api${path}/returns/${item.id}/download`}>
                    Download revision {item.revision}
                  </a>
                  {" · "}
                  {item.computation_run
                    ? "Python computation attached; review pending"
                    : item.validated
                      ? "JSON syntax passed"
                      : item.check_completed
                        ? "JSON syntax failed"
                        : "No completed JSON check attached"}
                  {item.revision !== workspace.revision && " · older revision"}
                  {item.reused_check &&
                    ` · reused JSON check from revision ${item.checked_revision}`}
                </p>
              ))}
            </section>
          )}
          <button
            className="secondary"
            disabled={busy}
            onClick={() =>
              act(async () => {
                setDiff(await api(`${path}/diff`));
                setWorkspace(await api(path));
              })
            }
          >
            Review saved changes
          </button>
          {diff && (
            <section className="workspace-changes">
              <h2>Changes from approved sources · revision {diff.revision}</h2>
              {!diff.changes.length && <p>No saved changes.</p>}
              {diff.changes.map((change) => (
                <article key={change.id}>
                  <h3>{change.name}</h3>
                  <pre>
                    {change.diff ||
                      "Line endings changed; content lines are unchanged."}
                  </pre>
                  <p className="muted">
                    Final newline:{" "}
                    {change.before_final_newline ? "present" : "absent"} →{" "}
                    {change.after_final_newline ? "present" : "absent"}
                  </p>
                  <details>
                    <summary>Content hashes</summary>
                    <p className="wrap">
                      Approved: {change.before_sha256}
                      <br />
                      Working copy: {change.after_sha256}
                    </p>
                  </details>
                </article>
              ))}
            </section>
          )}
        </>
      )}
    </main>
  );
}
