/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/**/*.{js,jsx,ts,tsx}",
    "./public/index.html"
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#E8F4FD',
          100: '#C7E5FB',
          500: '#007AFF',
          600: '#0056CC',
          700: '#003D99',
          900: '#001A66'
        },
        accent: {
          50: '#E8F6EC',
          100: '#C7EDD1',
          500: '#34C759',
          600: '#2BA946',
          700: '#228B34'
        },
        gray: {
          50: '#F2F2F7',
          100: '#E5E5EA',
          200: '#D1D1D6',
          300: '#C7C7CC',
          400: '#AEAEB2',
          500: '#8E8E93',
          600: '#636366',
          700: '#48484A',
          800: '#3A3A3C',
          900: '#1C1C1E'
        }
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      boxShadow: {
        'soft': '0 2px 15px rgba(0, 0, 0, 0.08)',
        'card': '0 4px 25px rgba(0, 0, 0, 0.1)',
        'neumorphism': '8px 8px 16px rgba(0, 0, 0, 0.1), -8px -8px 16px rgba(255, 255, 255, 0.7)'
      }
    },
  },
  plugins: [],
}
