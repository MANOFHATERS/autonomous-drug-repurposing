// EXAMPLE ONLY — DO NOT DEPLOY TO PRODUCTION WITHOUT REVIEW
//
// FE-012 ROOT FIX (Teammate 14, MEDIUM):
//
// This file is an EXAMPLE websocket server used by the chat demo. It is
// NOT wired into the production Next.js app (it lives in examples/).
// However, a careless operator might copy it into production. Two
// security defects made that dangerous:
//
//   1. `cors: { origin: "*" }` allowed ANY website to connect and
//      broadcast messages. An attacker could hijack the chat from any
//      origin — including a malicious page that a researcher visits in
//      the same browser session. The browser's same-origin policy does
//      NOT protect socket.io connections by default; CORS is the only
//      gate.
//
//   2. `Math.random().toString(36).substr(2, 9)` generated message IDs.
//      Math.random is NOT cryptographically secure — outputs are
//      predictable, and at scale (~75K messages) collisions occur
//      (birthday paradox). The codebase elsewhere explicitly bans
//      Math.random for security-sensitive IDs (see
//      use-account-data.tsx:55-67 generateSecureId). Message IDs aren't
//      a security-sensitive token, but they MUST be unique — a
//      collision would conflate two distinct chat messages, and a
//      predictable ID enables message-replay tricks in some socket.io
//      configurations.
//
// ROOT FIX:
//   1. Replace `origin: "*"` with an env-var-driven allowlist. The
//      default is `["http://localhost:3000"]` (the Next.js dev server).
//      Production deployments set `WS_ALLOWED_ORIGINS=https://app.drugos.example,https://staging.drugos.example`.
//      Unknown origins are rejected by socket.io at the handshake.
//
//   2. Replace `Math.random()` with `crypto.randomUUID()` (Node 19+).
//      `randomUUID` is cryptographically secure, collision-proof (128
//      bits, version 4 UUID), and standard. For Node 18 and below, we
//      fall back to `randomBytes(16).toString("hex")` (32 hex chars,
//      128 bits of entropy — equivalent collision resistance).
//
//   3. Add the "EXAMPLE ONLY — DO NOT DEPLOY" header at the top of the
//      file so operators reading the source see the warning before they
//      think about copying it into production.

import { createServer } from 'http'
import { Server } from 'socket.io'
import { randomUUID, randomBytes } from 'crypto'

const httpServer = createServer()

// FE-012 ROOT FIX: env-var-driven CORS allowlist.
//
// Production deployments set WS_ALLOWED_ORIGINS to a comma-separated
// list of allowed origins (e.g., "https://app.drugos.example,https://staging.drugos.example").
// The default is localhost:3000 (the Next.js dev server) so the demo
// works out-of-the-box locally. An empty allowlist would block ALL
// origins — but we default to localhost so the example is usable.
//
// Origins are matched EXACTLY (case-sensitive, including scheme). socket.io
// also supports regex origins, but exact matching is safer — a regex
// mistake could accidentally allow a broader origin than intended.
const allowedOrigins = (process.env.WS_ALLOWED_ORIGINS || 'http://localhost:3000')
  .split(',')
  .map(s => s.trim())
  .filter(s => s.length > 0)

const io = new Server(httpServer, {
  // DO NOT change the path, it is used by Caddy to forward the request to the correct port
  path: '/',
  cors: {
    // FE-012 ROOT FIX: explicit allowlist — no wildcard.
    origin: (origin, callback) => {
      // socket.io passes `origin: undefined` for same-origin / server-to-server
      // connections (where the browser doesn't send an Origin header). We
      // allow those — the CORS check is only for browser cross-origin requests.
      if (!origin) return callback(null, true)
      if (allowedOrigins.includes(origin)) {
        return callback(null, true)
      }
      // Reject unknown origins. The error message is logged server-side
      // (via the callback's Error) but socket.io does NOT echo it to the
      // client (to avoid leaking the allowlist). The client just sees
      // "cors rejected".
      console.warn(`[websocket] CORS rejected origin: ${origin}`)
      return callback(new Error('Origin not allowed by CORS policy'), false)
    },
    methods: ['GET', 'POST'],
  },
  pingTimeout: 60000,
  pingInterval: 25000,
})

interface User {
  id: string
  username: string
}

interface Message {
  id: string
  username: string
  content: string
  timestamp: Date
  type: 'user' | 'system'
}

const users = new Map<string, User>()

// FE-012 ROOT FIX: replace Math.random with crypto.randomUUID.
//
// `randomUUID` is available in Node 19+ (and Node 18 with the
// `--experimental-global-webcrypto` flag, but we don't rely on that).
// For older Node versions, we fall back to `randomBytes(16).toString("hex")`
// — 32 hex chars, 128 bits of entropy, equivalent collision resistance
// to a version-4 UUID.
//
// `randomUUID` is cryptographically secure (uses OpenSSL's
// RAND_bytes under the hood) and collision-proof (122 bits of entropy
// after the version/variant bits are reserved). Math.random has only
// ~52 bits of entropy and is NOT cryptographically secure — an
// attacker observing a few IDs could predict future ones.
const generateMessageId = (): string => {
  if (typeof randomUUID === 'function') {
    return randomUUID()
  }
  // Node 18 / older — fall back to randomBytes.
  return randomBytes(16).toString('hex')
}

const createSystemMessage = (content: string): Message => ({
  id: generateMessageId(),
  username: 'System',
  content,
  timestamp: new Date(),
  type: 'system'
})

const createUserMessage = (username: string, content: string): Message => ({
  id: generateMessageId(),
  username,
  content,
  timestamp: new Date(),
  type: 'user'
})

io.on('connection', (socket) => {
  console.log(`User connected: ${socket.id}`)

  // Add test event handler
  socket.on('test', (data) => {
    console.log('Received test message:', data)
    socket.emit('test-response', {
      message: 'Server received test message',
      data: data,
      timestamp: new Date().toISOString()
    })
  })

  socket.on('join', (data: { username: string }) => {
    const { username } = data

    // Create user object
    const user: User = {
      id: socket.id,
      username
    }

    // Add to user list
    users.set(socket.id, user)

    // Send join message to all users
    const joinMessage = createSystemMessage(`${username} joined the chat room`)
    io.emit('user-joined', { user, message: joinMessage })

    // Send current user list to new user
    const usersList = Array.from(users.values())
    socket.emit('users-list', { users: usersList })

    console.log(`${username} joined the chat room, current online users: ${users.size}`)
  })

  socket.on('message', (data: { content: string; username: string }) => {
    const { content, username } = data
    const user = users.get(socket.id)

    if (user && user.username === username) {
      const message = createUserMessage(username, content)
      io.emit('message', message)
      console.log(`${username}: ${content}`)
    }
  })

  socket.on('disconnect', () => {
    const user = users.get(socket.id)

    if (user) {
      // Remove from user list
      users.delete(socket.id)

      // Send leave message to all users
      const leaveMessage = createSystemMessage(`${user.username} left the chat room`)
      io.emit('user-left', { user: { id: socket.id, username: user.username }, message: leaveMessage })

      console.log(`${user.username} left the chat room, current online users: ${users.size}`)
    } else {
      console.log(`User disconnected: ${socket.id}`)
    }
  })

  socket.on('error', (error) => {
    console.error(`Socket error (${socket.id}):`, error)
  })
})

const PORT = 3003
httpServer.listen(PORT, () => {
  console.log(`WebSocket server running on port ${PORT}`)
  console.log(`Allowed origins: ${allowedOrigins.join(', ')}`)
})

// Graceful shutdown
process.on('SIGTERM', () => {
  console.log('Received SIGTERM signal, shutting down server...')
  httpServer.close(() => {
    console.log('WebSocket server closed')
    process.exit(0)
  })
})

process.on('SIGINT', () => {
  console.log('Received SIGINT signal, shutting down server...')
  httpServer.close(() => {
    console.log('WebSocket server closed')
    process.exit(0)
  })
})
