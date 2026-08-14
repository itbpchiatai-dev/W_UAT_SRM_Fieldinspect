import type { Config } from 'tailwindcss';

/*
 * Tokens live in src/index.css as HSL triplets — Tailwind references
 * them via the hsl() + CSS-variable pattern so opacity modifiers
 * (e.g. bg-primary/50) work. To rebrand: edit index.css only.
 * Do not hardcode hex here.
 */
export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    // Mobile-first breakpoints (Tailwind default)
    // sm: 640px, md: 768px, lg: 1024px, xl: 1280px, 2xl: 1536px
    extend: {
      colors: {
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
          readable: 'hsl(var(--accent-readable))',
          warm: 'hsl(var(--accent-warm))',
          'warm-foreground': 'hsl(var(--accent-warm-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        success: {
          DEFAULT: 'hsl(var(--success))',
          foreground: 'hsl(var(--success-foreground))',
          readable: 'hsl(var(--success-readable))',
        },
        warning: {
          DEFAULT: 'hsl(var(--warning))',
          foreground: 'hsl(var(--warning-foreground))',
          readable: 'hsl(var(--warning-readable))',
        },
        info: {
          DEFAULT: 'hsl(var(--info))',
          foreground: 'hsl(var(--info-foreground))',
        },
        chart: {
          blue: 'hsl(var(--chart-blue))',
          'blue-deep': 'hsl(var(--chart-blue-deep))',
          '1': 'hsl(var(--chart-1))',
          '2': 'hsl(var(--chart-2))',
          '3': 'hsl(var(--chart-3))',
          '4': 'hsl(var(--chart-4))',
          '5': 'hsl(var(--chart-5))',
          '6': 'hsl(var(--chart-6))',
          '7': 'hsl(var(--chart-7))',
          '8': 'hsl(var(--chart-8))',
        },
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
      // Brand type — self-hosted via @fontsource (imported in main.tsx).
      // `font-sans` (default body/UI) = IBM Plex Sans Thai: crisp at small
      // sizes for tables/forms. `font-display` (headings) = Prompt: the
      // distinctive CT-brand voice. Thai-capable fallbacks first, then
      // system, so text never falls back to a tofu box.
      fontFamily: {
        sans: ['"IBM Plex Sans Thai"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        display: ['Prompt', '"IBM Plex Sans Thai"', 'ui-sans-serif', 'sans-serif'],
      },
      fontSize: {
        xs: ['0.8125rem', { lineHeight: '1.6' }],
        sm: ['0.9375rem', { lineHeight: '1.6' }],
        base: ['1rem', { lineHeight: '1.65' }],
        lg: ['1.125rem', { lineHeight: '1.55' }],
        xl: ['1.3125rem', { lineHeight: '1.45' }],
      },
    },
  },
  plugins: [],
} satisfies Config;
