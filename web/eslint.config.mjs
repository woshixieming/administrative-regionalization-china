import { defineConfig, globalIgnores } from "eslint/config";
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import hooks from "eslint-plugin-react-hooks";

export default defineConfig([
  js.configs.recommended,
  ...tseslint.configs.recommended,
  hooks.configs.flat.recommended,
  {
    languageOptions: {
      globals: Object.fromEntries(["window", "document", "navigator", "fetch", "URL", "Blob", "AbortController", "DOMException", "setTimeout", "clearTimeout", "setInterval", "clearInterval", "console"].map((key) => [key, "readonly"])),
    },
    rules: {
      // 取消异步请求时同步清理临时提示，保留现有作用域切换行为。
      "react-hooks/set-state-in-effect": "off",
    },
  },
  globalIgnores([".next/**", "out/**", "node_modules/**", "next-env.d.ts", "public/data/**"]),
]);
