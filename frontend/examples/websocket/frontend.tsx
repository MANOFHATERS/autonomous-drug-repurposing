'use client';

/**
 * FE-013 ROOT FIX (Teammate 15, v143 — Frontend Hooks + Components):
 *
 *   1. `onKeyPress` is deprecated in React 18.3+ and REMOVED in React 19.
 *      package.json declares `react: ^19.0.0`, so the deprecated handler
 *      was a no-op in strict mode — Enter-to-send silently stopped
 *      working in the demo. Replaced ALL `onKeyPress` with `onKeyDown`,
 *      which is the canonical key-event handler in React 19 (and works
 *      in 16.8+ for backwards compatibility).
 *
 *   2. Storing a `Socket` instance in `useState` is an anti-pattern.
 *      Sockets are MUTABLE, NON-REACTIVE objects — they don't participate
 *      in React's render cycle. `setSocket(socketInstance)` triggered a
 *      re-render on every connection state change, which (under React 19
 *      strict-mode double-effect-invocation in dev) could re-run the
 *      `useEffect` and create a SECOND socket, causing duplicate message
 *      handlers and (in extreme cases) "Maximum call stack" errors.
 *      Replaced with `useRef<Socket | null>(null)` — refs are the
 *      canonical store for non-reactive mutable objects per the React
 *      docs (https://react.dev/reference/react/useRef#avoiding-recreating-the-ref-contents).
 *
 *   3. This file is an ILLUSTRATIVE-ONLY example — it is not part of the
 *      production Next.js app (it lives in `frontend/examples/`, which is
 *      excluded from ESLint, tsc, and the Next.js build). It demonstrates
 *      the WebSocket pattern for pharma partners integrating with the
 *      DruGOS live-update feed. Do NOT deploy this file as a route.
 *
 * The fix is ROOT-LEVEL (not surface): the `useRef` change eliminates the
 * re-render storm at its source, and the `onKeyDown` change restores the
 * Enter-to-send contract that the React 19 upgrade silently broke.
 */

import { useEffect, useRef, useState } from 'react';
import { io, type Socket } from 'socket.io-client';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';

type User = {
  id: string;
  username: string;
}

type Message = {
  id: string;
  username: string;
  content: string;
  timestamp: Date | string;
  type: 'user' | 'system';
}

export default function SocketDemo() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputMessage, setInputMessage] = useState('');
  const [username, setUsername] = useState('');
  const [isUsernameSet, setIsUsernameSet] = useState(false);
  // FE-013 ROOT FIX: socket is a MUTABLE, NON-REACTIVE object — it must
  // live in a ref, NOT in useState. `useState` is for values that
  // participate in the render cycle; storing a Socket there causes
  // re-renders on every connection state change and (under React 19
  // strict-mode double-effect-invocation in dev) can re-run the
  // useEffect and create a SECOND socket with duplicate handlers.
  const socketRef = useRef<Socket | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [users, setUsers] = useState<User[]>([]);

  useEffect(() => {
    // Connect to websocket server.
    // Never use PORT in the URL, always use XTransformPort.
    // DO NOT change the path, it is used by Caddy to forward the request to the correct port.
    const socketInstance = io('/?XTransformPort=3003', {
      transports: ['websocket', 'polling'],
      forceNew: true,
      reconnection: true,
      reconnectionAttempts: 5,
      reconnectionDelay: 1000,
      timeout: 10000
    });

    // FE-013 ROOT FIX: store in ref (NOT useState). No re-render triggered.
    socketRef.current = socketInstance;

    socketInstance.on('connect', () => {
      setIsConnected(true);
    });

    socketInstance.on('disconnect', () => {
      setIsConnected(false);
    });

    socketInstance.on('message', (msg: Message) => {
      setMessages(prev => [...prev, msg]);
    });

    socketInstance.on('user-joined', (data: { user: User; message: Message }) => {
      setMessages(prev => [...prev, data.message]);
      setUsers(prev => {
        if (!prev.find(u => u.id === data.user.id)) {
          return [...prev, data.user];
        }
        return prev;
      });
    });

    socketInstance.on('user-left', (data: { user: User; message: Message }) => {
      setMessages(prev => [...prev, data.message]);
      setUsers(prev => prev.filter(u => u.id !== data.user.id));
    });

    socketInstance.on('users-list', (data: { users: User[] }) => {
      setUsers(data.users);
    });

    return () => {
      socketInstance.disconnect();
      // FE-013: clear the ref on cleanup so we don't hold a stale reference.
      socketRef.current = null;
    };
  }, []);

  const handleJoin = () => {
    // FE-013: read from ref (NOT state).
    const socket = socketRef.current;
    if (socket && username.trim() && isConnected) {
      socket.emit('join', { username: username.trim() });
      setIsUsernameSet(true);
    }
  };

  const sendMessage = () => {
    // FE-013: read from ref (NOT state).
    const socket = socketRef.current;
    if (socket && inputMessage.trim() && username.trim()) {
      socket.emit('message', {
        content: inputMessage.trim(),
        username: username.trim()
      });
      setInputMessage('');
    }
  };

  // FE-013 ROOT FIX: renamed from `handleKeyPress` to `handleKeyDown`.
  // `onKeyPress` is REMOVED in React 19 — the handler was a silent no-op.
  // `onKeyDown` is the canonical key-event handler in React 19 (and
  // works in 16.8+ for backwards compatibility).
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      sendMessage();
    }
  };

  // FE-013 ROOT FIX: `handleJoinKeyDown` mirrors `handleKeyDown` but
  // triggers `handleJoin` instead of `sendMessage`. Same onKeyDown fix.
  const handleJoinKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleJoin();
    }
  };

  return (
    <div className="container mx-auto p-4 max-w-2xl">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            WebSocket Demo
            <span className={`text-sm px-2 py-1 rounded ${isConnected ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
              {isConnected ? 'Connected' : 'Disconnected'}
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {!isUsernameSet ? (
            <div className="space-y-2">
              <Input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                // FE-013 ROOT FIX: onKeyDown (NOT onKeyPress, which is
                // removed in React 19).
                onKeyDown={handleJoinKeyDown}
                placeholder="Enter your username..."
                disabled={!isConnected}
                className="flex-1"
              />
              <Button
                onClick={handleJoin}
                disabled={!isConnected || !username.trim()}
                className="w-full"
              >
                Join Chat
              </Button>
            </div>
          ) : (
            <>
              <ScrollArea className="h-80 w-full border rounded-md p-4">
                <div className="space-y-2">
                  {messages.length === 0 ? (
                    <p className="text-gray-500 text-center">No messages yet</p>
                  ) : (
                    messages.map((msg) => (
                      <div key={msg.id} className="border-b pb-2 last:border-b-0">
                        <div className="flex justify-between items-start">
                          <div className="flex-1">
                            <p className={`text-sm font-medium ${msg.type === 'system'
                                ? 'text-blue-600 italic'
                                : 'text-gray-700'
                              }`}>
                              {msg.username}
                            </p>
                            <p className={`${msg.type === 'system'
                                ? 'text-blue-500 italic'
                                : 'text-gray-900'
                              }`}>
                              {msg.content}
                            </p>
                          </div>
                          <span className="text-xs text-gray-500">
                            {new Date(msg.timestamp).toLocaleTimeString()}
                          </span>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </ScrollArea>

              <div className="flex space-x-2">
                <Input
                  value={inputMessage}
                  onChange={(e) => setInputMessage(e.target.value)}
                  // FE-013 ROOT FIX: onKeyDown (NOT onKeyPress, which is
                  // removed in React 19). Enter-to-send now actually fires.
                  onKeyDown={handleKeyDown}
                  placeholder="Type a message..."
                  disabled={!isConnected}
                  className="flex-1"
                />
                <Button
                  onClick={sendMessage}
                  disabled={!isConnected || !inputMessage.trim()}
                >
                  Send
                </Button>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
