/**
 * The rotating globe behind the opening.
 *
 * SVG and CSS transforms rather than WebGL: the console already ships 206 kB
 * of JavaScript and a decorative background is not worth a 3D library on top
 * of it. The whole thing is one `<svg>` of ellipses.
 *
 * How the rotation works, because it is not obvious from the markup:
 *
 *   A meridian seen from outside a sphere projects to an ellipse with the
 *   sphere's radius for its vertical axis and, for its horizontal axis,
 *   R·cos(angle). As the globe turns, that horizontal axis sweeps R → 0 → −R
 *   → 0 → R: the meridian flattens to a vertical line when it is edge-on and
 *   opens out again on the far side. So each meridian is an ellipse animated
 *   on `scaleX` from 1 to −1, each one started at a different offset. That
 *   single trick is the entire illusion, and it costs one GPU-composited
 *   transform per meridian.
 *
 *   Parallels do not move. Their horizontal axis is √(R² − y²) and their
 *   vertical axis is that times the tilt, which is why they bunch toward the
 *   poles on their own.
 *
 * Under prefers-reduced-motion the sweep stops and the globe is simply drawn,
 * which is still a globe.
 */

const R = 92;
const MERIDIANS = 14;
const PARALLELS = 9;
/** How far the pole is tipped towards the viewer; 0 would be a flat disc. */
const TILT = 0.26;
const SPIN_SECONDS = 42;

export default function Globe() {
  // Evenly spaced latitudes, poles excluded: an ellipse of zero width is not
  // worth drawing.
  const parallels = Array.from({ length: PARALLELS }, (_, i) => {
    const t = (i + 1) / (PARALLELS + 1);          // 0..1 down the sphere
    const y = -R + 2 * R * t;
    const rx = Math.sqrt(Math.max(0, R * R - y * y));
    return { y, rx, ry: rx * TILT };
  });

  return (
    <div className="globe" aria-hidden="true">
      <svg viewBox="-110 -110 220 220" role="presentation" focusable="false">
        <defs>
          <radialGradient id="globe-fill" cx="38%" cy="32%">
            <stop offset="0%" stopColor="rgba(167,139,250,.34)" />
            <stop offset="55%" stopColor="rgba(109,59,245,.18)" />
            <stop offset="100%" stopColor="rgba(50,22,120,.05)" />
          </radialGradient>
          <radialGradient id="globe-rim" cx="50%" cy="50%">
            <stop offset="0%" stopColor="transparent" />
            <stop offset="82%" stopColor="transparent" />
            <stop offset="97%" stopColor="rgba(196,166,255,.55)" />
            <stop offset="100%" stopColor="rgba(196,166,255,.12)" />
          </radialGradient>
          {/* Keeps every line inside the sphere, so a meridian at its widest
              does not spill past the rim. */}
          <clipPath id="globe-clip">
            <circle cx="0" cy="0" r={R} />
          </clipPath>
        </defs>

        <circle cx="0" cy="0" r={R} fill="url(#globe-fill)" />

        <g clipPath="url(#globe-clip)" className="globe-lines">
          <g transform={`rotate(-10)`}>
            {parallels.map((p, i) => (
              <ellipse key={`p${i}`} cx="0" cy={p.y} rx={p.rx} ry={p.ry} className="globe-par" />
            ))}

            {Array.from({ length: MERIDIANS }, (_, i) => (
              <ellipse
                key={`m${i}`}
                cx="0"
                cy="0"
                rx={R}
                ry={R}
                className="globe-mer"
                style={{ animationDelay: `${(-SPIN_SECONDS / MERIDIANS) * i}s` }}
              />
            ))}
          </g>
        </g>

        <circle cx="0" cy="0" r={R} fill="url(#globe-rim)" />
        <circle cx="0" cy="0" r={R} className="globe-edge" />
      </svg>
    </div>
  );
}
