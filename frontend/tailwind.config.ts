import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eef6ff",
          100: "#d9eaff",
          500: "#2b7fff",
          600: "#1f6ae6",
          700: "#1a55b8",
        },
      },
    },
  },
  plugins: [],
};
export default config;
