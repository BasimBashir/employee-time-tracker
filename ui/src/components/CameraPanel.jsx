import React from 'react';
import { api } from '../api.js';

// One MJPEG <img> fed by the service — the live view is just this, no
// per-frame JS. The zone line arrives normalized and is drawn as an SVG over
// the image, so it scales with any displayed size.
export function CameraPanel() {
  const [overlay, setOverlay] = React.useState(null);

  React.useEffect(() => {
    let alive = true;
    api.cameraOverlay().then((o) => alive && setOverlay(o)).catch(() => {});
    return () => { alive = false; };
  }, []);

  const line = overlay?.line;
  return (
    <section className="panel overflow-hidden">
      <div className="flex items-center gap-2 border-b border-ink-500/60 px-4 py-3">
        <h2 className="text-label font-medium uppercase tracking-widest text-paper-faint">
          Live camera
        </h2>
        {line && (
          <span className="ml-auto text-[10px] uppercase tracking-widest text-paper-faint">
            green line = out boundary
          </span>
        )}
      </div>
      <div className="relative bg-ink-800">
        <img
          src={api.cameraStreamUrl()}
          alt="Live camera"
          className="block w-full"
        />
        {line && (
          <svg
            viewBox="0 0 1 1"
            preserveAspectRatio="none"
            className="pointer-events-none absolute inset-0 h-full w-full"
            aria-hidden="true"
          >
            <line
              x1={line[0][0]} y1={line[0][1]}
              x2={line[1][0]} y2={line[1][1]}
              stroke="#2ecc71" strokeWidth="2"
              vectorEffect="non-scaling-stroke" strokeLinecap="round"
            />
          </svg>
        )}
        {!line && (
          <p className="absolute bottom-2 left-3 text-[10px] uppercase tracking-widest text-paper-faint/70">
            No zone line configured
          </p>
        )}
      </div>
    </section>
  );
}

export default CameraPanel;
