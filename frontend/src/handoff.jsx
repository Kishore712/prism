import React, { useEffect, useState } from "react";

const toggle = (items, value) =>
  items.includes(value) ? items.filter((x) => x !== value) : [...items, value];
const time = (value) =>
  new Date(value * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
const actionId = (action) => action?.id || action || null;
const isReferenceLinuxAction = (action) =>
  typeof action === "object" && action?.profile === "reference-linux";
const actionLabel = (action) => {
  if (typeof action === "object" && action?.label) return action.label;
  if (actionId(action) === "json-check") return "Check JSON syntax";
  if (actionId(action) === "bootstrap") return "Rerun synthetic bootstrap";
  return actionId(action)?.replaceAll("-", " ") || "No execution action";
};
const actionDetail = (action) => {
  if (actionId(action) === "json-check")
    return isReferenceLinuxAction(action)
      ? "The fixed tool parses only the selected JSON inputs in a Kata VM under the reference Linux runtime. It checks JSON syntax and does not execute project code. This profile is not a private pilot."
      : "The fixed tool parses only the selected JSON inputs in a constrained development container. It checks JSON syntax and does not execute project code.";
  if (actionId(action) === "bootstrap")
    return "The fixed synthetic evaluator accepts one integer seed from 0–1,000 and uses only its approved synthetic inputs.";
  return "Only this fixed action and its reviewed inputs can run.";
};

export function HandoffBuilder({
  path,
  initial,
  api,
  act,
  busy,
  onFrozen,
  onCancel,
}) {
  const [source, setSource] = useState(null);
  const [draft, setDraft] = useState(
    initial || {
      checkpoint: "",
      purpose: "",
      summary: "",
      open_questions: "",
      files: [],
      runs: [],
      excerpts: [],
      mode: "inspect",
    },
  );
  useEffect(() => {
    let active = true;
    act(async () => {
      const data = await api(path + "/handoff-source");
      if (active) {
        setSource(data);
        if (!data.action)
          setDraft((current) => ({ ...current, mode: "inspect" }));
      }
    });
    return () => {
      active = false;
    };
  }, [path]);
  const set = (key, value) => setDraft((d) => ({ ...d, [key]: value }));
  const checkpoint = source?.turns.find((t) => t.id === draft.checkpoint);
  const turns = checkpoint
    ? source.turns.filter(
        (t) =>
          (t.created < checkpoint.created ||
            (t.created === checkpoint.created && t.id <= checkpoint.id)) &&
          t.finished <= checkpoint.finished,
      )
    : [];
  const runs = checkpoint
    ? source.runs.filter((r) => r.finished <= checkpoint.finished)
    : [];
  const changeExcerpt = (turn, part, patch) =>
    setDraft((d) => ({
      ...d,
      excerpts: d.excerpts.map((e) =>
        e.turn === turn && e.part === part ? { ...e, ...patch } : e,
      ),
    }));
  const chooseExcerpt = (turn, part) =>
    setDraft((d) => ({
      ...d,
      excerpts: d.excerpts.some((e) => e.turn === turn && e.part === part)
        ? d.excerpts.filter((e) => !(e.turn === turn && e.part === part))
        : [
            ...d.excerpts,
            { turn, part, edited_text: null, omit_references: false },
          ],
    }));
  const missingSupport = draft.excerpts.some((e) => {
    const t = source?.turns.find((t) => t.id === e.turn);
    return (
      t &&
      e.part === "answer" &&
      !e.omit_references &&
      (t.citations.some((c) => !draft.files.includes(c.id)) ||
        t.run_references.some((r) => !draft.runs.includes(r)))
    );
  });
  const action = source?.action || null;
  const required = Array.isArray(action?.required_inputs)
    ? action.required_inputs
    : [];
  const missingInputs =
    (draft.mode === "verify" || draft.runs.length > 0) &&
    required.some(
      (name) =>
        !source?.files.some(
          (f) => f.name === name && draft.files.includes(f.id),
        ),
    );
  if (!source)
    return <p role="status">Loading this conversation’s handoff material…</p>;
  if (!source.turns.length)
    return (
      <section className="surface">
        <h2>No completed conversation checkpoint yet</h2>
        <p>
          Complete an owner chat turn before preparing a conversation handoff.
          Failed or running answers cannot be selected.
        </p>
        <button className="secondary" onClick={onCancel}>
          Back to project chat
        </button>
      </section>
    );
  return (
    <form
      className="handoff-builder"
      onSubmit={(e) => {
        e.preventDefault();
        act(async () => onFrozen(await api(path + "/handoffs", draft)));
      }}
    >
      <div className="page-heading">
        <div>
          <div className="eyebrow">PREPARE HANDOFF</div>
          <h1>Choose what travels with your work.</h1>
          <p>
            Select completed work, then review the exact content before
            approving it.
          </p>
        </div>
      </div>
      <section className="surface">
        <h2>1. Choose a checkpoint</h2>
        <label className="field">
          Completed checkpoint
          <select
            required
            aria-label="Completed checkpoint"
            value={draft.checkpoint}
            onChange={(e) =>
              setDraft((d) => ({
                ...d,
                checkpoint: e.target.value,
                excerpts: [],
                runs: [],
              }))
            }
          >
            <option value="">Choose a completed turn</option>
            {source.turns.map((t, i) => (
              <option value={t.id} key={t.id}>
                Turn {i + 1} · {time(t.finished)} · {t.question.slice(0, 70)}
              </option>
            ))}
          </select>
        </label>
        <p className="muted fine">
          Only completed messages and runs up to this checkpoint can be
          selected. Changing it clears message and run selections.{" "}
          {source.incomplete_turns > 0 &&
            `${source.incomplete_turns} unfinished or failed turns are excluded.`}
        </p>
        <label className="field">
          Handoff purpose
          <textarea
            required
            minLength={5}
            maxLength={1000}
            value={draft.purpose}
            onChange={(e) => set("purpose", e.target.value)}
            rows={2}
          />
        </label>
        <label className="field">
          Owner-written summary
          <textarea
            required
            maxLength={2000}
            value={draft.summary}
            onChange={(e) => set("summary", e.target.value)}
            rows={3}
          />
        </label>
        <label className="field">
          Open questions and next steps
          <textarea
            maxLength={1500}
            value={draft.open_questions}
            onChange={(e) => set("open_questions", e.target.value)}
            rows={2}
          />
        </label>
      </section>
      <section className="surface">
        <h2>2. Select conversation excerpts</h2>
        <p className="muted">
          Nothing is selected automatically. Answer excerpts include their
          visible limitations; expanded claim details are not copied. Edited
          text is labelled as owner-edited.
        </p>
        {!checkpoint && <p>Choose a checkpoint to see available messages.</p>}
        {turns.map((t, i) => (
          <div className="handoff-turn" key={t.id}>
            {["question", "answer"].map((part) => {
              const selected = draft.excerpts.find(
                (e) => e.turn === t.id && e.part === part,
              );
              return (
                <div className="excerpt-choice" key={part}>
                  <label className="handoff-check">
                    <input
                      type="checkbox"
                      checked={!!selected}
                      onChange={() => chooseExcerpt(t.id, part)}
                    />
                    <strong>
                      Turn {i + 1} ·{" "}
                      {part === "question" ? "Owner question" : "Agent answer"}
                    </strong>
                  </label>
                  <details>
                    <summary>Read original {part}</summary>
                    <p className="handoff-text">{t[part]}</p>
                  </details>
                  {selected && (
                    <>
                      <label className="field">
                        Text to include · turn {i + 1} {part}
                        <textarea
                          rows={4}
                          maxLength={5000}
                          required
                          value={selected.edited_text ?? t[part]}
                          onChange={(e) =>
                            changeExcerpt(t.id, part, {
                              edited_text: e.target.value,
                            })
                          }
                        />
                      </label>
                      <button
                        type="button"
                        className="text-button"
                        onClick={() =>
                          changeExcerpt(t.id, part, { edited_text: null })
                        }
                      >
                        Restore original text
                      </button>
                      {part === "answer" && (
                        <>
                          <p className="muted fine">
                            References:{" "}
                            {t.citations
                              .map(
                                (c) =>
                                  source.files.find((f) => f.id === c.id)
                                    ?.name || "Unavailable source",
                              )
                              .join(", ") || "no file citations"}
                            ; {t.run_references.length} run reference(s).
                          </p>
                          <label className="handoff-check">
                            <input
                              type="checkbox"
                              checked={selected.omit_references}
                              onChange={(e) =>
                                changeExcerpt(t.id, part, {
                                  omit_references: e.target.checked,
                                })
                              }
                            />
                            <span>
                              Omit attached references and show an evidence-gap
                              label
                            </span>
                          </label>
                        </>
                      )}
                    </>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </section>
      <section className="surface">
        <h2>3. Select resources and historical results</h2>
        <p className="muted">
          These are the exact files captured when the owner conversation began.
          Choose no more than eight.
        </p>
        {source.files.map((f) => (
          <label className="handoff-check resource-choice" key={f.id}>
            <input
              type="checkbox"
              checked={draft.files.includes(f.id)}
              onChange={() => set("files", toggle(draft.files, f.id))}
              disabled={!draft.files.includes(f.id) && draft.files.length >= 8}
            />
            <span>
              {f.name}
              <small>{f.bytes} bytes</small>
            </span>
          </label>
        ))}
        <h3>Historical results</h3>
        <p className="muted fine">
          These are completed results from the owner workspace. Including one
          does not rerun it. Its supporting inputs must also be selected to
          preserve provenance.
        </p>
        {!runs.length && (
          <p className="muted">No completed results at this checkpoint.</p>
        )}
        {runs.map((r, i) => (
          <label className="handoff-check resource-choice" key={r.id}>
            <input
              type="checkbox"
              checked={draft.runs.includes(r.id)}
              onChange={() => set("runs", toggle(draft.runs, r.id))}
            />
            <span>
              Result {i + 1} · completed {time(r.finished)}
              <small>
                Stored owner result · details available during review
              </small>
            </span>
          </label>
        ))}
        <label className="field">
          Permitted capability
          <select
            value={draft.mode}
            onChange={(e) => set("mode", e.target.value)}
          >
            <option value="inspect">Read the selected material</option>
            {action && (
              <option value="verify">
                Read material + {actionLabel(action)}
              </option>
            )}
          </select>
        </label>
        {action ? (
          <div className="notice action-notice">
            <div>
              <strong>{actionLabel(action)}</strong>
              <p>{actionDetail(action)}</p>
              {!!required.length && (
                <p className="muted fine">
                  Required selected inputs: {required.join(", ")}.
                </p>
              )}
              <p className="muted fine">
                Approval covers the exact selected inputs, hashes, fixed tool
                identity and runtime limits shown in the next review. It does
                not permit arbitrary commands, project editing, task network, or
                new host access.
              </p>
            </div>
          </div>
        ) : (
          <p className="muted fine">
            This project has no configured execution action. The collaborator
            can inspect only the selected material.
          </p>
        )}
      </section>
      <div className="notice">
        <div>
          <strong>Review the words, not just the file list.</strong>
          <p>
            Excluding a private file does not remove its contents from selected
            messages or summaries. Edit or omit sensitive text. Remove private
            source IDs from text; attached references receive new share-local
            IDs.
          </p>
          <p>
            Approval makes this fixed background available to fresh collaborator
            sessions. No original agent memory, private tools or processes are
            transferred.
          </p>
        </div>
      </div>
      {missingSupport && (
        <p role="status" className="handoff-warning">
          Some excerpts refer to unselected files or runs. Include their
          support, or explicitly omit references and show an evidence gap.
        </p>
      )}
      {missingInputs && (
        <p role="status" className="handoff-warning">
          This configured action or historical result requires these selected
          inputs: {required.join(", ")}.
        </p>
      )}
      <p className="muted fine">
        At least one excerpt and one file. Selected context is limited to 16
        KiB; oversized content is rejected without truncation. No model call is
        needed.
      </p>
      <div className="panel-actions">
        <button type="button" className="secondary" onClick={onCancel}>
          Back to project chat
        </button>
        <button
          disabled={
            busy ||
            !checkpoint ||
            !draft.excerpts.length ||
            !draft.files.length ||
            draft.files.length > 8 ||
            missingSupport ||
            missingInputs
          }
        >
          Review exact handoff
        </button>
      </div>
    </form>
  );
}

export function HandoffPreview({ manifest, onFile }) {
  const context = manifest.context;
  return (
    <div className="handoff-preview">
      <div className="section-label">HANDOFF CONTEXT · EXACT DISCLOSURE</div>
      <h3>Owner-written summary</h3>
      <p className="handoff-text">{context.summary}</p>
      <h3>Open questions and next steps</h3>
      <p className="handoff-text">
        {context.open_questions || "None specified."}
      </p>
      <h3>Selected excerpts</h3>
      {context.excerpts.map((e, i) => (
        <article className="excerpt-choice" key={e.id}>
          <strong>
            Excerpt {i + 1} ·{" "}
            {e.speaker === "owner" ? "Owner question" : "Agent answer"} ·{" "}
            {e.origin === "verbatim" ? "Verbatim" : "Owner-edited"}
          </strong>
          <p className="handoff-text">{e.text}</p>
          {e.evidence_gap && (
            <p className="handoff-warning">
              Evidence gap: original references were explicitly omitted.
              Supporting material is not asserted to be included.
            </p>
          )}
          <div className="citation-row">
            {e.citations.map((c, i) => {
              const file = manifest.files.find((f) => f.id === c.id);
              return (
                <button
                  type="button"
                  className="secondary"
                  key={i}
                  onClick={() => onFile(file)}
                >
                  {file.name} · lines {c.start}–{c.end}
                </button>
              );
            })}
          </div>
          {e.run_references.map((id) => (
            <p className="muted fine" key={id}>
              Historical result {context.runs.findIndex((r) => r.id === id) + 1}{" "}
              · included below
            </p>
          ))}
        </article>
      ))}
      <h3>Historical results from the owner workspace</h3>
      {!context.runs.length && (
        <p className="muted">No historical results selected.</p>
      )}
      {context.runs.map((r, i) => (
        <article className="excerpt-choice" key={r.id}>
          <strong>
            Result {i + 1} · Completed · historical owner result ·{" "}
            {r.id.slice(0, 12)}
          </strong>
          <p className="muted fine">
            Historical evidence imported with this handoff; separate from any
            new collaborator activity.
          </p>
          <details>
            <summary>Task details, exact output, and provenance</summary>
            <pre className="handoff-json">{JSON.stringify(r, null, 2)}</pre>
          </details>
        </article>
      ))}
      <details>
        <summary>Inspect all frozen context fields</summary>
        <pre className="handoff-json">{JSON.stringify(context, null, 2)}</pre>
      </details>
    </div>
  );
}
