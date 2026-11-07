import { defineConfig } from '@vscode/test-cli';

// 1.104.0 is the floor package.json declares: the first @types/vscode with
// lm.registerLanguageModelChatProvider. Running the tests against it means the floor is proven
// rather than assumed.
export default defineConfig({
  files: 'out/test/**/*.test.js',
  version: '1.104.0',
  mocha: {
    // The VS Code samples and these tests use the TDD vocabulary (suite/test), which mocha only
    // defines under this ui.
    ui: 'tdd',
    timeout: 60000,
  },
});
