/**
 * Ambient module declarations for OPTIONAL runtime dependencies.
 *
 * These packages are loaded via dynamic `import()` at runtime ONLY when
 * the corresponding env var is set (e.g. REDIS_URL → ioredis). They are
 * NOT listed in package.json `dependencies` because the platform needs to
 * run without them in single-instance dev/test mode.
 *
 * In production multi-instance deployments, the operator installs them
 * explicitly: `npm install ioredis`.
 *
 * This .d.ts file gives TypeScript enough type info to compile. The actual
 * runtime behavior is unchanged — if the package is missing, the dynamic
 * import throws and the caller falls back to the in-memory implementation
 * (which is the correct single-instance dev behavior).
 */

declare module "ioredis" {
  // Minimal surface area we use. The real `ioredis` package has many more
  // methods, but we only call `multi`, `scan`, and `del` directly. The
  // MULTI/EXEC builder methods (zremrangebyscore, zadd, zcard, pexpire)
  // are chained off `multi()` and are typed as `any` here.
  export interface RedisCommand {
    zremrangebyscore(key: string, min: string | number, max: string | number): RedisCommand;
    zadd(key: string, score: number, member: string): RedisCommand;
    zcard(key: string): RedisCommand;
    pexpire(key: string, ms: number): RedisCommand;
    exec(): Promise<Array<[Error | null, any]>>;
  }
  export interface RedisClient {
    multi(): RedisCommand;
    scan(cursor: string, ...args: any[]): Promise<[string, string[]]>;
    del(...keys: string[]): Promise<number>;
  }
  const Redis: {
    new (url: string, opts?: Record<string, unknown>): RedisClient;
  };
  export default Redis;
}
