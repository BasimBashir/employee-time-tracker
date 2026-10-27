/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Ink base — a cool near-black with a blue cast, never a tinted cliché #0B0B0B
        ink: {
          900: '#0B0E13',
          800: '#101318',
          700: '#161A22',
          600: '#1E232E',
          500: '#2A303C',
          400: '#3A4150',
        },
        paper: {
          DEFAULT: '#E6E9EE',
          soft: '#A9B1C2',
          faint: '#6C768C',
        },
        // Semantic, and the only two saturated colours in the interface.
        // rose = going OUT of the work area. jade = came back IN.
        rose: { DEFAULT: '#E5484D', soft: '#F2686C', bg: '#2A1216' },
        jade: { DEFAULT: '#30A46C', soft: '#3FBF87', bg: '#10251A' },
        amber: '#F5A524',
        // Chart series color for "delivery" time. Validated (dataviz six checks)
        // against the ink-700 panel surface alongside rose: CVD dE 23.0,
        // normal-vision dE 31.9, contrast >= 3:1.
        azure: '#3987E5',
      },
      fontFamily: {
        sans: ['"Space Grotesk"', 'system-ui', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        'display-lg': ['2.5rem', { lineHeight: '1.05', letterSpacing: '-0.02em' }],
        'display-md': ['1.75rem', { lineHeight: '1.15', letterSpacing: '-0.015em' }],
        label: ['0.6875rem', { lineHeight: '1', letterSpacing: '0.08em' }],
      },
      borderRadius: { panel: '10px' },
      keyframes: {
        pulseRing: {
          '0%, 100%': { opacity: '0.35', transform: 'scale(1)' },
          '50%': { opacity: '1', transform: 'scale(1.45)' },
        },
      },
      animation: { 'pulse-ring': 'pulseRing 2s ease-in-out infinite' },
    },
  },
  plugins: [],
};
