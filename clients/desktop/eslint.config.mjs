import js from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist/', 'bundled/', 'node_modules/', 'test-results/'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['src/renderer/**/*.ts'],
    languageOptions: { globals: globals.browser },
    rules: {
      'no-restricted-properties': [
        'error',
        { property: 'innerHTML', message: 'Build the DOM with textContent.' },
        { property: 'outerHTML', message: 'Build the DOM with textContent.' },
      ],
    },
  },
  { files: ['scripts/**/*.mjs', '*.config.*'], languageOptions: { globals: globals.node } },
);
