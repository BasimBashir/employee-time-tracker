import React from 'react';
import { api } from '../api.js';
import {
  AllowanceInput, EmployeePhoto, NumberInput, PhoneInput, PrayerToggles,
  RoleControl, TextField,
} from './controls.jsx';

// Training captures for the recognition model: plain files under the
// service's media folder (media/employees/<id>/), nothing in the database.
// Images render as thumbnails; videos get a play tile.
function EmployeeMedia({ emp, onChanged }) {
  const [files, setFiles] = React.useState(null);
  const inputRef = React.useRef(null);
  const load = React.useCallback(() => {
    api.mediaList(emp.employee_id).then(setFiles).catch(() => setFiles([]));
  }, [emp.employee_id]);
  React.useEffect(load, [load]);

  async function handleFiles(e) {
    const picked = [...(e.target.files ?? [])];
    e.target.value = '';
    if (!picked.length) return;
    try {
      for (const f of picked) await api.uploadMedia(emp.employee_id, f);
      load();
      onChanged?.();
    } catch (err) {
      window.alert(`Could not upload: ${err.message}`);
    }
  }

  async function removeFile(name) {
    try {
      await api.deleteMedia(emp.employee_id, name);
      load();
    } catch (err) {
      window.alert(`Could not delete ${name}: ${err.message}`);
    }
  }

  const mb = (b) =>
    b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`;
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span className="text-label uppercase tracking-widest text-paper-faint/70">
          Training media
        </span>
        <span className="text-[10px] text-paper-faint">
          face shots &amp; clips for recognition — kept on disk
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {(files ?? []).map((f) => (
          <div key={f.file} className="group relative">
            {f.kind === 'image' ? (
              <img
                src={api.mediaUrl(emp.employee_id, f.file)}
                alt=""
                title={`${f.file} · ${mb(f.bytes)}`}
                className="h-12 w-12 rounded-md border border-ink-500/60 object-cover"
              />
            ) : (
              <div
                title={`${f.file} · ${mb(f.bytes)}`}
                className="flex h-12 w-12 items-center justify-center rounded-md border border-ink-500/60 bg-ink-800 text-paper-faint"
              >
                ▶
              </div>
            )}
            <button
              type="button"
              onClick={() => removeFile(f.file)}
              title="Delete this capture"
              className="absolute -right-1 -top-1 hidden h-4 w-4 items-center justify-center rounded-full border border-ink-500 bg-ink-800 text-[9px] leading-none text-paper-soft hover:text-rose group-hover:flex"
            >
              ×
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          title="Add images or videos of this employee"
          className="h-12 w-12 rounded-md border border-dashed border-ink-500/60 text-lg text-paper-faint transition-colors hover:border-paper-faint hover:text-paper"
        >
          +
        </button>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept="image/jpeg,image/png,image/webp,video/mp4,video/webm,video/quicktime"
          className="hidden"
          onChange={handleFiles}
        />
      </div>
      {files?.length === 0 && (
        <p className="mt-1 text-[10px] text-paper-faint/70">
          Nothing yet — a few clear face shots (and clips from different angles)
          are what the recognition model will train on.
        </p>
      )}
    </div>
  );
}

function EmployeeCard({ emp, onChanged, onEnrolled }) {
  async function patch(body, what) {
    try {
      await api.updateEmployee(emp.employee_id, body);
      onChanged?.();
    } catch (err) {
      window.alert(`Could not update ${what}: ${err.message}`);
    }
  }

  async function remove() {
    const reason = window.prompt(
      `Remove ${emp.name}?\n` +
        'The roster and reports stop listing them; their history and training ' +
        'media stay. Reason goes into the audit log:',
    );
    if (!reason) return;
    try {
      await api.removeEmployee(emp.employee_id, reason);
      onChanged?.();
    } catch (err) {
      window.alert(`Could not remove ${emp.name}: ${err.message}`);
    }
  }

  async function enroll() {
    const path = window.prompt(
      `Enroll ${emp.name}\n` +
        'Path to a video (mp4) of their face that the service can read.\n' +
        'Include the angles the gate camera actually sees — it looks down ' +
        'from the ceiling, so side views matter.',
    );
    if (!path) return;
    try {
      const r = await api.enroll(emp.employee_id, path);
      window.alert(`Enrolled: ${r.poses} face poses captured.`);
    } catch (err) {
      window.alert(`Enrollment failed: ${err.message}`);
    }
    onChanged?.();
    onEnrolled?.();
  }

  return (
    <div className="flex flex-col gap-3 rounded-panel border border-ink-500/60 bg-ink-700 p-3.5">
      <div className="flex items-start gap-3">
        <EmployeePhoto id={emp.employee_id} hasAvatar={emp.has_avatar} onChanged={onChanged} />
        <div className="min-w-0 flex-1 space-y-1.5">
          <TextField
            value={emp.name}
            placeholder="Name"
            width="w-full"
            inputClassName="text-sm font-medium text-paper"
            onCommit={(v) => v && patch({ name: v }, 'the name')}
          />
          <div className="flex gap-2">
            <TextField
              value={emp.employee_code}
              placeholder="code"
              width="w-16"
              onCommit={(v) => patch({ employee_code: v || null }, 'the code')}
            />
            <TextField
              value={emp.department}
              placeholder="department"
              width="w-28"
              onCommit={(v) => patch({ department: v || null }, 'the department')}
            />
          </div>
        </div>
        <RoleControl role={emp.role} onChange={(role) => patch({ role }, 'the role')} />
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <TextField
          label="Out window"
          value={emp.out_windows}
          placeholder="any time"
          width="w-36"
          onCommit={(v) => patch({ out_windows: v || null }, 'the out window')}
        />
        <PhoneInput phone={emp.phone} onCommit={(v) => patch({ phone: v }, 'the WhatsApp number')} />
        <AllowanceInput
          allowedMinutes={emp.allowed_out_minutes}
          onCommit={(v) => patch({ allowed_out_minutes: v }, 'the allowance')}
        />
        <button
          type="button"
          onClick={enroll}
          title="Capture face references from a video file"
          className="ml-auto text-label text-paper-faint transition-colors hover:text-paper"
        >
          Enroll face
        </button>
        <button
          type="button"
          onClick={remove}
          className="text-label text-paper-faint transition-colors hover:text-rose"
        >
          Remove
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex items-center gap-1.5 text-xs text-paper-faint">
          Namaz
          <PrayerToggles
            prayers={emp.namaz_prayers}
            onChange={(v) => patch({ namaz_prayers: v }, 'the namaz plan')}
          />
        </div>
        <NumberInput
          label="Stagger"
          value={emp.namaz_offset_min}
          placeholder="0"
          min={0}
          max={180}
          suffix="min after"
          onCommit={(v) => patch({ namaz_offset_min: v }, 'the stagger')}
        />
        <NumberInput
          label="Slot"
          value={emp.namaz_duration_min}
          placeholder="20"
          min={5}
          max={120}
          suffix="min"
          onCommit={(v) => patch({ namaz_duration_min: v }, 'the slot length')}
        />
      </div>

      {emp.effective_windows && (
        <div className="text-[10px] text-paper-faint/70">
          <span className="uppercase tracking-widest">Today&rsquo;s windows</span>
          <span className="num ml-2 text-paper-soft">{emp.effective_windows}</span>
        </div>
      )}

      <EmployeeMedia emp={emp} onChanged={onChanged} />
    </div>
  );
}

export function EmployeesPanel({ employees, onChanged }) {
  const [draft, setDraft] = React.useState({ name: '', employee_code: '', department: '' });
  const [identity, setIdentity] = React.useState(null);
  const loadIdentity = React.useCallback(() => {
    api.resolverStatus().then(setIdentity).catch(() => setIdentity(null));
  }, []);
  React.useEffect(loadIdentity, [loadIdentity]);

  async function add() {
    if (!draft.name.trim()) return;
    try {
      await api.addEmployee({
        name: draft.name.trim(),
        employee_code: draft.employee_code.trim() || null,
        department: draft.department.trim() || null,
      });
      setDraft({ name: '', employee_code: '', department: '' });
      onChanged?.();
    } catch (err) {
      window.alert(`Could not add the employee: ${err.message}`);
    }
  }

  const field =
    'rounded-md border border-ink-500/60 bg-ink-800 px-2 py-1 text-sm text-paper ' +
    'placeholder:text-paper-faint/50 focus:border-paper-faint focus:outline-none';
  return (
    <section className="panel overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-500/60 px-4 py-3">
        <h2 className="text-label font-medium uppercase tracking-widest text-paper-faint">
          Employees — profiles &amp; rules
        </h2>
        <span
          className="num text-[10px] text-paper-faint/80"
          title={identity
            ? `identity confirms at cosine ${identity.confirm}, flags below certainty at ${identity.uncertain}`
            : 'resolver status unavailable'}
        >
          {identity
            ? `${identity.roster_size} face ref(s) · confirm ${identity.confirm}`
            : 'identity: —'}
        </span>
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={draft.name}
            placeholder="Full name"
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            onKeyDown={(e) => e.key === 'Enter' && add()}
            className={`w-44 ${field}`}
          />
          <input
            value={draft.employee_code}
            placeholder="Code"
            onChange={(e) => setDraft({ ...draft, employee_code: e.target.value })}
            onKeyDown={(e) => e.key === 'Enter' && add()}
            className={`num w-16 ${field}`}
          />
          <input
            value={draft.department}
            placeholder="Department"
            onChange={(e) => setDraft({ ...draft, department: e.target.value })}
            onKeyDown={(e) => e.key === 'Enter' && add()}
            className={`w-28 ${field}`}
          />
          <button
            type="button"
            onClick={add}
            className="rounded-md border border-ink-500/60 bg-ink-800 px-2.5 py-1 text-label text-paper transition-colors hover:bg-ink-700"
          >
            Add employee
          </button>
        </div>
      </div>
      <div className="grid gap-4 p-4 md:grid-cols-2 xl:grid-cols-3">
        {!employees ? (
          <p className="col-span-full py-6 text-center text-sm text-paper-faint">
            Loading employees…
          </p>
        ) : employees.length === 0 ? (
          <p className="col-span-full py-6 text-center text-sm text-paper-faint">
            No employees yet — add the first one above.
          </p>
        ) : (
          employees.map((emp) => (
            <EmployeeCard
              key={emp.employee_id}
              emp={emp}
              onChanged={onChanged}
              onEnrolled={loadIdentity}
            />
          ))
        )}
      </div>
    </section>
  );
}

export default EmployeesPanel;
