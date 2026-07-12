/**
 * Jest config for the frontend test suite.
 *
 * Uses ts-jest to transform TypeScript. The test environment is node because
 * all tests are backend/service tests (no DOM).
 */
module.exports = {
  preset: "ts-jest",
  testEnvironment: "node",
  testMatch: [
    "<rootDir>/src/lib/services/__tests__/**/*.test.ts",
    "<rootDir>/src/lib/auth/__tests__/**/*.test.ts",
    "<rootDir>/src/app/api/**/__tests__/**/*.test.ts",
    "<rootDir>/tests/api/**/*.test.ts",
  ],
  moduleNameMapper: {
    "^@/(.*)$": "<rootDir>/src/$1",
  },
  transform: {
    "^.+\\.tsx?$": [
      "ts-jest",
      {
        tsconfig: {
          target: "ES2022",
          module: "CommonJS",
          esModuleInterop: true,
          skipLibCheck: true,
          resolveJsonModule: true,
          allowJs: true,
        },
      },
    ],
  },
  transformIgnorePatterns: [
    "/node_modules/(?!(next|@prisma/client|@radix-ui|lucide-react))",
  ],
  testTimeout: 60000,
};
