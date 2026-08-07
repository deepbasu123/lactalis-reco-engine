// Inline SVG icons — no icon library dependency. 1.75px strokes, rounded,
// sized via currentColor so they inherit brand color.

type P = { size?: number; className?: string };
const base = (size: number) => ({
  width: size,
  height: size,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.75,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
});

export const IconStore = ({ size = 20, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M3 9l1.2-4.2A1 1 0 015.16 4h13.68a1 1 0 01.96.8L21 9" />
    <path d="M4 9v10a1 1 0 001 1h14a1 1 0 001-1V9" />
    <path d="M3 9a2.4 2.4 0 004.5 0 2.4 2.4 0 004.5 0 2.4 2.4 0 004.5 0 2.4 2.4 0 004.5 0" />
    <path d="M9 20v-5h6v5" />
  </svg>
);

export const IconConsole = ({ size = 20, className }: P) => (
  <svg {...base(size)} className={className}>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M7 9l3 3-3 3M13 15h4" />
  </svg>
);

export const IconSpark = ({ size = 18, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M12 3v3M12 18v3M4.2 6.2l2.1 2.1M17.7 17.7l2.1 2.1M3 12h3M18 12h3M4.2 17.8l2.1-2.1M17.7 6.3l2.1-2.1" />
    <circle cx="12" cy="12" r="3.2" />
  </svg>
);

export const IconPlus = ({ size = 18, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M12 5v14M5 12h14" />
  </svg>
);

export const IconCheck = ({ size = 18, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M20 6L9 17l-5-5" />
  </svg>
);

export const IconClock = ({ size = 16, className }: P) => (
  <svg {...base(size)} className={className}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" />
  </svg>
);

export const IconSun = ({ size = 16, className }: P) => (
  <svg {...base(size)} className={className}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </svg>
);

export const IconChevron = ({ size = 18, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M6 9l6 6 6-6" />
  </svg>
);

export const IconArrow = ({ size = 18, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </svg>
);

export const IconShield = ({ size = 18, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3z" />
    <path d="M9 12l2 2 4-4" />
  </svg>
);

export const IconBlock = ({ size = 18, className }: P) => (
  <svg {...base(size)} className={className}>
    <circle cx="12" cy="12" r="9" />
    <path d="M5.6 5.6l12.8 12.8" />
  </svg>
);

export const IconBox = ({ size = 18, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M21 8l-9-5-9 5 9 5 9-5z" />
    <path d="M3 8v8l9 5 9-5V8" />
    <path d="M12 13v8" />
  </svg>
);

export const IconLayers = ({ size = 18, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M12 3l9 5-9 5-9-5 9-5z" />
    <path d="M3 13l9 5 9-5M3 16.5l9 5 9-5" />
  </svg>
);

export const IconSearch = ({ size = 18, className }: P) => (
  <svg {...base(size)} className={className}>
    <circle cx="11" cy="11" r="7" />
    <path d="M21 21l-4.3-4.3" />
  </svg>
);

export const IconChart = ({ size = 18, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
  </svg>
);

export const IconExternal = ({ size = 16, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M14 4h6v6M20 4l-8 8M18 14v5a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1h5" />
  </svg>
);

// Category glyph — a subtle mark stamped on product cards.
export const IconDrop = ({ size = 22, className }: P) => (
  <svg {...base(size)} className={className}>
    <path d="M12 3s6 6.5 6 11a6 6 0 01-12 0c0-4.5 6-11 6-11z" />
  </svg>
);
