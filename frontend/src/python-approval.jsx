import React from "react";

export function PythonApproval({
  files,
  editable,
  entrypoint,
  outputs,
  inputs = null,
  onChange,
}) {
  const scripts = files.filter(
    (f) => f.name.endsWith(".py") && editable.includes(f.id),
  );
  const targets = files.filter(
    (f) =>
      f.name.startsWith("results/") &&
      f.name.endsWith(".json") &&
      editable.includes(f.id),
  );
  if (!scripts.length) return null;
  return (
    <div className="notice python-approval">
      <label className="field">
        Python execution (separate explicit approval)
        <select
          value={entrypoint || ""}
          onChange={(e) => onChange(e.target.value || null, [], null)}
        >
          <option value="">No Python execution</option>
          {scripts.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </select>
      </label>
      {entrypoint && (
        <>
          <p>
            This permits revised bytes of the selected script to run. Approved
            inputs are read only; output and temporary writes stay in a separate
            development container. No network, dependency installation or source
            writeback is granted.
          </p>
          <strong>Declared JSON results — choose one or two</strong>
          {targets.map((f) => (
            <label className="check" key={f.id}>
              <input
                type="checkbox"
                checked={outputs.includes(f.id)}
                onChange={(e) =>
                  onChange(
                    entrypoint,
                    e.target.checked
                      ? [...outputs, f.id]
                      : outputs.filter((id) => id !== f.id),
                    inputs === null ? null : inputs.filter((id) => id !== f.id),
                  )
                }
              />
              {f.name}
            </label>
          ))}
          {!targets.length && (
            <p>First select editable results/*.json files.</p>
          )}
          <strong>Execution inputs — readable by the script</strong>
          <p>
            Only checked files enter the runtime. Other shared files remain
            available to chat. Exclude a report here to allow report-only edits
            to retain matching numerical provenance.
          </p>
          {files
            .filter((f) => !outputs.includes(f.id))
            .map((f) => (
              <label className="check" key={`input-${f.id}`}>
                <input
                  type="checkbox"
                  disabled={f.id === entrypoint}
                  checked={
                    f.id === entrypoint ||
                    inputs === null ||
                    inputs.includes(f.id)
                  }
                  onChange={(e) => {
                    const current =
                      inputs ??
                      files
                        .filter((f) => !outputs.includes(f.id))
                        .map((f) => f.id);
                    onChange(
                      entrypoint,
                      outputs,
                      e.target.checked
                        ? [...current, f.id]
                        : current.filter((id) => id !== f.id),
                    );
                  }}
                />
                {f.name}
              </label>
            ))}
          <p className="muted">
            30 seconds · 1 CPU · 256 MiB · 32 processes · 32 MiB scratch.
            Execution is not mathematical validation or owner approval.
            Reference Linux/Kata support is pending.
          </p>
        </>
      )}
    </div>
  );
}
