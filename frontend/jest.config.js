/**
<<<<<<< HEAD
 * Backend test suite — Jest config.
=======
 * Jest config for the frontend test suite.
 *
 * Uses ts-jest to transform TypeScript. The test environment is node because
 * all tests are backend/service tests (no DOM).
>>>>>>> fix/v101-forensic-root-fixes-20-critical-bugs
 */
module.exports = {
  preset: "ts-jest",
  testEnvironment: "node",
<<<<<<< HEAD
  testMatch: ["<rootDir>/tests/api/**/*.test.ts", "<rootDir>/src/lib/services/__tests__/**/*.test.ts"],
=======
  testMatch: [
    "<rootDir>/src/lib/services/__tests__/**/*.test.ts",
    "<rootDir>/tests/api/**/*.test.ts",
  ],
>>>>>>> fix/v101-forensic-root-fixes-20-critical-bugs
  moduleNameMapper: {
    "^@/(.*)$": "<rootDir>/src/$1",
  },
  transform: {
<<<<<<< HEAD
    "^.+\\.tsx?$": ["ts-jest", { tsconfig: { target: "ES2022", module: "CommonJS", esModuleInterop: true, skipLibCheck: true, resolveJsonModule: true } }],
  },
  transformIgnorePatterns: ["/node_modules/(?!(next|@prisma/client|@radix-ui|lucide-react))"],
  setupFiles: ["<rootDir>/tests/api/env.ts"],
  setupFilesAfterEnv: ["<rootDir>/tests/api/setup.ts"],
  testTimeout: 60000,
  collectCoverageFrom: [
    "src/lib/services/**/*.ts",
    "src/lib/auth/**/*.ts",
    "src/app/api/**/*.ts",
  ],
=======
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
>>>>>>> fix/v101-forensic-root-fixes-20-critical-bugs
};
