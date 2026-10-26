const players = [
  [80, 240], [165, 115], [165, 235], [165, 365], [285, 155], [285, 315],
  [365, 225], [440, 110], [445, 350], [525, 180], [555, 290],
];
const opponents = [[625, 240], [535, 100], [525, 365], [440, 205], [440, 290], [350, 110], [345, 365], [260, 240]];

export default function PitchIllustration({ id = "hero" }: { id?: string }) {
  return (
    <svg className="illustrated-pitch" viewBox="0 0 700 480" fill="none"
      role="img" aria-label="Illustrative football tactics diagram, not tracked match observations">
      <defs>
        <linearGradient id={`${id}-field`} x1="0" y1="0" x2="700" y2="480" gradientUnits="userSpaceOnUse">
          <stop stopColor="#173b30" /><stop offset="1" stopColor="#10251f" />
        </linearGradient>
        <radialGradient id={`${id}-space`}>
          <stop stopColor="#d2ee83" stopOpacity=".22" /><stop offset="1" stopColor="#d2ee83" stopOpacity="0" />
        </radialGradient>
        <marker id={`${id}-arrow`} markerWidth="7" markerHeight="7" refX="5" refY="3.5" orient="auto">
          <path d="m0 0 6 3.5L0 7" stroke="#d2ee83" strokeWidth="1.4" />
        </marker>
      </defs>
      <rect width="700" height="480" rx="10" fill={`url(#${id}-field)`} />
      {[0, 2, 4, 6].map((stripe) => <rect key={stripe} x={stripe * 100} width="100" height="480" fill="#fff" opacity=".018" />)}
      <g stroke="#a0b8a8" strokeOpacity=".25" strokeWidth="1">
        <rect x="35" y="35" width="630" height="410" />
        <path d="M350 35v410M35 145h100v190H35m630-190H565v190h100M35 195h40v90H35m630-90h-40v90h40" />
        <circle cx="350" cy="240" r="58" />
        <circle cx="350" cy="240" r="2" fill="#a0b8a8" />
        <path d="M135 194a58 58 0 0 1 0 92m430-92a58 58 0 0 0 0 92" />
      </g>
      <ellipse cx="405" cy="155" rx="110" ry="80" fill={`url(#${id}-space)`} />
      <g stroke="#d2ee83" strokeOpacity=".7" strokeWidth="1.5" strokeDasharray="5 7">
        <path d="M165 235 285 155 365 225" markerEnd={`url(#${id}-arrow)`} />
        <path d="M365 225Q400 165 440 110" markerEnd={`url(#${id}-arrow)`} />
      </g>
      <path d="M440 110q55-6 85 70" stroke="#d2ee83" strokeWidth="2" markerEnd={`url(#${id}-arrow)`} />
      {opponents.map(([x, y], index) => (
        <g key={`opponent-${index}`}>
          <circle cx={x} cy={y} r="12" fill="#1c332d" stroke="#b0bab6" strokeOpacity=".65" />
          <circle cx={x} cy={y} r="3" fill="#b0bab6" />
        </g>
      ))}
      {players.map(([x, y], index) => (
        <g key={`player-${index}`}>
          <circle cx={x} cy={y} r="13" fill="#c6dd8c" fillOpacity=".12" />
          <circle cx={x} cy={y} r="6" fill="#d2ee83" />
        </g>
      ))}
      <circle cx="380" cy="213" r="4" fill="#f6f2e9" stroke="#10251f" strokeWidth="2" />
      <circle cx="440" cy="110" r="23" stroke="#d2ee83" strokeOpacity=".45" />
      <path d="M35 18h90m450 444h90" stroke="#d2ee83" strokeOpacity=".5" />
    </svg>
  );
}
